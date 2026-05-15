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
})
_DEPENDENCIES = frozenset({"warehouse", "lakebase", "genie"})


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
        return self


class RumBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[RumEvent] = Field(min_length=1, max_length=20)
