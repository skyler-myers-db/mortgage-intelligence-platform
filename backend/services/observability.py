"""Structured logging + per-request correlation for Module 0.

Slice-13 observability seam. The app runs on live Unity Catalog +
Lakebase + Genie; when one of those flakes in production, an operator
needs to grep a single correlation ID across every downstream call and
see timing + outcome + breaker transitions in one stream.

Design constraints:

* **Stdlib logging core.** We reuse ``logging.LogRecord`` + a custom
  ``Formatter`` and carry correlation IDs through ``ContextVar``. When
  ``MIP_OTEL_ENDPOINT`` is configured, the optional OTLP handler ships the
  same structured records to an external collector.
* **Drop-in addition.** Every existing ``logging.getLogger(__name__)``
  keeps working -- this module layers on top via a root ``Formatter``.
  Ad-hoc callers see structured output "for free"; callers that opt in
  via :func:`emit` get the richer ``event``/``dependency``/``duration_ms``
  keys.
* **PII-safe.** The ``_DENYLIST`` matches
  ``backend.services.pii_redaction._FORBIDDEN_OUTPUT_KEYS`` plus
  ``token``/``password``/``authorization``/``set-cookie``. Any field in
  an ``emit(**kwargs)`` payload whose lowercase name starts with a
  denylisted prefix is replaced with ``"<redacted>"`` before JSON
  serialization. Nested dicts are walked one level (log payloads are
  shallow by convention).
* **Cheap.** We construct a ``LogRecord`` and let the logging module
  decide whether to emit -- no JSON work happens for a filtered record.
  The formatter skips empty/None optional keys so a tight line is ~200
  bytes.

Public surface:

* :data:`correlation_id_var` -- per-request ``ContextVar``.
* :func:`get_correlation_id` -- returns current or mints a fresh UUID.
* :func:`set_correlation_id` / :func:`reset_correlation_id` -- explicit
  token-based management for middleware.
* :func:`emit` -- structured log helper; attaches correlation_id.
* :func:`timed_dependency` -- context manager that emits
  ``dependency_call_start`` / ``dependency_call_end`` with duration and
  outcome.
* :func:`configure_logging` -- idempotent root wiring.
* :class:`StructuredFormatter` -- JSON-line formatter.
* :func:`record_breaker_state_change` -- counter hook so
  ``/api/health`` can surface recent-hour breaker churn.
* :func:`record_error` -- counter hook for recent error count.
* :func:`recent_breaker_state_changes` / :func:`recent_error_count`
  -- rolling-hour read helpers for health payload.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Correlation-ID context
# ---------------------------------------------------------------------------

correlation_id_var: ContextVar[str | None] = ContextVar("mip_correlation_id", default=None)

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+", re.IGNORECASE)
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)")
_BORROWER_ID_RE = re.compile(r"B-[A-Za-z0-9][A-Za-z0-9_-]{0,126}")
_CLIP_ID_RE = re.compile(r"CL-[A-Za-z0-9][A-Za-z0-9_-]{1,126}")
_LONG_DIGIT_RE = re.compile(r"\d{9,}")
_CORRELATION_ID_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    _EMAIL_RE,
    _SSN_RE,
    _PHONE_RE,
    _BORROWER_ID_RE,
    _CLIP_ID_RE,
    _LONG_DIGIT_RE,
)


def is_safe_correlation_id(value: str) -> bool:
    """Return whether ``value`` is a safe caller-supplied correlation ID."""
    return bool(_CORRELATION_ID_PATTERN.fullmatch(value)) and not any(
        pattern.search(value) for pattern in _CORRELATION_ID_PII_PATTERNS
    )


def sanitize_correlation_id(raw: str | None) -> str | None:
    """Return a stripped safe correlation ID, or ``None`` when unsafe."""
    if raw is None:
        return None
    value = raw.strip()
    return value if is_safe_correlation_id(value) else None


def get_correlation_id() -> str:
    """Return the current correlation ID, minting a fresh UUID if absent.

    We do NOT set the var when we mint -- callers (middleware, tests)
    own the lifecycle via :func:`set_correlation_id`. Emitting a fresh
    ID here just keeps every log line tagged even when a background
    task runs outside a request scope.
    """
    cid = correlation_id_var.get()
    if cid:
        return cid
    return uuid.uuid4().hex


def set_correlation_id(value: str) -> Token[str | None]:
    """Bind ``value`` to the current async context and return the token.

    Caller (middleware) must pass the token to
    :func:`reset_correlation_id` when the request finishes so async tasks
    spawned later don't inherit a stale ID.
    """
    return correlation_id_var.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    correlation_id_var.reset(token)


# ---------------------------------------------------------------------------
# PII denylist. Mirrors _FORBIDDEN_OUTPUT_KEYS from pii_redaction with
# auth/secret additions. We match case-insensitive *prefix* so e.g.
# ``owner_name_hash`` is caught by ``owner_name`` and
# ``situs_street_raw`` is caught by ``situs_street``.
# ---------------------------------------------------------------------------

_DENYLIST_PREFIXES: tuple[str, ...] = (
    "owner_name",
    "owner_1_full_name",
    "owner_full_name",
    "buyer_1_full_name",
    "buyer_full_name",
    "mailing_",
    "situs_street",
    "trigger_timeline_json",
    "token",
    "password",
    "authorization",
    "set-cookie",
    "api_key",
    "apikey",
    "secret",
    "cookie",
)

_REDACTED = "<redacted>"

_SECRET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"),
        r"\1<redacted>",
    ),
    (
        re.compile(
            r"(?i)\b(token|password|api[_-]?key|apikey|secret|set-cookie|cookie)(\s*[:=]\s*)[^\s,;]+"
        ),
        r"\1\2<redacted>",
    ),
    (
        re.compile(r"(?i)(https?://)[^/@\s]+@"),
        r"\1<redacted>@",
    ),
    (
        _BORROWER_ID_RE,
        "B-<redacted>",
    ),
    (
        _CLIP_ID_RE,
        "CL-<redacted>",
    ),
    (
        _EMAIL_RE,
        "<email-redacted>",
    ),
    (
        _SSN_RE,
        "<ssn-redacted>",
    ),
    (
        _PHONE_RE,
        "<phone-redacted>",
    ),
    (
        _LONG_DIGIT_RE,
        "<numeric-id-redacted>",
    ),
    (
        re.compile(
            r"\b\d{1,6}\s+[A-Za-z0-9 .'-]+"
            r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|"
            r"court|ct|way|place|pl)\b",
            re.IGNORECASE,
        ),
        "<street-redacted>",
    ),
)


def _is_denylisted(key: str) -> bool:
    k = key.lower()
    if any(k.startswith(p) for p in _DENYLIST_PREFIXES):
        return True

    normalized = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
    if any(normalized.startswith(p.replace("-", "_")) for p in _DENYLIST_PREFIXES):
        return True
    if normalized in {"authorization", "cookie", "set_cookie", "token", "password", "secret"}:
        return True
    return (
        normalized.endswith("_token")
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
        or normalized.endswith("_cookie")
        or "api_key" in normalized
        or "apikey" in normalized
    )


def _scrub(value: Any) -> Any:
    """Walk a shallow mapping and redact denylisted keys.

    Log payloads are conventionally flat (`emit(event, key=value, ...)`),
    but we walk one level of nested dicts/lists so a bundled
    ``context={...}`` argument can still be filtered safely. Deeper
    structures are emitted as-is; operators should not be putting raw
    row dicts into log kwargs.
    """
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _is_denylisted(str(k)):
                out[k] = _REDACTED
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(value, list | tuple):
        scrubbed = [_scrub(v) for v in value]
        return scrubbed if isinstance(value, list) else tuple(scrubbed)
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _filter_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new dict with denylisted top-level keys redacted and
    values walked one level. Keys that *are* denylisted are kept in the
    output (so ops can see *that* a forbidden field was passed) but
    with ``<redacted>`` as the value."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if _is_denylisted(str(k)):
            out[k] = _REDACTED
        else:
            out[k] = _scrub(v)
    return out


def _scrub_text(value: str) -> str:
    """Redact obvious secret fragments in ad-hoc log messages."""
    scrubbed = value
    for pattern, replacement in _SECRET_TEXT_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


# ---------------------------------------------------------------------------
# StructuredFormatter
# ---------------------------------------------------------------------------


class StructuredFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    Required keys: ``ts`` (ISO-8601 UTC), ``level``, ``logger``,
    ``event``, ``correlation_id``. Optional keys that vary per call:
    ``dependency``, ``duration_ms``, ``outcome``, plus whatever extra
    kwargs the caller attached via :func:`emit`.

    For records emitted outside :func:`emit` (existing ``log.info("...")``
    calls), we still produce valid JSON: ``event`` falls back to the
    module's configured value or the formatted message, and the
    correlation ID is whatever is in the ContextVar (or ``"-"``). This
    keeps every line grep-able without forcing a rewrite of existing
    call sites.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Prefer the structured fields that :func:`emit` attaches; fall
        # back to the standard message for ad-hoc callers.
        event = getattr(record, "mip_event", None) or record.name.split(".")[-1]
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
            "correlation_id": getattr(record, "mip_correlation_id", None)
            or correlation_id_var.get()
            or "-",
        }

        # Structured dependency/duration/outcome are first-class keys so
        # operators can filter quickly. Only emit when present.
        for key in ("dependency", "duration_ms", "outcome"):
            attr = f"mip_{key}"
            if hasattr(record, attr):
                payload[key] = getattr(record, attr)

        # Free-form extras attached by emit(**kwargs) -- already scrubbed.
        extras: Mapping[str, Any] | None = getattr(record, "mip_extras", None)
        if extras:
            for k, v in extras.items():
                # Don't let extras overwrite the required top-level fields.
                if k in payload:
                    continue
                payload[k] = v

        # Pull the rendered message in only when no structured event was
        # set -- otherwise ``msg`` duplicates ``event``.
        if "mip_event" not in record.__dict__:
            payload["message"] = _scrub_text(record.getMessage())

        # If there's an exc_info, include a short summary. Don't dump the
        # full traceback at INFO; callers opt in via logger.exception.
        if record.exc_info:
            exc_type, exc_val, _tb = record.exc_info
            if exc_type is not None:
                payload["exc_type"] = exc_type.__name__
                if exc_val is not None:
                    payload["exc_msg"] = _scrub_text(str(exc_val)[:500])

        return json.dumps(payload, default=str, separators=(",", ":"))


# ---------------------------------------------------------------------------
# emit() helper
# ---------------------------------------------------------------------------


def emit(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    dependency: str | None = None,
    duration_ms: float | None = None,
    outcome: str | None = None,
    **kwargs: Any,
) -> None:
    """Log a structured event. All keyword args become top-level JSON fields.

    ``correlation_id`` is attached automatically from the ContextVar
    (or freshly minted if empty). PII-denylisted keys are replaced with
    ``<redacted>`` -- the denylist is load-bearing; see module docstring.
    """
    if not logger.isEnabledFor(level):
        return

    extras = _filter_payload(kwargs) if kwargs else {}
    cid = correlation_id_var.get() or get_correlation_id()

    # Hand the structured payload to the logging machinery via ``extra``;
    # StructuredFormatter reads the ``mip_*`` attributes it adds. We keep
    # ``msg`` terse for non-JSON fallback handlers (e.g. pytest capture).
    logger.log(
        level,
        event,
        extra={
            "mip_event": event,
            "mip_correlation_id": cid,
            "mip_dependency": dependency,
            "mip_duration_ms": duration_ms,
            "mip_outcome": outcome,
            "mip_extras": extras,
        },
    )


# ---------------------------------------------------------------------------
# timed_dependency -- context manager that times a dependency call
# ---------------------------------------------------------------------------


@contextmanager
def timed_dependency(
    name: str,
    operation: str,
    *,
    logger: logging.Logger | None = None,
    **extra: Any,
) -> Iterator[dict[str, Any]]:
    """Emit ``dependency_call_start`` + ``dependency_call_end``.

    ``name`` is the dependency short name (``warehouse`` / ``lakebase``
    / ``genie``); ``operation`` is a verb-phrase (``execute`` / ``ask``
    / ``fetchone``). The yielded dict is a free-form bag the caller can
    mutate during the block -- e.g. ``ctx["rows_returned"] = len(rows)``.
    Those keys become top-level JSON fields on the ``_end`` record.

    On exception we emit ``outcome="error"`` + ``exc_type``/``exc_msg``
    and re-raise. The caller's ``except`` block sees the original
    exception unchanged.
    """
    log = logger or logging.getLogger("mip.dependency")
    ctx: dict[str, Any] = dict(extra)
    # Ensure every call has a correlation ID even if no middleware ran
    # (e.g. background warm-start).
    token = set_correlation_id(get_correlation_id()) if correlation_id_var.get() is None else None

    emit(log, "dependency_call_start", dependency=name, operation=operation, **ctx)
    start = time.monotonic()
    try:
        yield ctx
    except BaseException as exc:
        duration_ms = (time.monotonic() - start) * 1000.0
        emit(
            log,
            "dependency_call_end",
            level=logging.WARNING,
            dependency=name,
            duration_ms=round(duration_ms, 2),
            outcome="error",
            operation=operation,
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
            **ctx,
        )
        record_error(dependency=name, exc_type=type(exc).__name__)
        raise
    else:
        duration_ms = (time.monotonic() - start) * 1000.0
        emit(
            log,
            "dependency_call_end",
            dependency=name,
            duration_ms=round(duration_ms, 2),
            outcome="ok",
            operation=operation,
            **ctx,
        )
    finally:
        if token is not None:
            reset_correlation_id(token)


# ---------------------------------------------------------------------------
# configure_logging -- idempotent root wiring
# ---------------------------------------------------------------------------


_CONFIGURED = False
_CONFIGURE_LOCK = threading.Lock()

# Slice-13 follow-up: we remember the OTLP handler we installed (if any)
# so tests can inspect / detach it without touching the root logger's
# full handler list. ``None`` means "no exporter wired this process".
_OTEL_HANDLER: logging.Handler | None = None


def _safe_otel_endpoint_for_log(endpoint: str) -> str:
    """Return a non-secret endpoint label for boot diagnostics.

    OTLP credentials belong in ``MIP_OTEL_HEADERS``, but operators
    sometimes paste tokens into URL userinfo, query strings, fragments,
    or path segments. Boot logs must never preserve those accidental
    secrets. Keep only scheme + host + port, and indicate that a path
    existed without printing it.
    """
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return "<configured>"

    if not parts.scheme or not parts.hostname:
        return "<configured>"

    netloc = parts.hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    path_marker = "/..." if parts.path and parts.path != "/" else ""
    return urlunsplit((parts.scheme, netloc, path_marker, "", ""))


def _parse_otel_headers(raw_headers: str) -> dict[str, str]:
    """Parse ``MIP_OTEL_HEADERS`` without logging values."""
    headers: dict[str, str] = {}
    if not raw_headers:
        return headers
    for part in raw_headers.split(","):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if k:
            headers[k] = v
    return headers


def configure_logging(level: str | int = "INFO") -> None:
    """Install :class:`StructuredFormatter` on the root handler.

    Idempotent: a second call is a no-op. Also wires ``uvicorn.access``
    and ``uvicorn.error`` so request logs share the JSON shape; those
    loggers normally carry their own formatter.

    Slice-13 follow-up: when ``MIP_OTEL_ENDPOINT`` is set we additionally
    attach an OTLP log exporter handler so every structured JSON line
    is shipped to a durable workspace-external sink. See
    :func:`_install_otel_handler_if_configured` for the failure posture.
    """
    global _CONFIGURED
    with _CONFIGURE_LOCK:
        if _CONFIGURED:
            return

        if isinstance(level, str):
            level_value = logging.getLevelName(level.upper())
            if not isinstance(level_value, int):
                level_value = logging.INFO
        else:
            level_value = level

        root = logging.getLogger()
        # Ensure there's at least one handler.
        if not root.handlers:
            root.addHandler(logging.StreamHandler())
        for handler in root.handlers:
            handler.setFormatter(StructuredFormatter())
        root.setLevel(level_value)

        # Uvicorn owns its own loggers with non-propagating handlers;
        # swap their formatter so access lines are JSON too.
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            lg = logging.getLogger(name)
            for handler in lg.handlers:
                handler.setFormatter(StructuredFormatter())

        _install_otel_handler_if_configured(root, level_value)

        _CONFIGURED = True


def _install_otel_handler_if_configured(root: logging.Logger, level_value: int) -> None:
    """Attach an OTLP log-exporter handler when ``MIP_OTEL_ENDPOINT`` is set.

    This is a best-effort hook. When the env var is unset we do nothing
    (the default stdout JSON stream is the only sink, same as Slice-13).

    When the env var IS set:

    * If ``opentelemetry-sdk`` + ``opentelemetry-exporter-otlp`` aren't
      importable we log a ``WARNING`` (via the stdlib root, which is
      already wired with :class:`StructuredFormatter`) and keep serving
      traffic. A missing-but-optional exporter must NOT crash the app.
    * On success we register the handler at module scope so tests can
      introspect it.

    We deliberately avoid importing the ``opentelemetry`` packages at
    module import time -- importing them is the expensive part on cold
    start, and the common case (no env var) should not pay for it.
    """
    global _OTEL_HANDLER
    # Late import to avoid a circular dep at module import time.
    from backend.config.settings import settings as _settings

    endpoint = _settings.mip_otel_endpoint
    if not endpoint:
        return
    endpoint_label = _safe_otel_endpoint_for_log(endpoint)

    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter,
        )
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:
        # Optional dep absent. Warn loudly and keep going -- the caller
        # turned OTEL on intentionally, but the runtime isn't allowed to
        # crash because of a missing wheel.
        emit(
            logging.getLogger("mip.observability"),
            "otel_exporter_missing_dependency",
            level=logging.WARNING,
            outcome="stdout_only",
            endpoint=endpoint_label,
            exc_type=type(exc).__name__,
        )
        return

    # Compose headers from the SecretStr env var (``k=v,k2=v2``). The
    # raw value never touches a log line -- we hand it straight to the
    # exporter.
    raw_headers = (
        _settings.mip_otel_headers.get_secret_value()
        if _settings.mip_otel_headers is not None
        else ""
    )
    headers = _parse_otel_headers(raw_headers)

    try:
        resource = Resource.create(
            {
                "service.name": "mortgage-intelligence-platform",
                "service.namespace": "mip",
                "deployment.environment": _settings.app_env,
            }
        )
        provider = LoggerProvider(resource=resource)
        exporter = OTLPLogExporter(endpoint=endpoint, headers=headers or None)
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        set_logger_provider(provider)
        handler = LoggingHandler(level=level_value, logger_provider=provider)
        handler.setFormatter(StructuredFormatter())
        root.addHandler(handler)
        _OTEL_HANDLER = handler
        emit(
            logging.getLogger("mip.observability"),
            "otel_exporter_installed",
            outcome="installed",
            endpoint=endpoint_label,
            header_keys=sorted(headers.keys()),
        )
    except Exception as exc:  # noqa: BLE001 -- see comment above
        emit(
            logging.getLogger("mip.observability"),
            "otel_exporter_wiring_failed",
            level=logging.WARNING,
            outcome="stdout_only",
            endpoint=endpoint_label,
            exc_type=type(exc).__name__,
        )


def get_otel_handler() -> logging.Handler | None:
    """Return the installed OTLP handler, or ``None`` when not wired.

    Test helper plus operator-facing introspection from the admin route.
    """
    return _OTEL_HANDLER


def _reset_configured_for_tests() -> None:
    """Test helper -- reset the idempotency latch and detach OTLP handler."""
    global _CONFIGURED, _OTEL_HANDLER
    with _CONFIGURE_LOCK:
        if _OTEL_HANDLER is not None:
            logging.getLogger().removeHandler(_OTEL_HANDLER)
            _OTEL_HANDLER = None
        _CONFIGURED = False


# ---------------------------------------------------------------------------
# Rolling-hour counters for /api/health
# ---------------------------------------------------------------------------


_BREAKER_CHANGES: deque[tuple[float, str, str, str]] = deque()
_ERRORS: deque[tuple[float, str, str]] = deque()
_PROTECTED_CLASS_SUPPRESSIONS: deque[tuple[float, str, str]] = deque()
_COUNTER_LOCK = threading.Lock()
_WINDOW_S = 3600.0


def _prune_locked(q: deque[Any], now: float) -> None:
    cutoff = now - _WINDOW_S
    while q and q[0][0] < cutoff:
        q.popleft()


def record_breaker_state_change(*, name: str, from_state: str, to_state: str) -> None:
    """Remember a breaker state change for the last-hour health counter."""
    now = time.monotonic()
    with _COUNTER_LOCK:
        _prune_locked(_BREAKER_CHANGES, now)
        _BREAKER_CHANGES.append((now, name, from_state, to_state))


def record_error(*, dependency: str, exc_type: str) -> None:
    """Remember a dependency error for the last-hour health counter."""
    now = time.monotonic()
    with _COUNTER_LOCK:
        _prune_locked(_ERRORS, now)
        _ERRORS.append((now, dependency, exc_type))


def recent_breaker_state_changes() -> int:
    now = time.monotonic()
    with _COUNTER_LOCK:
        _prune_locked(_BREAKER_CHANGES, now)
        return len(_BREAKER_CHANGES)


def recent_error_count() -> int:
    now = time.monotonic()
    with _COUNTER_LOCK:
        _prune_locked(_ERRORS, now)
        return len(_ERRORS)


def record_protected_class_suppression(*, term_bank: str, kind: str) -> None:
    """Remember a digit-minted protected-term suppression for the health body.

    Labels only, deliberately: see ``marketing_scan_provenance``. This counts
    the fair-lending scanner declining to fire because a NUMBER wore a
    governed term's spelling, which is the opposite of a block -- the text
    rendered. A non-zero value is normal on a numeric analytics surface; a
    sustained ZERO on Ask Genie is the interesting reading, because it means
    a rule that should fire on ordinary counts has stopped firing.
    """

    now = time.monotonic()
    with _COUNTER_LOCK:
        _prune_locked(_PROTECTED_CLASS_SUPPRESSIONS, now)
        _PROTECTED_CLASS_SUPPRESSIONS.append((now, term_bank, kind))


def recent_protected_class_suppressions() -> int:
    now = time.monotonic()
    with _COUNTER_LOCK:
        _prune_locked(_PROTECTED_CLASS_SUPPRESSIONS, now)
        return len(_PROTECTED_CLASS_SUPPRESSIONS)


def _reset_counters_for_tests() -> None:
    with _COUNTER_LOCK:
        _BREAKER_CHANGES.clear()
        _ERRORS.clear()
        _PROTECTED_CLASS_SUPPRESSIONS.clear()


__all__ = [
    "StructuredFormatter",
    "configure_logging",
    "correlation_id_var",
    "emit",
    "get_correlation_id",
    "get_otel_handler",
    "is_safe_correlation_id",
    "record_breaker_state_change",
    "record_error",
    "record_protected_class_suppression",
    "recent_breaker_state_changes",
    "recent_error_count",
    "recent_protected_class_suppressions",
    "reset_correlation_id",
    "sanitize_correlation_id",
    "set_correlation_id",
    "timed_dependency",
]
