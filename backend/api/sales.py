"""Sales-team assignment, disposition, aging, and conversion endpoints."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.portfolio import PortfolioCriteria
from backend.schemas.sales import (
    AssignLeadRequest,
    AssignmentResponse,
    BorrowerLifecycleResponse,
    DispositionRequest,
    DispositionResponse,
    DistributeLeadsRequest,
    DistributeLeadsResponse,
    LeadAssignment,
    LeadOutcomeRequest,
    LeadOutcomeResponse,
    SalesAgingLead,
    SalesConversionResponse,
    SalesOutcomeSummaryResponse,
    SalesStandupResponse,
    SalesTeamMember,
)
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.lakebase_bootstrap import ensure_sales_workflow_request_id_columns
from backend.services.repositories import (
    BorrowerRepository,
    LeadRepository,
    get_borrower_repository,
    get_lead_repository,
)
from backend.services.sales_state import SalesStateStore, get_sales_state_store

router = APIRouter(tags=["sales"])

BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
LeadRepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]


def _borrower_id(value: str) -> str:
    try:
        return validate_public_borrower_id(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid borrower_id") from exc


def _ensure_borrower(repo: BorrowerRepository, borrower_id: str) -> None:
    if repo.get(borrower_id) is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")


def _lakebase_503(exc: LakebaseError) -> HTTPException:
    return HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))


def _forbidden(exc: PermissionError | KeyError) -> HTTPException:
    return HTTPException(status_code=403, detail="sales operation is outside the actor scope")


def _ensure_assignable_borrower(
    borrower: object,
    *,
    lifecycle: BorrowerLifecycleResponse,
) -> None:
    if lifecycle.approval_status != "approved":
        raise HTTPException(status_code=409, detail="lead must be approved before assignment")
    fields_set = getattr(borrower, "model_fields_set", set())
    required_contactability = {"marketing_eligible", "consent_status", "suppression_reason"}
    if not required_contactability.issubset(set(fields_set)):
        raise HTTPException(status_code=409, detail="lead contactability is not configured")
    if getattr(borrower, "marketing_eligible", None) is not True:
        raise HTTPException(status_code=409, detail="lead is not marketing eligible")
    if getattr(borrower, "consent_status", None) != "opt_in":
        raise HTTPException(status_code=409, detail="lead does not have opt-in consent")
    if getattr(borrower, "suppression_reason", None):
        raise HTTPException(status_code=409, detail="lead is suppressed")


@router.get("/sales/team", response_model=list[SalesTeamMember])
def sales_team(request: Request, store: SalesStateDep) -> list[SalesTeamMember]:
    actor = resolve_actor(request)
    try:
        return store.list_team(actor=actor)
    except KeyError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.post("/leads/{borrower_id}/assign", response_model=AssignmentResponse)
def assign_lead(
    borrower_id: str,
    payload: AssignLeadRequest,
    request: Request,
    repo: BorrowerRepoDep,
    store: SalesStateDep,
) -> AssignmentResponse:
    borrower_id = _borrower_id(borrower_id)
    borrower = repo.get(borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    actor = resolve_actor(request)
    try:
        ensure_sales_workflow_request_id_columns(store._client)
        lifecycle = BorrowerLifecycleResponse(**store.lifecycle_for(borrower_id))
        _ensure_assignable_borrower(borrower, lifecycle=lifecycle)
        assignment, audit_event_id = store.assign_lead(
            borrower_id=borrower_id,
            assigned_to_email=payload.assigned_to_email,
            assigned_by=actor,
            expires_in_hours=payload.expires_in_hours,
            strategy=payload.strategy,
            subject_clip=borrower.clip,
            request_id=payload.request_id,
        )
        return AssignmentResponse(assignment=assignment, audit_event_id=audit_event_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="assigned_to_email is not an active loan officer") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/leads/{borrower_id}/assignment", response_model=LeadAssignment)
def lead_assignment(
    borrower_id: str,
    request: Request,
    repo: BorrowerRepoDep,
    store: SalesStateDep,
) -> LeadAssignment:
    borrower_id = _borrower_id(borrower_id)
    _ensure_borrower(repo, borrower_id)
    actor = resolve_actor(request)
    try:
        store.require_active_team_member(actor)
        assignment = store.active_assignment_for(borrower_id)
        if assignment is None:
            raise HTTPException(status_code=404, detail=f"No active assignment for borrower {borrower_id}")
        store.require_visible_assignee(actor=actor, assigned_to_email=assignment.assigned_to_email)
        return assignment
    except HTTPException:
        raise
    except KeyError as exc:
        raise _forbidden(exc) from exc
    except PermissionError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.post("/sales/distribute", response_model=DistributeLeadsResponse)
def distribute_leads(
    payload: DistributeLeadsRequest,
    request: Request,
    repo: LeadRepoDep,
    borrower_repo: BorrowerRepoDep,
    store: SalesStateDep,
) -> DistributeLeadsResponse:
    actor = resolve_actor(request)
    borrower_ids = list(payload.borrower_ids)
    if not borrower_ids:
        leads = repo.list(
            segment=None,
            portfolio_id=None,
            limit=payload.limit,
            approval_status=None if payload.approval_status == "any" else payload.approval_status,
            outreach_status=None if payload.outreach_status == "any" else payload.outreach_status,
            portfolio_criteria=PortfolioCriteria(marketing_eligibility="Eligible only"),
        )
        borrower_ids = [lead.borrower_id for lead in leads]
    try:
        ensure_sales_workflow_request_id_columns(store._client)
        store.require_manager_actor(actor)
        for borrower_id in borrower_ids[: payload.limit]:
            borrower = borrower_repo.get(borrower_id)
            if borrower is None:
                raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
            lifecycle = BorrowerLifecycleResponse(**store.lifecycle_for(borrower_id))
            _ensure_assignable_borrower(borrower, lifecycle=lifecycle)
        assignments, audit_event_id = store.distribute(
            borrower_ids=borrower_ids[: payload.limit],
            lo_emails=payload.lo_emails,
            assigned_by=actor,
            expires_in_hours=payload.expires_in_hours,
            strategy=payload.strategy,
            request_id=payload.request_id,
        )
        counts = Counter(a.assigned_to_email for a in assignments)
        return DistributeLeadsResponse(
            assigned_count=len(assignments),
            strategy=payload.strategy,
            assignments=assignments,
            per_lo_counts=dict(counts),
            audit_event_id=audit_event_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="lo_emails must all be active loan officers") from exc
    except PermissionError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.post("/leads/{borrower_id}/disposition", response_model=DispositionResponse)
def log_disposition(
    borrower_id: str,
    payload: DispositionRequest,
    request: Request,
    repo: BorrowerRepoDep,
    store: SalesStateDep,
) -> DispositionResponse:
    borrower_id = _borrower_id(borrower_id)
    borrower = repo.get(borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    actor = resolve_actor(request)
    try:
        ensure_sales_workflow_request_id_columns(store._client)
        disposition, audit_event_id = store.log_disposition(
            borrower_id=borrower_id,
            lo_email=payload.lo_email,
            actor=actor,
            outcome=payload.outcome,
            occurred_at=payload.occurred_at,
            callback_at=payload.callback_at,
            notes=payload.notes,
            subject_clip=borrower.clip,
            request_id=payload.request_id,
        )
        return DispositionResponse(disposition=disposition, audit_event_id=audit_event_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="lo_email is not an active loan officer") from exc
    except PermissionError as exc:
        if "assigned to another" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.post("/leads/{borrower_id}/outcome", response_model=LeadOutcomeResponse)
def record_lead_outcome(
    borrower_id: str,
    payload: LeadOutcomeRequest,
    request: Request,
    repo: BorrowerRepoDep,
    store: SalesStateDep,
) -> LeadOutcomeResponse:
    borrower_id = _borrower_id(borrower_id)
    borrower = repo.get(borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    actor = resolve_actor(request)
    try:
        outcome, audit_event_id = store.record_outcome(
            borrower_id=borrower_id,
            actor=actor,
            outcome_type=payload.outcome_type,
            source_system=payload.source_system,
            source_record_ref=payload.source_record_ref,
            assigned_to_email=payload.assigned_to_email,
            campaign_id=payload.campaign_id,
            loan_amount=payload.loan_amount,
            competitor_lender_label=payload.competitor_lender_label,
            occurred_at=payload.occurred_at,
            subject_clip=borrower.clip,
            request_id=payload.request_id,
        )
        return LeadOutcomeResponse(outcome=outcome, audit_event_id=audit_event_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="assigned_to_email is not an active loan officer") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/borrowers/{borrower_id}/lifecycle", response_model=BorrowerLifecycleResponse)
def borrower_lifecycle(
    borrower_id: str,
    request: Request,
    repo: BorrowerRepoDep,
    store: SalesStateDep,
) -> BorrowerLifecycleResponse:
    actor = resolve_actor(request)
    borrower_id = _borrower_id(borrower_id)
    _ensure_borrower(repo, borrower_id)
    try:
        store.require_active_team_member(actor)
        return BorrowerLifecycleResponse(**store.lifecycle_for(borrower_id))
    except KeyError as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/sales/aging", response_model=list[SalesAgingLead])
def sales_aging(
    request: Request,
    borrower_repo: BorrowerRepoDep,
    store: SalesStateDep,
    older_than_days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[SalesAgingLead]:
    actor = resolve_actor(request)
    try:
        store.require_manager_actor(actor)
        visible = store.visible_lo_emails(actor=actor)
        candidate_limit = min(500, max(limit * 5, limit))
        rows = store.aging(older_than_days=older_than_days, limit=candidate_limit)
        if visible is not None:
            rows = [
                row for row in rows
                if row.get("assigned_to_email") is None or row.get("assigned_to_email") in visible
            ]
        live_rows: list[dict[str, object]] = []
        for row in rows:
            borrower_id = str(row.get("borrower_id") or "")
            if not borrower_id:
                continue
            if borrower_repo.get(borrower_id) is None:
                continue
            live_rows.append(row)
            if len(live_rows) >= limit:
                break
        return [SalesAgingLead(**row) for row in live_rows]
    except (KeyError, PermissionError) as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/sales/standup", response_model=SalesStandupResponse)
def sales_standup(
    request: Request,
    store: SalesStateDep,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> SalesStandupResponse:
    actor = resolve_actor(request)
    target = day or (datetime.now().date() - timedelta(days=1))
    try:
        store.require_manager_actor(actor)
        return SalesStandupResponse(**store.standup(
            date=target.isoformat(),
            visible_lo_emails=store.visible_lo_emails(actor=actor),
        ))
    except (KeyError, PermissionError) as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/sales/conversion", response_model=SalesConversionResponse)
def sales_conversion(
    request: Request,
    store: SalesStateDep,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    group_by: Annotated[Literal["lo", "cohort"], Query(alias="groupBy")] = "lo",
) -> SalesConversionResponse:
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to must be on or after from")
    actor = resolve_actor(request)
    try:
        store.require_manager_actor(actor)
        rows = store.conversion(
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            group_by=group_by,
            visible_lo_emails=store.visible_lo_emails(actor=actor),
        )
        return SalesConversionResponse(
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            group_by=group_by,
            rows=rows,
        )
    except (KeyError, PermissionError) as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/sales/outcomes/summary", response_model=SalesOutcomeSummaryResponse)
def sales_outcome_summary(
    request: Request,
    store: SalesStateDep,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
) -> SalesOutcomeSummaryResponse:
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to must be on or after from")
    actor = resolve_actor(request)
    try:
        store.require_manager_actor(actor)
        summary = store.outcome_summary(
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            visible_lo_emails=store.visible_lo_emails(actor=actor),
        )
        return SalesOutcomeSummaryResponse(**summary)
    except (KeyError, PermissionError) as exc:
        raise _forbidden(exc) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc
