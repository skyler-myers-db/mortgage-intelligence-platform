"""Follow-ups due: the rail item's summary and the tray's list (D-shell-deviations-a).

Neither route writes an audit row, resolves the audit store or touches the
identity-fallback counter: they read the caller's own workflow state, never
another person's and never a borrower dossier. Both answer
``Cache-Control: private, no-store``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.schemas.sales_followups import FollowupList, FollowupSummary
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.rbac import AuthenticatedActorDep
from backend.services.repositories import BorrowerRepository, get_borrower_repository
from backend.services.sales_followups import (
    FollowupDependencyError,
    FollowupService,
    get_followup_service,
    normalize_actor,
)

router = APIRouter(tags=["sales"])

FollowupServiceDep = Annotated[FollowupService, Depends(get_followup_service)]
BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]

_NO_STORE = "private, no-store"


def _unavailable(exc: FollowupDependencyError) -> HTTPException:
    return HTTPException(status_code=503, detail=safe_dependency_detail(exc.dependency))


@router.get("/sales/followups/summary", response_model=FollowupSummary)
def followups_summary(
    request: Request,
    response: Response,
    service: FollowupServiceDep,
) -> FollowupSummary:
    """Whether the caller has a Follow-ups tray, and its next due time.

    AUDIT EXEMPT: the caller's own workflow state, polled once a minute by
    the rail; no borrower field and no audit row. Lakebase only: no
    warehouse statement. No identity, or an ineligible caller, reads
    ``eligible: false``.
    """
    response.headers["Cache-Control"] = _NO_STORE
    try:
        return service.summary(request)
    except FollowupDependencyError as exc:
        raise _unavailable(exc) from exc


@router.get("/sales/followups", response_model=FollowupList)
def followups_list(
    request: Request,
    response: Response,
    actor: AuthenticatedActorDep,
    service: FollowupServiceDep,
    repo: BorrowerRepoDep,
    window_days: Annotated[int, Query(ge=1, le=14)] = 7,
) -> FollowupList:
    """The caller's due callbacks and follow-ups and new assignments.

    AUDIT EXEMPT: the caller's own work list, read when the tray opens;
    every row is checked against gold with the Lead Queue's eligibility pair
    and carries only a masked id, times and closed codes. 401 without a
    forwarded identity, 403 when the caller is neither an active sales-team
    member nor an approver.
    """
    response.headers["Cache-Control"] = _NO_STORE
    normalized = normalize_actor(actor)
    try:
        if not service.is_eligible(request, normalized):
            raise HTTPException(status_code=403, detail="follow-ups are for sales-team members and approvers")
        return service.list(normalized, window_days=window_days, repo=repo)
    except FollowupDependencyError as exc:
        raise _unavailable(exc) from exc
