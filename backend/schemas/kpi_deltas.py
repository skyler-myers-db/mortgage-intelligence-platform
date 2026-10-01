"""Typed contracts for S3 KPI snapshots and last-login deltas.

The measure vocabulary is EXACTLY the S1 headline set defined by
``mip.semantics.portfolio_headline_metric_view`` (see the header of
``sql/metric_views/portfolio_headline_metric_view.sql``): marketable
population, refi economics screen (in-the-money), high opportunity
(canonical ``fn_high_opportunity`` threshold), offers available, offers
recommended, and average opportunity score. ``mip_app.kpi_snapshots``
persists the same names, so snapshot rows deserialize into these models
without a mapping layer.

Two EVENT measures ride beside them for Home's WHY NOW (audit flow-05):
listed for sale (``SUM(listed_for_sale)``) and competitor liens
(``SUM(is_competitor_lien)``). They are nullable: a snapshot taken before
the 2026-10 migration has no reading for them and stays NULL (never 0), so
its delta is None and the summary omits it rather than inventing a change.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Ordered list of the counted headline measures (everything except the
# average). Shared by the delta service and the parity contract test.
HEADLINE_COUNT_MEASURES: tuple[str, ...] = (
    "marketable_population",
    "refi_economics_screen",
    "high_opportunity",
    "offers_available",
    "offers_recommended",
)

# Nullable event measures (flow-05). Never in HEADLINE_COUNT_MEASURES: a
# missing reading is None end to end, never coerced to 0.
HEADLINE_EVENT_MEASURES: tuple[str, ...] = (
    "listed_for_sale",
    "competitor_lien",
)


class HeadlineKpis(BaseModel):
    """One reading of the S1 headline aggregates (live or snapshotted)."""

    marketable_population: int = Field(ge=0)
    refi_economics_screen: int = Field(ge=0)
    high_opportunity: int = Field(ge=0)
    offers_available: int = Field(ge=0)
    offers_recommended: int = Field(ge=0)
    avg_opportunity_score: float | None = Field(default=None, ge=0, le=100)
    listed_for_sale: int | None = Field(default=None, ge=0)
    competitor_lien: int | None = Field(default=None, ge=0)


class KpiSnapshot(HeadlineKpis):
    """A persisted ``mip_app.kpi_snapshots`` row."""

    snapshot_date: datetime | None = None
    snapshot_at: datetime
    source_view: str = "portfolio_headline_metric_view"


class KpiDeltas(BaseModel):
    """Signed current-minus-baseline differences per headline measure."""

    marketable_population: int
    refi_economics_screen: int
    high_opportunity: int
    offers_available: int
    offers_recommended: int
    avg_opportunity_score: float | None = None
    listed_for_sale: int | None = None
    competitor_lien: int | None = None


class KpiDeltaResult(BaseModel):
    """The S4 contract: current metrics vs the snapshot nearest the
    actor's previous visit.

    ``baseline`` / ``deltas`` are ``None`` when no anchor exists yet --
    either the actor has no recorded previous visit (first login) or the
    snapshot table is empty (pre-backfill install). ``current`` is always
    populated from the live metric view so S4 can render the headline
    numbers even without a comparison.
    """

    actor_email: str
    previous_visit_at: datetime | None = None
    baseline_snapshot_at: datetime | None = None
    current: HeadlineKpis
    baseline: HeadlineKpis | None = None
    deltas: KpiDeltas | None = None
