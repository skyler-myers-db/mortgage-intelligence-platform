"""POST /leads/{borrower_id}/snooze: snooze one Follow-ups tray item (wow-power-3 phase 2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead_snooze import LeadSnoozeRequest, LeadSnoozeResponse
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lead_snooze import LeadSnoozeService, SnoozeConflict, get_lead_snooze_service
from backend.services.rbac import AuthenticatedActorDep
from backend.services.repositories import BorrowerRepository, get_borrower_repository
from backend.services.sales_followups import (
    FollowupDependencyError,
    FollowupService,
    get_followup_service,
    normalize_actor,
)

router = APIRouter(tags=["sales"])

SnoozeServiceDep = Annotated[LeadSnoozeService, Depends(get_lead_snooze_service)]
FollowupServiceDep = Annotated[FollowupService, Depends(get_followup_service)]
BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]


@router.post(
    "/leads/{borrower_id}/snooze",
    response_model=LeadSnoozeResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def snooze_lead_followup(
    borrower_id: str,
    payload: LeadSnoozeRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    actor: AuthenticatedActorDep,
    followups: FollowupServiceDep,
    service: SnoozeServiceDep,
    repo: BorrowerRepoDep,
) -> LeadSnoozeResponse:
    """Hide one of the caller's own open follow-ups for 1, 3 or 7 days.

    The service writes the LEAD_SNOOZE audit row in the same Lakebase
    transaction as the snooze row, or exactly one LEAD_SNOOZE_REFUSED row
    when the item may not be contacted (409 not_contactable). 401 without a
    forwarded identity, 403 for a caller with no tray, 409 no_open_follow_up
    when the caller has no open item for the borrower.
    """
    try:
        borrower_id = validate_public_borrower_id(borrower_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid borrower_id") from exc
    normalized = normalize_actor(actor)
    try:
        if not followups.is_eligible(request, normalized):
            raise HTTPException(status_code=403, detail="follow-ups are for sales-team members and approvers")
        return service.snooze(
            actor=normalized,
            borrower_id=borrower_id,
            days=payload.snooze_days,
            request_id=payload.request_id,
            repo=repo,
        )
    except SnoozeConflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except FollowupDependencyError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail(exc.dependency)) from exc
