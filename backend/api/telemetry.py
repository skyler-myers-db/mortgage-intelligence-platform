"""Sanitized browser RUM telemetry ingestion endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from backend.config.settings import settings
from backend.schemas.telemetry import RumBatch
from backend.schemas.telemetry_response import RumAcceptedResponse
from backend.services import rum_rollup
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post(
    "/rum",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RumAcceptedResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def record_rum(
    batch: RumBatch,
    _: Annotated[None, Depends(require_json_content_type)],
) -> RumAcceptedResponse:
    """Accept sanitized browser performance telemetry.

    RUM events are operational telemetry, not audit rows. They are kept
    deliberately narrow: route-registry templates, metric name, numeric
    value, coarse rating, and closed-vocabulary details. The schema rejects
    query strings, borrower ids, UUIDs, and email-looking values first.

    Each accepted event is folded into the in-process day aggregates
    (backend/services/rum_rollup.py), which flush once a minute to
    mip_app.rum_daily. Nothing is logged per event and no actor is resolved:
    the stored rows carry no identifier of any kind (D-platform-process-d1).
    """
    if not settings.mip_rum_enabled:
        return RumAcceptedResponse(accepted=0, enabled=False)

    for event in batch.events:
        rum_rollup.add(event)
    return RumAcceptedResponse(accepted=len(batch.events), enabled=True)
