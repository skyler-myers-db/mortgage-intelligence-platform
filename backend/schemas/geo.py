"""Geography rollup schemas.

Used by ``/api/geo/state-rollups`` — the per-state aggregates that feed
the USChoroplethMap hover tooltips and state-fill levels. These come
from ``mip.gold.funnel_snapshot_daily`` rows where ``state != '_ALL'``
and ``segment_code = '_ALL'`` (the national rollup for each state at
the latest snapshot_date).

slice13-accuracy-validation: adds county + ZIP rollup schemas and
extends ``StateRollup`` with ``top_segment_code`` sourced from
``mip.gold.state_top_segment``.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class StateRollup(BaseModel):
    """One state's addressable + high-intent + top-tier count + avg score.

    ``state`` is the 2-char USPS code (uppercase) — the map component
    lowercases it to match ``@svg-maps/usa`` location ids.

    Fields map 1:1 to ``funnel_snapshot_daily`` columns so the UI never
    has to re-aggregate: ``addressable`` is the borrower count for the
    state, ``in_the_money`` is the high-intent subset,
    ``top_tier_opportunities`` is the ``opportunity_score ≥ 75`` subset
    (for the lvl-4 hero tier on the choropleth), and ``avg_score`` is
    the state-wide mean opportunity score.

    ``top_segment_code`` is the dominant SegmentCode for the state
    sourced from ``mip.gold.state_top_segment``. Nullable because the
    join is ``LEFT`` (state has data but no segment-code row yet on
    first deploy); the UI renders a neutral "unknown" segment label
    when null.
    """

    state: str = Field(min_length=2, max_length=2)
    addressable: int = Field(ge=0)
    in_the_money: int = Field(ge=0)
    top_tier_opportunities: int = Field(ge=0)
    avg_score: int = Field(ge=0, le=100)
    top_segment_code: str | None = None


class StateRollupResponse(BaseModel):
    """Wire envelope — list of per-state rows + snapshot metadata.

    ``snapshot_date`` lets the UI show "data as of YYYY-MM-DD" near the
    map without racing the headline ``/api/portfolio/preview`` call.
    """

    rollups: list[StateRollup]
    snapshot_date: str | None = None


class CountyRollup(BaseModel):
    """One county's aggregate for the USChoroplethMap county drill.

    ``fips_5`` is the 5-char FIPS (2-char state + 3-char county). The UI
    keys on this for hover lookup + keeps rendering "—" for any county
    not returned by the endpoint.
    """

    fips_5: str = Field(min_length=5, max_length=5)
    state: str = Field(min_length=2, max_length=2)
    county_name: str | None = None
    addressable_borrowers: int = Field(ge=0)
    in_the_money_borrowers: int = Field(ge=0)
    high_opportunity_borrowers: int = Field(ge=0)
    avg_opportunity_score: int = Field(ge=0, le=100)
    top_segment_code: str | None = None


class CountyRollupResponse(BaseModel):
    """Wire envelope — list of counties for a given state + snapshot date.

    ``scope_note`` is a short honesty string surfaced to the UI so a
    user drilling into a state understands the actual county coverage
    discovered from the current Cotality share. It is generated from
    ``mip.gold.county_rollup`` rather than a demo-specific constant.
    Null means no banner needs to render (e.g. out-of-footprint state,
    empty response). The field name matches the existing frontend
    ``CountyRollupResponse`` contract in ``frontend/src/types.ts``.
    """

    state: str = Field(min_length=2, max_length=2)
    rollups: list[CountyRollup]
    snapshot_date: str | None = None
    scope_note: str | None = None


class ZipRollup(BaseModel):
    """One ZIP's aggregate for the USChoroplethMap ZIP drill.

    ``sample_borrower_id`` is the stable-ranked top borrower in the ZIP
    (ORDER BY opportunity_score DESC, borrower_id ASC LIMIT 1). The UI
    uses this for the ZIP tile deep-link — clicking a tile navigates
    to ``/borrower-360/<sample_borrower_id>``.
    """

    zip: str = Field(min_length=5, max_length=5)
    state: str = Field(min_length=2, max_length=2)
    county_fips_5: str | None = None
    addressable_borrowers: int = Field(ge=0)
    avg_opportunity_score: int = Field(ge=0, le=100)
    top_segment_code: str | None = None
    sample_borrower_id: str | None = None


class ZipRollupResponse(BaseModel):
    """Wire envelope — list of ZIPs for a given county FIPS + snapshot date."""

    fips_5: str = Field(min_length=5, max_length=5)
    rollups: list[ZipRollup]
    snapshot_date: str | None = None
