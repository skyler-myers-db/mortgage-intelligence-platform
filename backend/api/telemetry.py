"""Sanitized browser RUM telemetry ingestion endpoint."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from backend.config.settings import settings
from backend.schemas.telemetry import RumBatch
from backend.schemas.telemetry_response import RumAcceptedResponse
from backend.services.backpressure import actor_key_for_request
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.observability import emit

log = logging.getLogger(__name__)
router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post(
    "/rum",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RumAcceptedResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def record_rum(
    batch: RumBatch,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
) -> RumAcceptedResponse:
    """Accept sanitized browser performance telemetry.

    RUM events are operational telemetry, not audit rows. They are kept
    deliberately narrow: sanitized route patterns, metric name, numeric
    value, coarse rating, and non-identifying details. The schema rejects
    query strings, borrower ids, UUIDs, and email-looking values before
    anything reaches logs or downstream collectors.
    """
    if not settings.mip_rum_enabled:
        return RumAcceptedResponse(accepted=0, enabled=False)

    actor_key = actor_key_for_request(request)
    actor_class = "anonymous" if actor_key in {"anonymous", "untrusted-edge"} else "authenticated"
    for event in batch.events:
        emit(
            log,
            "rum_metric",
            metric=event.metric,
            value=round(event.value, 2),
            rating=event.rating,
            route=event.route,
            navigation_type=event.navigation_type,
            actor_class=actor_class,
            details=event.details,
        )
    return RumAcceptedResponse(accepted=len(batch.events), enabled=True)
