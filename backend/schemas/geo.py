"""Geography rollup schemas.

Used by ``/api/geo/state-rollups`` — the per-state aggregates that feed
the USChoroplethMap hover tooltips and state-fill levels. These come
from ``mip.gold.funnel_snapshot_daily`` rows where ``state != '_ALL'``
and ``segment_code = '_ALL'`` (the national rollup for each state at
the latest snapshot_date).
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
    """

    state: str = Field(min_length=2, max_length=2)
    addressable: int = Field(ge=0)
    in_the_money: int = Field(ge=0)
    top_tier_opportunities: int = Field(ge=0)
    avg_score: int = Field(ge=0, le=100)


class StateRollupResponse(BaseModel):
    """Wire envelope — list of per-state rows + snapshot metadata.

    ``snapshot_date`` lets the UI show "data as of YYYY-MM-DD" near the
    map without racing the headline ``/api/portfolio/preview`` call.
    """

    rollups: list[StateRollup]
    snapshot_date: str | None = None
