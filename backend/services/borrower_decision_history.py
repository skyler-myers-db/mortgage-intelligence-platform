"""One borrower's governed decisions for the working team (audit flow-04 phase 2).

D-audit-reads-c2. ``GET /borrowers/{id}/decisions`` shows the people who work a
borrower what their colleagues already decided about it: approvals, rejections
and revokes, approval requests, assignments and their status, call
dispositions and outcomes, activation staging, and a blocked approve or
activation. It is a CLOSED, decision-only projection of
``mip_app.action_audit``:

* Only the 13 server-owned decision types below ever enter it. Reads
  (VIEW_*), Genie, drafts, recommendations, refusals and every legacy code a
  client could once author are never selected.
* Every row is re-proven to name THIS borrower, in SQL and again in Python
  (``row_names_borrower``): ``COALESCE(metadata->>'borrower_id', entity_id)``,
  or the requested ``borrower_ids`` list of an APPROVAL_REQUESTED batch or a
  LEAD_DISTRIBUTE run. ``subject_clip`` is never matched on: two borrowers can
  share a property.
* ``_project`` is the ONLY place a field crosses the boundary, each through a
  closed vocabulary. No free text, hash, evidence id, request id or list of
  other borrowers leaves it.

The read writes no audit row and calls no warehouse. It runs at most five
Lakebase statements: entity resolution, the indexed history read, the
LEAD_DISTRIBUTE read, the distribution assignees and the actors' team labels.
The type list is an SQL literal (never a bound ``ANY(%s)``) so the planner can
prove the partial index ``idx_action_audit_decision_entity`` under a generic
plan; ``lakebase/schema.sql`` carries the identical list.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, cast, get_args

from backend.schemas.activation import ActivationOutboxStatus
from backend.schemas.borrower_decisions import (
    BorrowerDecisionEvent,
    BorrowerDecisionHistoryResponse,
    DecisionActorKind,
    DecisionContactBlockLabel,
    DecisionHistoryEventType,
    DecisionHistoryOutcome,
    DecisionRationaleLabel,
)
from backend.schemas.common import validate_internal_staff_email
from backend.schemas.loan_officer import AssignmentLifecycleStatus
from backend.schemas.offer import OfferType, OutreachChannel
from backend.schemas.sales import CallDispositionOutcome, LeadOutcomeType
from backend.services.lakebase import LakebaseClient

HISTORY_LIMIT = 50
_FETCH_LIMIT = HISTORY_LIMIT + 1
_ENTITY_BRANCH_LIMIT = 200

DECISION_HISTORY_EVENT_TYPES: frozenset[str] = frozenset(
    {
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
    }
)
# A SUPPRESS_CONTACT row is a decision only where it blocked an approve or an
# activation; a blocked draft is not one.
SUPPRESS_ROUTES: frozenset[str] = frozenset({"outreach_approve", "activation_stage"})
_DISTRIBUTE = "LEAD_DISTRIBUTE"
_REQUESTED = "APPROVAL_REQUESTED"
_SUPPRESS = "SUPPRESS_CONTACT"
_SQL_CODE = re.compile(r"^[A-Z_]+$")
_SQL_ROUTE = re.compile(r"^[a-z_]+$")


def _sql_literal_list(values: Iterable[str], shape: re.Pattern[str]) -> str:
    items = sorted(values)
    for value in items:
        if not shape.fullmatch(value):
            raise ValueError(f"not an SQL-safe literal: {value!r}")
    return ", ".join(f"'{value}'" for value in items)


# Built once at import. The partial index predicate in lakebase/schema.sql is
# this exact list; a change to it needs a NEW index name there.
_ENTITY_TYPES_SQL_LIST = _sql_literal_list(DECISION_HISTORY_EVENT_TYPES - {_DISTRIBUTE}, _SQL_CODE)
_SUPPRESS_ROUTES_SQL_LIST = _sql_literal_list(SUPPRESS_ROUTES, _SQL_ROUTE)

# (a) The borrower's own decision entities, each branch bounded and indexed:
# approvals (idx_approvals_borrower, revoke rows included), activations
# (idx_activation_outbox_borrower) and approval-request batches
# (idx_approval_request_items_borrower).
ENTITY_RESOLUTION_SQL = f"""
(SELECT approval_id::text AS entity_id FROM mip_app.approvals
  WHERE borrower_id = %(bid)s ORDER BY decided_at DESC LIMIT {_ENTITY_BRANCH_LIMIT})
UNION ALL
(SELECT activation_id::text AS entity_id FROM mip_app.activation_outbox
  WHERE borrower_id = %(bid)s ORDER BY created_at DESC LIMIT {_ENTITY_BRANCH_LIMIT})
UNION ALL
(SELECT i.batch_id::text AS entity_id FROM mip_app.approval_request_items i
  JOIN mip_app.approval_request_batches b USING (batch_id)
  WHERE i.borrower_id = %(bid)s ORDER BY b.created_at DESC LIMIT {_ENTITY_BRANCH_LIMIT})
"""

_AUDIT_COLUMNS = (
    "audit_id, audit_sequence, event_type, actor_email, entity_type, entity_id, "
    "request_id, metadata, event_at"
)

# (b) Every decision type but LEAD_DISTRIBUTE, through idx_action_audit_decision_entity.
DECISION_HISTORY_SQL = f"""
SELECT {_AUDIT_COLUMNS}
FROM mip_app.action_audit
WHERE event_type IN ({_ENTITY_TYPES_SQL_LIST})
  AND entity_id = ANY(%(entity_ids)s)
  AND (
    COALESCE(metadata->>'borrower_id', entity_id) = %(bid)s
    OR (event_type = '{_REQUESTED}' AND metadata->'borrower_ids' ? %(bid)s)
  )
  AND (event_type <> '{_SUPPRESS}' OR metadata->>'route' IN ({_SUPPRESS_ROUTES_SQL_LIST}))
ORDER BY audit_sequence DESC
LIMIT {_FETCH_LIMIT}
"""

# (c) A distribution run is one shared row (entity '_sales_distribution');
# idx_action_audit_event_type serves it.
DISTRIBUTE_HISTORY_SQL = f"""
SELECT {_AUDIT_COLUMNS}
FROM mip_app.action_audit
WHERE event_type = '{_DISTRIBUTE}'
  AND metadata->'borrower_ids' ? %(bid)s
ORDER BY event_at DESC, audit_sequence DESC
LIMIT {_FETCH_LIMIT}
"""

# (d) This borrower's assignee in each shown distribution run, never the run's
# cohort-level per-officer counts or officer list.
DISTRIBUTION_ASSIGNEE_SQL = """
SELECT request_id, assigned_to_email
FROM mip_app.lead_assignments
WHERE borrower_id = %(bid)s
  AND assignment_scope = 'distribution'
  AND request_id = ANY(%(rids)s)
"""

# (e) Team labels for every actor and assignee shown.
ACTOR_TEAM_SQL = """
SELECT email, display_label, role
FROM mip_app.sales_team
WHERE email = ANY(%(emails)s)
"""

_OUTCOME_BY_TYPE: Mapping[str, DecisionHistoryOutcome] = {
    "ACTIVATION_STAGE": "activation",
    "APPROVAL_REQUESTED": "requested",
    "APPROVE": "approved",
    "CALL_DISPOSITION": "disposition",
    "LEAD_ASSIGN": "assigned",
    "LEAD_ASSIGNMENT_STATUS": "status_changed",
    "LEAD_DISTRIBUTE": "distributed",
    "LEAD_OUTCOME": "outcome",
    "LEAD_OUTCOME_RECORDED": "outcome",
    "LEAD_UNASSIGN": "unassigned",
    "OUTREACH_REJECT": "rejected",
    "OUTREACH_REVOKE": "revoked",
    "SUPPRESS_CONTACT": "contact_blocked",
}

# fair_lending_review reads 'Compliance review': the history never broadcasts
# a prohibited-basis signal about a borrower (ECOA / Reg B). The precise code
# stays in the receipt.
_RATIONALE_LABELS: Mapping[str, DecisionRationaleLabel] = {
    "out_of_footprint": "Out of footprint",
    "do_not_call": "Contact preference",
    "opt_out": "Contact preference",
    "fair_lending_review": "Compliance review",
    "low_intent": "Low intent",
    "data_quality": "Data quality",
    "other_with_text": "Other",
}
_CONTACT_BLOCK_LABELS: Mapping[str, DecisionContactBlockLabel] = {
    "consent_not_opt_in": "No marketing consent",
    "frequency_cap": "Contacted within 30 days",
    "suppressed": "Suppressed",
    "contactability_not_configured": "Eligibility not proven",
    "eligibility_not_proven": "Eligibility not proven",
}
_ROLE_LABELS: Mapping[str, str] = {
    "loan_officer": "Loan officer",
    "sales_manager": "Sales manager",
    "admin": "Admin",
}
_RECEIPT_TYPES = frozenset({"APPROVE", "OUTREACH_REJECT", "OUTREACH_REVOKE"})
_UUID_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

_OFFER_CODES = frozenset(get_args(OfferType))
_CHANNELS = frozenset(get_args(OutreachChannel))
_LIFECYCLE = frozenset(get_args(AssignmentLifecycleStatus))
_DISPOSITIONS = frozenset(get_args(CallDispositionOutcome))
_LEAD_OUTCOMES = frozenset(get_args(LeadOutcomeType))
_ACTIVATION_STATUSES = frozenset(get_args(ActivationOutboxStatus))


def _metadata(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _jsonb_has(value: object, key: str) -> bool:
    """Python twin of jsonb ``value ? key``: array element, object key or equal string."""
    if isinstance(value, list):
        return any(isinstance(item, str) and item == key for item in value)
    if isinstance(value, dict):
        return key in value
    return isinstance(value, str) and value == key


def _jsonb_text(value: object) -> str | None:
    """Python twin of ``->>``: JSON null is NULL, a scalar its text form."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value)


def row_names_borrower(
    event_type: str,
    entity_id: str,
    metadata: Mapping[str, Any],
    borrower_id: str,
) -> bool:
    """The Python belt: the same predicate the two history statements apply in SQL."""

    if event_type not in DECISION_HISTORY_EVENT_TYPES:
        return False
    if event_type == _SUPPRESS and _jsonb_text(metadata.get("route")) not in SUPPRESS_ROUTES:
        return False
    if event_type == _DISTRIBUTE:
        return _jsonb_has(metadata.get("borrower_ids"), borrower_id)
    named = _jsonb_text(metadata.get("borrower_id"))
    if (named if named is not None else entity_id) == borrower_id:
        return True
    return event_type == _REQUESTED and _jsonb_has(metadata.get("borrower_ids"), borrower_id)


def _closed(value: object, vocabulary: frozenset[str]) -> Any:
    return value if isinstance(value, str) and value in vocabulary else None


def _resolve_person(
    email: object,
    team: Mapping[str, tuple[str, str]],
    automation_identities: frozenset[str],
) -> tuple[str, DecisionActorKind]:
    """A team label, else a validated staff email, else Automation, else unverified."""

    text = email.strip().lower() if isinstance(email, str) else ""
    member = team.get(text)
    if member is not None:
        label, role = member
        return f"{label} ({_ROLE_LABELS.get(role, 'Team member')})", "staff"
    if text:
        try:
            return validate_internal_staff_email(text), "staff"
        except ValueError:
            pass
    if text and (text in automation_identities or _UUID_SHAPE.fullmatch(text)):
        return "Automation", "automation"
    return "Unverified identity", "unverified"


def _project(
    row: Mapping[str, Any],
    *,
    viewer: str,
    privileged: bool,
    team: Mapping[str, tuple[str, str]],
    automation_identities: frozenset[str],
    assignee_email: str | None,
) -> BorrowerDecisionEvent:
    """The ONLY place a ledger field crosses into the response."""

    event_type = str(row["event_type"])
    metadata = _metadata(row.get("metadata"))
    actor_email = str(row.get("actor_email") or "")
    actor_display, actor_kind = _resolve_person(actor_email, team, automation_identities)
    is_own = bool(viewer) and actor_email.strip().lower() == viewer.strip().lower()
    assignee = assignee_email if event_type == _DISTRIBUTE else metadata.get("assigned_to_email")
    assigned_to_display = (
        _resolve_person(assignee, team, automation_identities)[0] if isinstance(assignee, str) and assignee else None
    )
    rationale_label: DecisionRationaleLabel | None = None
    if event_type == "OUTREACH_REJECT":
        code = metadata.get("rationale_code")
        rationale_label = _RATIONALE_LABELS.get(code, "Other") if isinstance(code, str) else "Other"
    contact_block_label = (
        _CONTACT_BLOCK_LABELS.get(str(metadata.get("reason"))) if event_type == _SUPPRESS else None
    )
    occurred_at = row["event_at"]
    if not isinstance(occurred_at, datetime):
        occurred_at = datetime.fromisoformat(str(occurred_at))
    return BorrowerDecisionEvent(
        audit_event_id=str(row["audit_id"]),
        event_type=cast(DecisionHistoryEventType, event_type),  # proven by row_names_borrower
        outcome=_OUTCOME_BY_TYPE[event_type],
        occurred_at=occurred_at,
        actor_display=actor_display,
        actor_kind=actor_kind,
        is_own=is_own,
        offer_code=_closed(metadata.get("offer_code"), _OFFER_CODES),
        channel=_closed(metadata.get("channel"), _CHANNELS),
        rationale_label=rationale_label,
        contact_block_label=contact_block_label,
        assigned_to_display=assigned_to_display,
        from_status=_closed(metadata.get("from_status"), _LIFECYCLE),
        to_status=_closed(metadata.get("to_status"), _LIFECYCLE),
        disposition_outcome=(
            _closed(metadata.get("outcome"), _DISPOSITIONS) if event_type == "CALL_DISPOSITION" else None
        ),
        lead_outcome_type=_closed(metadata.get("lead_outcome_type"), _LEAD_OUTCOMES),
        activation_status=_closed(metadata.get("activation_status"), _ACTIVATION_STATUSES),
        bulk=bool(metadata.get("bulk_id")),
        receipt_available=event_type in _RECEIPT_TYPES and (is_own or privileged),
    )


def _distribution_assignees(
    lakebase: LakebaseClient, borrower_id: str, rows: list[dict[str, Any]]
) -> dict[str, str]:
    request_ids = sorted(
        {str(row["request_id"]) for row in rows if row["event_type"] == _DISTRIBUTE and row.get("request_id")}
    )
    if not request_ids:
        return {}
    found = lakebase.fetchall(
        DISTRIBUTION_ASSIGNEE_SQL,
        {"bid": borrower_id, "rids": request_ids},
        limit=_FETCH_LIMIT,
    )
    assignees: dict[str, str] = {}
    for item in found:
        if item.get("request_id") and item.get("assigned_to_email"):
            assignees.setdefault(str(item["request_id"]), str(item["assigned_to_email"]))
    return assignees


def _team_labels(lakebase: LakebaseClient, emails: set[str]) -> dict[str, tuple[str, str]]:
    if not emails:
        return {}
    rows = lakebase.fetchall(ACTOR_TEAM_SQL, {"emails": sorted(emails)}, limit=len(emails) + 1)
    return {
        str(row["email"]).strip().lower(): (str(row["display_label"]), str(row["role"]))
        for row in rows
        if row.get("email") and row.get("display_label")
    }


def list_borrower_decisions(
    lakebase: LakebaseClient,
    borrower_id: str,
    *,
    viewer: str,
    privileged: bool,
    automation_identities: frozenset[str],
) -> BorrowerDecisionHistoryResponse:
    """The borrower's latest 50 governed decisions, newest first. Audit-free."""

    resolved = lakebase.fetchall(ENTITY_RESOLUTION_SQL, {"bid": borrower_id}, limit=3 * _ENTITY_BRANCH_LIMIT)
    entity_ids = [borrower_id, *(str(row["entity_id"]) for row in resolved if row.get("entity_id"))]
    main = lakebase.fetchall(
        DECISION_HISTORY_SQL, {"bid": borrower_id, "entity_ids": entity_ids}, limit=_FETCH_LIMIT
    )
    distributed = lakebase.fetchall(DISTRIBUTE_HISTORY_SQL, {"bid": borrower_id}, limit=_FETCH_LIMIT)
    candidates = [
        row
        for row in [*main, *distributed]
        if row_names_borrower(
            str(row.get("event_type")),
            str(row.get("entity_id") or ""),
            _metadata(row.get("metadata")),
            borrower_id,
        )
    ]
    candidates.sort(key=lambda row: int(row["audit_sequence"]), reverse=True)
    truncated = len(main) > HISTORY_LIMIT or len(distributed) > HISTORY_LIMIT or len(candidates) > HISTORY_LIMIT
    shown = candidates[:HISTORY_LIMIT]
    assignees = _distribution_assignees(lakebase, borrower_id, shown)
    emails = {str(row.get("actor_email") or "").strip().lower() for row in shown}
    for row in shown:
        assigned = _metadata(row.get("metadata")).get("assigned_to_email")
        if isinstance(assigned, str):
            emails.add(assigned.strip().lower())
    emails |= {email.strip().lower() for email in assignees.values()}
    team = _team_labels(lakebase, {email for email in emails if email})
    items = [
        _project(
            row,
            viewer=viewer,
            privileged=privileged,
            team=team,
            automation_identities=automation_identities,
            assignee_email=assignees.get(str(row.get("request_id") or "")),
        )
        for row in shown
    ]
    return BorrowerDecisionHistoryResponse(borrower_id=borrower_id, items=items, truncated=truncated)
