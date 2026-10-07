"""Create a maker-checker approval request: replay, classify, write, audit.

Split out of the approval-request service (kept under its 450-line budget).
Every attempt is audited, refused ones included (integrator ruling R1,
2026-10-01): a request that holds borrowers writes APPROVAL_REQUESTED in the
same transaction as its batch, recording the requested ids and every skipped
id with its reason; a request that can hold none writes
APPROVAL_REQUEST_REFUSED with the same detail in its own transaction and the
router answers with counts only, so no per-borrower attribute is readable
without an audit row.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from backend.schemas.approval_request import (
    SKIP_REASONS,
    ApprovalRequestCreated,
    ApprovalRequestSkip,
    SkipReason,
)
from backend.services import approval_request_sql as sql
from backend.services.approval_requests import (
    ApprovalRequestConflict,
    ApprovalRequestRefused,
    normalize_actor,
)
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.repositories.protocols import LeadRepository

# The masked id the request tables CHECK; any other shape cannot be in gold.
_MASKED_BORROWER_ID = re.compile(r"^B-[0-9A-Z]{13}$")
_DECIDED_ACTIONS = frozenset({"approve", "reject", "hold"})
_DECIDED_GOLD_STATUSES = frozenset({"approved", "rejected", "hold"})


class _KeyTaken(Exception):
    """A concurrent request inserted this key first; re-read it."""


class _NothingRequested(Exception):
    def __init__(self, skipped: list[ApprovalRequestSkip]) -> None:
        super().__init__("nothing requested")
        self.skipped = skipped


def request_intent_hash(*, actor: str, borrower_ids: Sequence[str], note: str) -> str:
    """sha256 of the canonical request intent (actor, sorted ids, scrubbed note)."""

    material = json.dumps(
        {"actor": normalize_actor(actor), "borrower_ids": sorted(borrower_ids), "note": note},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _contactable(lead: Any) -> bool:
    # sales._ensure_assignable_borrower's suppression rule is the stricter
    # addition: a suppressed borrower is never requestable.
    return (
        getattr(lead, "marketing_eligible", None) is True
        and getattr(lead, "dnc", False) is not True
        and getattr(lead, "consent_status", None) == "opt_in"
        and not getattr(lead, "suppression_reason", None)
    )


def _already_decided(lakebase_action: str | None, gold_status: object) -> bool:
    # Lakebase wins over gold, which lags until the lifecycle sync. A latest
    # 'revoke' means the borrower is pending again.
    if lakebase_action is not None:
        return lakebase_action in _DECIDED_ACTIONS
    return str(gold_status or "") in _DECIDED_GOLD_STATUSES


def classify_borrowers(
    lakebase: LakebaseClient,
    lead_repo: LeadRepository,
    borrower_ids: Sequence[str],
) -> tuple[list[str], dict[str, SkipReason]]:
    """ONE gold read by id (no default filters) plus ONE Lakebase read."""

    ids = list(borrower_ids)
    leads = lead_repo.list(segment=None, portfolio_id=None, borrower_ids=ids, limit=len(ids))
    by_id = {str(lead.borrower_id): lead for lead in leads}
    decisions = lakebase.fetchall(
        sql.LATEST_FINALIZED_DECISIONS, {"borrower_ids": ids}, limit=len(ids)
    )
    latest_action = {str(row["borrower_id"]): str(row["action"]) for row in decisions}
    candidates: list[str] = []
    skipped: dict[str, SkipReason] = {}
    for borrower_id in ids:
        lead = by_id.get(borrower_id)
        if lead is None or not _MASKED_BORROWER_ID.fullmatch(borrower_id):
            skipped[borrower_id] = "not_found"
        elif not _contactable(lead):
            skipped[borrower_id] = "not_contactable"
        elif _already_decided(latest_action.get(borrower_id), getattr(lead, "approval_status", None)):
            skipped[borrower_id] = "already_decided"
        else:
            candidates.append(borrower_id)
    return candidates, skipped


def _skips_in_order(borrower_ids: Sequence[str], skipped: dict[str, SkipReason]) -> list[ApprovalRequestSkip]:
    return [
        ApprovalRequestSkip(borrower_id=borrower_id, reason=skipped[borrower_id])
        for borrower_id in borrower_ids
        if borrower_id in skipped
    ]


def _by_reason(skips: Sequence[ApprovalRequestSkip]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for skip in skips:
        grouped.setdefault(skip.reason, []).append(skip.borrower_id)
    return grouped


def _stored_replay(row: dict[str, Any] | None, intent_hash: str) -> ApprovalRequestCreated | None:
    if row is None:
        return None
    if str(row.get("request_intent_hash") or "") != intent_hash:
        raise ApprovalRequestConflict("request_key already belongs to a different request")
    response = row.get("response")
    if isinstance(response, str):
        response = json.loads(response)
    if not isinstance(response, dict):
        raise LakebaseError("approval request response is not finalized")
    return ApprovalRequestCreated.model_validate(response)


def _record_refusal(
    lakebase: LakebaseClient,
    *,
    actor: str,
    request_key: str,
    borrower_ids: Sequence[str],
    skips: list[ApprovalRequestSkip],
    note: str,
) -> ApprovalRequestRefused:
    """APPROVAL_REQUEST_REFUSED in its own transaction (no batch row exists)."""

    with lakebase.transaction() as conn:
        event = write_audit_event_in_transaction(
            conn,
            actor=actor,
            action="outreach.approval_request_refused",
            entity_type="approval_request",
            entity_id=request_key,
            payload_json={
                "borrower_ids": list(borrower_ids),
                "requested_count": 0,
                "skipped_count": len(skips),
                "skipped_by_reason": _by_reason(skips),
                "rationale": note,
            },
            event_type="APPROVAL_REQUEST_REFUSED",
            request_id=request_key,
        )
    counts: dict[str, int] = {reason: 0 for reason in SKIP_REASONS}
    for skip in skips:
        counts[skip.reason] += 1
    return ApprovalRequestRefused(counts, event.event_id)


def create_approval_request(
    lakebase: LakebaseClient,
    lead_repo: LeadRepository,
    *,
    actor: str,
    borrower_ids: Sequence[str],
    note: str,
    request_key: str,
) -> ApprovalRequestCreated:
    """Replay, classify, then one transaction: expire, batch, items, audit, finalize."""

    requested_by = normalize_actor(actor)
    intent_hash = request_intent_hash(actor=actor, borrower_ids=borrower_ids, note=note)
    key = {"requested_by": requested_by, "request_key": request_key}
    replay = _stored_replay(lakebase.fetchone(sql.BATCH_BY_KEY, key), intent_hash)
    if replay is not None:
        return replay
    candidates, skipped = classify_borrowers(lakebase, lead_repo, borrower_ids)
    if not candidates:
        raise _record_refusal(
            lakebase,
            actor=actor,
            request_key=request_key,
            borrower_ids=borrower_ids,
            skips=_skips_in_order(borrower_ids, skipped),
            note=note,
        )
    batch_id = str(uuid4())
    try:
        with lakebase.transaction() as conn:
            conn.execute(sql.EXPIRE_STALE_OPEN_ITEMS, {"borrower_ids": candidates})
            inserted = conn.execute(
                sql.INSERT_BATCH,
                {**key, "batch_id": batch_id, "request_intent_hash": intent_hash, "note": note},
            ).fetchone()
            if inserted is None:
                raise _KeyTaken()
            held = {
                str(row["borrower_id"])
                for row in conn.execute(
                    sql.INSERT_OPEN_ITEMS, {"batch_id": batch_id, "borrower_ids": candidates}
                ).fetchall()
            }
            for borrower_id in candidates:
                if borrower_id not in held:
                    skipped[borrower_id] = "already_requested"
            requested = [borrower_id for borrower_id in candidates if borrower_id in held]
            skips = _skips_in_order(borrower_ids, skipped)
            if not requested:
                raise _NothingRequested(skips)
            payload: dict[str, Any] = {
                "approval_request_batch_id": batch_id,
                "borrower_ids": requested,
                "requested_count": len(requested),
                "skipped_count": len(skips),
                "rationale": note,
            }
            if skips:
                payload["skipped_by_reason"] = _by_reason(skips)
            event = write_audit_event_in_transaction(
                conn,
                actor=actor,
                action="outreach.approval_request",
                entity_type="approval_request_batch",
                entity_id=batch_id,
                payload_json=payload,
                event_type="APPROVAL_REQUESTED",
                request_id=request_key,
            )
            created = ApprovalRequestCreated(
                batch_id=batch_id,
                audit_event_id=event.event_id,
                requested=requested,
                skipped=skips,
            )
            finalized = conn.execute(
                sql.FINALIZE_BATCH,
                {
                    "batch_id": batch_id,
                    "audit_event_id": event.event_id,
                    "response": json.dumps(
                        created.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                    ),
                },
            ).fetchone()
            if finalized is None:
                raise LakebaseError("approval request could not be finalized")
            return created
    except _KeyTaken:
        replay = _stored_replay(lakebase.fetchone(sql.BATCH_BY_KEY, key), intent_hash)
        if replay is None:
            raise LakebaseError("approval request key conflicted without a stored row") from None
        return replay
    except _NothingRequested as exc:
        # The batch, its items and the stale-item expiry rolled back; the
        # refusal is still audited (R1).
        raise _record_refusal(
            lakebase,
            actor=actor,
            request_key=request_key,
            borrower_ids=borrower_ids,
            skips=exc.skipped,
            note=note,
        ) from None


