"""Geography rollup endpoints.

/api/geo/state-rollups — per-state addressable / in-the-money /
top-tier / avg_score / top_segment_code driven by the latest
``mip.gold.funnel_snapshot_daily`` snapshot joined to
``mip.gold.state_top_segment``. Replaces the hardcoded ``STATE_FACTS``
literal the USChoroplethMap shipped with in Slice 9.

/api/geo/county-rollups?state=XX — per-county aggregates within the
given state, backed by ``mip.gold.county_rollup``. The UI lazy-fetches
when the user drills into a state; counties not present in the payload
render "—" (honest null).

/api/geo/zip-rollups?fips=NNNNN — per-ZIP aggregates within a county
(5-char FIPS), backed by ``mip.gold.zip_rollup``. Each row carries a
stable ``sample_borrower_id`` so the UI's ZIP-tile click-through deep-
links to a real borrower dossier.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.schemas.geo import (
    CountyRollupResponse,
    StateRollupResponse,
    ZipRollupResponse,
)
from backend.services.repositories import GeoRepository, get_geo_repository

router = APIRouter(prefix="/api/geo", tags=["geo"])

RepoDep = Annotated[GeoRepository, Depends(get_geo_repository)]


@router.get("/state-rollups", response_model=StateRollupResponse)
def state_rollups(
    repo: RepoDep,
    segment_codes: Annotated[
        str | None,
        Query(
            alias="segment_codes",
            description=(
                "Optional comma-separated SegmentCode list (itm,listed,permit,"
                "investor,equity,retention). When provided, per-state counts "
                "reflect ONLY borrowers matching the filter. Use segment_mode="
                "all when selected cards should narrow via intersection."
            ),
        ),
    ] = None,
    segment_mode: Annotated[
        str,
        Query(
            pattern="^(any|all)$",
            description=(
                "any = segment arrays overlap; all = borrower contains every "
                "selected segment code."
            ),
        ),
    ] = "any",
) -> StateRollupResponse:
    """Return per-state rollups for the latest funnel snapshot.

    60s TTL cache lives in the repository; the router is a thin pass-
    through so adding a per-portfolio filter later is a repo change,
    not a router change.

    2026-05-04 (FIX G): when ``segment_codes`` is supplied, the
    repository switches to a live segment-filtered query against
    ``mip.gold.borrower_360`` so the choropleth tooltip can show the
    borrower count for the active segment filter instead of the
    cross-segment total.
    """
    parsed: list[str] | None = None
    if segment_codes:
        parsed = [s.strip() for s in segment_codes.split(",") if s.strip()]
        # Cap the filter at six (the SegmentCode universe size). Anything
        # bigger than that is the same as no filter (returns _ALL); save
        # the warehouse the work and return the unfiltered rollup.
        if not parsed or len(parsed) >= 6:
            parsed = None
    return repo.state_rollups(segment_codes=parsed, segment_mode=segment_mode)


@router.get("/county-rollups", response_model=CountyRollupResponse)
def county_rollups(
    repo: RepoDep,
    state: Annotated[
        str,
        Query(
            min_length=2,
            max_length=2,
            description="2-char USPS state code (uppercased server-side).",
        ),
    ],
) -> CountyRollupResponse:
    """Return per-county rollups for the given state at the latest snapshot.

    State codes outside the 6-state footprint (IL/CA/FL/TX/WA/CO)
    legitimately return an empty ``rollups`` list -- the map UI renders
    "—" for any county not in the payload, which is the correct posture
    for a state without gold-layer coverage.
    """
    return repo.county_rollups(state)


@router.get("/zip-rollups", response_model=ZipRollupResponse)
def zip_rollups(
    repo: RepoDep,
    fips: Annotated[
        str,
        Query(
            alias="fips",
            min_length=5,
            max_length=5,
            description="5-char county FIPS (2-char state + 3-char county).",
        ),
    ],
) -> ZipRollupResponse:
    """Return per-ZIP rollups for the given county FIPS.

    Each ZIP carries a stable-ranked ``sample_borrower_id`` so the UI's
    ZIP-tile click-through can deep-link to ``/borrower-360/<id>``.
    Empty list when the county has no rollup rows (county outside
    footprint or CTAS hasn't run).
    """
    return repo.zip_rollups(fips)
