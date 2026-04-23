"""Geography rollup endpoints.

/api/geo/state-rollups — per-state addressable / in-the-money /
top-tier / avg_score driven by the latest ``mip.gold.funnel_snapshot_
daily`` snapshot. Replaces the hardcoded ``STATE_FACTS`` literal the
USChoroplethMap shipped with in Slice 9 so hover tooltips + fill levels
are real data, not synthetic copy.

County / ZIP rollups do NOT have a gold backing yet — we intentionally
scope this router to state grain and let the UI render county/ZIP
tooltips blank rather than lie.
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.geo import StateRollupResponse
from backend.services.repositories import GeoRepository, get_geo_repository

router = APIRouter(prefix="/api/geo", tags=["geo"])

RepoDep = Annotated[GeoRepository, Depends(get_geo_repository)]


@router.get("/state-rollups", response_model=StateRollupResponse)
def state_rollups(repo: RepoDep) -> StateRollupResponse:
    """Return per-state rollups for the latest funnel snapshot.

    60s TTL cache lives in the repository; the router is a thin pass-
    through so adding a per-portfolio filter later is a repo change,
    not a router change.
    """
    return repo.state_rollups()
