"""Atomic outreach decision commit: the approvals ledger statements, the
campaign decision lock, idempotent replay lookups, and the single transaction
that writes the approval row and its audit event together."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import AuditMetadataViolation, AuditPIIError
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.outreach_decision_intent import (
    _campaign_decision_proof_fingerprint,
    _canonical_intent,
    _decision_intent_without_owner_claim,
    _intent_hash,
)
from backend.services.outreach_decision_ordering import BORROWER_DECISION_LOCK

_APPROVAL_INSERT = """
INSERT INTO mip_app.approvals (
    approval_id, campaign_id, variant_name, channel, borrower_id, offer_code, action,
    actor_email, rationale, request_id, assigned_to_email, follow_up_at,
    decision_intent, decision_payload_hash, decided_at
) VALUES (
    %(approval_id)s, %(campaign_id)s, %(variant_name)s, %(channel)s,
    %(borrower_id)s, %(offer_code)s, %(action)s,
    %(actor_email)s, %(rationale)s, %(request_id)s, %(assigned_to_email)s, %(follow_up_at)s,
    %(decision_intent)s, %(decision_payload_hash)s, clock_timestamp()
)
ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
"""


_APPROVAL_INSERT_RETURNING = """
INSERT INTO mip_app.approvals (
    approval_id, campaign_id, variant_name, channel, borrower_id, offer_code, action,
    actor_email, rationale, request_id, assigned_to_email, follow_up_at,
    decision_intent, decision_payload_hash, decided_at
) VALUES (
    %(approval_id)s, %(campaign_id)s, %(variant_name)s, %(channel)s,
    %(borrower_id)s, %(offer_code)s, %(action)s,
    %(actor_email)s, %(rationale)s, %(request_id)s, %(assigned_to_email)s, %(follow_up_at)s,
    %(decision_intent)s, %(decision_payload_hash)s, clock_timestamp()
)
ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
RETURNING approval_id
"""


_APPROVAL_LOOKUP_BY_REQUEST_ID = """
SELECT approval_id, borrower_id, action, actor_email, decision_intent,
       decision_payload_hash, decision_response, audit_event_id,
       campaign_id, variant_name, channel
FROM mip_app.approvals
WHERE request_id = %(request_id)s
LIMIT 1
"""


_APPROVAL_FINALIZE = """
UPDATE mip_app.approvals
SET decision_response = %(decision_response)s::jsonb,
    audit_event_id = %(audit_event_id)s
WHERE approval_id = %(approval_id)s
  AND decision_response IS NULL
  AND audit_event_id IS NULL
RETURNING approval_id, decision_response, audit_event_id
"""

_CAMPAIGN_DECISION_LOCK_LOOKUP = """
SELECT campaign_id::text, owner_email, status, json_contract_version, criteria,
       suppression_policy, holdout, household_dedup,
       treatment_state, treatment_materialization_id::text,
       treatment_algorithm_version, treatment_contract_fingerprint,
       treatment_fingerprint, treatment_source_snapshot_id,
       treatment_delta_version
FROM mip_app.campaigns
WHERE campaign_id = %(campaign_id)s::uuid
FOR SHARE
"""


def _derive_fallback_request_id(
    *,
    actor: str,
    action: str,
    decision_intent: str,
) -> str:
    """Generate a deterministic fallback ``request_id`` for legacy clients.

    R6-19: the partial unique index on ``mip_app.approvals.request_id``
    only covers rows WHERE ``request_id IS NOT NULL``. A legacy caller
    that POSTs ``/approve`` or ``/reject`` without a ``request_id`` (no
    Idempotency-Key header plumbing, older mobile build, retry storm
    from a watchdog) therefore bypasses the idempotency contract
    completely: a double-submit writes two approvals.

    We close the loop by deriving a stable key from ``(actor, action, full
    normalized decision intent)``. An identical no-key retry therefore
    collapses even when a transport retry crosses an arbitrary clock boundary.
    A caller that intends a separate decision must send a new explicit
    ``request_id``; first-party clients already generate one per user action.
    """
    material = f"{actor}|{action}|{_decision_intent_without_owner_claim(decision_intent)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]  # noqa: S324 -- not a secret
    # Prefix so audit review can tell server-derived keys apart from
    # client-sent ones (which are typically UUIDs / opaque tokens).
    return f"auto-{digest}"


def _lookup_existing_approval(
    lakebase: LakebaseClient,
    request_id: str | None,
    *,
    actor: str,
    borrower_id: str,
    action: str,
    expected_intent: str,
) -> dict[str, Any] | None:
    """Return the complete response for an exact replay, or None.

    R5-01: the idempotency contract is "same request_id => same
    actor + borrower + action => same approval_id, no duplicate write".
    Reusing the same request_id for a different decision is rejected
    instead of silently returning another user's or borrower's approval.

    Safe to short-circuit with None when ``request_id`` is falsy --
    legacy callers keep their pre-R5-01 behaviour.
    """
    if not request_id:
        return None
    try:
        row = lakebase.fetchone(_APPROVAL_LOOKUP_BY_REQUEST_ID, {"request_id": request_id})
    except LakebaseError:
        # Don't paper over the outage -- let the subsequent INSERT raise
        # and surface the real error as 503. Returning None here means
        # "we don't know if there's a duplicate"; the INSERT's ON
        # CONFLICT clause is the second line of defence.
        return None
    return _existing_approval_response_or_conflict(
        row,
        actor=actor,
        borrower_id=borrower_id,
        action=action,
        expected_intent=expected_intent,
    )


def _stored_decision_intent(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    stored_intent = str(row.get("decision_intent") or "")
    stored_hash = str(row.get("decision_payload_hash") or "")
    if not stored_intent or stored_hash != _intent_hash(stored_intent):
        raise HTTPException(
            status_code=409,
            detail="prior outreach decision cannot be reconstructed safely",
        )
    try:
        parsed = json.loads(stored_intent)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=409,
            detail="prior outreach decision cannot be reconstructed safely",
        ) from exc
    if not isinstance(parsed, dict) or _canonical_intent(parsed) != stored_intent:
        raise HTTPException(
            status_code=409,
            detail="prior outreach decision cannot be reconstructed safely",
        )
    return parsed, stored_intent


def _lookup_persisted_decision_replay(
    lakebase: LakebaseClient,
    request_id: str,
    *,
    actor: str,
    borrower_id: str,
    action: str,
    intent_matches_payload: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    """Reconstruct an exact persisted retry before any mutable source reads."""

    row = lakebase.fetchone(_APPROVAL_LOOKUP_BY_REQUEST_ID, {"request_id": request_id})
    if row is None:
        return None
    if (
        str(row.get("actor_email") or "") != actor
        or str(row.get("borrower_id") or "") != borrower_id
        or str(row.get("action") or "") != action
    ):
        raise HTTPException(
            status_code=409,
            detail="request_id already belongs to a different outreach decision",
        )
    intent, stored_intent = _stored_decision_intent(row)
    if not intent_matches_payload(intent):
        raise HTTPException(
            status_code=409,
            detail="request_id already belongs to a different outreach decision",
        )
    return _existing_approval_response_or_conflict(
        row,
        actor=actor,
        borrower_id=borrower_id,
        action=action,
        expected_intent=stored_intent,
    )


def _existing_approval_response_or_conflict(
    row: dict[str, Any] | None,
    *,
    actor: str,
    borrower_id: str,
    action: str,
    expected_intent: str,
) -> dict[str, Any] | None:
    if row is None:
        return None
    same_actor = str(row.get("actor_email") or "") == actor
    same_borrower = str(row.get("borrower_id") or "") == borrower_id
    same_action = str(row.get("action") or "") == action
    stored_intent = str(row.get("decision_intent") or "")
    stored_hash = str(row.get("decision_payload_hash") or "")
    stored_hash_matches = stored_hash == _intent_hash(stored_intent)
    same_intent = stored_hash_matches and (
        stored_intent == expected_intent
        or _decision_intent_without_owner_claim(stored_intent)
        == _decision_intent_without_owner_claim(expected_intent)
    )
    if not (same_actor and same_borrower and same_action and same_intent):
        raise HTTPException(
            status_code=409,
            detail="request_id already belongs to a different outreach decision",
        )
    response_value = row.get("decision_response")
    if isinstance(response_value, str):
        try:
            response_value = json.loads(response_value)
        except json.JSONDecodeError:
            response_value = None
    if not isinstance(response_value, dict):
        raise HTTPException(
            status_code=409,
            detail="prior outreach decision cannot be reconstructed safely",
        )
    approval_id = str(row.get("approval_id") or "")
    audit_event_id = str(row.get("audit_event_id") or "")
    if (
        not approval_id
        or not audit_event_id
        or str(response_value.get("approval_id") or "") != approval_id
        or str(response_value.get("audit_event_id") or "") != audit_event_id
    ):
        raise HTTPException(
            status_code=409,
            detail="prior outreach decision cannot be reconstructed safely",
        )
    return response_value


def _supports_atomic_outreach_write(lakebase: LakebaseClient) -> bool:
    return getattr(lakebase, "_supports_atomic_transactions", False) is True and callable(
        getattr(lakebase, "transaction", None)
    )


def _lock_and_revalidate_campaign_decision(
    conn: Any,
    *,
    campaign_id: str | None,
    action: str,
    expected_proof_fingerprint: str | None,
) -> None:
    """Linearize a campaign decision with lifecycle/treatment mutation."""

    if campaign_id is None:
        if expected_proof_fingerprint is not None:
            raise HTTPException(status_code=409, detail="campaign decision proof is invalid")
        return
    if not expected_proof_fingerprint:
        raise HTTPException(status_code=409, detail="campaign decision proof is invalid")

    campaign = conn.execute(
        _CAMPAIGN_DECISION_LOCK_LOOKUP,
        {"campaign_id": campaign_id},
    ).fetchone()
    if campaign is None:
        raise HTTPException(
            status_code=409,
            detail="Campaign lifecycle state changed before the outreach decision was saved.",
        )
    allowed_statuses = (
        {"approved", "live", "active"}
        if action == "approve"
        else {"draft", "pending_review", "approved", "live", "active"}
    )
    if str(campaign.get("status") or "").strip().lower() not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail="Campaign lifecycle state changed before the outreach decision was saved.",
        )
    try:
        actual_proof_fingerprint = _campaign_decision_proof_fingerprint(campaign)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Campaign targeting proof changed before the outreach decision was saved.",
        ) from exc
    if not hmac.compare_digest(actual_proof_fingerprint, expected_proof_fingerprint):
        raise HTTPException(
            status_code=409,
            detail="Campaign targeting proof changed before the outreach decision was saved.",
        )


def _commit_outreach_decision_atomic(
    lakebase: LakebaseClient,
    *,
    approval_id: str,
    actor: str,
    action: str,
    borrower_id: str,
    campaign_id: str | None,
    variant_name: str | None,
    channel: str,
    offer_code: str | None,
    rationale: str | None,
    request_id: str,
    audit_payload: dict[str, Any],
    evidence_ids: list[str],
    event_action: str,
    event_type: str,
    audit_request_id: str | None,
    decision_intent: str,
    campaign_proof_fingerprint: str | None,
    response_payload: dict[str, Any],
    subject_clip: str | None = None,
    assigned_to_email: str | None = None,
    follow_up_at: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Write approval + audit in one Lakebase transaction."""
    try:
        with lakebase.transaction() as conn:
            conn.execute(
                BORROWER_DECISION_LOCK,
                {"borrower_id": borrower_id},
            )
            existing = conn.execute(
                _APPROVAL_LOOKUP_BY_REQUEST_ID,
                {"request_id": request_id},
            ).fetchone()
            existing_response = _existing_approval_response_or_conflict(
                existing,
                actor=actor,
                borrower_id=borrower_id,
                action=action,
                expected_intent=decision_intent,
            )
            if existing_response is not None:
                return existing_response, False
            _lock_and_revalidate_campaign_decision(
                conn,
                campaign_id=campaign_id,
                action=action,
                expected_proof_fingerprint=campaign_proof_fingerprint,
            )
            row = conn.execute(
                _APPROVAL_INSERT_RETURNING,
                {
                    "approval_id": approval_id,
                    "campaign_id": campaign_id,
                    "variant_name": variant_name,
                    "channel": channel,
                    "borrower_id": borrower_id,
                    "offer_code": offer_code,
                    "action": action,
                    "actor_email": actor,
                    "rationale": rationale,
                    "request_id": request_id,
                    "assigned_to_email": assigned_to_email,
                    "follow_up_at": follow_up_at,
                    "decision_intent": decision_intent,
                    "decision_payload_hash": _intent_hash(decision_intent),
                },
            ).fetchone()
            if row is None:
                existing = conn.execute(
                    _APPROVAL_LOOKUP_BY_REQUEST_ID, {"request_id": request_id}
                ).fetchone()
                existing_response = _existing_approval_response_or_conflict(
                    existing,
                    actor=actor,
                    borrower_id=borrower_id,
                    action=action,
                    expected_intent=decision_intent,
                )
                if existing_response is not None:
                    return existing_response, False
                raise LakebaseError("Lakebase approval insert returned no row")

            row_approval_id = str(row.get("approval_id") or approval_id)
            payload_for_audit = {**audit_payload, "approval_id": row_approval_id}
            event = write_audit_event_in_transaction(
                conn,
                actor=actor,
                action=event_action,
                entity_type="approval",
                entity_id=row_approval_id,
                payload_json=payload_for_audit,
                evidence_ids=evidence_ids,
                event_type=event_type,
                subject_clip=subject_clip,
                request_id=audit_request_id,
            )
            final_response = {
                **response_payload,
                "approval_id": row_approval_id,
                "audit_event_id": event.event_id,
            }
            finalized = conn.execute(
                _APPROVAL_FINALIZE,
                {
                    "approval_id": row_approval_id,
                    "audit_event_id": event.event_id,
                    "decision_response": json.dumps(
                        final_response,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                },
            ).fetchone()
            if finalized is None:
                raise LakebaseError("Lakebase approval response could not be finalized")
            return final_response, True
    except (AuditMetadataViolation, AuditPIIError):
        raise
    except HTTPException:
        raise
    except LakebaseError:
        raise
    except Exception as exc:  # noqa: BLE001 -- normalize raw psycopg/fake-client errors
        raise LakebaseError("Lakebase atomic outreach decision failed") from exc
