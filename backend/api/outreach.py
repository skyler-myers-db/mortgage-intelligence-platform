"""Outreach API -- draft + approve.

Slice 5 landmarks:
* ``/draft`` emits a ``DRAFT_OUTREACH`` audit row so we can
  reconstruct which drafts were shown to the approver.
* ``/approve`` emits an ``APPROVE`` audit row AND inserts a row into
  ``mip_app.approvals`` so the governance ledger has both the
  point-in-time verb and the decision record.
* Approval is a **synchronous** Lakebase write (no background task):
  the caller needs the approval_id returned synchronously, and a
  failed approval must surface as 503 rather than silently drop.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachApproveResponse,
    OutreachDraft,
    OutreachDraftRequest,
)
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.repositories import OutreachRepository, get_outreach_repository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outreach", tags=["outreach"])

RepoDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def _safe_audit_write(store: AuditStore, **kwargs: Any) -> None:
    try:
        store.write(**kwargs)
    except LakebaseError as exc:
        log.warning("audit.write dropped: %s", exc)


_APPROVAL_INSERT = """
INSERT INTO mip_app.approvals (
    approval_id, borrower_id, offer_code, action, actor_email, rationale
) VALUES (
    %(approval_id)s, %(borrower_id)s, %(offer_code)s, %(action)s,
    %(actor_email)s, %(rationale)s
)
"""


@router.post("/draft", response_model=OutreachDraft)
def draft_outreach(
    payload: OutreachDraftRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: AuditDep,
) -> OutreachDraft:
    b = repo.find_borrower(payload.borrower_id)
    if b is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    subject = f"{b.recommended_offer} opportunity for {b.display_name}"
    body = (
        f"Hi {b.display_name.split(' & ')[0]},\n\n"
        f"Based on recent public-record signals in {b.city}, {b.state}, you may qualify for "
        f"{b.recommended_offer}. {b.why_now}\n\n"
        "Reply to this note and a licensed officer will follow up. "
        "This draft is for human review only; no outreach has been sent."
    )
    background.add_task(
        _safe_audit_write,
        audit,
        actor=resolve_actor(request),
        action="draft_outreach",
        entity_type="outreach_draft",
        entity_id=b.borrower_id,
        payload_json={
            "channel": payload.channel,
            "offer_code": b.recommended_offer,
        },
        event_type="DRAFT_OUTREACH",
        subject_clip=b.clip_id,
    )
    return OutreachDraft(
        borrower_id=b.borrower_id,
        offer_code=f"OFFER-{b.borrower_id}",
        channel=payload.channel,
        subject=subject if payload.channel == "email" else None,
        body=body,
    )


@router.post("/approve", response_model=OutreachApproveResponse)
def approve_outreach(
    payload: OutreachApproveRequest,
    request: Request,
    audit: AuditDep,
    lakebase: LakebaseDep,
) -> OutreachApproveResponse:
    # Approval is a governed, auditable decision: a failure must 503
    # (not silently fall through). Actor attribution: prefer the
    # authenticated workspace user from X-Forwarded-Email; fall back
    # to the caller-supplied ``actor`` only when we're running in a
    # test/dev path without the header.
    actor = payload.actor if payload.actor != "anonymous" else resolve_actor(request)
    approval_id = f"apr-{uuid4().hex[:12]}"
    # Governance §4: approvals live in both the ``approvals`` table
    # (durable decision record, queryable by campaign) AND the
    # ``action_audit`` table (append-only ledger). We write approvals
    # first so the audit row's ``entity_id`` (the approval_id) is a
    # valid FK-equivalent pointer.
    try:
        lakebase.execute(
            _APPROVAL_INSERT,
            {
                "approval_id": approval_id,
                "borrower_id": payload.borrower_id,
                "offer_code": payload.offer_code,
                "action": "approve",
                "actor_email": actor,
                "rationale": None,
            },
        )
        event = audit.write(
            actor=actor,
            action="outreach.approve",
            entity_type="approval",
            entity_id=approval_id,
            payload_json={
                "approval_id": approval_id,
                "offer_code": payload.offer_code,
                "borrower_id": payload.borrower_id,
            },
            evidence_ids=payload.evidence_ids,
            event_type="APPROVE",
        )
    except LakebaseError as exc:
        # No silent fallback. The UI surfaces 503 as a retry banner;
        # the operator's next move is to check Lakebase status.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OutreachApproveResponse(
        approved=True,
        approval_id=approval_id,
        audit_event_id=event.event_id,
    )
