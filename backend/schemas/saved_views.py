"""Contracts for user-saved Lead Queue views (audit tables-09 phase 2, flow-08).

A saved view is a name plus the canonical Lead Queue query string. The name
is the one free-text field, so it passes the same public-text policy a
campaign name does, unchanged, and a refusal answers ONE fixed message that
never echoes what was typed. The route canonicalizes the params against the
Copy-link grammar (the saved-view params service) before anything is stored.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.portfolio_campaign import assert_public_campaign_text

SAVED_VIEW_NAME_MAX_LENGTH = 60
SAVED_VIEW_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &(),./:+\-–≥≤>]{0,59}$")
SAVED_VIEW_NAME_REFUSED = "saved view name must be a short public-safe label"


def normalize_saved_view_name(value: object) -> str:
    """Whitespace-normalize a view name and apply the campaign-text policy.

    Same call shape as ``project_public_campaign_name``: the policy scans the
    case-folded text so title casing never decides what reads as a person.
    Every refusal (shape or policy) raises the one fixed message.
    """

    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not SAVED_VIEW_NAME_PATTERN.fullmatch(name):
        raise ValueError(SAVED_VIEW_NAME_REFUSED)
    try:
        assert_public_campaign_text(
            name.casefold(),
            field_name="saved view name",
            max_length=SAVED_VIEW_NAME_MAX_LENGTH,
        )
    except ValueError as exc:
        raise ValueError(SAVED_VIEW_NAME_REFUSED) from exc
    return name


class SavedViewCreateRequest(BaseModel):
    """POST /workspace/saved-views: name the current Lead Queue view."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=SAVED_VIEW_NAME_MAX_LENGTH,
        description="A short label for the view, without personal details.",
    )
    params: str = Field(
        min_length=1,
        max_length=2048,
        description="The Lead Queue query string: its filters, sort and column preset only.",
    )

    @field_validator("name")
    @classmethod
    def _name_is_public_safe(cls, value: str) -> str:
        return normalize_saved_view_name(value)


class SavedView(BaseModel):
    """One saved Lead Queue view the actor owns."""

    view_id: str = Field(description="Server-issued view id (UUID).")
    name: str
    params: str = Field(description="Canonical Lead Queue query string, without a leading question mark.")
    created_at: datetime
    updated_at: datetime


class SavedViewListResponse(BaseModel):
    """The actor's saved Lead Queue views, most recently updated first."""

    saved_views: list[SavedView] = Field(max_length=25)


class SavedViewMutationResponse(BaseModel):
    """What a save or a delete wrote, including its audit row."""

    ok: bool
    view_id: str
    audit_event_id: str | None = None
