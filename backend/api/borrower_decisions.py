"""GET /borrowers/{borrower_id}/decisions: the working team's decision history.

Audit flow-04 phase 2 / tables-10 (D-audit-reads-c2). Admission is decided
INSIDE the handler, short-circuiting in this order: an administrator, a
configured auditor (D-audit-reads-c3), an approver, else an active sales-team
member; anyone else gets 403 and the client hides the section. It is not a
``require_admin`` / ``require_approver`` dependency on purpose: this read is
open to the working team, not only to approvers.

The response is the closed projection of
``borrower_decision_history.list_borrower_decisions``. The read is audit-free
(it writes no row and is never polled or prefetched by the client), calls no
warehouse, and is ``private, no-store``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.config.settings import settings
from backend.schemas.borrower_decisions import BorrowerDecisionHistoryResponse
from backend.schemas.common import validate_public_borrower_id
from backend.services.borrower_decision_history import list_borrower_decisions
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.rbac import (
    AuthenticatedActorDep,
    can_access_admin,
    can_access_approver,
    can_read_audit,
)
from backend.services.sales_state import SalesStateStore, get_sales_state_store

router = APIRouter(tags=["borrowers"])

LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]
SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]


def _automation_identities() -> frozenset[str]:
    """Configured service-principal identities: their rows read as 'Automation'."""

    configured = f"{settings.admin_identities or ''},{settings.approver_identities or ''}"
    return frozenset(token.strip().lower() for token in configured.split(",") if token.strip())


@router.get("/borrowers/{borrower_id}/decisions", response_model=BorrowerDecisionHistoryResponse)
def borrower_decisions(
    borrower_id: str,
    request: Request,
    response: Response,
    actor: AuthenticatedActorDep,
    lakebase: LakebaseDep,
    store: SalesStateDep,
) -> BorrowerDecisionHistoryResponse:
    """The borrower's latest governed decisions for the working team (audit-free)."""

    try:
        borrower_id = validate_public_borrower_id(borrower_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid borrower_id") from exc
    privileged = can_access_admin(request) or can_read_audit(request)
    try:
        if not privileged and not can_access_approver(request):
            try:
                store.require_active_team_member(actor, use_cache=True)
            except KeyError as exc:
                raise HTTPException(status_code=403, detail="forbidden") from exc
        history = list_borrower_decisions(
            lakebase,
            borrower_id,
            viewer=actor,
            privileged=privileged,
            automation_identities=_automation_identities(),
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return history
