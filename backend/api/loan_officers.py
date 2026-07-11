"""Loan-officer roster and audited assignment-lifecycle endpoints (S2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.loan_officer import (
    AssignLoanOfficerRequest,
    AssignmentLifecycleStatus,
    AssignmentStatusUpdateRequest,
    LoanOfficer,
    LoanOfficerAssignment,
    LoanOfficerAssignmentResponse,
)
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseError
from backend.services.loan_officer_state import (
    IllegalStatusTransitionError,
    LoanOfficerStateStore,
    get_loan_officer_state_store,
)
from backend.services.repositories import BorrowerRepository, get_borrower_repository

router = APIRouter(tags=["loan-officers"])

BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
LoanOfficerStateDep = Annotated[LoanOfficerStateStore, Depends(get_loan_officer_state_store)]


def _lakebase_503(exc: LakebaseError) -> HTTPException:
    return HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))


def _forbidden(exc: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail="loan-officer operation is outside the actor scope")


@router.get("/loan-officers", response_model=list[LoanOfficer])
def list_loan_officers(store: LoanOfficerStateDep) -> list[LoanOfficer]:
    try:
        return store.list_officers()
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/loan-officers/assignments", response_model=list[LoanOfficerAssignment])
def list_loan_officer_assignments(
    store: LoanOfficerStateDep,
    loan_officer_id: str | None = None,
    borrower_id: str | None = None,
    status: AssignmentLifecycleStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[LoanOfficerAssignment]:
    if borrower_id is not None:
        try:
            borrower_id = validate_public_borrower_id(borrower_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid borrower_id") from exc
    try:
        return store.list_assignments(
            loan_officer_id=loan_officer_id,
            borrower_id=borrower_id,
            status=status,
            limit=limit,
        )
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.post(
    "/loan-officers/assignments",
    response_model=LoanOfficerAssignmentResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def assign_loan_officer(
    payload: AssignLoanOfficerRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    repo: BorrowerRepoDep,
    store: LoanOfficerStateDep,
) -> LoanOfficerAssignmentResponse:
    borrower = repo.get(payload.borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    actor = resolve_actor(request)
    try:
        assignment, audit_event_id = store.assign_lead(
            borrower_id=payload.borrower_id,
            loan_officer_id=payload.loan_officer_id,
            assigned_by=actor,
            subject_clip=borrower.clip,
            request_id=payload.request_id,
        )
        return LoanOfficerAssignmentResponse(
            assignment=assignment,
            audit_event_id=audit_event_id or None,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail="loan_officer_id is not an active loan officer",
        ) from exc
    except PermissionError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.patch(
    "/loan-officers/assignments/{assignment_id}/status",
    response_model=LoanOfficerAssignmentResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def update_assignment_status(
    assignment_id: str,
    payload: AssignmentStatusUpdateRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    store: LoanOfficerStateDep,
) -> LoanOfficerAssignmentResponse:
    actor = resolve_actor(request)
    try:
        assignment, audit_event_id = store.transition_status(
            assignment_id=assignment_id,
            to_status=payload.status,
            actor=actor,
            request_id=payload.request_id,
        )
        return LoanOfficerAssignmentResponse(
            assignment=assignment,
            audit_event_id=audit_event_id or None,
        )
    except IllegalStatusTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="assignment not found") from exc
    except PermissionError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc
