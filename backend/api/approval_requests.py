"""Maker-checker approval requests (audit flow-02 / shell-06, 12.4 #10).

A signed-in user without the approver role asks an approver to review named
borrowers. The router is thin: identity and role gates, the rationale's governed
text screen, and the mapping of the service's typed outcomes to HTTP.

* POST /outreach/approval-requests: approvers are told to decide directly
  (409, nothing read or written); a refused rationale answers 422 before any read
  or write; every attempt that reaches classification is audited, a
  zero-eligible one included (409 with counts per reason only).
* GET /outreach/approval-requests: audit-free; approvers default to the open
  requests, everyone else to their own; a non-approver may not list others'.
* POST /outreach/approval-requests/{batch_id}/withdraw: the requester only.

Approve and reject are untouched here: they stay approver-only and accept an
optional ``approval_request_batch_id`` that links the decision to a request.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.approval_request import (
    APPROVAL_REQUEST_NOTE_MAX_LENGTH,
    ApprovalRequestCreate,
    ApprovalRequestCreated,
    ApprovalRequestList,
    ApprovalRequestScope,
    ApprovalRequestWithdrawn,
)
from backend.services.approval_request_create import create_approval_request
from backend.services.approval_requests import (
    ApprovalRequestConflict,
    ApprovalRequestForbidden,
    ApprovalRequestNotFound,
    ApprovalRequestRefused,
    list_approval_requests,
    withdraw_approval_request,
)
from backend.services.audit_store import AuditMetadataValueViolation
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.outreach_text_policy import refuse_ungoverned_text, text_policy_refusal
from backend.services.pii_redaction import scrub_free_text
from backend.services.rbac import can_access_approver, require_authenticated_actor
from backend.services.repositories import LeadRepository, get_lead_repository

router = APIRouter(prefix="/outreach/approval-requests", tags=["outreach"])

LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]
LeadRepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
ScopeParam = Annotated[
    ApprovalRequestScope | None,
    Query(description="open: every open request (approvers only); mine: your own. Defaults by role."),
]

APPROVER_DECIDES_DIRECTLY = "Approvers decide directly; open the review instead."
NOTHING_REQUESTABLE = "No selected borrower can be requested."
REQUEST_KEY_CONFLICT = "request_key already belongs to a different approval request"
OPEN_SCOPE_FORBIDDEN = "Only approvers can list every open approval request."
REQUEST_NOT_FOUND = "Approval request not found"
REQUEST_NOT_YOURS = "Only the requester can withdraw this approval request."
RATIONALE_TOO_LONG_REDACTED = (
    "rationale is too long once personal details are redacted; shorten it and leave them out"
)


def _lakebase_503(exc: LakebaseError) -> HTTPException:
    return HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))


@router.post("", response_model=ApprovalRequestCreated, responses=JSON_CONTENT_TYPE_RESPONSE)
def request_outreach_approval(
    payload: ApprovalRequestCreate,
    request: Request,
    lakebase: LakebaseDep,
    lead_repo: LeadRepoDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> ApprovalRequestCreated:
    actor = require_authenticated_actor(request)
    if can_access_approver(request):
        raise HTTPException(status_code=409, detail=APPROVER_DECIDES_DIRECTLY)
    note = scrub_free_text(payload.rationale)
    # The note is stored exactly as the audit row's rationale carries it, and
    # refused before any read or write when the ledger would refuse it. A
    # redaction token can be longer than what it replaced, and the batch's
    # length CHECK would then fail inside the commit (a 503 that also trips
    # the Lakebase breaker), so the scrubbed length is checked here too.
    if len(note) > APPROVAL_REQUEST_NOTE_MAX_LENGTH:
        raise HTTPException(status_code=422, detail=RATIONALE_TOO_LONG_REDACTED)
    refuse_ungoverned_text({"rationale": note})
    try:
        return create_approval_request(
            lakebase,
            lead_repo,
            actor=actor,
            borrower_ids=payload.borrower_ids,
            note=note,
            request_key=payload.request_key,
        )
    except ApprovalRequestConflict as exc:
        raise HTTPException(status_code=409, detail=REQUEST_KEY_CONFLICT) from exc
    except ApprovalRequestRefused as exc:
        # Counts per reason only: which borrower failed which check is in the
        # APPROVAL_REQUEST_REFUSED audit row, not in this answer (ruling R1).
        raise HTTPException(
            status_code=409,
            detail={
                "message": NOTHING_REQUESTABLE,
                "skipped_counts": exc.skipped_counts,
                "audit_event_id": exc.audit_event_id,
            },
        ) from exc
    except AuditMetadataValueViolation as exc:
        raise text_policy_refusal(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("", response_model=ApprovalRequestList)
def list_outreach_approval_requests(
    request: Request,
    lakebase: LakebaseDep,
    scope: ScopeParam = None,
) -> ApprovalRequestList:
    """Audit-free: Lakebase workflow app state, no borrower attribute."""

    actor = require_authenticated_actor(request)
    is_approver = can_access_approver(request)
    effective: Literal["open", "mine"] = scope or ("open" if is_approver else "mine")
    if effective == "open" and not is_approver:
        raise HTTPException(status_code=403, detail=OPEN_SCOPE_FORBIDDEN)
    try:
        return list_approval_requests(lakebase, actor=actor, scope=effective)
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.post(
    "/{batch_id}/withdraw",
    response_model=ApprovalRequestWithdrawn,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def withdraw_outreach_approval_request(
    batch_id: UUID,
    request: Request,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> ApprovalRequestWithdrawn:
    actor = require_authenticated_actor(request)
    try:
        return withdraw_approval_request(lakebase, actor=actor, batch_id=str(batch_id))
    except ApprovalRequestNotFound as exc:
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND) from exc
    except ApprovalRequestForbidden as exc:
        raise HTTPException(status_code=403, detail=REQUEST_NOT_YOURS) from exc
    except AuditMetadataValueViolation as exc:
        raise text_policy_refusal(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc
