from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachApproveResponse,
    OutreachDraft,
    OutreachDraftRequest,
)
from backend.services.audit_store import audit_store
from backend.services.repositories import OutreachRepository, get_outreach_repository

router = APIRouter(prefix="/api/outreach", tags=["outreach"])

RepoDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]


@router.post("/draft", response_model=OutreachDraft)
def draft_outreach(payload: OutreachDraftRequest, repo: RepoDep) -> OutreachDraft:
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
    return OutreachDraft(
        borrower_id=b.borrower_id,
        offer_code=f"OFFER-{b.borrower_id}",
        channel=payload.channel,
        subject=subject if payload.channel == "email" else None,
        body=body,
    )


@router.post("/approve", response_model=OutreachApproveResponse)
def approve_outreach(payload: OutreachApproveRequest) -> OutreachApproveResponse:
    approval_id = f"apr-{uuid4().hex[:12]}"
    event = audit_store.write(
        actor=payload.actor,
        action="outreach.approve",
        entity_type="borrower",
        entity_id=payload.borrower_id,
        payload_json={
            "approval_id": approval_id,
            "offer_code": payload.offer_code,
        },
        evidence_ids=payload.evidence_ids,
    )
    return OutreachApproveResponse(
        approved=True,
        approval_id=approval_id,
        audit_event_id=event.event_id,
    )
