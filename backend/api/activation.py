"""Governed customer activation / writeback staging."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.activation import (
    ActivationDestination,
    ActivationOutboxItem,
    ActivationStageRequest,
    ActivationStageResponse,
    ActivationSummary,
)
from backend.services.activation_state import (
    ActivationStateStore,
    get_activation_state_store,
)
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.repositories import (
    BorrowerRepository,
    get_borrower_repository,
)
from backend.services.sales_state import SalesStateStore, get_sales_state_store

router = APIRouter(prefix="/activation", tags=["activation"])

ActivationDep = Annotated[ActivationStateStore, Depends(get_activation_state_store)]
BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]


def _lakebase_503(exc: LakebaseError) -> HTTPException:
    return HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))


def _assert_activation_eligible(borrower: object, lifecycle: dict[str, object]) -> None:
    if lifecycle.get("approval_status") != "approved":
        raise HTTPException(status_code=409, detail="lead must be approved before activation staging")
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


@router.get("/destinations", response_model=list[ActivationDestination])
def list_activation_destinations(store: ActivationDep) -> list[ActivationDestination]:
    """Return the governed destination registry.

    Destinations can be configured, dry-run, disabled, or explicitly
    not-configured. The endpoint never accepts or returns destination secrets.
    """

    try:
        return store.list_destinations()
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/outbox", response_model=list[ActivationOutboxItem])
def list_activation_outbox(
    store: ActivationDep,
    borrower_id: Annotated[str | None, Query()] = None,
    destination_key: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[ActivationOutboxItem]:
    """Return staged activation records from the Lakebase outbox."""

    try:
        return store.list_outbox(
            borrower_id=borrower_id,
            destination_key=destination_key,
            limit=limit,
        )
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.get("/summary", response_model=ActivationSummary)
def activation_summary(store: ActivationDep) -> ActivationSummary:
    """Return destination status plus recent outbox activity."""

    try:
        return ActivationSummary(
            destinations=store.list_destinations(),
            recent_outbox=store.list_outbox(limit=10),
        )
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc


@router.post("/stage", response_model=ActivationStageResponse, status_code=202)
def stage_activation(
    payload: ActivationStageRequest,
    request: Request,
    store: ActivationDep,
    repo: BorrowerRepoDep,
    sales_state: SalesStateDep,
) -> ActivationStageResponse:
    """Stage an approved borrower for a governed customer destination.

    This is an outbox write, not external delivery. If the destination is not
    configured the row is still useful as a dry-run proof of what would leave
    MIP after customer connector setup.
    """

    actor = resolve_actor(request)
    borrower = repo.get(payload.borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    try:
        destination = store.get_destination(payload.destination_key)
        if destination is None:
            raise HTTPException(status_code=404, detail="activation destination is not configured")
        if destination.status == "disabled":
            raise HTTPException(status_code=409, detail="activation destination is disabled")
        if "stage_lead" not in destination.allowed_actions:
            raise HTTPException(status_code=409, detail="destination does not allow lead staging")
        lifecycle = sales_state.lifecycle_for(payload.borrower_id)
        _assert_activation_eligible(borrower, lifecycle)
        lifecycle_approval_id = str(lifecycle.get("approval_id") or "")
        if lifecycle_approval_id != payload.approval_id:
            raise HTTPException(status_code=409, detail="approval_id is not the current approved decision for this borrower")
        approved_decision = store.approved_decision_for(
            approval_id=payload.approval_id,
            borrower_id=payload.borrower_id,
        )
        if approved_decision is None:
            raise HTTPException(status_code=409, detail="approval_id is not an approved decision for this borrower")
        result = store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=payload,
            approved_decision=approved_decision,
            actor=actor,
        )
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LakebaseError as exc:
        raise _lakebase_503(exc) from exc
    return ActivationStageResponse(
        staged=True,
        activation=result.activation,
        audit_event_id=result.audit_event_id,
    )
