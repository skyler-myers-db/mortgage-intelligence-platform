"""Workspace save/draft request and response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.common import PUBLIC_UUID_PATTERN, validate_public_borrower_id
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


class _SavedDraftProofInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_id: str = Field(min_length=1, max_length=128)
    generation_id: str = Field(pattern=PUBLIC_UUID_PATTERN)
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)


class SavedDraftInput(_SavedDraftProofInput):
    """Public save request; borrower copy is reconstructed from proof."""

    # Deprecated compatibility fields remain in OpenAPI during the v1 cutover,
    # but the server no longer accepts client-authored borrower copy.
    offer_code: str | None = Field(default=None, max_length=128)
    channel: Literal["email", "sms", "direct_mail"] | None = None
    subject: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _copy_fields_are_server_owned(self) -> SavedDraftInput:
        if any(
            value is not None for value in (self.offer_code, self.channel, self.subject, self.body)
        ):
            raise ValueError(
                "workspace draft copy is server-owned; submit generation_id and response_hash only"
            )
        return self


class SavedDraftRecordInput(_SavedDraftProofInput):
    """Server-reconstructed copy persisted after generated-proof verification."""

    offer_code: str | None = Field(default=None, max_length=128)
    channel: Literal["email", "sms", "direct_mail"] = "email"
    subject: str | None = Field(default=None, max_length=120)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("subject")
    @classmethod
    def _subject_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.strip() != value:
            raise ValueError("saved draft subject must not have leading or trailing whitespace")
        if not value:
            return None
        subject_for_validation = assert_public_campaign_text(
            value,
            field_name="saved draft subject",
            max_length=120,
        )
        assert_borrower_campaign_copy(
            subject_for_validation,
            field_name="saved draft subject",
        )
        return value

    @field_validator("body")
    @classmethod
    def _body_is_governed_borrower_copy(cls, value: str) -> str:
        exact_body = value.strip()
        body_for_validation = assert_public_campaign_text(
            value,
            field_name="saved draft body",
            max_length=5000,
        )
        assert_borrower_campaign_copy(body_for_validation, field_name="saved draft body")
        if exact_body != value:
            raise ValueError("saved draft body must not have leading or trailing whitespace")
        return value


class SavedDraft(SavedDraftRecordInput):
    saved_at: str
    updated_at: str


class WorkspaceState(BaseModel):
    saved_leads: list[SavedLead]
    saved_drafts: list[SavedDraft]


class WorkspaceMutationResponse(BaseModel):
    ok: bool
    borrower_id: str
    audit_event_id: str | None = None
