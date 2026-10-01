"""Maker-checker approval request contracts (audit flow-02 / shell-06).

A signed-in user who is not an approver names the borrowers they want
released and writes why; an approver opens that request in the Lead Queue
and reviews each borrower's copy through the unchanged review sheet. The
request never drafts copy and never decides anything: approve and reject
stay the approvers' only decision writes, and each one may carry the
request's batch id so the ledger links the decision to the request (the
server refuses a requester deciding their own request).

Request state is Lakebase workflow app state. Each borrower's state is
derived from the finalized decisions in the approvals ledger, the requester's
withdraw and a 30-day expiry; it is never stored as approved or rejected.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import (
    validate_no_human_name_shape,
    validate_public_borrower_id,
    validate_public_opaque_id,
)

MAX_APPROVAL_REQUEST_BORROWERS = 500
APPROVAL_REQUEST_NOTE_MAX_LENGTH = 500

SkipReason = Literal["not_found", "not_contactable", "already_decided", "already_requested"]
SKIP_REASONS: tuple[SkipReason, ...] = (
    "not_found",
    "not_contactable",
    "already_decided",
    "already_requested",
)
ApprovalRequestRowState = Literal[
    "open", "approved", "rejected", "decided_outside", "withdrawn", "expired"
]
ApprovalRequestScope = Literal["open", "mine"]


class ApprovalRequestCreate(BaseModel):
    """POST /outreach/approval-requests: ask an approver to review named borrowers."""

    model_config = ConfigDict(extra="forbid")

    borrower_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_APPROVAL_REQUEST_BORROWERS,
        description="The masked borrower ids to request approval for, each named once.",
    )
    # Named rationale, not note: the request's justification is the audit
    # row's rationale, and no request body may grow a free-text note
    # property (tests/unit/test_no_free_text_borrower_notes.py).
    rationale: str = Field(
        min_length=1,
        max_length=APPROVAL_REQUEST_NOTE_MAX_LENGTH,
        description=(
            "Why these borrowers: required, without personal details. It is "
            "screened by the governed text policy and recorded on the audit row."
        ),
    )
    request_key: str = Field(
        min_length=1,
        max_length=64,
        description="Client idempotency key (a UUID); a retry with the same key replays the stored answer.",
    )

    @field_validator("borrower_ids")
    @classmethod
    def _borrower_ids_are_public_and_unique(cls, value: list[str]) -> list[str]:
        ids = [validate_public_borrower_id(item) for item in value]
        if len(set(ids)) != len(ids):
            raise ValueError("borrower_ids must name each borrower once")
        return ids

    @field_validator("rationale")
    @classmethod
    def _rationale_is_public_safe(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale must not be blank")
        return validate_no_human_name_shape(value, field_name="rationale")

    @field_validator("request_key")
    @classmethod
    def _request_key_is_opaque(cls, value: str) -> str:
        return validate_public_opaque_id(value)


class ApprovalRequestSkip(BaseModel):
    """A selected borrower the request did not include, and why."""

    borrower_id: str
    reason: SkipReason = Field(
        description=(
            "not_found: no such borrower; not_contactable: not marketing eligible, "
            "do-not-contact, no opt-in consent or suppressed; already_decided: an "
            "approval decision exists; already_requested: another open request "
            "already holds the borrower."
        )
    )


class ApprovalRequestCreated(BaseModel):
    """The stored answer to one request; a replay of its key returns it unchanged."""

    batch_id: str = Field(description="Server-issued request id (UUID).")
    audit_event_id: str = Field(description="The APPROVAL_REQUESTED audit row written with the request.")
    requested: list[str] = Field(description="The borrowers the request now holds open.")
    skipped: list[ApprovalRequestSkip]


class ApprovalRequestRow(BaseModel):
    """One requested borrower and the state derived for it."""

    borrower_id: str
    state: ApprovalRequestRowState = Field(
        description=(
            "approved / rejected: an approver decided it through this request; "
            "decided_outside: decided without the request link; withdrawn: the "
            "requester withdrew it; expired: older than 30 days or reopened by a "
            "revoke; open: awaiting an approver."
        )
    )
    approval_id: str | None = Field(
        default=None,
        description="The linked approvals row, for approved and rejected only.",
    )


class ApprovalRequestBatchView(BaseModel):
    """One request as a reader may see it."""

    batch_id: str
    requested_by_display: str | None = Field(
        description="A readable label derived from the requester's identity, never looked up."
    )
    is_mine: bool
    rationale: str = Field(description="The requester's screened justification.")
    created_at: datetime
    requested_by: str | None = Field(
        default=None,
        description="The requester's own identity, present only in their own scope=mine list.",
    )
    rows: list[ApprovalRequestRow]


class ApprovalRequestList(BaseModel):
    """GET /outreach/approval-requests: audit-free, Lakebase app state only."""

    scope: ApprovalRequestScope
    batches: list[ApprovalRequestBatchView]


class ApprovalRequestWithdrawn(BaseModel):
    """The requester's withdraw: open borrowers close; a repeat changes nothing."""

    batch_id: str
    withdrawn_now: int = Field(description="Borrowers this call withdrew.")
    already_closed: int = Field(description="Borrowers that were no longer open.")
    audit_event_id: str | None = Field(
        default=None,
        description="The APPROVAL_REQUEST_WITHDRAWN audit row, only when this call withdrew any.",
    )
