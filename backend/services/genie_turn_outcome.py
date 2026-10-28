"""Process-local record of the most recent real Genie model turn.

Why this exists (2026-10-28 post-deploy finding): every live Genie turn on
the dev workspace ended ``FAILED`` with ``BUDGET_EXCEEDED_EXCEPTION`` for two
days while ``/api/health`` said ``genie: up``. The health probe only pings
``GET /spaces/{id}`` (no model turn, no cost, no audit row), and the breaker
stayed closed, so nothing on the health surface could see a model turn fail.

This module closes that gap WITHOUT making health start Genie turns: the
Genie client records the outcome of every real turn it already runs, and
the health routes read the record.

What is stored is deliberately tiny and closed: ``outcome`` (``success`` /
``failed``), an ``error_class`` drawn from ``GENIE_TURN_ERROR_CLASSES`` and a
UTC timestamp. Upstream error text, question text, SQL, rows and ids never
enter the record; classification reads the upstream error text at the raise
site and keeps only the class token. Like the other health counters the
record is process-local (one per App replica) and resets on restart.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal, get_args

from backend.schemas.health import GenieTurnErrorClass

GenieTurnOutcomeKind = Literal["success", "failed"]

GENIE_TURN_ERROR_CLASSES: frozenset[str] = frozenset(get_args(GenieTurnErrorClass))

#: Classes that mean "no model turn will succeed until an operator acts"
#: (spend budget or quota exhausted). RATE_LIMITED is NOT here: the space's
#: per-minute limit clears on its own within a minute, and flipping the whole
#: shell to degraded for it would cry wolf.
QUOTA_DEGRADED_CLASSES: frozenset[str] = frozenset({"BUDGET_EXCEEDED", "QUOTA_EXCEEDED"})

#: How long a budget/quota failure keeps ``/health`` degraded when no later
#: turn has succeeded. Health never runs a turn to re-check, so the window is
#: the only way a stale failure stops degrading an idle process; the next real
#: turn (success or failure) replaces the record either way.
QUOTA_DEGRADED_WINDOW_S: float = 15 * 60.0

_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# Databricks error envelopes lead with the canonical code ("RESOURCE_EXHAUSTED:
# You've reached your budget."); only that leading token is ever read as a code.
_LEADING_CODE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,63})\s*:")
_RATE_LIMIT_CODES: frozenset[str] = frozenset(
    {"RESOURCE_EXHAUSTED", "REQUEST_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"}
)


def _code_tokens(error_type: object, detail: object) -> tuple[set[str], set[str]]:
    """``(structured, leading)`` upper-case error-code tokens.

    ``structured`` holds the Genie error ``type`` and an HTTP body's JSON
    ``error_code``; ``leading`` holds a leading ``CODE:`` prefix of the free
    text. Anything that is not a plain ``[A-Z0-9_]`` token is ignored, so free
    text never becomes a code.
    """

    structured: set[str] = set()
    raw_type = str(error_type or "").strip().upper()
    if _TOKEN_RE.fullmatch(raw_type):
        structured.add(raw_type)
    text = str(detail or "")
    try:
        parsed: Any = json.loads(text) if text.lstrip().startswith("{") else None
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        code = str(parsed.get("error_code") or "").strip().upper()
        if _TOKEN_RE.fullmatch(code):
            structured.add(code)
        text = str(parsed.get("message") or "")
    leading = {match.group(1)} if (match := _LEADING_CODE_RE.match(text)) else set()
    return structured, leading


def classify_genie_failure(
    *,
    status_code: int | None = None,
    state: str | None = None,
    error_type: object = None,
    detail: object = None,
) -> GenieTurnErrorClass:
    """Map one upstream failure onto the closed ``GenieTurnErrorClass`` set.

    ``detail`` (upstream error text) is read here and never returned or
    stored. Budget/quota only count from a structured code (the Genie error
    ``type`` or an HTTP ``error_code``) or as wording next to a
    ``RESOURCE_EXHAUSTED`` code, so a SQL error that quotes a ``budget``
    column cannot read as an exhausted spend budget.
    """

    structured, leading = _code_tokens(error_type, detail)
    tokens = structured | leading
    words = {word for token in tokens for word in token.split("_")}
    structured_words = {word for token in structured for word in token.split("_")}
    text = str(detail or "").upper()
    if "BUDGET" in structured_words:
        return "BUDGET_EXCEEDED"
    if "QUOTA" in structured_words:
        return "QUOTA_EXCEEDED"
    if "RESOURCE_EXHAUSTED" in tokens:
        if "BUDGET" in text:
            return "BUDGET_EXCEEDED"
        if "QUOTA" in text:
            return "QUOTA_EXCEEDED"
    if status_code == 429 or tokens & _RATE_LIMIT_CODES or {"RATE", "LIMIT"} <= words:
        return "RATE_LIMITED"
    upper_state = str(state or "").strip().upper()
    if upper_state == "TIMEOUT" or "TIMEOUT" in words or status_code == 504:
        return "TIMEOUT"
    if status_code in {401, 403} or tokens & {"PERMISSION_DENIED", "UNAUTHENTICATED"}:
        return "AUTH"
    if status_code == 404:
        return "NOT_FOUND"
    if status_code is not None and status_code >= 500:
        return "UPSTREAM_ERROR"
    if upper_state in {"CANCELED", "CANCELLED"}:
        return "CANCELED"
    if upper_state in {"EXPIRED", "QUERY_RESULT_EXPIRED"}:
        return "EXPIRED"
    if upper_state == "FAILED" or status_code is not None:
        return "FAILED"
    return "UNKNOWN"


def classify_transport_failure(exc: BaseException) -> GenieTurnErrorClass:
    """TIMEOUT for a timed-out socket anywhere in the cause chain, else NETWORK."""

    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, TimeoutError):
            return "TIMEOUT"
        if isinstance(seen, urllib.error.URLError) and isinstance(seen.reason, TimeoutError):
            return "TIMEOUT"
        seen = seen.__cause__
    return "NETWORK"


@dataclass(frozen=True)
class GenieTurnOutcome:
    outcome: GenieTurnOutcomeKind
    error_class: GenieTurnErrorClass | None
    at: datetime
    monotonic_at: float


_LAST_TURN: GenieTurnOutcome | None = None
_LOCK = Lock()


def _store(outcome: GenieTurnOutcomeKind, error_class: GenieTurnErrorClass | None) -> None:
    global _LAST_TURN
    record = GenieTurnOutcome(
        outcome=outcome,
        error_class=error_class,
        at=datetime.now(UTC),
        monotonic_at=time.monotonic(),
    )
    with _LOCK:
        _LAST_TURN = record


def record_genie_turn_success() -> None:
    _store("success", None)


def record_genie_turn_failure(exc: BaseException) -> None:
    """Record a failed turn. Never raises: it runs inside the caller's handler.

    The class comes from the exception's ``error_class`` (set at the Genie
    client's raise site); a value outside the closed set reads as ``UNKNOWN``
    so nothing free-form can reach the health body.
    """

    try:
        error_class = getattr(exc, "error_class", None)
        if error_class not in GENIE_TURN_ERROR_CLASSES:
            error_class = (
                classify_transport_failure(exc) if isinstance(exc, OSError) else "UNKNOWN"
            )
        _store("failed", error_class)
    except Exception:  # noqa: BLE001 -- recording must never mask the turn's own error
        return


def last_genie_turn() -> GenieTurnOutcome | None:
    with _LOCK:
        return _LAST_TURN


def last_genie_turn_payload() -> dict[str, object] | None:
    """The admin-health shape: ``{outcome, error_class, at}`` or ``None``."""

    record = last_genie_turn()
    if record is None:
        return None
    return {"outcome": record.outcome, "error_class": record.error_class, "at": record.at}


def genie_turn_degraded_reason(now_monotonic: float | None = None) -> GenieTurnErrorClass | None:
    """The quota/budget class that should mark Genie degraded right now, if any."""

    record = last_genie_turn()
    if record is None or record.outcome != "failed" or record.error_class is None:
        return None
    if record.error_class not in QUOTA_DEGRADED_CLASSES:
        return None
    now = time.monotonic() if now_monotonic is None else now_monotonic
    if now - record.monotonic_at > QUOTA_DEGRADED_WINDOW_S:
        return None
    return record.error_class


def _reset_genie_turn_outcome_for_tests() -> None:
    global _LAST_TURN
    with _LOCK:
        _LAST_TURN = None
