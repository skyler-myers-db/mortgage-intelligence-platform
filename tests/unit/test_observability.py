"""Unit tests for ``backend.services.observability``.

Covers:
* StructuredFormatter produces parseable JSON with required fields
* PII denylist strips forbidden keys before emission
* correlation_id_var propagates across a ``timed_dependency`` block
* Correlation middleware mints + echoes the X-Correlation-ID header
* Health endpoint exposes ``breaker_state_changes_last_hour`` and
  ``recent_errors_count``

Every test is stdlib-only and deterministic -- no sleeps, no network.
"""
from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import health as health_mod
from backend.main import app
from backend.services import observability as obs
from backend.services import resilience

client = TestClient(app)


# ---------------------------------------------------------------------------
# StructuredFormatter
# ---------------------------------------------------------------------------


def _capture(logger: logging.Logger) -> io.StringIO:
    """Attach a stream handler with StructuredFormatter to ``logger``."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs.StructuredFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return buf


def test_structured_formatter_emits_valid_json() -> None:
    log = logging.getLogger("test.obs.format")
    buf = _capture(log)
    try:
        obs.emit(log, "warehouse_query_end", dependency="warehouse",
                 duration_ms=42.5, outcome="ok", rows_returned=7)
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "warehouse_query_end"
        assert record["level"] == "INFO"
        assert record["dependency"] == "warehouse"
        assert record["duration_ms"] == 42.5
        assert record["outcome"] == "ok"
        assert record["rows_returned"] == 7
        assert "ts" in record and record["ts"].endswith("+00:00")
        assert record["correlation_id"]  # non-empty
        assert record["logger"] == "test.obs.format"
    finally:
        log.handlers.clear()
        log.propagate = True


def test_pii_denylist_redacts_forbidden_kwargs() -> None:
    log = logging.getLogger("test.obs.pii")
    buf = _capture(log)
    try:
        obs.emit(
            log,
            "sink",
            owner_name_hash="dead-beef",
            mailing_street_address="123 Main St",
            situs_street_address="456 Oak Ave",
            authorization="Bearer secret-token",
            token="s3cr3t",
            password="hunter2",
            safe_field="visible",
        )
        record = json.loads(buf.getvalue().splitlines()[0])
        for key in (
            "owner_name_hash",
            "mailing_street_address",
            "situs_street_address",
            "authorization",
            "token",
            "password",
        ):
            assert record[key] == "<redacted>", f"{key} not redacted"
        assert record["safe_field"] == "visible"
    finally:
        log.handlers.clear()
        log.propagate = True


def test_pii_denylist_walks_nested_dict() -> None:
    log = logging.getLogger("test.obs.nested")
    buf = _capture(log)
    try:
        obs.emit(
            log,
            "sink",
            context={
                "clip_id": "CL-123",
                "owner_name_hash": "dead-beef",
                "mailing_city": "Denver",
            },
        )
        record = json.loads(buf.getvalue().splitlines()[0])
        ctx = record["context"]
        assert ctx["clip_id"] == "CL-123"
        assert ctx["owner_name_hash"] == "<redacted>"
        assert ctx["mailing_city"] == "<redacted>"
    finally:
        log.handlers.clear()
        log.propagate = True


# ---------------------------------------------------------------------------
# Correlation-ID propagation across timed_dependency
# ---------------------------------------------------------------------------


def test_correlation_id_propagates_into_timed_dependency() -> None:
    log = logging.getLogger("test.obs.cid")
    buf = _capture(log)
    token = obs.set_correlation_id("cid-fixed-123")
    try:
        with obs.timed_dependency("warehouse", "execute", logger=log) as ctx:
            ctx["rows_returned"] = 3
        lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 2
        start, end = lines
        assert start["event"] == "dependency_call_start"
        assert end["event"] == "dependency_call_end"
        assert start["correlation_id"] == "cid-fixed-123"
        assert end["correlation_id"] == "cid-fixed-123"
        assert end["dependency"] == "warehouse"
        assert end["outcome"] == "ok"
        assert "duration_ms" in end
        assert end["rows_returned"] == 3
    finally:
        obs.reset_correlation_id(token)
        log.handlers.clear()
        log.propagate = True


def test_timed_dependency_emits_error_on_exception() -> None:
    log = logging.getLogger("test.obs.err")
    buf = _capture(log)
    obs._reset_counters_for_tests()
    with (
        pytest.raises(ValueError, match="boom"),
        obs.timed_dependency("lakebase", "fetchone", logger=log),
    ):
        raise ValueError("boom")
    lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln]
    assert len(lines) == 2
    end = lines[1]
    assert end["event"] == "dependency_call_end"
    assert end["outcome"] == "error"
    assert end["exc_type"] == "ValueError"
    assert end["exc_msg"] == "boom"
    assert obs.recent_error_count() >= 1
    log.handlers.clear()
    log.propagate = True


# ---------------------------------------------------------------------------
# Correlation middleware
# ---------------------------------------------------------------------------


def test_middleware_mints_correlation_id_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
    res = client.get("/api/health")
    assert res.status_code == 200
    cid = res.headers.get("X-Correlation-ID")
    assert cid
    # Minted UUIDs are 32 hex chars (uuid.uuid4().hex).
    assert len(cid) == 32
    assert all(c in "0123456789abcdef" for c in cid)


def test_middleware_echoes_client_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
    res = client.get(
        "/api/health",
        headers={"X-Correlation-ID": "trace-abc-123"},
    )
    assert res.status_code == 200
    assert res.headers["X-Correlation-ID"] == "trace-abc-123"


# ---------------------------------------------------------------------------
# R5-17 -- logged `path` field is templated, not verbatim. Borrower-level
# routes carry an ID in the path; the raw value must never land in logs.
# ---------------------------------------------------------------------------


def test_http_request_log_uses_templated_route_path() -> None:
    """R5-17: log lines must carry ``/api/borrowers/{borrower_id}``,
    not ``/api/borrowers/B-00042``.

    Why: today ``B-00042`` is a synthetic ID, but the roadmap swaps it
    for real Cotality CLIPs. A verbatim-path log line would become a
    CLIP trail keyed to the correlation id.
    """
    from backend.main import CorrelationIdMiddleware

    log = logging.getLogger("mip.http")
    buf = _capture(log)
    try:
        res = client.get("/api/borrowers/B-00042")
        # The route exists (conftest wires an in-process borrower repo),
        # so we expect a 2xx/4xx, not a 500 from the middleware itself.
        assert res.status_code in (200, 404)
        records = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln]
        http_lines = [r for r in records if r.get("event") == "http_request"]
        assert http_lines, "no http_request event was emitted"
        line = http_lines[-1]
        # The canonical template from the router.
        assert line["path"] == "/api/borrowers/{borrower_id}"
        # And, defensively, the raw ID NEVER appears on ANY log record.
        for rec in records:
            flat = json.dumps(rec)
            assert "B-00042" not in flat, (
                f"borrower id leaked into log record: {rec}"
            )
    finally:
        log.handlers.clear()
        log.propagate = True
    # Direct unit test of the normaliser confirms the fallback regex
    # works when no route matched (static / 404 / SPA fallback paths).
    for raw, expected in (
        ("/api/borrowers/B-00042", "/api/borrowers/{id}"),
        ("/api/borrowers/B-00042/evidence", "/api/borrowers/{id}/evidence"),
        ("/api/leads/12345678/evidence", "/api/leads/{id}/evidence"),
        ("/api/leads/CL-abc123", "/api/leads/{id}"),
        # Non-id segments are untouched.
        ("/api/health", "/api/health"),
        ("/api/genie/message", "/api/genie/message"),
    ):
        got = CorrelationIdMiddleware._ID_SEGMENT_PATTERN.sub("/{id}", raw)
        assert got == expected, f"{raw!r} normalised to {got!r}, want {expected!r}"


# ---------------------------------------------------------------------------
# Health endpoint observability fields
# ---------------------------------------------------------------------------


def test_health_exposes_observability_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
    obs._reset_counters_for_tests()
    obs.record_breaker_state_change(
        name="warehouse", from_state="closed", to_state="open"
    )
    obs.record_error(dependency="warehouse", exc_type="DatabricksSqlError")
    res = client.get("/api/health")
    payload: dict[str, Any] = res.json()
    assert payload["breaker_state_changes_last_hour"] >= 1
    assert payload["recent_errors_count"] >= 1


# ---------------------------------------------------------------------------
# Breaker state changes emit structured events
# ---------------------------------------------------------------------------


def test_breaker_open_transition_records_observability_event() -> None:
    resilience._reset_breakers_for_tests()
    obs._reset_counters_for_tests()
    cb = resilience.CircuitBreaker("obs-test", failure_threshold=2, cooldown_s=5.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    assert obs.recent_breaker_state_changes() >= 1


def test_structured_formatter_handles_adhoc_log_call() -> None:
    """A bare ``log.info("msg")`` still produces valid JSON."""
    log = logging.getLogger("test.obs.adhoc")
    buf = _capture(log)
    try:
        log.info("classic message, no mip_event attached")
        record = json.loads(buf.getvalue().splitlines()[0])
        assert record["event"] == "adhoc"  # last path segment of logger name
        assert record["message"] == "classic message, no mip_event attached"
        assert record["correlation_id"]
    finally:
        log.handlers.clear()
        log.propagate = True


# ---------------------------------------------------------------------------
# Slice-13 follow-up: optional OTLP log exporter (env-gated)
# ---------------------------------------------------------------------------


def test_health_exposes_counters_persistence_and_log_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable-path posture must be visible in /api/health.

    Operators look at the body to tell (a) whether the rolling-hour
    counters are trustworthy across a restart (they're not; the label is
    ``process-local``) and (b) whether a durable sink is wired (``otlp``
    vs ``stdout-only``). If either key disappears we've broken the
    contract documented in docs/observability.md.
    """
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
    res = client.get("/api/health")
    assert res.status_code == 200
    payload = res.json()
    assert payload["counters_persistence"] == "process-local"
    # Default test env has no OTEL endpoint set, so the sink is stdout.
    assert payload["log_export"] == "stdout-only"
    # The two pre-existing counter keys MUST still be present -- any
    # frontend or dashboard reading them would break otherwise.
    assert "breaker_state_changes_last_hour" in payload
    assert "recent_errors_count" in payload


def test_configure_logging_skips_otel_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var -> no OTLP handler -> no imports attempted."""
    from backend.config.settings import settings

    monkeypatch.setattr(settings, "mip_otel_endpoint", None, raising=False)
    obs._reset_configured_for_tests()
    try:
        obs.configure_logging()
        assert obs.get_otel_handler() is None
    finally:
        obs._reset_configured_for_tests()
        # Re-install default logging so later tests keep working.
        obs.configure_logging()


def test_configure_logging_logs_warning_when_otel_dep_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the env var IS set but the opentelemetry packages aren't
    importable, ``configure_logging`` must log a warning and NOT crash.

    We simulate the missing wheel by patching the import hook: any
    ``opentelemetry.*`` import raises ImportError. The app boots, the
    handler stays None, and a ``WARNING`` line hits
    ``mip.observability``.
    """
    import builtins

    from backend.config.settings import settings

    monkeypatch.setattr(
        settings, "mip_otel_endpoint", "http://127.0.0.1:9999", raising=False
    )
    monkeypatch.setattr(settings, "mip_otel_headers", None, raising=False)

    real_import = builtins.__import__

    def _blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("opentelemetry"):
            raise ImportError(f"simulated missing wheel for {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)

    warn_log = logging.getLogger("mip.observability")
    buf = _capture(warn_log)
    obs._reset_configured_for_tests()
    try:
        # Must not raise even though the env var is set + packages missing.
        obs.configure_logging()
        assert obs.get_otel_handler() is None
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        # At least one WARNING-level line referencing the endpoint.
        warning_lines = [
            json.loads(ln) for ln in lines if '"level":"WARNING"' in ln
        ]
        assert any(
            "127.0.0.1:9999" in (rec.get("message", "") or "")
            for rec in warning_lines
        ), f"expected warning about missing opentelemetry wheel; saw {lines}"
    finally:
        warn_log.handlers.clear()
        warn_log.propagate = True
        obs._reset_configured_for_tests()
        obs.configure_logging()


def test_otel_handler_attached_when_exporter_mocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: env var set + exporter mocked out at import level.

    We don't actually hit a real OTLP endpoint (the task's hard
    constraint forbids it). Instead we install fake
    ``opentelemetry.*`` modules in ``sys.modules`` so the late import
    inside :func:`_install_otel_handler_if_configured` resolves to our
    stubs. The handler attached to the root logger must be the
    ``LoggingHandler`` stub we provide, and ``get_otel_handler()`` must
    return it.
    """
    import sys

    from backend.config.settings import settings

    monkeypatch.setattr(
        settings, "mip_otel_endpoint", "http://127.0.0.1:4318/v1/logs", raising=False
    )
    from pydantic import SecretStr

    monkeypatch.setattr(
        settings,
        "mip_otel_headers",
        SecretStr("x-api-key=fake,team=mip"),
        raising=False,
    )

    # Build fake OTEL modules. The handler must be a real logging.Handler
    # so the stdlib attach flow works; everything else is a stub that
    # records how it was called.
    import types

    calls: dict[str, Any] = {"headers": None, "endpoint": None}

    class _FakeExporter:
        def __init__(self, *, endpoint: str, headers: Any = None) -> None:
            calls["endpoint"] = endpoint
            calls["headers"] = headers

    class _FakeProcessor:
        def __init__(self, _exporter: Any) -> None:
            pass

    class _FakeProvider:
        def __init__(self, *, resource: Any = None) -> None:
            self._resource = resource

        def add_log_record_processor(self, _p: Any) -> None:
            pass

    class _FakeResource:
        @staticmethod
        def create(attrs: dict[str, Any]) -> Any:
            return attrs

    class _FakeLoggingHandler(logging.Handler):
        def __init__(self, *, level: int, logger_provider: Any) -> None:
            super().__init__(level=level)
            self._provider = logger_provider

        def emit(self, _record: logging.LogRecord) -> None:
            pass

    def _set_logger_provider(_p: Any) -> None:
        pass

    otel_logs = types.ModuleType("opentelemetry._logs")
    otel_logs.set_logger_provider = _set_logger_provider  # type: ignore[attr-defined]

    exporter_mod = types.ModuleType(
        "opentelemetry.exporter.otlp.proto.http._log_exporter"
    )
    exporter_mod.OTLPLogExporter = _FakeExporter  # type: ignore[attr-defined]

    sdk_logs = types.ModuleType("opentelemetry.sdk._logs")
    sdk_logs.LoggerProvider = _FakeProvider  # type: ignore[attr-defined]
    sdk_logs.LoggingHandler = _FakeLoggingHandler  # type: ignore[attr-defined]

    sdk_logs_export = types.ModuleType("opentelemetry.sdk._logs.export")
    sdk_logs_export.BatchLogRecordProcessor = _FakeProcessor  # type: ignore[attr-defined]

    sdk_resources = types.ModuleType("opentelemetry.sdk.resources")
    sdk_resources.Resource = _FakeResource  # type: ignore[attr-defined]

    # Place the fakes in sys.modules so late ``from opentelemetry... import``
    # calls resolve to them without hitting the real wheel.
    inserted = {
        "opentelemetry._logs": otel_logs,
        "opentelemetry.exporter.otlp.proto.http._log_exporter": exporter_mod,
        "opentelemetry.sdk._logs": sdk_logs,
        "opentelemetry.sdk._logs.export": sdk_logs_export,
        "opentelemetry.sdk.resources": sdk_resources,
    }
    for k, v in inserted.items():
        monkeypatch.setitem(sys.modules, k, v)

    obs._reset_configured_for_tests()
    try:
        obs.configure_logging()
        handler = obs.get_otel_handler()
        assert isinstance(handler, _FakeLoggingHandler)
        # Endpoint passed through verbatim.
        assert calls["endpoint"] == "http://127.0.0.1:4318/v1/logs"
        # Headers parsed from ``k=v,k2=v2`` into a dict.
        assert calls["headers"] == {"x-api-key": "fake", "team": "mip"}
        # Handler actually sits on the root logger.
        assert handler in logging.getLogger().handlers
    finally:
        obs._reset_configured_for_tests()
        obs.configure_logging()


def test_boot_smoke_with_otel_env_and_missing_wheel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repro of the operator scenario: env is set, wheel isn't.

    The task's verification gate includes::

        MIP_OTEL_ENDPOINT=http://127.0.0.1:9999 \\
            python -c "from backend.main import app; print('boot OK with OTEL env')"

    This test asserts the same invariant inside pytest so regressions
    surface in CI rather than only in the ad-hoc shell run.
    """
    import builtins

    from backend.config.settings import settings

    monkeypatch.setattr(
        settings, "mip_otel_endpoint", "http://127.0.0.1:9999", raising=False
    )
    real_import = builtins.__import__

    def _blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("opentelemetry"):
            raise ImportError(f"simulated missing wheel for {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    obs._reset_configured_for_tests()
    try:
        # Must not raise.
        obs.configure_logging()
        # And the app's TestClient must still answer /api/health.
        monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
        monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
        monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["log_export"] == "stdout-only"
    finally:
        obs._reset_configured_for_tests()
        obs.configure_logging()
