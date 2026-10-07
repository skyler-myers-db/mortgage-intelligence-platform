"""POST /outreach/revoke: an approver revokes an approval (audit flow-v2).

Approver-only (the same fail-closed ``require_approver`` gate as approve and
reject), rationale required and screened before any read or write, request
id required for retry safety. The revoke appends a decision row and its
OUTREACH_REVOKE audit row in one Lakebase transaction; the approve row it
supersedes is never updated or deleted. After a NEW revoke commits, the
sales-state cache clears and the lifecycle sync is enqueued with reason
'revocation', exactly as approve and reject enqueue theirs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.schemas.outreach_revoke import OutreachRevokeRequest, OutreachRevokeResponse
from backend.services.audit_store import AuditMetadataValueViolation
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.job_trigger import enqueue_lifecycle_trigger
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.outreach_decision_commit import _lookup_persisted_decision_replay
from backend.services.outreach_revoke import (
    REVOKE_REFUSED_DETAIL,
    RevokeRefused,
    revoke_approval,
    revoke_intent_matches_payload,
)
from backend.services.outreach_text_policy import refuse_ungoverned_text, text_policy_refusal
from backend.services.pii_redaction import scrub_free_text
from backend.services.rbac import require_approver
from backend.services.repositories import OutreachRepository, get_outreach_repository
from backend.services.sales_state import clear_sales_state_cache

router = APIRouter(prefix="/outreach", tags=["outreach"])

RepoDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


@router.post("/revoke", response_model=OutreachRevokeResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def revoke_outreach(
    payload: OutreachRevokeRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> OutreachRevokeResponse:
    actor = require_approver(request)
    rationale = scrub_free_text(payload.rationale)
    refuse_ungoverned_text({"rationale": rationale})
    try:
        replay = _lookup_persisted_decision_replay(
            lakebase,
            payload.request_id,
            actor=actor,
            borrower_id=payload.borrower_id,
            action="revoke",
            intent_matches_payload=lambda intent: revoke_intent_matches_payload(
                intent, payload=payload, actor=actor, rationale=rationale
            ),
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if replay is not None:
        return OutreachRevokeResponse.model_validate(replay)
    borrower = repo.find_borrower(payload.borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    try:
        response, created = revoke_approval(
            lakebase,
            actor=actor,
            payload=payload,
            rationale=rationale,
            subject_clip=getattr(borrower, "clip_id", None),
        )
    except RevokeRefused as exc:
        raise HTTPException(status_code=409, detail=REVOKE_REFUSED_DETAIL[exc.kind]) from exc
    except AuditMetadataValueViolation as exc:
        raise text_policy_refusal(exc) from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if created:
        clear_sales_state_cache()
        enqueue_lifecycle_trigger(background, reason="revocation")
    return response
