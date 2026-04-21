from uuid import uuid4

from fastapi import APIRouter, HTTPException

from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachApproveResponse,
    OutreachDraft,
    OutreachDraftRequest,
)
from backend.services import mock_data
from backend.services.audit_store import audit_store

router = APIRouter(prefix="/api/outreach", tags=["outreach"])


@router.post("/draft", response_model=OutreachDraft)
def draft_outreach(payload: OutreachDraftRequest) -> OutreachDraft:
    for b in mock_data.BORROWERS:
        if b.borrower_id == payload.borrower_id:
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
    raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")


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
