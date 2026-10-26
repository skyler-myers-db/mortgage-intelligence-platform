"""Sales assignment, disposition, aging, and conversion contracts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas._validators_person_names import contains_human_name_shape
from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.common import (
    contains_pii_marker,
    validate_internal_staff_email,
    validate_public_audit_identifier_or_none,
    validate_public_borrower_id,
    validate_public_opaque_id,
)
from backend.schemas.loan_officer import AssignmentLifecycleStatus

AssignmentStrategy = Literal["manual", "round_robin", "score_balanced"]
# What a single assign may record: one officer chosen by the manager, or the
# round-robin label. 'score_balanced' is a greedy capacity/region fill that only
# POST /sales/distribute runs (audit wow-power-5 step 2), so a single assign
# that names it is a 422, never a recorded balancing that did not happen.
SingleAssignStrategy = Literal["manual", "round_robin"]
# Why a release did not hand the lead back to the officer it replaced
# (audit states-07 item 1). Closed set; the audit row records the code only.
RestoreSkippedReason = Literal[
    "previous_assignee_inactive",
    "previous_assignee_out_of_scope",
    "lead_not_approved",
    "lead_not_contactable",
]
_PLAN_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"
_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
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
    strategy: SingleAssignStrategy = "manual"
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
    # The Distribution Board (wow-power-5 step 2): a dry run plans and writes
    # nothing; a commit names the fingerprint of the plan it previewed.
    dry_run: bool = False
    expected_plan_fingerprint: str | None = Field(default=None, pattern=_PLAN_FINGERPRINT_PATTERN)

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

    @model_validator(mode="after")
    def _board_fields(self) -> DistributeLeadsRequest:
        board = self.dry_run or self.expected_plan_fingerprint is not None
        if board and self.strategy != "score_balanced":
            raise ValueError("dry_run and expected_plan_fingerprint require strategy score_balanced")
        if self.dry_run and self.request_id is not None:
            raise ValueError("a dry run writes nothing and takes no request_id")
        if self.dry_run and self.expected_plan_fingerprint is not None:
            raise ValueError("a dry run does not take expected_plan_fingerprint")
        return self


class DistributionOfficerLoad(BaseModel):
    """One officer's share of a planned fill: staff aggregates only."""

    email: str
    display_label: str
    region: str | None = None
    capacity_per_day: int
    open_count: int
    added_count: int
    added_score_sum: int
    in_region_count: int


class DistributionPreview(BaseModel):
    """What the score-balanced fill placed: counts and sums, never a borrower id."""

    strategy: Literal["score_balanced"] = "score_balanced"
    plan_fingerprint: str
    pool_count: int
    placed_count: int
    placed_score_sum: int
    unplaced_no_capacity: int
    skipped_not_assignable: int
    skipped_already_assigned: int
    officers: list[DistributionOfficerLoad]


class DistributeLeadsResponse(BaseModel):
    assigned_count: int
    strategy: AssignmentStrategy
    assignments: list[LeadAssignment]
    per_lo_counts: dict[str, int]
    audit_event_id: str | None = None
    preview: DistributionPreview | None = None


class AssignmentReleaseRequest(BaseModel):
    """Undo one assignment (audit states-07 item 1): a compensating write."""

    model_config = ConfigDict(extra="forbid")

    assignment_id: str = Field(pattern=_UUID_PATTERN)
    request_id: str

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return validate_public_opaque_id(value)


class AssignmentReleaseResponse(BaseModel):
    released: LeadAssignment
    restored: LeadAssignment | None = None
    restore_skipped_reason: RestoreSkippedReason | None = None
    audit_event_id: str


class DistributionReleaseRequest(BaseModel):
    """Undo one distribution run: release that run's own rows by the same actor."""

    model_config = ConfigDict(extra="forbid")

    distribution_request_id: str
    request_id: str

    @field_validator("distribution_request_id", "request_id")
    @classmethod
    def _opaque_id(cls, value: str) -> str:
        return validate_public_opaque_id(value)


class DistributionReleaseResponse(BaseModel):
    released_count: int
    restored_count: int
    skipped_started_count: int
    skipped_not_active_count: int
    restore_skipped_count: int
    released: list[LeadAssignment]
    restored: list[LeadAssignment]
    audit_event_ids: list[str]


class DispositionRequest(BaseModel):
    # D-shell-deviations-g2 (flow-08 adjacent, critic-05): the write-only
    # free-text disposition note is gone. After its one tolerant release, any
    # `notes` key (blank included) is an unknown field: extra='forbid' answers
    # the app-wide 422, whose input echo main.py strips, and nothing is written.
    model_config = ConfigDict(extra="forbid")

    lo_email: str
    outcome: CallDispositionOutcome
    occurred_at: datetime | None = None
    callback_at: datetime | None = None
    request_id: str | None = None

    @field_validator("lo_email")
    @classmethod
    def _lo_email(cls, value: str) -> str:
        return validate_internal_staff_email(value)

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
        name_scan = re.sub(r"[._-]+", " ", clean)
        if contains_human_name_shape(name_scan):
            raise ValueError("source_record_ref must be an opaque machine identifier")
        if re.fullmatch(r"auto-[a-f0-9]{32}", clean):
            raise ValueError(
                "source_record_ref must be the upstream identifier, not a server-issued token"
            )
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
    # The audit row the latest decision (approve, reject or hold) wrote;
    # the Decision receipt reads it back.
    audit_event_id: str | None = None
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


class SalesStandupLoRow(BaseModel):
    """One loan officer's call dispositions on the standup day."""

    lo_email: str
    outcomes: dict[str, int]
    calls_logged: int


class SalesStandupResponse(BaseModel):
    date: str
    calls_logged: int
    contacts_reached: int
    callbacks_scheduled: int
    applications_started: int
    dead_leads: int
    by_lo: list[SalesStandupLoRow]


class SalesConversionRow(BaseModel):
    """One conversion group: a loan officer, or the single all_cohorts group."""

    group_key: str
    calls_attempted: int
    contacts_reached: int
    callbacks_scheduled: int
    applications_started: int
    unique_leads_contacted: int
    unique_contacts_reached: int
    unique_application_starts: int
    application_start_rate: float


class SalesConversionResponse(BaseModel):
    from_date: str
    to_date: str
    group_by: Literal["lo", "cohort"]
    rows: list[SalesConversionRow]


class CampaignPerformanceFunnelResponse(BaseModel):
    from_date: str
    to_date: str
    unique_leads_attempted: int
    unique_contacts_reached: int
    unique_application_starts: int
    unique_applications_submitted: int
    unique_closed_funded: int
    methodology: Literal["same_borrower_nested_funnel"] = "same_borrower_nested_funnel"


class SalesOutcomeSourceRow(BaseModel):
    """Outcome counts for one source system."""

    source_system: str
    outcomes: dict[str, int]
    total: int


class SalesOutcomeSourceStatus(BaseModel):
    """One outcome feed's configuration status and its outcome count in the window."""

    source_system: str
    display_name: str
    status: str
    configured: bool
    outcome_count: int


class SalesOutcomeLoRow(BaseModel):
    """Outcome counts for one assigned loan officer (or unassigned)."""

    lo_email: str
    outcomes: dict[str, int]
    total: int


class SalesTopCompetitorRow(BaseModel):
    """A public competitor label and its lost-to-competitor count."""

    competitor_lender_label: str
    lost_to_competitor: int


class SalesOutcomeSummaryResponse(BaseModel):
    from_date: str
    to_date: str
    total_outcomes: int
    applications_submitted: int
    closed_funded: int
    unique_applications_submitted: int = 0
    unique_closed_funded: int = 0
    lost_to_competitor: int
    withdrawn: int
    not_qualified: int
    by_source_system: list[SalesOutcomeSourceRow]
    source_statuses: list[SalesOutcomeSourceStatus]
    by_lo: list[SalesOutcomeLoRow]
    top_competitors: list[SalesTopCompetitorRow]
