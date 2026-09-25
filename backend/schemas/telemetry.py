"""Sanitized browser RUM telemetry request contracts."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RumMetricName = Literal[
    "navigation_load",
    "route_change",
    "lcp",
    "cls",
    "inp",
    "long_task",
    "api_call",
    "client_error",
]
RumRating = Literal["good", "needs_improvement", "poor", "info"]
RumDetailKey = Literal[
    "dom_content_loaded_ms",
    "ttfb_ms",
    "transfer_size",
    "from_route",
    "duration_ms",
    "attempt",
    "retryable",
    "dependency",
    "error_name",
    "error_kind",
    "error_source",
    "boundary",
    "api_route",
    "cache",
    "warehouse_ms",
    "lakebase_ms",
    "total_ms",
]
RumDetailValue = str | int | float | bool | None

_BORROWER_ID_RE = re.compile(r"\bB-[A-Za-z0-9][A-Za-z0-9_-]{0,126}\b")
_CLIP_ID_RE = re.compile(r"\bCL-[A-Za-z0-9][A-Za-z0-9_-]{1,126}\b")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_NUMERIC_ID_RE = re.compile(r"(?:(?<=/)\d{5,}(?=/|$)|\b\d{9,}\b)")
_PHONE_RE = re.compile(
    r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9 .'-]+"
    r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|"
    r"court|ct|way|place|pl)\b",
    re.IGNORECASE,
)
_NAME_SHAPE_RE = re.compile(r"\b[A-Z][a-z]{1,30}\s+[A-Z][a-z]{1,30}\b")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b")
RumNavigationType = Literal["navigate", "reload", "back_forward", "prerender"]

_NUMERIC_DETAIL_KEYS = frozenset({
    "dom_content_loaded_ms",
    "ttfb_ms",
    "transfer_size",
    "duration_ms",
    "warehouse_ms",
    "lakebase_ms",
    "total_ms",
})
_DEPENDENCIES = frozenset({"warehouse", "lakebase", "genie"})

# ---------------------------------------------------------------------------
# Closed vocabularies for `client_error` and `api_call` (2026-09-21 UI/UX audit
# stack-01, shell-01, states-01, quality-01, delivery-v3).
#
# A client error never carries its message, stack, component stack or a URL:
# each of those can hold a borrower id, a query string or free text. It
# carries only these four closed values. `api_call` carries a templated API
# path whose every segment is a literal route segment of this app or `:id`.
#
# Parity pin: frontend/src/lib/rumBridge.ts (the three client-error sets) and
# frontend/src/lib/rumApiRoute.ts (the API segments) declare the same lists;
# tests/unit/test_rum_client_error.py asserts set equality, and a drift pin
# there fails when a new /api route adds a literal segment missing below.
# ---------------------------------------------------------------------------
CLIENT_ERROR_NAMES = frozenset({
    "Error",
    "TypeError",
    "RangeError",
    "ReferenceError",
    "SyntaxError",
    "EvalError",
    "URIError",
    "AggregateError",
    "ChunkLoadError",
    "ApiError",
    "GenieLiveError",
    "NotFoundError",
    "InvalidStateError",
    "QuotaExceededError",
    "SecurityError",
    "Other",
})
CLIENT_ERROR_KINDS = frozenset({"chunk", "render"})
CLIENT_ERROR_SOURCES = frozenset({
    "uncaught",
    "caught",
    "recoverable",
    "preload",
    "window",
    "rejection",
})
CLIENT_ERROR_BOUNDARIES = frozenset({"root", "route", "console", "genie", "drawer"})
RUM_CACHE_STATES = frozenset({"hit", "miss", "stale"})
# Every literal path segment of the /api routes mounted in the app, without
# the `v1` version prefix and the `{param}` segments; sorted.
RUM_API_ROUTE_SEGMENTS = frozenset({
    "actions", "activation", "admin", "agent", "aging", "analytics", "approve",
    "assets", "assign", "assignment", "assignment-overlay", "assignments", "audit",
    "borrowers", "campaign-performance", "campaign-recommendation", "campaigns",
    "cancel",
    "capabilities", "complete", "compose", "config", "conversion", "county-rollups",
    "create", "custom", "data-estate", "destinations", "disposition", "distribute",
    "draft", "drafts", "economics", "event", "events", "evidence", "executive",
    "execute",
    "export-receipt", "feedback", "footprint", "force-degraded", "funnel", "genie",
    "geo", "geography", "growth-agent", "health", "home", "leads", "lifecycle",
    "lineage", "loan-officers", "lookup", "manifest", "message", "metadata",
    "monitors", "my-events", "notification-drafts", "offers", "operations", "options",
    "outbox", "outcome", "outcomes", "outreach", "page", "points", "portfolio",
    "plan",
    "preview", "progress", "proof", "property-loan", "rate-sensitivity", "rate-window",
    "queue-version",
    "receipt",
    "recommend", "refusal-report", "reject", "rollups", "rules", "rum", "run",
    "runs",
    "run-due", "run-due-all", "sales", "search", "segments", "session", "sessions",
    "settings", "signals", "sources", "stage", "standup", "start", "state-rollups",
    "status", "submit", "summary", "team", "telemetry", "workflows", "workspace",
    "zip-rollups",
})
RUM_API_ROUTE_ID_SEGMENT = ":id"

_CLIENT_ERROR_DETAIL_KEYS = frozenset({"error_name", "error_kind", "error_source", "boundary"})
_CLIENT_ERROR_REQUIRED_KEYS = frozenset({"error_name", "error_kind", "error_source"})
_API_CALL_DETAIL_KEYS = frozenset({
    "api_route",
    "cache",
    "warehouse_ms",
    "lakebase_ms",
    "total_ms",
})
_API_ROUTE_RE = re.compile(r"^/api(/[^/?#\s]+){1,10}$")


def _assert_public_value(value: Any) -> None:
    if isinstance(value, str):
        if "?" in value:
            raise ValueError("RUM payload values must not include query strings")
        if _EMAIL_RE.search(value):
            raise ValueError("RUM payload values must not include email addresses")
        if _BORROWER_ID_RE.search(value):
            raise ValueError("RUM payload values must not include borrower ids")
        if _CLIP_ID_RE.search(value):
            raise ValueError("RUM payload values must not include CLIP ids")
        if _UUID_RE.search(value):
            raise ValueError("RUM payload values must not include raw UUIDs")
        if _PHONE_RE.search(value):
            raise ValueError("RUM payload values must not include phone numbers")
        if _SSN_RE.search(value):
            raise ValueError("RUM payload values must not include SSNs")
        if _STREET_RE.search(value):
            raise ValueError("RUM payload values must not include street addresses")
        if _NUMERIC_ID_RE.search(value):
            raise ValueError("RUM payload values must not include numeric identifiers")
        if _NAME_SHAPE_RE.search(value):
            raise ValueError("RUM payload values must not include name-shaped values")
    elif isinstance(value, dict):
        for key, nested in value.items():
            _assert_public_value(str(key))
            _assert_public_value(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _assert_public_value(nested)


class RumEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: RumMetricName
    value: float = Field(ge=0, le=600_000)
    rating: RumRating = "info"
    route: str = Field(min_length=1, max_length=160)
    navigation_type: RumNavigationType | None = None
    details: dict[RumDetailKey, RumDetailValue] = Field(default_factory=dict)

    @field_validator("route")
    @classmethod
    def _route_is_sanitized(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("route must be a sanitized absolute path")
        _assert_public_value(value)
        return value

    @field_validator("navigation_type")
    @classmethod
    def _navigation_type_is_public(cls, value: str | None) -> str | None:
        if value is not None:
            _assert_public_value(value)
        return value

    @model_validator(mode="after")
    def _details_are_public(self) -> RumEvent:
        if len(self.details) > 8:
            raise ValueError("details may contain at most 8 keys")
        _assert_public_value(self.details)
        for key, value in self.details.items():
            if key in _NUMERIC_DETAIL_KEYS:
                if isinstance(value, bool) or not isinstance(value, int | float):
                    raise ValueError(f"{key} must be numeric")
                if value < 0 or value > 600_000:
                    raise ValueError(f"{key} must be between 0 and 600000")
                continue
            if key == "attempt":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("attempt must be an integer")
                if value < 1 or value > 20:
                    raise ValueError("attempt must be between 1 and 20")
                continue
            if key == "retryable":
                if not isinstance(value, bool):
                    raise ValueError("retryable must be boolean")
                continue
            if key == "dependency":
                if value not in _DEPENDENCIES:
                    raise ValueError("dependency must be a known runtime dependency")
                continue
            if key == "from_route":
                if not isinstance(value, str):
                    raise ValueError("from_route must be a sanitized route")
                if len(value) > 160:
                    raise ValueError("from_route must be at most 160 characters")
                if not value.startswith("/"):
                    raise ValueError("from_route must be a sanitized absolute path")
                if any(ch.isspace() for ch in value):
                    raise ValueError("from_route must not contain whitespace")
                continue
            if key == "cache":
                if value not in RUM_CACHE_STATES:
                    raise ValueError("cache must be hit, miss or stale")
                continue
            if key == "api_route":
                _assert_api_route(value)
                continue
        _assert_metric_detail_scope(self)
        return self


def _assert_api_route(value: RumDetailValue) -> None:
    if not isinstance(value, str):
        raise ValueError("api_route must be a templated API path")
    if len(value) > 160:
        raise ValueError("api_route must be at most 160 characters")
    if not _API_ROUTE_RE.fullmatch(value):
        raise ValueError("api_route must be a templated /api path without a query")
    for segment in value.split("/")[2:]:
        if segment != RUM_API_ROUTE_ID_SEGMENT and segment not in RUM_API_ROUTE_SEGMENTS:
            raise ValueError("api_route segments must be known API route segments or :id")


def _assert_client_error(event: RumEvent) -> None:
    if event.value != 1:
        raise ValueError("client_error value must be 1")
    if event.rating != "info":
        raise ValueError("client_error rating must be info")
    keys = set(event.details)
    if not keys <= _CLIENT_ERROR_DETAIL_KEYS:
        raise ValueError("client_error details may only carry the closed error keys")
    if not keys >= _CLIENT_ERROR_REQUIRED_KEYS:
        raise ValueError("client_error requires error_name, error_kind and error_source")
    if event.details["error_name"] not in CLIENT_ERROR_NAMES:
        raise ValueError("error_name must be a known error name")
    if event.details["error_kind"] not in CLIENT_ERROR_KINDS:
        raise ValueError("error_kind must be chunk or render")
    if event.details["error_source"] not in CLIENT_ERROR_SOURCES:
        raise ValueError("error_source must be a known error source")
    boundary = event.details.get("boundary")
    if boundary is not None and boundary not in CLIENT_ERROR_BOUNDARIES:
        raise ValueError("boundary must be a known error boundary")


def _assert_metric_detail_scope(event: RumEvent) -> None:
    """Error keys belong to client_error only; the delivery keys to api_call only."""
    keys = set(event.details)
    if event.metric == "client_error":
        _assert_client_error(event)
    elif keys & _CLIENT_ERROR_DETAIL_KEYS:
        raise ValueError("error keys are only allowed on client_error")
    if event.metric != "api_call" and keys & _API_CALL_DETAIL_KEYS:
        raise ValueError("api_route, cache and timing keys are only allowed on api_call")


class RumBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[RumEvent] = Field(min_length=1, max_length=20)
