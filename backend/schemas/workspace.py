from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SavedLeadInput(BaseModel):
    borrower_id: str = Field(min_length=1, max_length=128)
    city: str | None = Field(default=None, max_length=128)
    state: str | None = Field(default=None, max_length=16)
    zip: str | None = Field(default=None, max_length=16)
    recommended_offer: str | None = Field(default=None, max_length=128)
    opportunity_score: int | None = Field(default=None, ge=0, le=100)
    confidence: int | None = Field(default=None, ge=0, le=100)


class SavedLead(SavedLeadInput):
    saved_at: str
    updated_at: str


class SavedDraftInput(BaseModel):
    borrower_id: str = Field(min_length=1, max_length=128)
    offer_code: str | None = Field(default=None, max_length=128)
    channel: Literal["email", "sms"] = "email"
    body: str = Field(min_length=1, max_length=5000)


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
