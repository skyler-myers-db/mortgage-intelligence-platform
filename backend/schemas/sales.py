"""Sales assignment, disposition, aging, and conversion contracts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas._validators import normalize_public_lender_ref
from backend.schemas.common import (
    contains_pii_marker,
    validate_internal_staff_email,
    validate_public_audit_identifier_or_none,
    validate_public_borrower_id,
    validate_public_opaque_id,
)
from backend.schemas.loan_officer import AssignmentLifecycleStatus

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
LeadOutcomeType = Literal[
    "application_submitted",
    "closed_funded",
    "lost_to_competitor",
    "withdrawn",
    "not_qualified",
]
LeadOutcomeSourceSystem = Literal[
    "salesforce",
    "crm_cdp",
    "los_pos",
    "servicing",
    "webhook",
    "manual_import",
]

_NOTE_UNSAFE_TEXT_PATTERN = re.compile(
    r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b|"
    r"\[(?:first|last|full)[_\s-]?[Nn]ame\]|\{(?:first|last|full)[_\s-]?[Nn]ame\}",
)
_PUBLIC_COMPETITOR_LABEL_PATTERN = re.compile(r"^Competitor ([A-Z]|Other)$")
_MAX_CLIENT_CLOCK_SKEW = timedelta(minutes=5)


def _datetime_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _not_future(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if _datetime_utc(value) > datetime.now(UTC) + _MAX_CLIENT_CLOCK_SKEW:
        raise ValueError(f"{field_name} cannot be in the future")
    return value


def _public_competitor_label(value: str | None) -> str | None:
    if value is None:
        return None
    clean = re.sub(r"\s+", " ", value.strip())
    if not clean:
        return None
    if contains_pii_marker(clean):
        raise ValueError("competitor_lender_label must not contain borrower PII")
    try:
        normalized = normalize_public_lender_ref(clean)
    except ValueError as exc:
        raise ValueError("competitor_lender_label must be a governed competitor alias") from exc
    if normalized is None or not _PUBLIC_COMPETITOR_LABEL_PATTERN.fullmatch(normalized):
        raise ValueError("competitor_lender_label must be a governed competitor alias")
    return normalized


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
    # S2 lifecycle stage; rows written before the loan-officer migration
    # read back as the entry stage.
    status: AssignmentLifecycleStatus = "assigned"


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

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at(cls, value: datetime | None) -> datetime | None:
        return _not_future(value, "occurred_at")

    @model_validator(mode="after")
    def _callback_required(self) -> DispositionRequest:
        if self.outcome == "callback_scheduled" and self.callback_at is None:
            raise ValueError("callback_at is required for callback_scheduled")
        effective_occurred_at = self.occurred_at or datetime.now(UTC)
        if (
            self.callback_at is not None
            and _datetime_utc(self.callback_at) <= _datetime_utc(effective_occurred_at)
        ):
            raise ValueError("callback_at must be after occurred_at")
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


class LeadOutcomeRequest(BaseModel):
    """PII-safe closed-loop outcome event from CRM/LOS/POS/servicing."""

    model_config = ConfigDict(extra="forbid")

    outcome_type: LeadOutcomeType
    source_system: LeadOutcomeSourceSystem
    source_record_ref: str | None = Field(default=None, max_length=128)
    assigned_to_email: str | None = None
    campaign_id: str | None = None
    loan_amount: int | None = Field(default=None, ge=0, le=100_000_000)
    competitor_lender_label: str | None = Field(default=None, max_length=80)
    occurred_at: datetime | None = None
    request_id: str | None = None

    @field_validator("source_record_ref")
    @classmethod
    def _source_record_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return None
        if contains_pii_marker(clean):
            raise ValueError("source_record_ref must not contain PII")
        return validate_public_audit_identifier_or_none(clean)

    @field_validator("assigned_to_email")
    @classmethod
    def _assigned_to_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_internal_staff_email(value)

    @field_validator("campaign_id", "request_id")
    @classmethod
    def _opaque_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)

    @field_validator("competitor_lender_label")
    @classmethod
    def _competitor_lender_label(cls, value: str | None) -> str | None:
        return _public_competitor_label(value)

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at(cls, value: datetime | None) -> datetime | None:
        return _not_future(value, "occurred_at")

    @model_validator(mode="after")
    def _lost_requires_competitor(self) -> LeadOutcomeRequest:
        if not self.request_id and not self.source_record_ref:
            raise ValueError("request_id or source_record_ref is required for idempotent outcome ingestion")
        if self.outcome_type == "lost_to_competitor" and not self.competitor_lender_label:
            raise ValueError("competitor_lender_label is required for lost_to_competitor")
        return self


class LeadOutcome(BaseModel):
    outcome_id: str
    borrower_id: str
    outcome_type: LeadOutcomeType
    source_system: LeadOutcomeSourceSystem
    source_record_ref: str | None = None
    assigned_to_email: str | None = None
    campaign_id: str | None = None
    loan_amount: int | None = None
    competitor_lender_label: str | None = None
    occurred_at: datetime
    request_id: str | None = None
    audit_event_id: str | None = None
    created_at: datetime | None = None


class LeadOutcomeResponse(BaseModel):
    outcome: LeadOutcome
    audit_event_id: str | None = None


class BorrowerLifecycleResponse(BaseModel):
    borrower_id: str
    approval_status: Literal["pending", "approved", "rejected", "hold"] = "pending"
    outreach_status: OutreachStatus = "none"
    approval_id: str | None = None
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


class SalesOutcomeSummaryResponse(BaseModel):
    from_date: str
    to_date: str
    total_outcomes: int
    applications_submitted: int
    closed_funded: int
    lost_to_competitor: int
    withdrawn: int
    not_qualified: int
    by_source_system: list[dict[str, object]]
    source_statuses: list[dict[str, object]]
    by_lo: list[dict[str, object]]
    top_competitors: list[dict[str, object]]
