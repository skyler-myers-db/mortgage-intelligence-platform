"""Native analytics API schemas.

These contracts back the in-app analytics workspace. They intentionally
mirror the Lakeview dashboard datasets over ``mip.gold`` and
``mip.semantics`` while exposing only app-safe identifiers and aggregate
signals. Raw CLIP, owner names, addresses, and share-level identifiers do
not cross this boundary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.lead import SegmentCode

# Schema modules cannot import runtime services. These bounds mirror
# backend.services.scoring.score_band; the analytics schema tests pin every
# edge against that canonical function so either side changing alone fails CI.
_SCORE_BAND_HIGH_MIN = 85
_SCORE_BAND_MED_MIN = 65


def _expected_score_band(opportunity_score: int) -> Literal["high", "med", "low"]:
    if opportunity_score >= _SCORE_BAND_HIGH_MIN:
        return "high"
    if opportunity_score >= _SCORE_BAND_MED_MIN:
        return "med"
    return "low"


class AnalyticsFilters(BaseModel):
    """Validated filter bag shared by the native analytics endpoints."""

    states: list[str] = Field(default_factory=list)
    segment_codes: list[SegmentCode] = Field(default_factory=list)
    segment_mode: Literal["any", "all"] = "any"
    lender_relationship: (
        Literal[
            "Current customer",
            "Former customer",
            "Competitor customer",
        ]
        | None
    ) = None
    target_lender_ref: str | None = None
    signal_types: list[str] = Field(default_factory=list)
    days: int = Field(default=30, ge=1, le=90)

    @field_validator("target_lender_ref")
    @classmethod
    def _target_lender_ref_is_public_safe(cls, value: str | None) -> str | None:
        normalised = normalize_public_lender_ref(value, allow_all=True)
        return None if normalised == "All" else normalised


class FunnelTotals(BaseModel):
    snapshot_date: str | None = None
    addressable_borrowers: int = Field(default=0, ge=0)
    in_the_money_borrowers: int = Field(default=0, ge=0)
    high_opportunity_borrowers: int = Field(default=0, ge=0)
    offer_recommended_borrowers: int = Field(default=0, ge=0)
    approved_borrowers: int = Field(default=0, ge=0)
    actioned_borrowers: int = Field(default=0, ge=0)


class FunnelStage(BaseModel):
    stage: str
    stage_order: int = Field(ge=1)
    borrower_count: int = Field(ge=0)
    # Same per-stage disclosure contract as the approval funnel's
    # ApprovalFunnelStage.source: every count names the objects it was
    # computed from, so the two analytics tabs are comparable rather than
    # merely adjacent. The workflow stages name the lagging gold mirror.
    source: str


class ScoreBucket(BaseModel):
    score_bucket: int = Field(ge=0, le=100)
    borrower_count: int = Field(ge=0)


class ExecutiveProvenance(BaseModel):
    """As-of boundary for the executive funnel's gold reads.

    ``snapshot_date`` is the scoring refresh boundary carried on
    ``mip.gold.borrower_360``. ``lifecycle_synced_at`` is the last
    Lakebase-to-``mip.gold.borrower_lifecycle_state`` mirror sync that touched
    a lifecycle row — the reason Approved/Actioned here can trail the approval
    funnel tab, which reads Lakebase directly and is exact. The lag is by
    design; publishing it is what keeps the two tabs honest.
    """

    snapshot_date: str | None = None
    lifecycle_synced_at: str | None = None
    population_source: str
    workflow_source: str
    note: str


class ExecutiveAnalyticsResponse(BaseModel):
    totals: FunnelTotals
    stages: list[FunnelStage]
    score_distribution: list[ScoreBucket]
    provenance: ExecutiveProvenance


class StateOpportunityRow(BaseModel):
    state: str = Field(min_length=2, max_length=2)
    borrower_count: int = Field(ge=0)
    mean_opportunity_score: int = Field(ge=0, le=100)
    in_the_money_borrowers: int = Field(ge=0)


class StateAvmValueRow(BaseModel):
    state: str = Field(min_length=2, max_length=2)
    total_avm_value_usd: int = Field(ge=0)
    total_lien_balance_usd: int = Field(ge=0)
    total_equity_usd: int = Field(ge=0)


class TopZipOpportunityRow(BaseModel):
    state: str = Field(min_length=2, max_length=2)
    zip: str = Field(min_length=5, max_length=5)
    city: str | None = None
    borrower_count: int = Field(ge=0)
    in_the_money_borrowers: int = Field(ge=0)
    mean_opportunity_score: int = Field(ge=0, le=100)
    mean_rate_spread_bps: int


class GeographyAnalyticsResponse(BaseModel):
    state_opportunities: list[StateOpportunityRow]
    state_avm_values: list[StateAvmValueRow]
    top_zips: list[TopZipOpportunityRow]


class RateSpreadBucket(BaseModel):
    spread_bucket_bps: int
    borrower_count: int = Field(ge=0)


class EquitySpreadPoint(BaseModel):
    borrower_id: str
    display_name: str
    segment: str
    state: str = Field(min_length=2, max_length=2)
    equity_pct: int = Field(ge=0, le=100)
    rate_spread_bps: int
    opportunity_score: int = Field(ge=0, le=100)
    # Canonical band from mip.gold.fn_score_band, carried on the
    # equity_spread_points gold row. Optional so pre-S7 fixtures stay valid.
    score_band: Literal["high", "med", "low"] | None = None
    in_the_money: bool | None = None
    # Exact population at this equity/spread coordinate before the response
    # cap is applied. This prevents client-side cluster counts from becoming
    # an understated sample when a dense coordinate crosses the server cap.
    coordinate_total: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _score_band_matches_opportunity_score(self) -> EquitySpreadPoint:
        if self.score_band is None:
            return self
        expected = _expected_score_band(self.opportunity_score)
        if self.score_band != expected:
            raise ValueError("score_band must match the canonical band for opportunity_score")
        return self


# Bound literals below mirror the pinned plot-domain constants in
# backend/services/economics_scatter.py (schemas must not import runtime
# services); tests/unit/test_economics_scatter_api.py asserts the parity.
class EquitySpreadViewport(BaseModel):
    """Zoom window for the points endpoint, clamped to the plot domain."""

    equity_min: int = Field(default=0, ge=0, le=100)
    equity_max: int = Field(default=100, ge=0, le=100)
    spread_min: int = Field(default=-100, ge=-100, le=400)
    spread_max: int = Field(default=400, ge=-100, le=400)

    @model_validator(mode="after")
    def _ordered(self) -> EquitySpreadViewport:
        if self.equity_min > self.equity_max:
            raise ValueError("equity_min must be <= equity_max")
        if self.spread_min > self.spread_max:
            raise ValueError("spread_min must be <= spread_max")
        return self


class EquitySpreadBin(BaseModel):
    """One density cell of the scatter overview (bin lower edges)."""

    equity_bin_pct: int = Field(ge=0, le=100)
    spread_bin_bps: int
    borrower_count: int = Field(ge=0)
    mean_opportunity_score: int = Field(ge=0, le=100)
    in_the_money_borrowers: int = Field(ge=0)


class EquitySpreadOverview(BaseModel):
    """Server-side density bins for the economics scatter overview.

    The overview never ships raw borrower rows; the zoomed points endpoint
    returns real borrowers with an honest showing-N-of-M payload.
    """

    bins: list[EquitySpreadBin]
    total_borrowers: int = Field(ge=0)
    equity_bin_pct: int = Field(gt=0)
    spread_bin_bps: int = Field(gt=0)
    equity_domain_min: int
    equity_domain_max: int
    spread_domain_min: int
    spread_domain_max: int
    source_table: str
    refreshed_at: str | None = None


class EquitySpreadPointsResponse(BaseModel):
    """Real borrower points for a zoomed viewport, capped server-side."""

    points: list[EquitySpreadPoint]
    total_matching: int = Field(ge=0)
    showing: int = Field(ge=0)
    point_cap: int = Field(gt=0)
    truncated: bool
    viewport: EquitySpreadViewport
    source_table: str
    refreshed_at: str | None = None


class TopBorrowerAnalyticsRow(BaseModel):
    borrower_id: str
    display_name: str
    state: str = Field(min_length=2, max_length=2)
    city: str | None = None
    opportunity_score: int = Field(ge=0, le=100)
    rate_spread_bps: int
    equity_pct: int = Field(ge=0, le=100)
    recommended_offer: str
    rank_overall: int = Field(ge=1)


class EconomicsAnalyticsResponse(BaseModel):
    rate_spread_histogram: list[RateSpreadBucket]
    # S7: the scatter overview is server-side density bins over
    # mip.gold.equity_spread_points; raw borrower rows moved to the
    # dedicated capped points endpoint (deliberate wire change, see
    # CHANGELOG).
    equity_spread: EquitySpreadOverview
    top_borrowers: list[TopBorrowerAnalyticsRow]


class SegmentOverviewRow(BaseModel):
    segment_code: SegmentCode
    name: str
    borrower_count: int = Field(ge=0)
    mean_opportunity_score: int = Field(ge=0, le=100)
    delta_vs_prior_label: str
    description: str
    approval_rate: float | None = None
    outreach_rate: float | None = None
    mean_rate_spread_bps: int | None = None
    mean_equity_pct: int | None = Field(default=None, ge=0, le=100)
    in_the_money_borrowers: int = Field(default=0, ge=0)


class SegmentMetricRow(BaseModel):
    segment_code: SegmentCode
    segment_name: str
    value: int = Field(ge=0)


class SegmentByStateRow(BaseModel):
    state: str = Field(min_length=2, max_length=2)
    segment_code: SegmentCode
    segment_name: str
    borrower_count: int = Field(ge=0)


class TopSegmentByStateRow(SegmentByStateRow):
    state_rank: int = Field(ge=1, le=3)


class AnalyticsScope(BaseModel):
    code: str
    label: str
    description: str


class SegmentAnalyticsResponse(BaseModel):
    scope: AnalyticsScope
    overview: list[SegmentOverviewRow]
    counts: list[SegmentMetricRow]
    average_scores: list[SegmentMetricRow]
    by_state: list[SegmentByStateRow]
    top_segments_by_state: list[TopSegmentByStateRow]


class EvidenceDailyRow(BaseModel):
    event_date: str
    signal_type: str
    event_count: int = Field(ge=0)


class EvidenceBySignalRow(BaseModel):
    signal_type: str
    source_product: str
    source_table: str
    source_label: str | None = None
    event_count: int = Field(ge=0)
    mean_confidence: float | None = None
    confidence_source: str
    confidence_label: str | None = None


class SignalEvidenceExample(BaseModel):
    borrower_id: str
    display_name: str
    state: str = Field(min_length=2, max_length=2)
    signal_type: str
    source_product: str
    signal_value: str
    display_text: str
    confidence: float = Field(ge=0, le=1)
    timestamp: str


class SignalAnalyticsResponse(BaseModel):
    evidence_daily: list[EvidenceDailyRow]
    evidence_by_signal: list[EvidenceBySignalRow]
    evidence_examples: list[SignalEvidenceExample]
