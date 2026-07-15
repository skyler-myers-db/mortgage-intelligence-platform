"""Workspace save/draft request and response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas.common import validate_no_human_name_shape, validate_public_borrower_id
from backend.schemas.portfolio_campaign import (
    assert_borrower_campaign_copy,
    assert_public_campaign_text,
)


class SavedLeadInput(BaseModel):
    borrower_id: str = Field(min_length=1, max_length=128)
    city: str | None = Field(default=None, max_length=128)
    state: str | None = Field(default=None, max_length=16)
    zip: str | None = Field(default=None, max_length=16)
    recommended_offer: str | None = Field(default=None, max_length=128)
    opportunity_score: int | None = Field(default=None, ge=0, le=100)
    confidence: int | None = Field(default=None, ge=0, le=100)

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)


class SavedLead(SavedLeadInput):
    saved_at: str
    updated_at: str


class SavedDraftInput(BaseModel):
    borrower_id: str = Field(min_length=1, max_length=128)
    offer_code: str | None = Field(default=None, max_length=128)
    channel: Literal["email", "sms", "direct_mail"] = "email"
    subject: str | None = Field(default=None, max_length=120)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("subject")
    @classmethod
    def _subject_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        subject = value.strip()
        if not subject:
            return None
        assert_public_campaign_text(subject, field_name="saved draft subject", max_length=120)
        assert_borrower_campaign_copy(subject, field_name="saved draft subject")
        return subject

    @field_validator("body")
    @classmethod
    def _body_has_no_human_names(cls, value: str) -> str:
        return validate_no_human_name_shape(value, field_name="saved draft body")

class SavedDraft(SavedDraftInput):
    saved_at: str
    updated_at: str


class WorkspaceState(BaseModel):
    saved_leads: list[SavedLead]
    saved_drafts: list[SavedDraft]


class WorkspaceMutationResponse(BaseModel):
    ok: bool
    borrower_id: str
    audit_event_id: str | None = None
