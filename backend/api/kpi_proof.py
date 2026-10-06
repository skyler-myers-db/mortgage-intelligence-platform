"""Reproduce SQL for a headline KPI (audit 2026-09-21 ``flow-06`` phase 2).

Decision ``D-audit-reads-c1`` (its flow-06 ruling): server-emitted,
parameter-bound, gold-only reproduce SQL for a KPI may be shown to any
authenticated user, as the borrower proof drawer already does. The text comes
from ``backend/services/kpi_proof_sql.py``, built from the governed constants
the KPI's own read executes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from backend.schemas.kpi_proof import KpiProofKey, KpiProofResponse
from backend.services.kpi_proof_sql import KpiProofRefusedError, build_kpi_proof
from backend.services.rbac import AuthenticatedActorDep

router = APIRouter(prefix="/kpi-proof", tags=["evidence"])


@router.get("", response_model=KpiProofResponse)
def get_kpi_proof(
    kpi: Annotated[KpiProofKey, Query()],
    response: Response,
    _actor: AuthenticatedActorDep,
) -> KpiProofResponse:
    """The governed statement that produced one KPI card's number.

    AUDIT EXEMPT: emits fixed governed SQL text; reads nothing.
    """

    response.headers["Cache-Control"] = "private, no-store"
    try:
        return build_kpi_proof(kpi)
    except KpiProofRefusedError as exc:
        # Fail closed: a statement outside the reproduce policy is a server
        # defect and is never emitted (nor relaxed to pass).
        raise HTTPException(status_code=500, detail="reproduce SQL unavailable for this KPI") from exc
