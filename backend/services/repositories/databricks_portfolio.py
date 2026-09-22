"""Pure Portfolio repository helpers for the Databricks implementation."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from backend.schemas.portfolio import (
    CAMPAIGN_BUILD_LIMIT,
    HouseholdDedupSummary,
    KpiTrend,
    PortfolioCreateRequest,
    PortfolioCriteria,
    PortfolioOfferMixRow,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.observability import emit
from backend.services.repositories.databricks_portfolio_campaign_mappers import (
    _NORMALIZED_CAMPAIGN_VARIANTS_SQL,
    _PUBLIC_CAMPAIGN_VARIANT_FIELDS,
    _project_campaign_json_or_default,
    _project_campaign_json_with_status,
    _project_campaign_name_or_default,
    _project_campaign_name_with_status,
    _public_campaign_variant,
    campaign_summary_from_row,
)
from backend.services.repositories.databricks_portfolio_campaigns import (
    _CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS,
    _CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S,
    _CAMPAIGN_STATUS_LOOKUP_ATTEMPTS,
    _get_lakebase_client,
    _PortfolioCampaignPersistence,
)
from backend.services.repositories.databricks_portfolio_predicates import (
    PORTFOLIO_EQUITY_THRESHOLDS,
    PORTFOLIO_PRODUCT_CODES,
    build_kpi_trend,
    build_preview_predicates,
    coerce_utc_datetime,
    household_dedup_config_from_value,
    household_dedup_summary_from_value,
    json_value,
    preview_cache_key,
)
from backend.services.resilience import TTLCache

log = logging.getLogger(__name__)

# Import hub: the split moved the query-builder helpers, the campaign row
# mappers, and the campaign persistence block into sibling modules. Every name
# callers and tests resolve through ``databricks_portfolio`` stays importable
# here.
__all__ = [
    "PORTFOLIO_EQUITY_THRESHOLDS",
    "PORTFOLIO_PRODUCT_CODES",
    "DatabricksPortfolioRepository",
    "_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS",
    "_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S",
    "_CAMPAIGN_STATUS_LOOKUP_ATTEMPTS",
    "_NORMALIZED_CAMPAIGN_VARIANTS_SQL",
    "_PUBLIC_CAMPAIGN_VARIANT_FIELDS",
    "_get_lakebase_client",
    "_project_campaign_json_or_default",
    "_project_campaign_json_with_status",
    "_project_campaign_name_or_default",
    "_project_campaign_name_with_status",
    "_public_campaign_variant",
    "build_kpi_trend",
    "build_preview_predicates",
    "campaign_summary_from_row",
    "coerce_utc_datetime",
    "household_dedup_config_from_value",
    "household_dedup_summary_from_value",
    "json_value",
    "log",
    "preview_cache_key",
]


class DatabricksPortfolioRepository(_PortfolioCampaignPersistence):
    """Portfolio preview rollup over ``gold.borrower_360``.

    Slice-4 scope: the criteria on the request don't yet shift the
    rollup -- the whole population is the portfolio preview. A later
    slice adds criteria push-down.

    Slice-6: wraps the ``preview`` read in a short-TTL cache. Portfolio
    aggregates change slowly; a 30s stale read during a user click-
    through is invisible and saves two or three warehouse round trips
    per route transition.
    """

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 30.0,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s

    # S1: headline KPIs aggregate over the named semantic view
    # mip.semantics.portfolio_headline_metric_view — the single home for
    # every demoed headline measure. The high-opportunity threshold lives
    # only in mip.gold.fn_high_opportunity (surfaced as the view's
    # `is_high_opportunity` indicator); no score-threshold literal may
    # appear here (tests/unit/test_score_threshold_guard.py enforces).
    _PREVIEW_SQL_TEMPLATE = (
        "WITH preview_population AS ("
        "  SELECT "
        "    headline.borrower_id, headline.state, headline.opportunity_score, "
        "    headline.in_the_money, headline.is_high_opportunity, headline.score_band, "
        "    headline.offer_available, headline.offer_recommended, "
        "    headline.recommended_offer_code, headline.is_owner_occupied, "
        "    headline.current_lien_balance, headline.second_pos_amount, "
        "    headline.related_property_count, headline.listed_for_sale, "
        "    headline.has_heloc_propensity_trigger, headline.is_current_customer, "
        "    headline.is_former_customer, headline.is_competitor_lien, "
        "    headline.current_lender_ref, headline.loan_product_type, "
        "    headline.origination_channel, headline.equity_pct, "
        "    headline.marketing_eligible, headline.consent_status, "
        "    headline.suppression_reason, headline.dnc, "
        "    headline.eligible_recontact_at, headline.last_touch_at, "
        "    headline.has_unresolved_owner, headline.refreshed_at, "
        "    borrower.rate_spread_bps AS preview_rate_spread_bps "
        f"  FROM {qualify('semantics', 'portfolio_headline_metric_view')} AS headline "
        f"  LEFT JOIN {qualify('gold', 'borrower_360')} AS borrower "
        "    ON borrower.borrower_id = headline.borrower_id"
        ") "
        "SELECT "
        "  COUNT(*)                                                    AS marketable_population, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END)               AS high_intent_leads, "
        "  SUM(CASE WHEN is_high_opportunity THEN 1 ELSE 0 END)        AS top_tier_opportunities, "
        "  SUM(CASE WHEN offer_recommended THEN 1 ELSE 0 END)          AS offers_recommended, "
        "  SUM(CASE WHEN offer_available THEN 1 ELSE 0 END)            AS offers_available, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)                  AS avg_score, "
        "  CAST(ROUND(AVG(current_lien_balance)) AS BIGINT)            AS avg_current_lien_balance_usd, "
        "  CAST(ROUND(AVG(CASE WHEN in_the_money THEN current_lien_balance END)) AS BIGINT) "
        "    AS avg_high_intent_lien_balance_usd, "
        "  CAST(ROUND(SUM(current_lien_balance)) AS BIGINT)            AS total_current_lien_balance_usd, "
        "  ROUND(AVG(equity_pct), 1)                                   AS avg_equity_pct, "
        "  ROUND(AVG(preview_rate_spread_bps), 1)                      AS avg_rate_spread_bps, "
        "  MAX(refreshed_at)                                            AS data_refreshed_at, "
        "  SUM(CASE WHEN recommended_offer_code = 'purchase' THEN 1 ELSE 0 END) AS offer_purchase, "
        "  SUM(CASE WHEN recommended_offer_code = 'refi_plus_heloc' THEN 1 ELSE 0 END) AS offer_refi_plus_heloc, "
        "  SUM(CASE WHEN recommended_offer_code = 'heloc' THEN 1 ELSE 0 END) AS offer_heloc, "
        "  SUM(CASE WHEN recommended_offer_code = 'refi' THEN 1 ELSE 0 END) AS offer_refi, "
        "  SUM(CASE WHEN recommended_offer_code = 'cash_out' THEN 1 ELSE 0 END) AS offer_cash_out, "
        "  SUM(CASE WHEN recommended_offer_code = 'investor' THEN 1 ELSE 0 END) AS offer_investor, "
        "  SUM(CASE WHEN recommended_offer_code = 'retention' THEN 1 ELSE 0 END) AS offer_retention, "
        "  SUM(CASE WHEN recommended_offer_code = 'nurture' THEN 1 ELSE 0 END) AS offer_nurture "
        "FROM preview_population "
        "{where}"
    )

    _HOUSEHOLD_DEDUP_SUMMARY_SQL_TEMPLATE = """
    WITH filtered_borrowers AS (
      SELECT *
      FROM {borrower_table}
      {where}
    ),
    eligible_candidates AS (
      SELECT
        b.borrower_id,
        b.opportunity_score,
        COALESCE(
          h.household_id,
          CONCAT('HH-', SUBSTR(sha2(CONCAT('singleton:', b.borrower_id), 256), 1, 16))
        ) AS household_id,
        COALESCE(h.household_derivation_method, 'singleton') AS household_derivation_method
      FROM filtered_borrowers AS b
      LEFT JOIN {household_table} AS h
        ON h.borrower_id = b.borrower_id
      WHERE b.marketing_eligible = TRUE
        AND COALESCE(b.has_unresolved_owner, FALSE) = FALSE
    ),
    ranked AS (
      SELECT
        *,
        ROW_NUMBER() OVER (
          PARTITION BY household_id
          ORDER BY opportunity_score DESC, borrower_id ASC
        ) AS campaign_household_rank
      FROM eligible_candidates
    )
    SELECT
      CAST(COUNT(*) AS INT) AS candidate_borrower_count,
      CAST(COALESCE(SUM(CASE WHEN campaign_household_rank = 1 THEN 1 ELSE 0 END), 0) AS INT)
        AS selected_primary_count,
      CAST(COALESCE(SUM(CASE WHEN campaign_household_rank > 1 THEN 1 ELSE 0 END), 0) AS INT)
        AS suppressed_co_owner_count,
      CAST(COUNT(DISTINCT household_id) AS INT) AS household_count,
      CAST(COUNT(DISTINCT CASE WHEN household_derivation_method = 'owner_link' THEN household_id END) AS INT)
        AS owner_link_household_count,
      CAST(COUNT(DISTINCT CASE WHEN household_derivation_method = 'mailing_address' THEN household_id END) AS INT)
        AS mailing_address_household_count,
      CAST(COUNT(DISTINCT CASE WHEN household_derivation_method = 'singleton' THEN household_id END) AS INT)
        AS singleton_household_count
    FROM ranked
    """

    # Translate display labels from the portfolio-builder UI into
    # mip.gold.borrower_360 predicates. Every value is a short enum the
    # frontend emits verbatim; we keep the mapping here rather than ship
    # the strings to SQL so a typo in a dropdown can't open a SQL-injection
    # vector.
    #
    # Geography labels are computed from current live coverage. The broad
    # option is exactly "all N states" for the current N; individual state
    # names come from StateFootprintResolver. No fixed MSA or demo-only
    # shortcuts are accepted here.

    @classmethod
    def _state_sets(cls) -> dict[str, list[str]]:
        """Build the active _STATE_SETS dict from live coverage.

        Active labels:

          1. Per-state-name entries from ``state_name_to_codes()``.
          2. ``all N states`` computed from current coverage.

        Called once per `_build_preview_predicates` invocation; the
        resolver caches the UC result for 300s so this is cheap.
        """
        from backend.services.state_footprint import get_state_footprint_resolver

        resolver = get_state_footprint_resolver()
        footprint_codes = resolver.state_codes()
        state_name_map = resolver.state_name_to_codes()
        all_key = f"all {len(footprint_codes)} states"
        return {
            **state_name_map,
            all_key: list(footprint_codes),
        }

    # Canonical `recommended_offer_code` values emitted by fn_next_best_offer
    # (see sql/uc_functions/fn_next_best_offer.sql). Keep in sync.
    _PRODUCT_CODES: dict[str, list[str]] = {
        **PORTFOLIO_PRODUCT_CODES,
    }

    _EQUITY_THRESHOLDS: dict[str, int] = {
        "≥ 15%": PORTFOLIO_EQUITY_THRESHOLDS["≥ 15%"],
        "≥ 25%": PORTFOLIO_EQUITY_THRESHOLDS["≥ 25%"],
        "≥ 40%": PORTFOLIO_EQUITY_THRESHOLDS["≥ 40%"],
    }

    @classmethod
    def _build_preview_predicates(
        cls,
        criteria: PortfolioCriteria | None,
    ) -> tuple[str, dict[str, Any]]:
        """Convert validated PortfolioCriteria into a (WHERE clause, params)
        pair. Returns `("", {})` when no predicates apply so the caller can
        run the criteria-free SELECT."""
        if criteria is None:
            return "", {}
        return build_preview_predicates(
            criteria,
            state_sets=cls._state_sets() if criteria.geography else {},
            product_codes=cls._PRODUCT_CODES,
            equity_thresholds=cls._EQUITY_THRESHOLDS,
        )

    # 7-day history for KPI sparklines + the two real funnel counts that
    # replaced the old hardcoded cost_per_contact / projected_contact_to_app
    # placeholders. Reads the national rollup row (state='_ALL',
    # segment_code='_ALL') from the daily funnel snapshot. Returns 0-7 rows
    # ordered newest-first (repository reverses to oldest-first before
    # sparkline rendering).
    _TREND_SQL = (
        "SELECT "
        "  snapshot_date, "
        "  snapshot_at, "
        "  addressable_borrowers          AS marketable_population, "
        "  in_the_money_borrowers         AS high_intent_leads, "
        "  high_opportunity_borrowers     AS top_tier_opportunities, "
        "  offer_recommended_borrowers    AS offers_recommended, "
        "  avg_opportunity_score          AS avg_score, "
        "  approved_borrowers             AS approved_count, "
        "  actioned_borrowers             AS in_outreach_count "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} "
        "WHERE state = '_ALL' AND segment_code = '_ALL' "
        "ORDER BY snapshot_date DESC "
        "LIMIT 7"
    )

    _LIVE_WORKFLOW_COUNTS_SQL = (
        "SELECT "
        "  CAST(SUM(CASE WHEN COALESCE(ls.approval_status, 'pending') = 'approved' THEN 1 ELSE 0 END) "
        "    AS INT) AS approved_count, "
        # 2026-08-07 platform audit F5: keep "in outreach" a subset of
        # "approved" so the Home KPI row and the executive funnel agree and
        # a rejected borrower never reads as worked. Mirrors the predicate in
        # DatabricksAnalyticsRepository._LIVE_FUNNEL_SQL.
        "  CAST(SUM(CASE WHEN COALESCE(ls.approval_status, 'pending') = 'approved' "
        "                 AND COALESCE(ls.outreach_status, 'none') = 'actioned' "
        "            THEN 1 ELSE 0 END) "
        "    AS INT) AS in_outreach_count "
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} AS ls "
        "  ON ls.borrower_id = b.borrower_id"
    )

    _PREVIEW_CACHE_KEY = "portfolio.preview.all"
    _DAY_ZERO_CACHE_KEY = "portfolio.day_zero"

    # Authoritative "this workspace has never had a gold refresh" signal
    # (R5-20). Unfiltered population count on mip.gold.lead_population,
    # because the day-zero state is workspace-wide and must not shift
    # with the caller's PortfolioCriteria (a criteria that happens to
    # match zero borrowers is NOT day-zero). LIMIT 1 + EXISTS-style CASE
    # so the warehouse returns one row no matter how large the table
    # becomes. The result is cached longer than the preview -- day-zero
    # flips at most once in a workspace's lifetime.
    _DAY_ZERO_SQL = (
        "SELECT CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END AS day_zero "
        f"FROM {qualify('gold', 'lead_population')}"
    )

    @staticmethod
    def _build_trend(points: list[tuple[str, float]]) -> KpiTrend:
        """Compute KpiTrend from oldest-first (date label, value) points.

        The live funnel table currently has a bootstrap row where some
        later-added metrics are 0 before the metric existed. A percent
        change from that row is mathematically undefined and visually
        misleading. Drop leading zero bootstrap points when later non-zero
        rows exist, then label the comparison date explicitly so the UI
        never says "7d ago" unless the data really represents that grain.
        """
        return build_kpi_trend(points)

    def _load_funnel(
        self,
        *,
        include_trends: bool,
    ) -> tuple[dict[str, KpiTrend], dict[str, Any], str, str | None]:
        """Query the funnel snapshot and return trends + latest metadata.

        `trends` is a dict keyed by KPI field; series are oldest-first.
        `latest` is the newest row (keys include `approved_count`,
        `in_outreach_count`, `snapshot_at`) — used by the preview to
        surface the real current counts + data_refreshed_at timestamp.

        Trend lines are only cohort-correct for the unfiltered portfolio
        because ``funnel_snapshot_daily`` currently snapshots the national
        _ALL/_ALL row, not arbitrary filter combinations. For filtered
        requests we still read the latest refresh metadata but deliberately
        return no sparkline series and a note for the UI.
        """
        try:
            rows = self._client.execute(self._TREND_SQL) or []
        except Exception as exc:  # noqa: BLE001 -- surface unavailable, don't invent trends
            emit(
                log,
                "portfolio_funnel_snapshot_query_failed",
                level=logging.WARNING,
                dependency="warehouse",
                outcome="degraded",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            return (
                {},
                {},
                "unavailable",
                "Trend snapshots are unavailable; headline KPIs still come from live borrower_360.",
            )
        if not rows:
            return {}, {}, "empty", "No daily funnel snapshots have been written yet."
        # Query is DESC; the FIRST row is newest. Reverse for oldest-first
        # sparkline rendering.
        latest = rows[0]
        if not include_trends:
            return (
                {},
                latest,
                "not_applicable",
                "Trend lines are hidden for this filtered build because daily snapshots are not stored at this custom filter grain.",
            )
        ordered = list(reversed(rows))
        trends: dict[str, KpiTrend] = {}
        for key in (
            "marketable_population",
            "high_intent_leads",
            "top_tier_opportunities",
            "offers_recommended",
            "avg_score",
            "approved_count",
            "in_outreach_count",
        ):
            points = [
                (str(r.get("snapshot_date") or "prior snapshot"), float(r.get(key) or 0))
                for r in ordered
            ]
            trends[key] = self._build_trend(points)
        return trends, latest, "live", None

    def _load_live_workflow_counts(self) -> dict[str, int]:
        """Return current approval/outreach counts from the lifecycle mirror.

        The daily funnel snapshot is still the trend source, but workflow
        state can change after the scoring refresh. Reading the live mirror
        keeps Home and Analytics consistent with Borrower 360 chips and Lead
        Queue drilldowns.
        """

        try:
            row = self._client.execute_one(self._LIVE_WORKFLOW_COUNTS_SQL) or {}
        except Exception as exc:  # noqa: BLE001 -- fall back to snapshot counts
            emit(
                log,
                "portfolio_workflow_counts_query_failed",
                level=logging.WARNING,
                dependency="warehouse",
                outcome="degraded",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            return {}
        return {
            "approved_count": int(row.get("approved_count") or 0),
            "in_outreach_count": int(row.get("in_outreach_count") or 0),
        }

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        """Normalise ``MAX(snapshot_at)`` into a tz-aware UTC ``datetime``.

        The Databricks SQL connector returns TIMESTAMP as a tz-naive Python
        ``datetime`` (no ``tzinfo``). Pydantic would serialise that without
        a ``Z`` / ``+00:00`` suffix, so ``new Date(...)`` in the browser
        interprets it as local time — and a European viewer sees the wrong
        hour on ``data_refreshed_at``. Stamp UTC on the way out so the wire
        contract is unambiguous (hole-finder round 2 #4, 2026-04-23).
        """
        return coerce_utc_datetime(value)

    @classmethod
    def _preview_cache_key(
        cls,
        where_clause: str,
        params: dict[str, Any],
        campaign_build_config: dict[str, Any] | None = None,
    ) -> str:
        """Deterministic cache key for ``preview`` results.

        R5-08: the prior key embedded ``str(sorted(params.items()))``,
        which produced semantically-equivalent-but-different strings
        depending on dict iteration order, Python version, and repr of
        edge-case values. That was a minor cache-miss waste today and a
        500-risk if a non-hashable value ever slipped in. We now hash
        the canonical JSON form of ``(where_clause, params)`` -- stable
        regardless of insertion order (``sort_keys=True``), string-safe
        for everything pydantic emits (``default=str``), and bounded in
        length via SHA-256.
        """
        return preview_cache_key(
            cls._PREVIEW_CACHE_KEY,
            where_clause,
            params,
            campaign_build_config=campaign_build_config,
        )

    def _load_day_zero(self) -> bool:
        """Return True when ``mip.gold.lead_population`` is empty.

        R5-20: authoritative day-zero signal. Cached under its own key
        so it's shared across every criteria variant (day-zero doesn't
        depend on the filter).

        R6-06/R6-07: exceptions propagate. The prior implementation
        swallowed any failure into ``return False``, which yielded a
        misleading preview -- the frontend would say "there IS data"
        (day_zero=False) alongside KPIs of 0 (because the preview
        execute_one ALSO failed, but differently) and show a degraded
        banner on top. Letting the exception bubble out means the
        preview route's surrounding ``DependencyDownError`` -> 503
        path fires cleanly and the UI shows a single honest "warming
        up" message instead of a misleading empty grid.

        The ``execute_one`` here runs through ``ResilientSqlClient``,
        so transient warehouse failures already surface as
        ``DependencyDownError``. Any non-resilience exception (e.g.
        schema drift) is also a legitimate 503 signal -- we are not
        in the business of quietly rendering zeros for unknown
        failure modes.

        R6-17: skip the cache get/set when ``_cache_ttl_s`` is 0.
        ``TTLCache.set`` already short-circuits on ttl<=0 but the ``get``
        acquires a lock for no benefit; bypassing both keeps the
        tests-with-caching-disabled path allocation-free.
        """
        if self._cache_ttl_s <= 0:
            row = self._client.execute_one(self._DAY_ZERO_SQL) or {}
            return bool(row.get("day_zero"))
        cached = self._cache.get(self._DAY_ZERO_CACHE_KEY)
        if cached is not None:
            return bool(cached)
        row = self._client.execute_one(self._DAY_ZERO_SQL) or {}
        day_zero = bool(row.get("day_zero"))
        self._cache.set(self._DAY_ZERO_CACHE_KEY, day_zero, self._cache_ttl_s)
        return day_zero

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        criteria = request.criteria if request is not None else None
        campaign_build_config = request.campaign_build_config if request is not None else None
        campaign_build_cache_config = (
            campaign_build_config.model_dump(mode="json")
            if campaign_build_config is not None
            else None
        )
        where_clause, params = self._build_preview_predicates(criteria)
        # R6-17: when caching is disabled (MIP_CACHE_TTL_S=0, test
        # defaults, some dev loops), skip the SHA-256 hash + dict
        # serialisation that build the cache key. Saves one hashlib
        # invocation per request on the hottest route without changing
        # the caller contract.
        caching_enabled = self._cache_ttl_s > 0
        cache_key = (
            self._preview_cache_key(
                where_clause,
                params,
                campaign_build_config=campaign_build_cache_config,
            )
            if caching_enabled
            else ""
        )

        def build() -> PortfolioPreview:
            sql = self._PREVIEW_SQL_TEMPLATE.format(where=where_clause)
            row = self._client.execute_one(sql, params) or {}
            campaign_build_contact_count: int | None = None
            if campaign_build_config is not None:
                # Local import avoids the portfolio/lead-cohort compiler cycle.
                from backend.services.repositories.databricks_lead_cohorts import (
                    LeadCohortFilters,
                    LeadCohortQueries,
                )

                treatment_preflight = LeadCohortQueries(
                    self._client,
                    cache_ttl_s=0,
                ).campaign_treatment_preflight(
                    LeadCohortFilters(
                        segment=None,
                        portfolio_criteria=criteria or PortfolioCriteria(),
                    ),
                    frequency_cap_days=int(
                        str(campaign_build_config.suppression_policy.get("frequency_cap_days", 30))
                    ),
                    household_dedup_enabled=campaign_build_config.household_dedup.enabled,
                )
                campaign_build_contact_count = int(treatment_preflight["selected_primary_count"])
            trends, latest, trend_status, trend_note = self._load_funnel(
                include_trends=not bool(where_clause),
            )
            workflow_counts = self._load_live_workflow_counts() if not where_clause else {}
            offer_mix = [
                PortfolioOfferMixRow.model_validate(
                    {
                        "offer_code": code,
                        "borrower_count": int(row.get(f"offer_{code}") or 0),
                    }
                )
                for code in (
                    "purchase",
                    "refi_plus_heloc",
                    "heloc",
                    "refi",
                    "cash_out",
                    "investor",
                    "retention",
                    "nurture",
                )
            ]
            return PortfolioPreview(
                marketable_population=int(row.get("marketable_population") or 0),
                campaign_build_limit=CAMPAIGN_BUILD_LIMIT,
                campaign_build_contact_count=campaign_build_contact_count,
                campaign_build_eligible=(
                    campaign_build_contact_count <= CAMPAIGN_BUILD_LIMIT
                    if campaign_build_contact_count is not None
                    else None
                ),
                high_intent_leads=int(row.get("high_intent_leads") or 0),
                top_tier_opportunities=(
                    int(row["top_tier_opportunities"])
                    if row.get("top_tier_opportunities") is not None
                    else None
                ),
                offers_recommended=(
                    int(row["offers_recommended"])
                    if row.get("offers_recommended") is not None
                    else None
                ),
                offers_available=(
                    int(row["offers_available"])
                    if row.get("offers_available") is not None
                    else None
                ),
                avg_score=(int(row["avg_score"]) if row.get("avg_score") is not None else None),
                avg_current_lien_balance_usd=(
                    int(row["avg_current_lien_balance_usd"])
                    if row.get("avg_current_lien_balance_usd") is not None
                    else None
                ),
                avg_high_intent_lien_balance_usd=(
                    int(row["avg_high_intent_lien_balance_usd"])
                    if row.get("avg_high_intent_lien_balance_usd") is not None
                    else None
                ),
                total_current_lien_balance_usd=(
                    int(row["total_current_lien_balance_usd"])
                    if row.get("total_current_lien_balance_usd") is not None
                    else None
                ),
                avg_equity_pct=(
                    float(row["avg_equity_pct"]) if row.get("avg_equity_pct") is not None else None
                ),
                avg_rate_spread_bps=(
                    float(row["avg_rate_spread_bps"])
                    if row.get("avg_rate_spread_bps") is not None
                    else None
                ),
                offer_mix=offer_mix,
                approved_count=(
                    workflow_counts.get("approved_count", int(latest["approved_count"]))
                    if not where_clause and latest.get("approved_count") is not None
                    else None
                ),
                in_outreach_count=(
                    workflow_counts.get("in_outreach_count", int(latest["in_outreach_count"]))
                    if not where_clause and latest.get("in_outreach_count") is not None
                    else None
                ),
                # This timestamp comes from the same borrower-grain statement
                # as the economics above. Funnel snapshots are a separate
                # historical surface and must not date the current economics.
                data_refreshed_at=self._coerce_datetime(row.get("data_refreshed_at")),
                trends=trends,
                trend_status=trend_status,
                trend_note=trend_note,
                day_zero=self._load_day_zero(),
            )

        if caching_enabled:
            return self._cache.get_or_set(
                cache_key,
                build,
                ttl_s=self._cache_ttl_s,
                stale_if_error=True,
            )
        return build()

    def _load_household_dedup_summary(
        self,
        payload: PortfolioCreateRequest,
    ) -> HouseholdDedupSummary:
        if not payload.household_dedup.enabled:
            return HouseholdDedupSummary(enabled=False)

        where_clause, params = self._build_preview_predicates(payload.criteria)
        sql = self._HOUSEHOLD_DEDUP_SUMMARY_SQL_TEMPLATE.format(
            borrower_table=qualify("gold", "borrower_360"),
            household_table=qualify("gold", "household_rollup"),
            where=where_clause,
        )
        row = self._client.execute_one(sql, params) or {}
        return HouseholdDedupSummary(
            enabled=True,
            candidate_borrower_count=int(row.get("candidate_borrower_count") or 0),
            selected_primary_count=int(row.get("selected_primary_count") or 0),
            suppressed_co_owner_count=int(row.get("suppressed_co_owner_count") or 0),
            household_count=int(row.get("household_count") or 0),
            owner_link_household_count=int(row.get("owner_link_household_count") or 0),
            mailing_address_household_count=int(row.get("mailing_address_household_count") or 0),
            singleton_household_count=int(row.get("singleton_household_count") or 0),
        )

