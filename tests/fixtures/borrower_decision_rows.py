"""Seed ledger rows for the borrower decision history (audit flow-04 phase 2).

Test-only (never imported by backend). The SAME rows feed the unit suite's
in-memory Lakebase (tests/unit/test_borrower_decision_history.py) and the
real-PostgreSQL suite (tests/integration/test_borrower_decision_history_postgres.py),
which inserts them by raw SQL. Each audit row names the borrowers whose
history must show it in ``shown_to``; an empty set is a negative control that
no borrower's history may return.

Three borrowers: the primary one, a colleague borrower on the SAME property
(subject_clip), and a phone-shaped masked id (C7: ``B-5551234567XYZ``), which
the AuditStore refuses until the refusal lane's NB-1 lands, so it appears only
in rows inserted by raw SQL: one APPROVAL_REQUESTED ``borrower_ids`` row and
one LEAD_DISTRIBUTE row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

BORROWER = "B-DECISIONS0001"
OTHER = "B-DECISIONS0002"
PHONE_SHAPED = "B-5551234567XYZ"
SHARED_CLIP = "clip_ref_shared_decisions"

OWN = "lo01@summit.example"
COLLEAGUE = "lo02@summit.example"
MANAGER = "sam.manager@summit.example"
APPROVER = "pat.approver@summit.example"
AUTOMATION = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
UNVERIFIED = "someone@elsewhere.test"

APPROVAL = "11111111-1111-4111-8111-111111111101"
REJECTION = "11111111-1111-4111-8111-111111111102"
REVOCATION = "11111111-1111-4111-8111-111111111103"
OTHER_APPROVAL = "11111111-1111-4111-8111-111111111104"
ACTIVATION = "22222222-2222-4222-8222-222222222201"
BATCH = "33333333-3333-4333-8333-333333333301"
DISTRIBUTION_REQUEST = "dist-req-0001"
OTHER_DISTRIBUTION_REQUEST = "dist-req-0002"

# Free text every projection must drop (rationales, notes, drafts).
SECRET_TEXT = "SECRET-FREE-TEXT never leaves the ledger"

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class SeedAudit:
    """One action_audit row and the histories that must show it."""

    key: str
    event_type: str
    actor_email: str
    entity_type: str
    entity_id: str
    metadata: dict[str, Any]
    shown_to: frozenset[str] = field(default_factory=frozenset)
    subject_clip: str | None = None
    request_id: str | None = None


def _row(key: str, event_type: str, actor: str, entity: tuple[str, str], metadata: dict[str, Any], *shown: str, **extra: Any) -> SeedAudit:
    return SeedAudit(key, event_type, actor, entity[0], entity[1], metadata, frozenset(shown), **extra)


_B = ("borrower", BORROWER)
_ROWS: tuple[SeedAudit, ...] = (
    # Positives for BORROWER (and the two shared multi-borrower rows).
    _row("approve", "APPROVE", OWN, ("approval", APPROVAL), {
        "borrower_id": BORROWER, "offer_code": "refi", "channel": "email", "rationale": SECRET_TEXT,
        "draft_body": SECRET_TEXT, "draft_subject": SECRET_TEXT, "assigned_to_email": OWN,
        "draft_response_hash": "f" * 64, "review_mode": "individual",
    }, BORROWER, subject_clip=SHARED_CLIP),
    _row("reject", "OUTREACH_REJECT", COLLEAGUE, ("approval", REJECTION), {
        "borrower_id": BORROWER, "offer_code": "heloc", "channel": "sms",
        "rationale_code": "fair_lending_review", "rationale": SECRET_TEXT,
    }, BORROWER, subject_clip=SHARED_CLIP),
    _row("revoke", "OUTREACH_REVOKE", APPROVER, ("approval", REVOCATION), {
        "borrower_id": BORROWER, "offer_code": "refi", "channel": "email",
        "revoked_approval_id": APPROVAL, "rationale": SECRET_TEXT,
    }, BORROWER, subject_clip=SHARED_CLIP),
    _row("requested", "APPROVAL_REQUESTED", MANAGER, ("approval_request_batch", BATCH), {
        "approval_request_batch_id": BATCH, "borrower_ids": [BORROWER, PHONE_SHAPED],
        "requested_count": 2, "skipped_count": 0, "rationale": SECRET_TEXT,
    }, BORROWER, PHONE_SHAPED),
    _row("assign", "LEAD_ASSIGN", MANAGER, _B, {
        "borrower_id": BORROWER, "assigned_to_email": OWN, "strategy": "manual",
    }, BORROWER, subject_clip=SHARED_CLIP),
    _row("status", "LEAD_ASSIGNMENT_STATUS", OWN, _B, {
        "borrower_id": BORROWER, "assigned_to_email": OWN,
        "from_status": "assigned", "to_status": "contact_drafted",
    }, BORROWER),
    _row("distribute", "LEAD_DISTRIBUTE", MANAGER, ("lead_queue", "_sales_distribution"), {
        "borrower_ids": [BORROWER, PHONE_SHAPED, OTHER], "assigned_count": 3,
        "lo_emails": [OWN, COLLEAGUE], "per_lo_counts": {OWN: 2, COLLEAGUE: 1}, "strategy": "round_robin",
    }, BORROWER, PHONE_SHAPED, OTHER, request_id=DISTRIBUTION_REQUEST),
    _row("disposition", "CALL_DISPOSITION", OWN, _B, {
        "borrower_id": BORROWER, "lo_email": OWN, "outcome": "connected", "notes": SECRET_TEXT,
    }, BORROWER),
    _row("lead_outcome", "LEAD_OUTCOME", MANAGER, _B, {
        "borrower_id": BORROWER, "lead_outcome_type": "closed_funded", "assigned_to_email": OWN,
    }, BORROWER),
    _row("outcome_recorded", "LEAD_OUTCOME_RECORDED", OWN, _B, {
        "borrower_id": BORROWER, "assigned_to_email": OWN, "from_status": "actioned",
        "to_status": "outcome_recorded", "assignment_outcome": "success",
    }, BORROWER),
    _row("activation", "ACTIVATION_STAGE", AUTOMATION, ("activation", ACTIVATION), {
        "borrower_id": BORROWER, "activation_id": ACTIVATION, "activation_status": "dry_run",
        "offer_code": "refi", "channel": "email", "approval_id": APPROVAL,
    }, BORROWER),
    _row("blocked_approve", "SUPPRESS_CONTACT", APPROVER, _B, {
        "borrower_id": BORROWER, "route": "outreach_approve", "reason": "consent_not_opt_in",
        "consent_status": "opt_out", "suppression_reason": None, "dnc": False,
    }, BORROWER),
    _row("blocked_activation", "SUPPRESS_CONTACT", UNVERIFIED, _B, {
        "borrower_id": BORROWER, "route": "activation_stage", "reason": "frequency_cap",
    }, BORROWER),
    _row("unassign", "LEAD_UNASSIGN", MANAGER, _B, {
        "borrower_id": BORROWER, "assigned_to_email": COLLEAGUE,
    }, BORROWER),
    # Negative controls: never in BORROWER's history.
    _row("blocked_draft", "SUPPRESS_CONTACT", OWN, _B, {
        "borrower_id": BORROWER, "route": "outreach_draft", "reason": "suppressed",
    }),
    _row("legacy_outreach_approve", "OUTREACH_APPROVE", OWN, _B, {"borrower_id": BORROWER}),
    _row("legacy_reject", "REJECT", OWN, _B, {"borrower_id": BORROWER}),
    _row("legacy_hold", "HOLD", OWN, _B, {"borrower_id": BORROWER}),
    _row("legacy_outreach_hold", "OUTREACH_HOLD", OWN, _B, {"borrower_id": BORROWER}),
    _row("view", "VIEW_BORROWER", OWN, _B, {"borrower_id": BORROWER}),
    _row("genie", "RUN_GENIE", OWN, _B, {"borrower_id": BORROWER}),
    _row("recommend", "RECOMMEND_OFFER", OWN, _B, {"borrower_id": BORROWER}),
    _row("draft", "DRAFT_OUTREACH", OWN, _B, {"borrower_id": BORROWER, "draft_body": SECRET_TEXT}),
    _row("governed_text_refused", "GOVERNED_TEXT_REFUSED", OWN, _B, {"surface": "approve", "screen": "pii"}),
    _row("request_refused", "APPROVAL_REQUEST_REFUSED", MANAGER, ("approval_request", "req-key-refused"), {
        "borrower_ids": [BORROWER], "requested_count": 0, "skipped_count": 1, "rationale": SECRET_TEXT,
    }),
    # An approval-id row that names another borrower (only BORROWER's
    # approval id resolves it, and the COALESCE re-check refuses it).
    _row("approval_naming_other", "APPROVE", COLLEAGUE, ("approval", APPROVAL), {
        "borrower_id": OTHER, "offer_code": "refi", "channel": "email",
    }),
    # A request row on BORROWER's resolved batch whose borrower_ids lack it.
    _row("requested_without_borrower", "APPROVAL_REQUESTED", MANAGER, ("approval_request_batch", BATCH), {
        "approval_request_batch_id": BATCH, "borrower_ids": [OTHER], "rationale": SECRET_TEXT,
    }),
    # OTHER's own rows: same property, different borrower.
    _row("other_assign_same_clip", "LEAD_ASSIGN", MANAGER, ("borrower", OTHER), {
        "borrower_id": OTHER, "assigned_to_email": COLLEAGUE,
    }, OTHER, subject_clip=SHARED_CLIP),
    _row("other_approve", "APPROVE", COLLEAGUE, ("approval", OTHER_APPROVAL), {
        "borrower_id": OTHER, "offer_code": "heloc", "channel": "email", "bulk_id": "bulk-0001",
    }, OTHER, subject_clip=SHARED_CLIP),
    _row("other_distribute", "LEAD_DISTRIBUTE", MANAGER, ("lead_queue", "_sales_distribution"), {
        "borrower_ids": [OTHER], "assigned_count": 1, "lo_emails": [COLLEAGUE],
        "per_lo_counts": {COLLEAGUE: 1}, "strategy": "manual",
    }, OTHER, request_id=OTHER_DISTRIBUTION_REQUEST),
)

AUDIT_ROWS: tuple[SeedAudit, ...] = _ROWS


def event_at(index: int) -> datetime:
    """Seed rows are written one minute apart, in tuple order."""
    return T0 + timedelta(minutes=index)


# The tables entity resolution reads, keyed by borrower.
APPROVALS: tuple[dict[str, Any], ...] = (
    {"approval_id": APPROVAL, "borrower_id": BORROWER, "action": "approve", "actor_email": OWN, "offer_code": "refi", "channel": "email"},
    {"approval_id": REJECTION, "borrower_id": BORROWER, "action": "reject", "actor_email": COLLEAGUE, "offer_code": "heloc", "channel": "sms"},
    {"approval_id": REVOCATION, "borrower_id": BORROWER, "action": "revoke", "actor_email": APPROVER, "offer_code": "refi", "channel": "email"},
    {"approval_id": OTHER_APPROVAL, "borrower_id": OTHER, "action": "approve", "actor_email": COLLEAGUE, "offer_code": "heloc", "channel": "email"},
)
ACTIVATIONS: tuple[dict[str, Any], ...] = (
    {"activation_id": ACTIVATION, "borrower_id": BORROWER, "approval_id": APPROVAL},
)
BATCH_ITEMS: tuple[dict[str, Any], ...] = (
    {"batch_id": BATCH, "borrower_id": BORROWER},
    {"batch_id": BATCH, "borrower_id": PHONE_SHAPED},
)
DISTRIBUTION_ASSIGNMENTS: tuple[dict[str, Any], ...] = (
    {"borrower_id": BORROWER, "assigned_to_email": OWN, "request_id": DISTRIBUTION_REQUEST},
    {"borrower_id": PHONE_SHAPED, "assigned_to_email": COLLEAGUE, "request_id": DISTRIBUTION_REQUEST},
    {"borrower_id": OTHER, "assigned_to_email": COLLEAGUE, "request_id": DISTRIBUTION_REQUEST},
    {"borrower_id": OTHER, "assigned_to_email": COLLEAGUE, "request_id": OTHER_DISTRIBUTION_REQUEST},
)
# The seeded roster in lakebase/seed_campaigns.sql carries these three.
TEAM: tuple[dict[str, Any], ...] = (
    {"email": MANAGER, "display_label": "Summit Sales Manager", "role": "sales_manager", "active": True},
    {"email": OWN, "display_label": "Summit LO 01", "role": "loan_officer", "active": True},
    {"email": COLLEAGUE, "display_label": "Summit LO 02", "role": "loan_officer", "active": True},
)


def expected_keys(borrower_id: str) -> list[str]:
    """The seed keys ``borrower_id``'s history shows, newest first."""
    shown = [(index, row.key) for index, row in enumerate(AUDIT_ROWS) if borrower_id in row.shown_to]
    return [key for _, key in sorted(shown, reverse=True)]
