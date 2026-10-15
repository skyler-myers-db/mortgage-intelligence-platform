"""Growth Agent watchlist routes added in W5d (audit 2026-09-21 ``wow-ai-2``).

``POST /api/growth-agent/rate-watch`` saves "Watch this level" from the Rate
Lever. It is a sub-router of the ``/growth-agent`` prefix (mounted right after
``growth_agent_compose_routes``) so ``backend/api/growth_agent.py`` stays under
the file-size gate: the saved watch runs through that module's existing
``/monitors/{id}/run``, ``/monitors/run-due`` and ``/monitors/run-due-all``
routes, which rebuild the reviewed workflow from its stored criteria.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from backend.schemas.growth_agent_rate_watch import (
    GrowthAgentRateWatchRequest,
    GrowthAgentRateWatchResponse,
)
from backend.services.audit_store import resolve_actor
from backend.services.growth_agent_rate_watch import save_rate_watch
from backend.services.growth_agent_scheduler import growth_agent_scheduler_status
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.repositories import (
    RateSensitivityRepository,
    RateWindowRepository,
    get_rate_sensitivity_repository,
    get_rate_window_repository,
)

router = APIRouter(prefix="/growth-agent", tags=["growth-agent"])

LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]
RateSensitivityRepoDep = Annotated[RateSensitivityRepository, Depends(get_rate_sensitivity_repository)]
RateWindowRepoDep = Annotated[RateWindowRepository, Depends(get_rate_window_repository)]


@router.post(
    "/rate-watch",
    response_model=GrowthAgentRateWatchResponse,
    responses={
        **JSON_CONTENT_TYPE_RESPONSE,
        409: {"description": "Rate scenarios are not built, or the step is no longer on today's grid. Nothing was saved."},
    },
)
def save_growth_agent_rate_watch(
    payload: GrowthAgentRateWatchRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    lakebase: LakebaseDep,
    grid_repo: RateSensitivityRepoDep,
    window_repo: RateWindowRepoDep,
) -> GrowthAgentRateWatchResponse:
    """Watch the par level of one Rate Lever step as a reviewed watchlist.

    The body carries the step only: the server reads the level from today's
    scenario grid and names the watchlist. One ``GROWTH_AGENT_MONITOR_SAVE``
    audit row is written; an identical active watch is returned as-is with
    ``replayed`` true and no second row. No run starts and nothing is sent.
    """

    actor = resolve_actor(request)
    grid = grid_repo.rate_sensitivity()
    window = window_repo.rate_window()
    return save_rate_watch(
        lakebase,
        actor=actor,
        payload=payload,
        grid=grid,
        window=window,
        scheduler=growth_agent_scheduler_status(),
    )
