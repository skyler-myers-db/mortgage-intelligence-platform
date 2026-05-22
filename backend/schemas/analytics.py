"""Native analytics API schemas.

These contracts back the in-app analytics workspace. They intentionally
mirror the Lakeview dashboard datasets over ``mip.gold`` and
``mip.semantics`` while exposing only app-safe identifiers and aggregate
signals. Raw CLIP, owner names, addresses, and share-level identifiers do
not cross this boundary.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.lead import SegmentCode


class AnalyticsFilters(BaseModel):
    """Validated filter bag shared by the native analytics endpoints."""

    states: list[str] = Field(default_factory=list)
    segment_codes: list[SegmentCode] = Field(default_factory=list)
    segment_mode: Literal["any", "all"] = "any"
    signal_types: list[str] = Field(default_factory=list)
    days: int = Field(default=30, ge=1, le=90)


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


class ScoreBucket(BaseModel):
    score_bucket: int = Field(ge=0, le=100)
    borrower_count: int = Field(ge=0)


class ExecutiveAnalyticsResponse(BaseModel):
    totals: FunnelTotals
    stages: list[FunnelStage]
    score_distribution: list[ScoreBucket]


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
    equity_vs_spread: list[EquitySpreadPoint]
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
    event_count: int = Field(ge=0)
    mean_confidence: float | None = None
    confidence_source: str


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
