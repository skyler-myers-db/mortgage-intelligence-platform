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
    AnalyticsFilters,
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
    SignalEvidenceExample,
    StateAvmValueRow,
    StateOpportunityRow,
    TopBorrowerAnalyticsRow,
    TopSegmentByStateRow,
    TopZipOpportunityRow,
)
from backend.schemas.funnel import FunnelPopulation
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.resilience import TTLCache
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD, source_display_label


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


def _filters(filters: AnalyticsFilters | None) -> AnalyticsFilters:
    return filters or AnalyticsFilters()


def _filter_key(filters: AnalyticsFilters) -> str:
    states = ",".join(filters.states)
    segments = ",".join(filters.segment_codes)
    signals = ",".join(filters.signal_types)
    return (
        f"states={states or '*'}|segments={segments or '*'}|"
        f"mode={filters.segment_mode}|relationship={filters.lender_relationship or '*'}|"
        f"target_lender={filters.target_lender_ref or '*'}|"
        f"signals={signals or '*'}|days={filters.days}"
    )


def _confidence_source(signal_type: str) -> str:
    if signal_type == "equity":
        return (
            "AVG(mip.gold.evidence_events.confidence); equity rows inherit AVM "
            "confidence when present, clipped to 0..1."
        )
    return (
        "AVG(mip.gold.evidence_events.confidence); this signal uses the governed "
        "deterministic confidence assigned in gold_evidence_events.sql."
    )


def _confidence_label(signal_type: str) -> str:
    if signal_type == "equity":
        return "Mean evidence confidence, using AVM confidence when available."
    return "Mean governed evidence confidence."


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

    @staticmethod
    def _borrower_predicates(
        filters: AnalyticsFilters,
        *,
        alias: str = "b",
        params: dict[str, object] | None = None,
    ) -> tuple[list[str], dict[str, object]]:
        out_params = dict(params or {})
        predicates: list[str] = []
        if filters.states:
            state_keys: list[str] = []
            for idx, state in enumerate(filters.states):
                key = f"state_{idx}"
                out_params[key] = state
                state_keys.append(f":{key}")
            predicates.append(f"{alias}.state IN (" + ", ".join(state_keys) + ")")
        if filters.segment_codes:
            segment_predicates: list[str] = []
            for idx, code in enumerate(filters.segment_codes):
                key = f"segment_{idx}"
                out_params[key] = code
                segment_predicates.append(f"array_contains({alias}.segment_codes, :{key})")
            joiner = " AND " if filters.segment_mode == "all" else " OR "
            predicates.append("(" + joiner.join(segment_predicates) + ")")
        if filters.lender_relationship == "Current customer":
            predicates.append(f"{alias}.is_current_customer = TRUE")
        elif filters.lender_relationship == "Former customer":
            predicates.append(f"{alias}.is_former_customer = TRUE")
        elif filters.lender_relationship == "Competitor customer":
            predicates.append(f"{alias}.is_competitor_lien = TRUE")
        if filters.target_lender_ref:
            out_params["target_lender_ref"] = filters.target_lender_ref
            predicates.append(f"{alias}.current_lender_ref = :target_lender_ref")
        return predicates, out_params

    @classmethod
    def _where(
        cls,
        filters: AnalyticsFilters,
        *,
        alias: str = "b",
        extra: list[str] | None = None,
        params: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, object]]:
        predicates, out_params = cls._borrower_predicates(filters, alias=alias, params=params)
        predicates = [*(extra or []), *predicates]
        if not predicates:
            return "", out_params
        return "WHERE " + " AND ".join(predicates), out_params

    # S6: the approval funnel's population stages come from the S1 headline
    # metric view; ``is_high_opportunity`` carries the canonical
    # fn_high_opportunity predicate, so no threshold literal appears here.
    _FUNNEL_POPULATION_SQL = (
        "SELECT "
        "  CAST(COUNT(*) AS INT) AS population, "
        "  CAST(COALESCE(SUM(CASE WHEN is_high_opportunity THEN 1 ELSE 0 END), 0) AS INT) "
        "    AS high_opportunity "
        f"FROM {qualify('semantics', 'portfolio_headline_metric_view')}"
    )

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

    _LIVE_FUNNEL_SQL = (
        "SELECT "
        "  CAST(MAX(DATE(b.refreshed_at)) AS STRING) AS snapshot_date, "
        "  CAST(COUNT(*) AS INT) AS addressable_borrowers, "
        "  CAST(SUM(CASE WHEN b.in_the_money THEN 1 ELSE 0 END) AS INT) AS in_the_money_borrowers, "
        "  CAST(SUM(CASE WHEN b.opportunity_score "
        f"                >= {HIGH_OPPORTUNITY_THRESHOLD} THEN 1 ELSE 0 END) AS INT) AS high_opportunity_borrowers, "
        "  CAST(SUM(CASE WHEN LOWER(b.recommended_offer_code) <> 'nurture' THEN 1 ELSE 0 END) AS INT) "
        "    AS offer_recommended_borrowers, "
        "  CAST(SUM(CASE WHEN COALESCE(ls.approval_status, 'pending') = 'approved' THEN 1 ELSE 0 END) AS INT) "
        "    AS approved_borrowers, "
        "  CAST(SUM(CASE WHEN COALESCE(ls.outreach_status, 'none') = 'actioned' THEN 1 ELSE 0 END) AS INT) "
        "    AS actioned_borrowers "
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} AS ls "
        "  ON ls.borrower_id = b.borrower_id "
        "{where}"
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
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        "{where} "
        "GROUP BY CAST(FLOOR(opportunity_score / 5) * 5 AS INT) "
        "ORDER BY score_bucket"
    )

    _STATE_OPPORTUNITY_SQL = (
        "SELECT "
        "  state AS state, "
        "  COUNT(DISTINCT clip) AS borrower_count, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT) AS mean_opportunity_score, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS in_the_money_borrowers "
        f"FROM {qualify('semantics', 'borrower_opportunity_metric_view')} AS v "
        "{where} "
        "GROUP BY state "
        "ORDER BY in_the_money_borrowers DESC, mean_opportunity_score DESC"
    )

    _STATE_AVM_VALUE_SQL = (
        "SELECT "
        "  state AS state, "
        "  CAST(SUM(avm_value) AS BIGINT) AS total_avm_value_usd, "
        "  CAST(SUM(current_lien_balance) AS BIGINT) AS total_lien_balance_usd, "
        "  CAST(SUM(equity_estimate) AS BIGINT) AS total_equity_usd "
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        "{where} "
        "GROUP BY state "
        "ORDER BY total_avm_value_usd DESC"
    )

    _TOP_ZIPS_ITM_SQL = (
        "WITH zip_base AS ( "
        "  SELECT state, zip, city, in_the_money, opportunity_score, rate_spread_bps "
        f"  FROM {qualify('gold', 'borrower_360')} AS b "
        "  {where} "
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
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        "{where} "
        "GROUP BY CAST(FLOOR(rate_spread_bps / 25) * 25 AS INT) "
        "ORDER BY spread_bucket_bps"
    )

    _EQUITY_VS_SPREAD_SQL = (
        "SELECT "
        "  b.borrower_id AS borrower_id, "
        "  b.display_name AS display_name, "
        "  CASE "
        "    WHEN SIZE(b.segment_codes) = 0 THEN 'None / Unsegmented' "
        "    WHEN b.segment_codes[0] = 'equity' THEN 'Home Equity Candidate' "
        "    WHEN b.segment_codes[0] = 'itm' THEN 'Prime Refi Candidates' "
        "    WHEN b.segment_codes[0] = 'investor' THEN 'Investor / Multi-Property' "
        "    WHEN b.segment_codes[0] = 'listed' THEN 'Listed for Sale' "
        "    WHEN b.segment_codes[0] = 'permit' THEN 'HELOC Intent' "
        "    WHEN b.segment_codes[0] = 'retention' THEN 'Retention Risk' "
        "    ELSE 'None / Unsegmented' "
        "  END AS segment, "
        "  b.state AS state, "
        "  b.equity_pct AS equity_pct, "
        "  b.rate_spread_bps AS rate_spread_bps, "
        "  b.opportunity_score AS opportunity_score "
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        "{where} "
        "LIMIT 5000"
    )

    _TOP_BORROWERS_SQL = (
        "WITH ranked AS ( "
        "  SELECT "
        "    b.borrower_id AS borrower_id, "
        "    b.display_name AS display_name, "
        "    b.state AS state, "
        "    b.city AS city, "
        "    b.opportunity_score AS opportunity_score, "
        "    b.rate_spread_bps AS rate_spread_bps, "
        "    b.equity_pct AS equity_pct, "
        "    b.recommended_offer AS recommended_offer, "
        "    ROW_NUMBER() OVER (ORDER BY b.opportunity_score DESC, b.clip) AS rank_overall "
        f"  FROM {qualify('gold', 'borrower_360')} AS b "
        "  {where} "
        ") "
        "SELECT borrower_id, display_name, state, city, opportunity_score, rate_spread_bps, "
        "       equity_pct, recommended_offer, rank_overall "
        "FROM ranked "
        "ORDER BY rank_overall "
        "LIMIT 10"
    )

    _SEGMENT_OVERVIEW_SQL = (
        "WITH segment_dim AS ( "
        "  SELECT * FROM VALUES "
        "    ('itm', 'Prime Refi Candidates', 'Borrowers passing the refinance economics screen.'), "
        "    ('equity', 'Home Equity Candidate', 'Borrowers with clean equity capacity.'), "
        "    ('investor', 'Investor / Multi-Property', 'Borrowers linked to investor or multi-property signals.'), "
        "    ('retention', 'Retention Risk', 'Current-customer retention opportunities.'), "
        "    ('listed', 'Listed for Sale', 'Current active or under-contract Cotality MLS listing.'), "
        "    ('permit', 'HELOC Intent', 'High Cotality HELOC propensity; filed permit source remains pending.') "
        "  AS segment_dim(segment_code, name, description) "
        "), filtered AS ( "
        "  SELECT b.*, COALESCE(ls.approval_status, 'pending') AS lifecycle_approval_status, "
        "         COALESCE(ls.outreach_status, 'none') AS lifecycle_outreach_status "
        f"  FROM {qualify('gold', 'borrower_360')} AS b "
        f"  LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} AS ls "
        "    ON ls.borrower_id = b.borrower_id "
        "  {where} "
        "), exploded AS ( "
        "  SELECT seg.segment_code, f.opportunity_score, f.rate_spread_bps, f.equity_pct, "
        "         f.in_the_money, f.lifecycle_approval_status AS approval_status, "
        "         f.lifecycle_outreach_status AS outreach_status "
        "  FROM filtered AS f "
        "  LATERAL VIEW EXPLODE(f.segment_codes) seg AS segment_code "
        "), agg AS ( "
        "  SELECT "
        "    segment_code, "
        "    COUNT(*) AS borrower_count, "
        "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS mean_opportunity_score, "
        "    CAST(ROUND(AVG(rate_spread_bps)) AS INT) AS mean_rate_spread_bps, "
        "    CAST(ROUND(AVG(equity_pct)) AS INT) AS mean_equity_pct, "
        "    SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS in_the_money_borrowers, "
        "    CAST(ROUND(100.0 * SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS DOUBLE) AS approval_rate, "
        "    CAST(ROUND(100.0 * SUM(CASE WHEN outreach_status = 'actioned' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS DOUBLE) AS outreach_rate "
        "  FROM exploded "
        "  GROUP BY segment_code "
        ") "
        "SELECT "
        "  d.segment_code AS segment_code, "
        "  d.name AS name, "
        "  COALESCE(a.borrower_count, 0) AS borrower_count, "
        "  COALESCE(a.mean_opportunity_score, 0) AS mean_opportunity_score, "
        "  '+0%' AS delta_vs_prior_label, "
        "  d.description AS description, "
        "  a.approval_rate AS approval_rate, "
        "  a.outreach_rate AS outreach_rate, "
        "  a.mean_rate_spread_bps AS mean_rate_spread_bps, "
        "  a.mean_equity_pct AS mean_equity_pct, "
        "  COALESCE(a.in_the_money_borrowers, 0) AS in_the_money_borrowers "
        "FROM segment_dim AS d "
        "LEFT JOIN agg AS a ON d.segment_code = a.segment_code "
        "ORDER BY borrower_count DESC, d.name"
    )

    _SEGMENT_BY_STATE_SQL = (
        "WITH filtered AS ( "
        f"  SELECT b.state, b.segment_codes FROM {qualify('gold', 'borrower_360')} AS b "
        "  {where} "
        "), exploded AS ( "
        "  SELECT state, seg.segment_code "
        "  FROM filtered "
        "  LATERAL VIEW EXPLODE(segment_codes) seg AS segment_code "
        ") "
        "SELECT "
        "  state AS state, "
        "  segment_code AS segment_code, "
        "  CASE "
        "    WHEN segment_code = 'itm' THEN 'Prime Refi Candidates' "
        "    WHEN segment_code = 'equity' THEN 'Home Equity Candidate' "
        "    WHEN segment_code = 'investor' THEN 'Investor / Multi-Property' "
        "    WHEN segment_code = 'retention' THEN 'Retention Risk' "
        "    WHEN segment_code = 'listed' THEN 'Listed for Sale' "
        "    WHEN segment_code = 'permit' THEN 'HELOC Intent' "
        "    ELSE segment_code "
        "  END AS segment_name, "
        "  COUNT(*) AS borrower_count "
        "FROM exploded "
        "WHERE state IS NOT NULL AND state <> '' "
        "GROUP BY state, segment_code "
        "ORDER BY state, borrower_count DESC"
    )

    _TOP_SEGMENTS_BY_STATE_SQL = (
        "WITH filtered AS ( "
        f"  SELECT b.state, b.segment_codes FROM {qualify('gold', 'borrower_360')} AS b "
        "  {where} "
        "), by_state AS ( "
        "  SELECT "
        "    state, "
        "    seg.segment_code AS segment_code, "
        "    COUNT(*) AS borrower_count "
        "  FROM filtered "
        "  LATERAL VIEW EXPLODE(segment_codes) seg AS segment_code "
        "  WHERE state IS NOT NULL AND state <> '' "
        "  GROUP BY state, seg.segment_code "
        "), ranked AS ( "
        "  SELECT "
        "    state, "
        "    segment_code, "
        "    CASE "
        "      WHEN segment_code = 'itm' THEN 'Prime Refi Candidates' "
        "      WHEN segment_code = 'equity' THEN 'Home Equity Candidate' "
        "      WHEN segment_code = 'investor' THEN 'Investor / Multi-Property' "
        "      WHEN segment_code = 'retention' THEN 'Retention Risk' "
        "      WHEN segment_code = 'listed' THEN 'Listed for Sale' "
        "      WHEN segment_code = 'permit' THEN 'HELOC Intent' "
        "      ELSE segment_code "
        "    END AS segment_name, "
        "    borrower_count, "
        "    ROW_NUMBER() OVER (PARTITION BY state ORDER BY borrower_count DESC, segment_code) AS state_rank "
        "  FROM by_state "
        ") "
        "SELECT state, segment_code, segment_name, borrower_count, state_rank "
        "FROM ranked "
        "WHERE state_rank <= 3 "
        "ORDER BY state, state_rank"
    )

    _EVIDENCE_DAILY_SQL = (
        "SELECT "
        "  TO_DATE(e.`timestamp`) AS event_date, "
        "  e.signal_type AS signal_type, "
        "  COUNT(*) AS event_count "
        f"FROM {qualify('gold', 'evidence_events')} AS e "
        f"JOIN {qualify('gold', 'borrower_360')} AS b ON b.clip = e.clip "
        "{where} "
        "GROUP BY TO_DATE(e.`timestamp`), e.signal_type "
        "ORDER BY event_date, e.signal_type"
    )

    _EVIDENCE_BY_SIGNAL_SQL = (
        "SELECT "
        "  e.signal_type AS signal_type, "
        "  e.source_product AS source_product, "
        "  MIN(e.source_table) AS source_table, "
        "  COUNT(*) AS event_count, "
        "  CAST(ROUND(AVG(e.confidence), 3) AS DOUBLE) AS mean_confidence "
        f"FROM {qualify('gold', 'evidence_events')} AS e "
        f"JOIN {qualify('gold', 'borrower_360')} AS b ON b.clip = e.clip "
        "{where} "
        "GROUP BY e.signal_type, e.source_product "
        "ORDER BY event_count DESC"
    )

    _EVIDENCE_EXAMPLES_SQL = (
        "SELECT "
        "  b.borrower_id AS borrower_id, "
        "  b.display_name AS display_name, "
        "  b.state AS state, "
        "  e.signal_type AS signal_type, "
        "  e.source_product AS source_product, "
        "  e.signal_value AS signal_value, "
        "  e.display_text AS display_text, "
        "  CAST(e.confidence AS DOUBLE) AS confidence, "
        "  e.`timestamp` AS timestamp "
        f"FROM {qualify('gold', 'evidence_events')} AS e "
        f"JOIN {qualify('gold', 'borrower_360')} AS b ON b.clip = e.clip "
        "{where} "
        "ORDER BY e.confidence DESC, e.`timestamp` DESC, b.borrower_id "
        "LIMIT 25"
    )

    def _cached(self, key: str, builder: Callable[[], Any]) -> Any:
        return self._cache.get_or_set(key, builder, ttl_s=self._cache_ttl_s)

    def _execute_template(
        self,
        template: str,
        filters: AnalyticsFilters,
        *,
        alias: str = "b",
        extra: list[str] | None = None,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        where, bound = self._where(filters, alias=alias, extra=extra, params=params)
        statement = template.format(where=where)
        return self._client.execute(statement, bound or None)

    def _signal_where(self, filters: AnalyticsFilters) -> tuple[str, dict[str, object]]:
        extra = ["TO_DATE(e.`timestamp`) >= DATE_SUB(CURRENT_DATE(), :days)"]
        params: dict[str, object] = {"days": filters.days}
        if filters.signal_types:
            signal_keys: list[str] = []
            for idx, signal_type in enumerate(filters.signal_types):
                key = f"signal_type_{idx}"
                params[key] = signal_type
                signal_keys.append(f":{key}")
            extra.append("e.signal_type IN (" + ", ".join(signal_keys) + ")")
        return self._where(filters, alias="b", extra=extra, params=params)

    def _execute_signal_template(
        self,
        template: str,
        filters: AnalyticsFilters,
    ) -> list[dict[str, Any]]:
        where, bound = self._signal_where(filters)
        statement = template.format(where=where)
        return self._client.execute(statement, bound)

    def executive(self, filters: AnalyticsFilters | None = None) -> ExecutiveAnalyticsResponse:
        analytics_filters = _filters(filters)
        def build() -> ExecutiveAnalyticsResponse:
            totals_row = (
                self._execute_template(self._LIVE_FUNNEL_SQL, analytics_filters) or [{}]
            )[0]
            totals = FunnelTotals(
                snapshot_date=_date_text(totals_row.get("snapshot_date")),
                addressable_borrowers=_int(totals_row.get("addressable_borrowers")),
                in_the_money_borrowers=_int(totals_row.get("in_the_money_borrowers")),
                high_opportunity_borrowers=_int(totals_row.get("high_opportunity_borrowers")),
                offer_recommended_borrowers=_int(totals_row.get("offer_recommended_borrowers")),
                approved_borrowers=_int(totals_row.get("approved_borrowers")),
                actioned_borrowers=_int(totals_row.get("actioned_borrowers")),
            )
            stages = [
                FunnelStage(stage="Addressable", stage_order=1, borrower_count=totals.addressable_borrowers),
                FunnelStage(stage="Refi Economics", stage_order=2, borrower_count=totals.in_the_money_borrowers),
                FunnelStage(stage="Opportunity Score 75+", stage_order=3, borrower_count=totals.high_opportunity_borrowers),
                FunnelStage(stage="Primary Offer Selected", stage_order=4, borrower_count=totals.offer_recommended_borrowers),
                FunnelStage(stage="Approved", stage_order=5, borrower_count=totals.approved_borrowers),
                FunnelStage(stage="Actioned", stage_order=6, borrower_count=totals.actioned_borrowers),
            ]
            score_distribution = [
                ScoreBucket(
                    score_bucket=_int(row.get("score_bucket")),
                    borrower_count=_int(row.get("borrower_count")),
                )
                for row in self._execute_template(
                    self._SCORE_DISTRIBUTION_SQL,
                    analytics_filters,
                )
            ]
            return ExecutiveAnalyticsResponse(
                totals=totals,
                stages=stages,
                score_distribution=score_distribution,
            )

        return self._cached(f"analytics.executive:{_filter_key(analytics_filters)}", build)

    def funnel_population(self) -> FunnelPopulation:
        def build() -> FunnelPopulation:
            row = (self._client.execute(self._FUNNEL_POPULATION_SQL) or [{}])[0]
            return FunnelPopulation(
                population=_int(row.get("population")),
                high_opportunity=_int(row.get("high_opportunity")),
                source=qualify("semantics", "portfolio_headline_metric_view"),
            )

        return self._cached("analytics.funnel_population", build)

    def geography(self, filters: AnalyticsFilters | None = None) -> GeographyAnalyticsResponse:
        analytics_filters = _filters(filters)

        def build() -> GeographyAnalyticsResponse:
            return GeographyAnalyticsResponse(
                state_opportunities=[
                    StateOpportunityRow(
                        state=str(row.get("state") or ""),
                        borrower_count=_int(row.get("borrower_count")),
                        mean_opportunity_score=_int(row.get("mean_opportunity_score")),
                        in_the_money_borrowers=_int(row.get("in_the_money_borrowers")),
                    )
                    for row in self._execute_template(
                        self._STATE_OPPORTUNITY_SQL,
                        analytics_filters,
                        alias="v",
                        extra=["v.state IS NOT NULL", "v.state <> ''"],
                    )
                ],
                state_avm_values=[
                    StateAvmValueRow(
                        state=str(row.get("state") or ""),
                        total_avm_value_usd=_int(row.get("total_avm_value_usd")),
                        total_lien_balance_usd=_int(row.get("total_lien_balance_usd")),
                        total_equity_usd=_int(row.get("total_equity_usd")),
                    )
                    for row in self._execute_template(
                        self._STATE_AVM_VALUE_SQL,
                        analytics_filters,
                        extra=["b.state IS NOT NULL", "b.state <> ''"],
                    )
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
                    for row in self._execute_template(
                        self._TOP_ZIPS_ITM_SQL,
                        analytics_filters,
                        extra=["b.zip IS NOT NULL", "LENGTH(b.zip) = 5"],
                    )
                ],
            )

        return self._cached(f"analytics.geography:{_filter_key(analytics_filters)}", build)

    def economics(self, filters: AnalyticsFilters | None = None) -> EconomicsAnalyticsResponse:
        analytics_filters = _filters(filters)

        def build() -> EconomicsAnalyticsResponse:
            return EconomicsAnalyticsResponse(
                rate_spread_histogram=[
                    RateSpreadBucket(
                        spread_bucket_bps=_int(row.get("spread_bucket_bps")),
                        borrower_count=_int(row.get("borrower_count")),
                    )
                    for row in self._execute_template(
                        self._RATE_SPREAD_HIST_SQL,
                        analytics_filters,
                        extra=["b.rate_spread_bps BETWEEN -100 AND 400"],
                    )
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
                    for row in self._execute_template(
                        self._EQUITY_VS_SPREAD_SQL,
                        analytics_filters,
                        extra=[
                            "b.rate_spread_bps BETWEEN -100 AND 400",
                            "b.equity_pct BETWEEN 0 AND 100",
                        ],
                    )
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
                    for row in self._execute_template(
                        self._TOP_BORROWERS_SQL,
                        analytics_filters,
                        extra=["b.opportunity_score >= 50"],
                    )
                ],
            )

        return self._cached(f"analytics.economics:{_filter_key(analytics_filters)}", build)

    def segments(self, filters: AnalyticsFilters | None = None) -> SegmentAnalyticsResponse:
        analytics_filters = _filters(filters)

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
                for row in self._execute_template(self._SEGMENT_OVERVIEW_SQL, analytics_filters)
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
                    for row in self._execute_template(self._SEGMENT_BY_STATE_SQL, analytics_filters)
                ],
                top_segments_by_state=[
                    TopSegmentByStateRow(
                        state=str(row.get("state") or ""),
                        segment_code=row["segment_code"],
                        segment_name=str(row.get("segment_name") or row.get("segment_code") or ""),
                        borrower_count=_int(row.get("borrower_count")),
                        state_rank=_int(row.get("state_rank"), 1),
                    )
                    for row in self._execute_template(self._TOP_SEGMENTS_BY_STATE_SQL, analytics_filters)
                ],
            )

        return self._cached(f"analytics.segments:{_filter_key(analytics_filters)}", build)

    def signals(self, filters: AnalyticsFilters | None = None) -> SignalAnalyticsResponse:
        analytics_filters = _filters(filters)

        def build() -> SignalAnalyticsResponse:
            return SignalAnalyticsResponse(
                evidence_daily=[
                    EvidenceDailyRow(
                        event_date=_date_text(row.get("event_date")) or "",
                        signal_type=str(row.get("signal_type") or ""),
                        event_count=_int(row.get("event_count")),
                    )
                    for row in self._execute_signal_template(self._EVIDENCE_DAILY_SQL, analytics_filters)
                ],
                evidence_by_signal=[
                    EvidenceBySignalRow(
                        signal_type=str(row.get("signal_type") or ""),
                        source_product=str(row.get("source_product") or ""),
                        source_table=str(row.get("source_table") or "mip.gold.evidence_events"),
                        source_label=source_display_label(str(row.get("source_table") or "mip.gold.evidence_events")),
                        event_count=_int(row.get("event_count")),
                        mean_confidence=_float_or_none(row.get("mean_confidence")),
                        confidence_source=_confidence_source(str(row.get("signal_type") or "")),
                        confidence_label=_confidence_label(str(row.get("signal_type") or "")),
                    )
                    for row in self._execute_signal_template(self._EVIDENCE_BY_SIGNAL_SQL, analytics_filters)
                ],
                evidence_examples=[
                    SignalEvidenceExample(
                        borrower_id=str(row.get("borrower_id") or ""),
                        display_name=str(row.get("display_name") or "Borrower"),
                        state=str(row.get("state") or ""),
                        signal_type=str(row.get("signal_type") or ""),
                        source_product=str(row.get("source_product") or ""),
                        signal_value=str(row.get("signal_value") or ""),
                        display_text=str(row.get("display_text") or ""),
                        confidence=float(row.get("confidence") or 0.0),
                        timestamp=str(row.get("timestamp") or ""),
                    )
                    for row in self._execute_signal_template(self._EVIDENCE_EXAMPLES_SQL, analytics_filters)
                ],
            )

        return self._cached(f"analytics.signals:{_filter_key(analytics_filters)}", build)
