"""Revoke an approval in one Lakebase transaction (audit flow-v2).

The revoke never edits history: it appends an approvals row with action
'revoke' and writes its OUTREACH_REVOKE audit row in the same transaction,
then finalizes the row exactly like approve and reject. The lifecycle sync,
``SalesStateStore.lifecycle_for`` and the activation delivery guard
(``is_current_approval``) already read a latest non-approve/reject/hold row as
pending / none / superseded, so a revoke reopens the borrower without any
change there.

Allowed only while the approval is the borrower's CURRENT, finalized,
unbound (v1: no campaign) decision and its outreach is none or queued: no
call disposition or lead outcome since it was decided, no delivered
activation for it, and no active assignment a loan officer has worked. A
not-yet-worked active assignment is released, and the borrower's open
approval-request items are expired so the pending borrower can be requested
again.
"""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException

from backend.schemas.outreach_revoke import OutreachRevokeRequest, OutreachRevokeResponse
from backend.services.approval_requests import expire_open_items_for_borrower
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    AuditMetadataViolation,
    AuditPIIError,
)
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.outreach_decision_commit import (
    _APPROVAL_FINALIZE,
    _APPROVAL_INSERT_RETURNING,
    _APPROVAL_LOOKUP_BY_REQUEST_ID,
    _existing_approval_response_or_conflict,
)
from backend.services.outreach_decision_intent import _canonical_intent, _intent_hash
from backend.services.outreach_decision_ordering import (
    BORROWER_DECISION_LOCK,
    LATEST_BORROWER_DECISION,
    is_current_approval,
)

RevokeRefusal = Literal[
    "not_current",
    "campaign_bound",
    "dispositioned",
    "outcome_recorded",
    "delivered",
    "assignment_worked",
]

REVOKE_REFUSED_DETAIL: dict[str, str] = {
    "not_current": "This approval is no longer the borrower's current decision; reload to see the latest.",
    "campaign_bound": "A campaign-bound approval cannot be revoked in this release.",
    "dispositioned": "A call has been logged for this borrower since the approval; it can no longer be revoked.",
    "outcome_recorded": "A lead outcome has been recorded since the approval; it can no longer be revoked.",
    "delivered": "This approval's outreach has been delivered; it can no longer be revoked.",
    "assignment_worked": "The loan officer has already worked this lead; it can no longer be revoked.",
}

APPROVAL_ROW = """
SELECT approval_id::text AS approval_id, action, campaign_id::text AS campaign_id,
       channel, offer_code, decided_at, audit_event_id::text AS audit_event_id
FROM mip_app.approvals
WHERE approval_id = %(approval_id)s
  AND borrower_id = %(borrower_id)s
"""

OUTREACH_SINCE_APPROVAL = """
WITH dispositions AS (
    SELECT COUNT(*) AS n
    FROM mip_app.call_dispositions
    WHERE borrower_id = %(borrower_id)s
      AND occurred_at >= %(decided_at)s
),
outcomes AS (
    SELECT COUNT(*) AS n
    FROM mip_app.lead_outcomes
    WHERE borrower_id = %(borrower_id)s
      AND occurred_at >= %(decided_at)s
),
deliveries AS (
    SELECT COUNT(*) AS n
    FROM mip_app.activation_outbox
    WHERE approval_id = %(approval_id)s
      AND status = 'delivered'
)
SELECT dispositions.n AS dispositions, outcomes.n AS outcomes, deliveries.n AS deliveries
FROM dispositions, outcomes, deliveries
"""

ACTIVE_ASSIGNMENT_FOR_UPDATE = """
SELECT assignment_id::text AS assignment_id, status
FROM mip_app.lead_assignments
WHERE borrower_id = %(borrower_id)s
  AND released_at IS NULL
FOR UPDATE
"""

RELEASE_ASSIGNMENT = """
UPDATE mip_app.lead_assignments
SET released_at = now()
WHERE assignment_id = %(assignment_id)s
  AND released_at IS NULL
RETURNING assignment_id::text AS assignment_id
"""

_WORKED_ASSIGNMENT_STATUSES = frozenset({"actioned", "outcome_recorded"})


class RevokeRefused(RuntimeError):
    """The approval can no longer be revoked; ``kind`` names why."""

    def __init__(self, kind: RevokeRefusal) -> None:
        super().__init__(kind)
        self.kind: RevokeRefusal = kind


def revoke_intent(
    *, actor: str, payload: OutreachRevokeRequest, rationale: str, channel: str, offer_code: str
) -> str:
    """The canonical revoke intent (its hash is the row's decision_payload_hash)."""

    return _canonical_intent(
        {
            "action": "revoke",
            "actor": actor,
            "borrower_id": payload.borrower_id,
            "revoked_approval_id": payload.approval_id,
            "rationale": rationale,
            "channel": channel,
            "offer_code": offer_code,
        }
    )


def revoke_intent_matches_payload(
    intent: dict[str, Any], *, payload: OutreachRevokeRequest, actor: str, rationale: str
) -> bool:
    """Replay match on what the caller sent (channel/offer come from the approve row)."""

    return (
        intent.get("action") == "revoke"
        and intent.get("actor") == actor
        and intent.get("borrower_id") == payload.borrower_id
        and intent.get("revoked_approval_id") == payload.approval_id
        and intent.get("rationale") == rationale
    )


def _check_revocable(conn: Any, payload: OutreachRevokeRequest) -> dict[str, Any]:
    keys = {"borrower_id": payload.borrower_id, "approval_id": payload.approval_id}
    latest = conn.execute(LATEST_BORROWER_DECISION, {"borrower_id": payload.borrower_id}).fetchone()
    approval = conn.execute(APPROVAL_ROW, keys).fetchone()
    if (
        not is_current_approval(latest, approval_id=payload.approval_id)
        or approval is None
        or approval.get("action") != "approve"
        or not approval.get("audit_event_id")
    ):
        raise RevokeRefused("not_current")
    if approval.get("campaign_id"):
        raise RevokeRefused("campaign_bound")
    since = conn.execute(
        OUTREACH_SINCE_APPROVAL, {**keys, "decided_at": approval["decided_at"]}
    ).fetchone() or {}
    for column, kind in (
        ("dispositions", "dispositioned"),
        ("outcomes", "outcome_recorded"),
        ("deliveries", "delivered"),
    ):
        if int(since.get(column) or 0) > 0:
            raise RevokeRefused(kind)  # type: ignore[arg-type]
    return dict(approval)


def _release_unworked_assignment(conn: Any, borrower_id: str) -> str | None:
    active = conn.execute(ACTIVE_ASSIGNMENT_FOR_UPDATE, {"borrower_id": borrower_id}).fetchone()
    if active is None:
        return None
    if str(active.get("status") or "") in _WORKED_ASSIGNMENT_STATUSES:
        raise RevokeRefused("assignment_worked")
    released = conn.execute(
        RELEASE_ASSIGNMENT, {"assignment_id": active["assignment_id"]}
    ).fetchone()
    return str(released["assignment_id"]) if released else None


def revoke_approval(
    lakebase: LakebaseClient,
    *,
    actor: str,
    payload: OutreachRevokeRequest,
    rationale: str,
    subject_clip: str | None,
) -> tuple[OutreachRevokeResponse, bool]:
    """One transaction under the borrower decision lock; returns (body, created)."""

    try:
        with lakebase.transaction() as conn:
            conn.execute(BORROWER_DECISION_LOCK, {"borrower_id": payload.borrower_id})
            replay = _replay_in_transaction(conn, payload, actor=actor, rationale=rationale)
            if replay is not None:
                return replay, False
            approval = _check_revocable(conn, payload)
            channel = str(approval.get("channel") or "email")
            offer_code = str(approval.get("offer_code") or "")
            released_assignment_id = _release_unworked_assignment(conn, payload.borrower_id)
            expire_open_items_for_borrower(conn, payload.borrower_id)
            intent = revoke_intent(
                actor=actor, payload=payload, rationale=rationale, channel=channel, offer_code=offer_code
            )
            revoke_id = str(uuid4())
            row = conn.execute(
                _APPROVAL_INSERT_RETURNING,
                {
                    "approval_id": revoke_id,
                    "campaign_id": None,
                    "variant_name": None,
                    "channel": channel,
                    "borrower_id": payload.borrower_id,
                    "offer_code": offer_code,
                    "action": "revoke",
                    "actor_email": actor,
                    "rationale": rationale,
                    "request_id": payload.request_id,
                    "assigned_to_email": None,
                    "follow_up_at": None,
                    "decision_intent": intent,
                    "decision_payload_hash": _intent_hash(intent),
                },
            ).fetchone()
            if row is None:
                raise LakebaseError("Lakebase revoke insert returned no row")
            audit_payload: dict[str, Any] = {
                "approval_id": revoke_id,
                "revoked_approval_id": payload.approval_id,
                "borrower_id": payload.borrower_id,
                "offer_code": offer_code,
                "channel": channel,
                "rationale": rationale,
                "request_id": payload.request_id,
            }
            if released_assignment_id is not None:
                audit_payload["released_assignment_id"] = released_assignment_id
            event = write_audit_event_in_transaction(
                conn,
                actor=actor,
                action="outreach.revoke",
                entity_type="approval",
                entity_id=revoke_id,
                payload_json=audit_payload,
                event_type="OUTREACH_REVOKE",
                subject_clip=subject_clip,
                request_id=payload.request_id,
            )
            response = OutreachRevokeResponse(
                revoked=True,
                approval_id=revoke_id,
                revoked_approval_id=payload.approval_id,
                audit_event_id=event.event_id,
                released_assignment_id=released_assignment_id,
            )
            finalized = conn.execute(
                _APPROVAL_FINALIZE,
                {
                    "approval_id": revoke_id,
                    "audit_event_id": event.event_id,
                    "decision_response": json.dumps(
                        response.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                    ),
                },
            ).fetchone()
            if finalized is None:
                raise LakebaseError("Lakebase revoke could not be finalized")
            return response, True
    # A value-policy refusal stays itself (the router answers 422), as does a
    # replay conflict (409) and a refusal; anything else is a Lakebase outage.
    except (
        AuditMetadataViolation,
        AuditMetadataValueViolation,
        AuditPIIError,
        HTTPException,
        RevokeRefused,
        LakebaseError,
    ):
        raise
    except Exception as exc:  # noqa: BLE001 -- normalize raw psycopg errors
        raise LakebaseError("Lakebase revoke failed") from exc


def _replay_in_transaction(
    conn: Any, payload: OutreachRevokeRequest, *, actor: str, rationale: str
) -> OutreachRevokeResponse | None:
    existing = conn.execute(_APPROVAL_LOOKUP_BY_REQUEST_ID, {"request_id": payload.request_id}).fetchone()
    if existing is None:
        return None
    channel = str(existing.get("channel") or "email")
    stored_intent = str(existing.get("decision_intent") or "")
    try:
        offer_code = str(json.loads(stored_intent).get("offer_code") or "")
    except (json.JSONDecodeError, AttributeError):
        offer_code = ""
    stored = _existing_approval_response_or_conflict(
        existing,
        actor=actor,
        borrower_id=payload.borrower_id,
        action="revoke",
        expected_intent=revoke_intent(
            actor=actor, payload=payload, rationale=rationale, channel=channel, offer_code=offer_code
        ),
    )
    return OutreachRevokeResponse.model_validate(stored) if stored is not None else None
