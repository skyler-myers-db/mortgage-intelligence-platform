"""Databricks-backed native analytics repository.

The in-app analytics route reads the same Unity Catalog gold and semantic
surfaces as the Lakeview dashboards, but returns typed JSON for the React app.
Every projection is explicit so a future gold-table expansion cannot expose
raw borrower identifiers or share-level fields by accident.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from backend.schemas.analytics import (
    AnalyticsScope,
    EconomicsAnalyticsResponse,
    EquitySpreadPoint,
    EvidenceBySignalRow,
    EvidenceDailyRow,
    ExecutiveAnalyticsResponse,
    FunnelStage,
    FunnelTotals,
    GeographyAnalyticsResponse,
    RateSpreadBucket,
    ScoreBucket,
    SegmentAnalyticsResponse,
    SegmentByStateRow,
    SegmentMetricRow,
    SegmentOverviewRow,
    SignalAnalyticsResponse,
    StateAvmValueRow,
    StateOpportunityRow,
    TopBorrowerAnalyticsRow,
    TopSegmentByStateRow,
    TopZipOpportunityRow,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.resilience import TTLCache


def _date_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


class DatabricksAnalyticsRepository:
    """Typed analytics read model over UC gold/semantic tables."""

    _SEGMENT_SCOPE = AnalyticsScope(
        code="full_population_pre_suppression",
        label="Full population · pre-suppression",
        description=(
            "Segment analytics are computed over the full Cotality-covered borrower "
            "population before marketing eligibility and contactability filters. "
            "Segment Intelligence applies those marketable-lead filters separately."
        ),
    )

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 60.0,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s

    _FUNNEL_TOTALS_SQL = (
        "SELECT "
        "  snapshot_date, "
        "  addressable_borrowers, "
        "  in_the_money_borrowers, "
        "  high_opportunity_borrowers, "
        "  offer_recommended_borrowers, "
        "  approved_borrowers, "
        "  actioned_borrowers "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} "
        "WHERE state = '_ALL' AND segment_code = '_ALL' "
        "ORDER BY snapshot_date DESC, snapshot_at DESC "
        "LIMIT 1"
    )

    _LIVE_WORKFLOW_COUNTS_SQL = (
        "SELECT "
        "  CAST(SUM(CASE WHEN COALESCE(ls.approval_status, 'pending') = 'approved' THEN 1 ELSE 0 END) AS INT) "
        "    AS approved_borrowers, "
        "  CAST(SUM(CASE WHEN COALESCE(ls.outreach_status, 'none') = 'actioned' THEN 1 ELSE 0 END) AS INT) "
        "    AS actioned_borrowers "
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} AS ls "
        "  ON ls.borrower_id = b.borrower_id"
    )

    _SCORE_DISTRIBUTION_SQL = (
        "SELECT "
        "  CAST(FLOOR(opportunity_score / 5) * 5 AS INT) AS score_bucket, "
        "  COUNT(*) AS borrower_count "
        f"FROM {qualify('gold', 'borrower_360')} "
        "GROUP BY CAST(FLOOR(opportunity_score / 5) * 5 AS INT) "
        "ORDER BY score_bucket"
    )

    _STATE_OPPORTUNITY_SQL = (
        "SELECT "
        "  state AS state, "
        "  COUNT(DISTINCT clip) AS borrower_count, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT) AS mean_opportunity_score, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS in_the_money_borrowers "
        f"FROM {qualify('semantics', 'borrower_opportunity_metric_view')} "
        "WHERE state IS NOT NULL AND state <> '' "
        "GROUP BY state "
        "ORDER BY in_the_money_borrowers DESC, mean_opportunity_score DESC"
    )

    _STATE_AVM_VALUE_SQL = (
        "SELECT "
        "  state AS state, "
        "  CAST(SUM(avm_value) AS BIGINT) AS total_avm_value_usd, "
        "  CAST(SUM(current_lien_balance) AS BIGINT) AS total_lien_balance_usd, "
        "  CAST(SUM(equity_estimate) AS BIGINT) AS total_equity_usd "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE state IS NOT NULL AND state <> '' "
        "GROUP BY state "
        "ORDER BY total_avm_value_usd DESC"
    )

    _TOP_ZIPS_ITM_SQL = (
        "WITH zip_base AS ( "
        "  SELECT state, zip, city, in_the_money, opportunity_score, rate_spread_bps "
        f"  FROM {qualify('gold', 'borrower_360')} "
        "  WHERE zip IS NOT NULL AND LENGTH(zip) = 5 "
        ") "
        "SELECT "
        "  state AS state, "
        "  zip AS zip, "
        "  MIN(city) AS city, "
        "  COUNT(*) AS borrower_count, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS in_the_money_borrowers, "
        "  CAST(ROUND(AVG(CASE WHEN in_the_money THEN opportunity_score END)) AS INT) AS mean_opportunity_score, "
        "  CAST(ROUND(AVG(CASE WHEN in_the_money THEN rate_spread_bps END)) AS INT) AS mean_rate_spread_bps "
        "FROM zip_base "
        "GROUP BY state, zip "
        "HAVING SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) > 0 "
        "ORDER BY in_the_money_borrowers DESC "
        "LIMIT 20"
    )

    _RATE_SPREAD_HIST_SQL = (
        "SELECT "
        "  CAST(FLOOR(rate_spread_bps / 25) * 25 AS INT) AS spread_bucket_bps, "
        "  COUNT(*) AS borrower_count "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE rate_spread_bps BETWEEN -100 AND 400 "
        "GROUP BY CAST(FLOOR(rate_spread_bps / 25) * 25 AS INT) "
        "ORDER BY spread_bucket_bps"
    )

    _EQUITY_VS_SPREAD_SQL = (
        "SELECT "
        "  borrower_id AS borrower_id, "
        "  display_name AS display_name, "
        "  CASE "
        "    WHEN SIZE(segment_codes) = 0 THEN 'None / Unsegmented' "
        "    WHEN segment_codes[0] = 'equity' THEN 'Home Equity Candidate' "
        "    WHEN segment_codes[0] = 'itm' THEN 'In the Money' "
        "    WHEN segment_codes[0] = 'investor' THEN 'Investor / Multi-Property' "
        "    WHEN segment_codes[0] = 'listed' THEN 'Listed for Sale' "
        "    WHEN segment_codes[0] = 'permit' THEN 'Permit Activity' "
        "    WHEN segment_codes[0] = 'retention' THEN 'Retention Risk' "
        "    ELSE 'None / Unsegmented' "
        "  END AS segment, "
        "  state AS state, "
        "  equity_pct AS equity_pct, "
        "  rate_spread_bps AS rate_spread_bps, "
        "  opportunity_score AS opportunity_score "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE rate_spread_bps BETWEEN -100 AND 400 "
        "  AND equity_pct BETWEEN 0 AND 100 "
        "LIMIT 5000"
    )

    _TOP_BORROWERS_SQL = (
        "WITH ranked AS ( "
        "  SELECT "
        "    borrower_id AS borrower_id, "
        "    display_name AS display_name, "
        "    state AS state, "
        "    city AS city, "
        "    opportunity_score AS opportunity_score, "
        "    rate_spread_bps AS rate_spread_bps, "
        "    equity_pct AS equity_pct, "
        "    recommended_offer AS recommended_offer, "
        "    ROW_NUMBER() OVER (ORDER BY opportunity_score DESC, clip) AS rank_overall "
        f"  FROM {qualify('gold', 'borrower_360')} "
        "  WHERE opportunity_score >= 50 "
        ") "
        "SELECT borrower_id, display_name, state, city, opportunity_score, rate_spread_bps, "
        "       equity_pct, recommended_offer, rank_overall "
        "FROM ranked "
        "ORDER BY rank_overall "
        "LIMIT 10"
    )

    _SEGMENT_OVERVIEW_SQL = (
        "SELECT "
        "  sp.segment_code AS segment_code, "
        "  sp.name AS name, "
        "  sp.count AS borrower_count, "
        "  sp.avg_score AS mean_opportunity_score, "
        "  sp.delta_vs_prior AS delta_vs_prior_label, "
        "  sp.description AS description, "
        "  sp.approval_rate AS approval_rate, "
        "  sp.outreach_rate AS outreach_rate, "
        "  agg.mean_rate_spread_bps AS mean_rate_spread_bps, "
        "  agg.mean_equity_pct AS mean_equity_pct, "
        "  agg.in_the_money_borrowers AS in_the_money_borrowers "
        f"FROM {qualify('semantics', 'segment_performance_metric_view')} AS sp "
        "LEFT JOIN ( "
        "  SELECT "
        "    segment_code AS segment_code, "
        "    CAST(ROUND(AVG(rate_spread_bps)) AS INT) AS mean_rate_spread_bps, "
        "    CAST(ROUND(AVG(equity_pct)) AS INT) AS mean_equity_pct, "
        "    SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS in_the_money_borrowers "
        f"  FROM {qualify('semantics', 'borrower_opportunity_metric_view')} "
        "  LATERAL VIEW EXPLODE(segment_codes) seg AS segment_code "
        "  GROUP BY segment_code "
        ") AS agg ON sp.segment_code = agg.segment_code "
        "WHERE sp.state = '_ALL' "
        "ORDER BY sp.count DESC"
    )

    _SEGMENT_BY_STATE_SQL = (
        "SELECT "
        "  state AS state, "
        "  segment_code AS segment_code, "
        "  name AS segment_name, "
        "  count AS borrower_count "
        f"FROM {qualify('semantics', 'segment_performance_metric_view')} "
        "WHERE state <> '_ALL' "
        "ORDER BY state, count DESC"
    )

    _TOP_SEGMENTS_BY_STATE_SQL = (
        "WITH ranked AS ( "
        "  SELECT "
        "    state, "
        "    segment_code, "
        "    name AS segment_name, "
        "    count AS borrower_count, "
        "    ROW_NUMBER() OVER (PARTITION BY state ORDER BY count DESC) AS state_rank "
        f"  FROM {qualify('semantics', 'segment_performance_metric_view')} "
        "  WHERE state <> '_ALL' "
        ") "
        "SELECT state, segment_code, segment_name, borrower_count, state_rank "
        "FROM ranked "
        "WHERE state_rank <= 3 "
        "ORDER BY state, state_rank"
    )

    _EVIDENCE_DAILY_SQL = (
        "SELECT "
        "  TO_DATE(`timestamp`) AS event_date, "
        "  signal_type AS signal_type, "
        "  COUNT(*) AS event_count "
        f"FROM {qualify('gold', 'evidence_events')} "
        "WHERE TO_DATE(`timestamp`) >= CURRENT_DATE() - INTERVAL 30 DAYS "
        "GROUP BY TO_DATE(`timestamp`), signal_type "
        "ORDER BY event_date, signal_type"
    )

    _EVIDENCE_BY_SIGNAL_SQL = (
        "SELECT "
        "  signal_type AS signal_type, "
        "  source_product AS source_product, "
        "  COUNT(*) AS event_count, "
        "  CAST(ROUND(AVG(confidence), 3) AS DOUBLE) AS mean_confidence "
        f"FROM {qualify('gold', 'evidence_events')} "
        "GROUP BY signal_type, source_product "
        "ORDER BY event_count DESC"
    )

    def _cached(self, key: str, builder: Callable[[], Any]) -> Any:
        return self._cache.get_or_set(key, builder, ttl_s=self._cache_ttl_s)

    def executive(self) -> ExecutiveAnalyticsResponse:
        def build() -> ExecutiveAnalyticsResponse:
            totals_row = (self._client.execute(self._FUNNEL_TOTALS_SQL) or [{}])[0]
            workflow_row = (self._client.execute(self._LIVE_WORKFLOW_COUNTS_SQL) or [{}])[0]
            totals = FunnelTotals(
                snapshot_date=_date_text(totals_row.get("snapshot_date")),
                addressable_borrowers=_int(totals_row.get("addressable_borrowers")),
                in_the_money_borrowers=_int(totals_row.get("in_the_money_borrowers")),
                high_opportunity_borrowers=_int(totals_row.get("high_opportunity_borrowers")),
                offer_recommended_borrowers=_int(totals_row.get("offer_recommended_borrowers")),
                approved_borrowers=_int(
                    workflow_row.get("approved_borrowers"),
                    _int(totals_row.get("approved_borrowers")),
                ),
                actioned_borrowers=_int(
                    workflow_row.get("actioned_borrowers"),
                    _int(totals_row.get("actioned_borrowers")),
                ),
            )
            stages = [
                FunnelStage(stage="Addressable", stage_order=1, borrower_count=totals.addressable_borrowers),
                FunnelStage(stage="In the Money", stage_order=2, borrower_count=totals.in_the_money_borrowers),
                FunnelStage(stage="High Opportunity", stage_order=3, borrower_count=totals.high_opportunity_borrowers),
                FunnelStage(stage="Offer Recommended", stage_order=4, borrower_count=totals.offer_recommended_borrowers),
                FunnelStage(stage="Approved", stage_order=5, borrower_count=totals.approved_borrowers),
                FunnelStage(stage="Actioned", stage_order=6, borrower_count=totals.actioned_borrowers),
            ]
            score_distribution = [
                ScoreBucket(
                    score_bucket=_int(row.get("score_bucket")),
                    borrower_count=_int(row.get("borrower_count")),
                )
                for row in self._client.execute(self._SCORE_DISTRIBUTION_SQL)
            ]
            return ExecutiveAnalyticsResponse(
                totals=totals,
                stages=stages,
                score_distribution=score_distribution,
            )

        return self._cached("analytics.executive", build)

    def geography(self) -> GeographyAnalyticsResponse:
        def build() -> GeographyAnalyticsResponse:
            return GeographyAnalyticsResponse(
                state_opportunities=[
                    StateOpportunityRow(
                        state=str(row.get("state") or ""),
                        borrower_count=_int(row.get("borrower_count")),
                        mean_opportunity_score=_int(row.get("mean_opportunity_score")),
                        in_the_money_borrowers=_int(row.get("in_the_money_borrowers")),
                    )
                    for row in self._client.execute(self._STATE_OPPORTUNITY_SQL)
                ],
                state_avm_values=[
                    StateAvmValueRow(
                        state=str(row.get("state") or ""),
                        total_avm_value_usd=_int(row.get("total_avm_value_usd")),
                        total_lien_balance_usd=_int(row.get("total_lien_balance_usd")),
                        total_equity_usd=_int(row.get("total_equity_usd")),
                    )
                    for row in self._client.execute(self._STATE_AVM_VALUE_SQL)
                ],
                top_zips=[
                    TopZipOpportunityRow(
                        state=str(row.get("state") or ""),
                        zip=str(row.get("zip") or ""),
                        city=row.get("city"),
                        borrower_count=_int(row.get("borrower_count")),
                        in_the_money_borrowers=_int(row.get("in_the_money_borrowers")),
                        mean_opportunity_score=_int(row.get("mean_opportunity_score")),
                        mean_rate_spread_bps=_int(row.get("mean_rate_spread_bps")),
                    )
                    for row in self._client.execute(self._TOP_ZIPS_ITM_SQL)
                ],
            )

        return self._cached("analytics.geography", build)

    def economics(self) -> EconomicsAnalyticsResponse:
        def build() -> EconomicsAnalyticsResponse:
            return EconomicsAnalyticsResponse(
                rate_spread_histogram=[
                    RateSpreadBucket(
                        spread_bucket_bps=_int(row.get("spread_bucket_bps")),
                        borrower_count=_int(row.get("borrower_count")),
                    )
                    for row in self._client.execute(self._RATE_SPREAD_HIST_SQL)
                ],
                equity_vs_spread=[
                    EquitySpreadPoint(
                        borrower_id=str(row.get("borrower_id") or ""),
                        display_name=str(row.get("display_name") or "Borrower"),
                        segment=str(row.get("segment") or "None / Unsegmented"),
                        state=str(row.get("state") or ""),
                        equity_pct=_int(row.get("equity_pct")),
                        rate_spread_bps=_int(row.get("rate_spread_bps")),
                        opportunity_score=_int(row.get("opportunity_score")),
                    )
                    for row in self._client.execute(self._EQUITY_VS_SPREAD_SQL)
                ],
                top_borrowers=[
                    TopBorrowerAnalyticsRow(
                        borrower_id=str(row.get("borrower_id") or ""),
                        display_name=str(row.get("display_name") or "Borrower"),
                        state=str(row.get("state") or ""),
                        city=row.get("city"),
                        opportunity_score=_int(row.get("opportunity_score")),
                        rate_spread_bps=_int(row.get("rate_spread_bps")),
                        equity_pct=_int(row.get("equity_pct")),
                        recommended_offer=str(row.get("recommended_offer") or "Nurture"),
                        rank_overall=max(1, _int(row.get("rank_overall"), 1)),
                    )
                    for row in self._client.execute(self._TOP_BORROWERS_SQL)
                ],
            )

        return self._cached("analytics.economics", build)

    def segments(self) -> SegmentAnalyticsResponse:
        def build() -> SegmentAnalyticsResponse:
            overview = [
                SegmentOverviewRow(
                    segment_code=row["segment_code"],
                    name=str(row.get("name") or row.get("segment_code") or ""),
                    borrower_count=_int(row.get("borrower_count")),
                    mean_opportunity_score=_int(row.get("mean_opportunity_score")),
                    delta_vs_prior_label=str(row.get("delta_vs_prior_label") or "+0%"),
                    description=str(row.get("description") or ""),
                    approval_rate=_float_or_none(row.get("approval_rate")),
                    outreach_rate=_float_or_none(row.get("outreach_rate")),
                    mean_rate_spread_bps=(
                        None if row.get("mean_rate_spread_bps") is None
                        else _int(row.get("mean_rate_spread_bps"))
                    ),
                    mean_equity_pct=(
                        None if row.get("mean_equity_pct") is None
                        else _int(row.get("mean_equity_pct"))
                    ),
                    in_the_money_borrowers=_int(row.get("in_the_money_borrowers")),
                )
                for row in self._client.execute(self._SEGMENT_OVERVIEW_SQL)
            ]
            return SegmentAnalyticsResponse(
                scope=self._SEGMENT_SCOPE,
                overview=overview,
                counts=[
                    SegmentMetricRow(
                        segment_code=row.segment_code,
                        segment_name=row.name,
                        value=row.borrower_count,
                    )
                    for row in overview
                ],
                average_scores=[
                    SegmentMetricRow(
                        segment_code=row.segment_code,
                        segment_name=row.name,
                        value=row.mean_opportunity_score,
                    )
                    for row in sorted(overview, key=lambda r: r.mean_opportunity_score, reverse=True)
                ],
                by_state=[
                    SegmentByStateRow(
                        state=str(row.get("state") or ""),
                        segment_code=row["segment_code"],
                        segment_name=str(row.get("segment_name") or row.get("segment_code") or ""),
                        borrower_count=_int(row.get("borrower_count")),
                    )
                    for row in self._client.execute(self._SEGMENT_BY_STATE_SQL)
                ],
                top_segments_by_state=[
                    TopSegmentByStateRow(
                        state=str(row.get("state") or ""),
                        segment_code=row["segment_code"],
                        segment_name=str(row.get("segment_name") or row.get("segment_code") or ""),
                        borrower_count=_int(row.get("borrower_count")),
                        state_rank=_int(row.get("state_rank"), 1),
                    )
                    for row in self._client.execute(self._TOP_SEGMENTS_BY_STATE_SQL)
                ],
            )

        return self._cached("analytics.segments", build)

    def signals(self) -> SignalAnalyticsResponse:
        def build() -> SignalAnalyticsResponse:
            return SignalAnalyticsResponse(
                evidence_daily=[
                    EvidenceDailyRow(
                        event_date=_date_text(row.get("event_date")) or "",
                        signal_type=str(row.get("signal_type") or ""),
                        event_count=_int(row.get("event_count")),
                    )
                    for row in self._client.execute(self._EVIDENCE_DAILY_SQL)
                ],
                evidence_by_signal=[
                    EvidenceBySignalRow(
                        signal_type=str(row.get("signal_type") or ""),
                        source_product=str(row.get("source_product") or ""),
                        event_count=_int(row.get("event_count")),
                        mean_confidence=_float_or_none(row.get("mean_confidence")),
                    )
                    for row in self._client.execute(self._EVIDENCE_BY_SIGNAL_SQL)
                ],
            )

        return self._cached("analytics.signals", build)
