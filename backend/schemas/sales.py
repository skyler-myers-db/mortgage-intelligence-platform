"""Sales assignment, disposition, aging, and conversion contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.common import (
    validate_internal_staff_email,
    validate_public_borrower_id,
    validate_public_opaque_id,
)

AssignmentStrategy = Literal["manual", "round_robin", "score_balanced"]
OutreachStatus = Literal["none", "queued", "actioned", "sent", "bounced", "replied"]
CallDispositionOutcome = Literal[
    "called_no_answer",
    "called_left_voicemail",
    "connected",
    "callback_scheduled",
    "application_started",
    "not_interested",
    "not_now",
    "dead",
]

_NOTE_UNSAFE_TEXT_PATTERN = re.compile(
    r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b|"
    r"\[(?:first|last|full)[_\s-]?[Nn]ame\]|\{(?:first|last|full)[_\s-]?[Nn]ame\}",
)


class SalesTeamMember(BaseModel):
    email: str
    display_label: str
    role: Literal["loan_officer", "sales_manager", "admin"] = "loan_officer"
    region: str | None = None
    manager_email: str | None = None
    capacity_per_day: int = Field(default=35, ge=0, le=250)
    active: bool = True

    @field_validator("email", "manager_email")
    @classmethod
    def _staff_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_internal_staff_email(value)


class LeadAssignment(BaseModel):
    assignment_id: str
    borrower_id: str
    assigned_to_email: str
    assigned_to_label: str | None = None
    assigned_by: str
    assigned_at: datetime
    expires_at: datetime | None = None
    released_at: datetime | None = None
    strategy: AssignmentStrategy = "manual"


class AssignLeadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_to_email: str
    expires_in_hours: int | None = Field(default=24, ge=1, le=168)
    strategy: AssignmentStrategy = "manual"
    request_id: str | None = None

    @field_validator("assigned_to_email")
    @classmethod
    def _assignee_email(cls, value: str) -> str:
        return validate_internal_staff_email(value)

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class AssignmentResponse(BaseModel):
    assignment: LeadAssignment
    audit_event_id: str | None = None


class DistributeLeadsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_ids: list[str] = Field(default_factory=list, max_length=500)
    lo_emails: list[str] = Field(min_length=1, max_length=25)
    strategy: AssignmentStrategy = "round_robin"
    expires_in_hours: int | None = Field(default=24, ge=1, le=168)
    limit: int = Field(default=250, ge=1, le=500)
    approval_status: Literal["approved", "pending", "rejected", "hold", "any"] = "approved"
    outreach_status: Literal["none", "queued", "actioned", "sent", "bounced", "replied", "any"] = "none"
    request_id: str | None = None

    @field_validator("borrower_ids")
    @classmethod
    def _borrower_ids(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            borrower_id = validate_public_borrower_id(value)
            if borrower_id not in out:
                out.append(borrower_id)
        return out

    @field_validator("lo_emails")
    @classmethod
    def _lo_emails(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            email = validate_internal_staff_email(value)
            if email not in out:
                out.append(email)
        return out

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class DistributeLeadsResponse(BaseModel):
    assigned_count: int
    strategy: AssignmentStrategy
    assignments: list[LeadAssignment]
    per_lo_counts: dict[str, int]
    audit_event_id: str | None = None


class DispositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lo_email: str
    outcome: CallDispositionOutcome
    occurred_at: datetime | None = None
    callback_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=500)
    request_id: str | None = None

    @field_validator("lo_email")
    @classmethod
    def _lo_email(cls, value: str) -> str:
        return validate_internal_staff_email(value)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        note = re.sub(r"\s+", " ", value.strip())
        if not note:
            return None
        if _NOTE_UNSAFE_TEXT_PATTERN.search(note):
            raise ValueError("notes must not contain names or unresolved placeholders")
        return note

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)

    @model_validator(mode="after")
    def _callback_required(self) -> DispositionRequest:
        if self.outcome == "callback_scheduled" and self.callback_at is None:
            raise ValueError("callback_at is required for callback_scheduled")
        return self


class CallDisposition(BaseModel):
    disposition_id: str
    borrower_id: str
    lo_email: str
    outcome: CallDispositionOutcome
    attempt_number: int
    occurred_at: datetime
    callback_at: datetime | None = None
    notes: str | None = None
    audit_event_id: str | None = None


class DispositionResponse(BaseModel):
    disposition: CallDisposition
    audit_event_id: str | None = None


class BorrowerLifecycleResponse(BaseModel):
    borrower_id: str
    approval_status: Literal["pending", "approved", "rejected", "hold"] = "pending"
    outreach_status: OutreachStatus = "none"
    approved_at: datetime | None = None
    outreach_at: datetime | None = None
    synced_at: datetime | None = None
    assignment: LeadAssignment | None = None
    latest_disposition: CallDisposition | None = None


class SalesAgingLead(BaseModel):
    borrower_id: str
    approval_status: Literal["approved"]
    approved_at: datetime
    age_days: int
    outreach_status: OutreachStatus = "none"
    outreach_at: datetime | None = None
    assigned_to_email: str | None = None
    latest_disposition_outcome: str | None = None
    latest_disposition_at: datetime | None = None


class SalesStandupResponse(BaseModel):
    date: str
    calls_logged: int
    contacts_reached: int
    callbacks_scheduled: int
    applications_started: int
    dead_leads: int
    by_lo: list[dict[str, object]]


class SalesConversionResponse(BaseModel):
    from_date: str
    to_date: str
    group_by: Literal["lo", "cohort"]
    rows: list[dict[str, object]]
