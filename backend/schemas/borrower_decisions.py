"""The borrower decision history contract (audit flow-04 phase 2, D-audit-reads-c2).

One borrower's governed decisions, newest first, as a CLOSED projection: every
field below is derived from a ledger row through a closed vocabulary, and a
metadata value outside it projects to null. Free text never crosses this
boundary: no rationale or note, no draft copy, no hash, evidence id,
correlation or request id, no raw reason or consent code, and no list of other
borrowers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.schemas.activation import ActivationOutboxStatus
from backend.schemas.loan_officer import AssignmentLifecycleStatus
from backend.schemas.offer import OfferType, OutreachChannel
from backend.schemas.sales import CallDispositionOutcome, LeadOutcomeType

DecisionHistoryEventType = Literal[
    "ACTIVATION_STAGE",
    "APPROVAL_REQUESTED",
    "APPROVE",
    "CALL_DISPOSITION",
    "LEAD_ASSIGN",
    "LEAD_ASSIGNMENT_STATUS",
    "LEAD_DISTRIBUTE",
    "LEAD_OUTCOME",
    "LEAD_OUTCOME_RECORDED",
    "LEAD_UNASSIGN",
    "OUTREACH_REJECT",
    "OUTREACH_REVOKE",
    "SUPPRESS_CONTACT",
]

DecisionHistoryOutcome = Literal[
    "approved",
    "rejected",
    "revoked",
    "requested",
    "assigned",
    "unassigned",
    "distributed",
    "status_changed",
    "disposition",
    "outcome",
    "activation",
    "contact_blocked",
]

DecisionActorKind = Literal["staff", "automation", "unverified"]

# A coarse reason, never the precise code: the receipt keeps the code.
DecisionRationaleLabel = Literal[
    "Out of footprint",
    "Contact preference",
    "Compliance review",
    "Low intent",
    "Data quality",
    "Other",
]

# The client prefixes "Contact blocked: ".
DecisionContactBlockLabel = Literal[
    "No marketing consent",
    "Contacted within 30 days",
    "Suppressed",
    "Eligibility not proven",
]


class BorrowerDecisionEvent(BaseModel):
    """One governed decision on the borrower, projected through closed vocabularies."""

    model_config = ConfigDict(extra="forbid")

    audit_event_id: str
    event_type: DecisionHistoryEventType
    outcome: DecisionHistoryOutcome
    occurred_at: datetime
    actor_display: str
    actor_kind: DecisionActorKind
    is_own: bool
    offer_code: OfferType | None = None
    channel: OutreachChannel | None = None
    rationale_label: DecisionRationaleLabel | None = None
    contact_block_label: DecisionContactBlockLabel | None = None
    assigned_to_display: str | None = None
    from_status: AssignmentLifecycleStatus | None = None
    to_status: AssignmentLifecycleStatus | None = None
    disposition_outcome: CallDispositionOutcome | None = None
    lead_outcome_type: LeadOutcomeType | None = None
    activation_status: ActivationOutboxStatus | None = None
    bulk: bool = False
    receipt_available: bool = False


class BorrowerDecisionHistoryResponse(BaseModel):
    """The latest 50 decisions on one borrower; truncated when older ones exist."""

    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    items: list[BorrowerDecisionEvent]
    truncated: bool
