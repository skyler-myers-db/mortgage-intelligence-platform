"""Pure Portfolio repository helpers for the Databricks implementation."""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.schemas.campaign_status import validate_campaign_status_transition
from backend.schemas.portfolio import (
    CampaignListResponse,
    CampaignStatusPatchRequest,
    CampaignSummary,
    HouseholdDedupConfig,
    HouseholdDedupSummary,
    KpiTrend,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioCriteria,
    PortfolioOfferMixRow,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.campaign_intelligence import (
    campaign_criteria_fingerprint,
    inspect_campaign_variant_provenance,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate, suppressed_sql_predicate
from backend.services.lakebase import (
    LakebaseError,
)
from backend.services.lakebase import (
    get_lakebase_client as _get_lakebase_client_default,
)
from backend.services.observability import emit, get_correlation_id
from backend.services.resilience import TTLCache

log = logging.getLogger(__name__)

_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS = 3
_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S = 0.01


def _get_lakebase_client():
    """Preserve the historical ``databricks_repo.get_lakebase_client`` patch seam."""
    facade = sys.modules.get("backend.services.repositories.databricks_repo")
    patched = getattr(facade, "get_lakebase_client", None) if facade is not None else None
    if callable(patched):
        return patched()
    return _get_lakebase_client_default()


PORTFOLIO_PRODUCT_CODES: dict[str, list[str]] = {
    "refi": ["refi", "refi_plus_heloc"],
    "heloc": ["heloc", "refi_plus_heloc"],
    "cash-out": ["cash_out"],
    "purchase": ["purchase"],
    "retention": ["retention"],
}

PORTFOLIO_EQUITY_THRESHOLDS: dict[str, int] = {
    "≥ 15%": 15,
    "≥ 25%": 25,
    "≥ 40%": 40,
}


class DatabricksPortfolioRepository:
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
        "  ROUND(AVG(rate_spread_bps), 1)                              AS avg_rate_spread_bps, "
        "  MAX(refreshed_at)                                            AS data_refreshed_at, "
        "  SUM(CASE WHEN recommended_offer_code = 'purchase' THEN 1 ELSE 0 END) AS offer_purchase, "
        "  SUM(CASE WHEN recommended_offer_code = 'refi_plus_heloc' THEN 1 ELSE 0 END) AS offer_refi_plus_heloc, "
        "  SUM(CASE WHEN recommended_offer_code = 'heloc' THEN 1 ELSE 0 END) AS offer_heloc, "
        "  SUM(CASE WHEN recommended_offer_code = 'refi' THEN 1 ELSE 0 END) AS offer_refi, "
        "  SUM(CASE WHEN recommended_offer_code = 'cash_out' THEN 1 ELSE 0 END) AS offer_cash_out, "
        "  SUM(CASE WHEN recommended_offer_code = 'investor' THEN 1 ELSE 0 END) AS offer_investor, "
        "  SUM(CASE WHEN recommended_offer_code = 'retention' THEN 1 ELSE 0 END) AS offer_retention, "
        "  SUM(CASE WHEN recommended_offer_code = 'nurture' THEN 1 ELSE 0 END) AS offer_nurture "
        f"FROM {qualify('semantics', 'portfolio_headline_metric_view')} "
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
        "  CAST(SUM(CASE WHEN COALESCE(ls.outreach_status, 'none') = 'actioned' THEN 1 ELSE 0 END) "
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

    _CAMPAIGN_INSERT_SQL = """
    WITH inserted_campaign AS (
      INSERT INTO mip_app.campaigns (
        name, owner_email, status, criteria, suppression_policy,
        message_variants, channel_cascade, send_window, holdout,
        roi_assumptions, household_dedup, household_summary,
        idempotency_key, request_payload_hash, creation_response, updated_at
      )
      VALUES (
        %(name)s, %(owner_email)s, 'draft', %(criteria)s::jsonb,
        %(suppression_policy)s::jsonb, %(message_variants)s::jsonb,
        %(channel_cascade)s::jsonb, %(send_window)s::jsonb,
        %(holdout)s::jsonb, %(roi_assumptions)s::jsonb,
        %(household_dedup)s::jsonb, %(household_summary)s::jsonb,
        %(idempotency_key)s, %(request_payload_hash)s,
        %(creation_response)s::jsonb, now()
      )
      ON CONFLICT (owner_email, idempotency_key)
        WHERE idempotency_key IS NOT NULL
      DO NOTHING
      RETURNING campaign_id, request_payload_hash, creation_response
    ),
    inserted_audit AS (
      INSERT INTO mip_app.action_audit (
        event_type, actor_email, entity_type, entity_id,
        request_id, correlation_id, evidence_ids, metadata
      )
      SELECT
        'PORTFOLIO_CREATE',
        %(owner_email)s,
        'campaign',
        inserted_campaign.campaign_id::text,
        %(request_id)s,
        %(correlation_id)s,
        ARRAY[]::TEXT[],
        %(metadata)s::jsonb
      FROM inserted_campaign
      RETURNING audit_id
    ),
    inserted_variants AS (
      INSERT INTO mip_app.campaign_message_variants (
        campaign_id, variant_name, channel, subject, body, weight_pct,
        generation_mode, generator_label, provenance_key_id,
        provenance_issued_at, provenance_expires_at, provenance_copy_hash,
        provenance_criteria_fingerprint, provenance_performance_fingerprint,
        provenance_token_digest
      )
      SELECT
        inserted_campaign.campaign_id,
        variant.variant_name,
        variant.channel,
        variant.subject,
        variant.body,
        variant.weight_pct,
        variant.generation_mode,
        variant.generator_label,
        variant.provenance_key_id,
        variant.provenance_issued_at,
        variant.provenance_expires_at,
        variant.provenance_copy_hash,
        variant.provenance_criteria_fingerprint,
        variant.provenance_performance_fingerprint,
        variant.provenance_token_digest
      FROM inserted_campaign
      CROSS JOIN jsonb_to_recordset(%(variant_rows)s::jsonb) AS variant(
        variant_name TEXT,
        channel TEXT,
        subject TEXT,
        body TEXT,
        weight_pct NUMERIC,
        generation_mode TEXT,
        generator_label TEXT,
        provenance_key_id TEXT,
        provenance_issued_at TIMESTAMPTZ,
        provenance_expires_at TIMESTAMPTZ,
        provenance_copy_hash TEXT,
        provenance_criteria_fingerprint TEXT,
        provenance_performance_fingerprint TEXT,
        provenance_token_digest TEXT
      )
      RETURNING campaign_id
    )
    SELECT
      inserted_campaign.campaign_id,
      inserted_audit.audit_id,
      inserted_campaign.request_payload_hash,
      inserted_campaign.creation_response,
      TRUE AS created,
      (SELECT COUNT(*) FROM inserted_variants) AS variant_count
    FROM inserted_campaign
    LEFT JOIN inserted_audit ON TRUE
    """

    _CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL = """
    SELECT
      c.campaign_id,
      c.request_payload_hash,
      c.creation_response,
      (
        SELECT a.audit_id
        FROM mip_app.action_audit a
        WHERE a.entity_type = 'campaign'
          AND a.entity_id = c.campaign_id::text
          AND a.event_type = 'PORTFOLIO_CREATE'
        ORDER BY a.occurred_at ASC
        LIMIT 1
      ) AS audit_id
    FROM mip_app.campaigns c
    WHERE c.owner_email = %(owner_email)s
      AND c.idempotency_key = %(idempotency_key)s
    LIMIT 1
    """

    # Re-audit #4 (2026-06-12): 'archived' is the "hide from the product"
    # status, but the default listing showed every status — so a governed
    # PATCH-to-archived (which sets updated_at=now()) bumped the archived
    # row to the TOP of Saved Campaigns instead of removing it. Default
    # listing now excludes archived; an explicit status='archived' query
    # still returns them (admin/audit). This is also what makes the booth
    # Saved Campaigns panel show only real builds after dev detritus
    # (load-test + Genie-draft rows) is archived.
    _CAMPAIGN_LIST_SQL = """
    SELECT campaign_id::text, name, owner_email, status, criteria,
           suppression_policy, message_variants, channel_cascade, send_window,
           holdout, roi_assumptions, household_dedup, household_summary, created_at, updated_at
    FROM mip_app.campaigns
    WHERE (%(owner_email)s::text IS NULL OR owner_email = %(owner_email)s::text)
      AND (
        CASE
          WHEN %(status)s::text IS NULL THEN status <> 'archived'
          ELSE status = %(status)s::text
        END
      )
    ORDER BY updated_at DESC, created_at DESC
    LIMIT %(limit)s
    """

    _CAMPAIGN_GET_SQL = """
    SELECT campaign_id::text, name, owner_email, status, criteria,
           suppression_policy, message_variants, channel_cascade, send_window,
           holdout, roi_assumptions, household_dedup, household_summary, created_at, updated_at
    FROM mip_app.campaigns
    WHERE campaign_id = %(campaign_id)s::uuid
    LIMIT 1
    """

    _CAMPAIGN_PATCH_SQL = """
    WITH updated_campaign AS (
      UPDATE mip_app.campaigns
      SET status = %(status)s, updated_at = now()
      WHERE campaign_id = %(campaign_id)s::uuid
        AND status = %(current_status)s
      RETURNING campaign_id::text, name, owner_email, status, criteria,
                suppression_policy, message_variants, channel_cascade, send_window,
                holdout, roi_assumptions, household_dedup, household_summary, created_at, updated_at
    ),
    inserted_audit AS (
      INSERT INTO mip_app.action_audit (
        event_type, actor_email, entity_type, entity_id,
        request_id, correlation_id, evidence_ids, metadata
      )
      SELECT
        'CAMPAIGN_STATUS_UPDATE',
        %(actor)s,
        'campaign',
        updated_campaign.campaign_id,
        %(request_id)s,
        %(correlation_id)s,
        %(evidence_ids)s::TEXT[],
        %(metadata)s::jsonb
      FROM updated_campaign
      RETURNING audit_id
    )
    SELECT updated_campaign.*, inserted_audit.audit_id
    FROM updated_campaign
    LEFT JOIN inserted_audit ON TRUE
    """

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
    def _preview_cache_key(cls, where_clause: str, params: dict[str, Any]) -> str:
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
        return preview_cache_key(cls._PREVIEW_CACHE_KEY, where_clause, params)

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
        where_clause, params = self._build_preview_predicates(criteria)
        # R6-17: when caching is disabled (MIP_CACHE_TTL_S=0, test
        # defaults, some dev loops), skip the SHA-256 hash + dict
        # serialisation that build the cache key. Saves one hashlib
        # invocation per request on the hottest route without changing
        # the caller contract.
        caching_enabled = self._cache_ttl_s > 0
        cache_key = self._preview_cache_key(where_clause, params) if caching_enabled else ""

        def build() -> PortfolioPreview:
            sql = self._PREVIEW_SQL_TEMPLATE.format(where=where_clause)
            row = self._client.execute_one(sql, params) or {}
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

    def create(
        self,
        payload: PortfolioCreateRequest,
        *,
        actor: str | None = None,
        idempotency_key: str,
    ) -> PortfolioCreateResponse:
        owner_email = actor or "unknown"
        request_payload = payload.model_dump(mode="json", exclude_none=False)
        request_payload_hash = hashlib.sha256(
            json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        lakebase = _get_lakebase_client()
        existing = lakebase.fetchone(
            self._CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL,
            {"owner_email": owner_email, "idempotency_key": idempotency_key},
        )
        if existing is not None:
            return self._campaign_create_response_from_idempotency_row(
                existing,
                expected_payload_hash=request_payload_hash,
            )
        preview = self.preview(PortfolioPreviewRequest(criteria=payload.criteria))
        household_summary = self._load_household_dedup_summary(payload)
        household_summary_dict = household_summary.model_dump()
        household_dedup_dict = payload.household_dedup.model_dump()
        criteria_fingerprint = campaign_criteria_fingerprint(payload.criteria)
        variant_rows: list[dict[str, object]] = []
        for variant in payload.message_variants:
            if not str(variant.get("body") or "").strip():
                continue
            requested_mode = str(variant.get("generation_mode") or "operator")
            requested_label = str(variant.get("generator_label") or "Operator edited")
            if requested_mode == "operator":
                generation_mode = "operator"
                generator_label = "Operator edited"
                provenance_proof = None
            else:
                provenance_proof = inspect_campaign_variant_provenance(
                    variant,
                    criteria_fingerprint=criteria_fingerprint,
                )
                if provenance_proof is None:
                    raise ValueError(
                        "model-generated campaign provenance is missing, expired, or invalid"
                    )
                generation_mode = provenance_proof.generation_mode
                generator_label = provenance_proof.generator_label
                if requested_mode != generation_mode or requested_label != generator_label:
                    raise ValueError(
                        "model-generated campaign provenance does not match the server proof"
                    )
            variant_rows.append(
                {
                    "variant_name": str(
                        variant.get("variant_name") or variant.get("name") or "default"
                    )[:64],
                    "channel": str(variant.get("channel") or "email"),
                    "subject": variant.get("subject"),
                    "body": str(variant.get("body") or ""),
                    "weight_pct": variant.get("weight_pct"),
                    "generation_mode": generation_mode,
                    "generator_label": generator_label,
                    "provenance_key_id": (provenance_proof.key_id if provenance_proof else None),
                    "provenance_issued_at": (
                        datetime.fromtimestamp(provenance_proof.issued_at, UTC).isoformat()
                        if provenance_proof
                        else None
                    ),
                    "provenance_expires_at": (
                        datetime.fromtimestamp(provenance_proof.expires_at, UTC).isoformat()
                        if provenance_proof
                        else None
                    ),
                    "provenance_copy_hash": (
                        provenance_proof.copy_hash if provenance_proof else None
                    ),
                    "provenance_criteria_fingerprint": (
                        provenance_proof.criteria_fingerprint if provenance_proof else None
                    ),
                    "provenance_performance_fingerprint": (
                        provenance_proof.performance_fingerprint if provenance_proof else None
                    ),
                    "provenance_token_digest": (
                        provenance_proof.token_digest if provenance_proof else None
                    ),
                }
            )
        generation_modes = {
            str(variant.get("generation_mode") or "operator") for variant in variant_rows
        }
        generator_labels = {
            str(variant.get("generator_label") or "Operator edited") for variant in variant_rows
        }
        campaign_generation_mode = (
            next(iter(generation_modes))
            if len(generation_modes) == 1
            else "mixed"
            if generation_modes
            else "operator"
        )
        campaign_generator_label = (
            next(iter(generator_labels))
            if len(generator_labels) == 1
            else "Multiple generators"
            if generator_labels
            else "Operator edited"
        )
        variant_provenance: list[dict[str, object]] = []
        for variant in variant_rows:
            proof_row: dict[str, object] = {
                "variant_name": variant["variant_name"],
                "generation_mode": variant["generation_mode"],
                "generator_label": variant["generator_label"],
            }
            if variant["provenance_key_id"] is not None:
                proof_row.update(
                    {
                        "provenance_key_id": variant["provenance_key_id"],
                        "provenance_issued_at": variant["provenance_issued_at"],
                        "provenance_expires_at": variant["provenance_expires_at"],
                        "provenance_copy_hash": variant["provenance_copy_hash"],
                        "provenance_criteria_fingerprint": variant[
                            "provenance_criteria_fingerprint"
                        ],
                        "provenance_performance_fingerprint": variant[
                            "provenance_performance_fingerprint"
                        ],
                    }
                )
            variant_provenance.append(proof_row)
        creation_response = {
            "name": payload.name,
            "marketable_population": preview.marketable_population,
            "household_summary": household_summary_dict,
        }
        row = lakebase.fetchone(
            self._CAMPAIGN_INSERT_SQL,
            {
                "name": payload.name,
                "owner_email": owner_email,
                "criteria": json.dumps(payload.criteria.model_dump(exclude_none=True)),
                "suppression_policy": json.dumps(payload.suppression_policy, sort_keys=True),
                "message_variants": json.dumps(variant_rows, sort_keys=True),
                "channel_cascade": json.dumps(payload.channel_cascade, sort_keys=True),
                "send_window": json.dumps(payload.send_window, sort_keys=True),
                "holdout": json.dumps(payload.holdout, sort_keys=True)
                if payload.holdout is not None
                else "null",
                "roi_assumptions": json.dumps(payload.roi_assumptions, sort_keys=True)
                if payload.roi_assumptions is not None
                else "null",
                "household_dedup": json.dumps(household_dedup_dict, sort_keys=True),
                "household_summary": json.dumps(household_summary_dict, sort_keys=True),
                "variant_rows": json.dumps(variant_rows, sort_keys=True),
                "idempotency_key": idempotency_key,
                "request_payload_hash": request_payload_hash,
                "creation_response": json.dumps(creation_response, sort_keys=True),
                "request_id": idempotency_key,
                "correlation_id": get_correlation_id(),
                "metadata": json.dumps(
                    build_safe_audit_metadata(
                        {
                            "source": "portfolio_builder",
                            "portfolio_criteria": payload.criteria.model_dump(exclude_none=True),
                            "suppression_policy": payload.suppression_policy,
                            "channel_cascade": payload.channel_cascade,
                            "send_window": payload.send_window,
                            "holdout": payload.holdout,
                            "roi_assumptions": payload.roi_assumptions,
                            "marketable_population": preview.marketable_population,
                            "dedupe_unit": payload.household_dedup.dedupe_unit,
                            "household_dedup_enabled": payload.household_dedup.enabled,
                            "household_primary_strategy": (
                                payload.household_dedup.primary_contact_strategy
                            ),
                            "household_candidate_count": (
                                household_summary.candidate_borrower_count
                            ),
                            "household_primary_count": household_summary.selected_primary_count,
                            "household_suppressed_count": (
                                household_summary.suppressed_co_owner_count
                            ),
                            "household_household_count": household_summary.household_count,
                            "household_owner_link_count": (
                                household_summary.owner_link_household_count
                            ),
                            "household_mailing_address_count": (
                                household_summary.mailing_address_household_count
                            ),
                            "household_singleton_count": (
                                household_summary.singleton_household_count
                            ),
                            "source_assets": household_summary.source_assets,
                            "campaign_generation_mode": campaign_generation_mode,
                            "generator_label": campaign_generator_label,
                            "variant_provenance": variant_provenance,
                        },
                        action="portfolio.create",
                    ),
                    sort_keys=True,
                ),
            },
        )
        if row is None or not row.get("campaign_id"):
            return self._campaign_create_response_after_insert_conflict(
                lakebase,
                owner_email=owner_email,
                idempotency_key=idempotency_key,
                expected_payload_hash=request_payload_hash,
            )
        return self._campaign_create_response_from_idempotency_row(
            row,
            expected_payload_hash=request_payload_hash,
        )

    def _campaign_create_response_after_insert_conflict(
        self,
        lakebase: Any,
        *,
        owner_email: str,
        idempotency_key: str,
        expected_payload_hash: str,
    ) -> PortfolioCreateResponse:
        params = {"owner_email": owner_email, "idempotency_key": idempotency_key}
        for attempt in range(_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS):
            if attempt:
                time.sleep(_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S * attempt)
            existing = lakebase.fetchone(self._CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL, params)
            if existing is not None:
                return self._campaign_create_response_from_idempotency_row(
                    existing,
                    expected_payload_hash=expected_payload_hash,
                )
        raise LakebaseError("campaign insert returned no row and idempotency lookup was empty")

    def _campaign_create_response_from_idempotency_row(
        self,
        row: dict[str, Any],
        *,
        expected_payload_hash: str,
    ) -> PortfolioCreateResponse:
        stored_hash = str(row.get("request_payload_hash") or "")
        if stored_hash != expected_payload_hash:
            raise ValueError("Idempotency-Key already belongs to a different campaign payload")
        try:
            response_data = self._json_value(row.get("creation_response"), {})
            if not isinstance(response_data, dict):
                raise LakebaseError("campaign idempotency record is missing its creation response")
            campaign_id = str(row.get("campaign_id") or "")
            if not campaign_id:
                raise LakebaseError("campaign idempotency record is missing its campaign id")
            household = HouseholdDedupSummary.model_validate(
                response_data.get("household_summary") or {}
            )
            return PortfolioCreateResponse(
                portfolio_id=campaign_id,
                campaign_id=campaign_id,
                name=str(response_data.get("name") or "Portfolio build"),
                marketable_population=int(response_data.get("marketable_population") or 0),
                household_summary=household,
                audit_event_id=str(row["audit_id"]) if row.get("audit_id") else None,
            )
        except LakebaseError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise LakebaseError("campaign idempotency record is invalid") from exc

    @staticmethod
    def _json_value(value: Any, fallback: Any) -> Any:
        return json_value(value, fallback)

    @classmethod
    def _campaign_from_row(cls, row: dict[str, Any]) -> CampaignSummary:
        return campaign_summary_from_row(row)

    def list_campaigns(
        self,
        *,
        owner_email: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> CampaignListResponse:
        rows = _get_lakebase_client().fetchall(
            self._CAMPAIGN_LIST_SQL,
            {"owner_email": owner_email, "status": status, "limit": max(1, min(limit, 200))},
            limit=max(1, min(limit, 200)),
        )
        return CampaignListResponse(campaigns=[self._campaign_from_row(row) for row in rows])

    def get(self, portfolio_id: str) -> dict[str, object]:
        row = _get_lakebase_client().fetchone(
            self._CAMPAIGN_GET_SQL,
            {"campaign_id": portfolio_id},
        )
        if row is None:
            return {}
        return self._campaign_from_row(row).model_dump()

    def patch_status(
        self,
        portfolio_id: str,
        payload: CampaignStatusPatchRequest,
        *,
        actor: str | None = None,
    ) -> CampaignSummary:
        existing = _get_lakebase_client().fetchone(
            self._CAMPAIGN_GET_SQL,
            {"campaign_id": portfolio_id},
        )
        if existing is None:
            raise LakebaseError("campaign status update returned no row")
        actor_email = actor or "unknown"
        current_status = str(existing.get("status") or "")
        transition_evidence = validate_campaign_status_transition(
            payload,
            campaign_id=portfolio_id,
            current_status=current_status,
            actor=actor_email,
        )
        if payload.status in {"pending_review", "approved", "live", "active"}:
            criteria = self._json_value(existing.get("criteria"), {})
            suppression_policy = self._json_value(existing.get("suppression_policy"), {})
            criteria_ok = (
                isinstance(criteria, dict)
                and criteria.get("marketing_eligibility") == "Eligible only"
            )
            policy_ok = isinstance(suppression_policy, dict) and (
                suppression_policy.get("default") == "eligible_only"
                or suppression_policy.get("require_marketing_eligible") is True
                or str(suppression_policy.get("marketing_eligibility") or "")
                .strip()
                .lower()
                .replace(" ", "_")
                == "eligible_only"
            )
            if not (criteria_ok and policy_ok):
                raise ValueError(
                    "campaign cannot advance without an Eligible only contactability policy"
                )
        metadata: dict[str, object] = {
            "status": payload.status,
            "rationale": payload.rationale,
        }
        evidence_ids: list[str] = []
        if transition_evidence is not None:
            metadata.update(
                {
                    "approval_id": transition_evidence.approval_id,
                    "occurred_at": transition_evidence.authorized_at,
                }
            )
            evidence_ids.append(transition_evidence.approval_id)
        row = _get_lakebase_client().fetchone(
            self._CAMPAIGN_PATCH_SQL,
            {
                "campaign_id": portfolio_id,
                "current_status": current_status,
                "status": payload.status,
                "actor": actor_email,
                "request_id": f"campaign-status-{uuid.uuid4()}",
                "correlation_id": get_correlation_id(),
                "evidence_ids": evidence_ids,
                "metadata": json.dumps(
                    build_safe_audit_metadata(
                        metadata,
                        action="campaign.status_update",
                    ),
                    sort_keys=True,
                ),
            },
        )
        if row is None:
            raise LakebaseError("campaign status update returned no row")
        campaign = self._campaign_from_row(row)
        return campaign


def build_preview_predicates(
    criteria: PortfolioCriteria | None,
    *,
    state_sets: dict[str, list[str]],
    product_codes: dict[str, list[str]] = PORTFOLIO_PRODUCT_CODES,
    equity_thresholds: dict[str, int] = PORTFOLIO_EQUITY_THRESHOLDS,
) -> tuple[str, dict[str, Any]]:
    """Convert validated PortfolioCriteria into a warehouse WHERE clause."""
    if criteria is None:
        return "", {}

    clauses: list[str] = []
    params: dict[str, Any] = {}

    states: list[str] = []
    if criteria.states:
        states.extend(criteria.states)
    if criteria.geography:
        key = criteria.geography.lower()
        if key == "all" or (key.startswith("all ") and key in state_sets):
            pass
        else:
            states.extend(state_sets.get(key) or [])
    states = list(dict.fromkeys(states))
    if states:
        placeholders = ", ".join(f":geo_state_{i}" for i in range(len(states)))
        clauses.append(f"state IN ({placeholders})")
        for i, state in enumerate(states):
            params[f"geo_state_{i}"] = state

    if criteria.occupancy == "Owner-occupied":
        clauses.append("is_owner_occupied = TRUE")
    elif criteria.occupancy == "Non-owner-occupied":
        clauses.append("is_owner_occupied = FALSE")

    lien_status = (criteria.lien_status or "").strip().lower()
    if lien_status in {"free & clear", "free and clear"}:
        clauses.append("current_lien_balance = 0")
    elif lien_status in {"open 1st lien", "open first lien"}:
        clauses.append("current_lien_balance > 0")
        clauses.append("COALESCE(second_pos_amount, 0) = 0")
    elif lien_status in {"open heloc", "open 2nd lien / heloc", "multiple liens"}:
        clauses.append("COALESCE(second_pos_amount, 0) > 0")

    owner_link = (criteria.owner_link or "").strip().lower()
    if owner_link == "single-property owner":
        clauses.append("COALESCE(related_property_count, 1) <= 1")
    elif owner_link == "multi-property (2-4)":
        clauses.append("COALESCE(related_property_count, 1) BETWEEN 2 AND 4")
    elif owner_link == "portfolio investor (5+)":
        clauses.append("COALESCE(related_property_count, 1) >= 5")

    purchase_intent = (criteria.purchase_intent or "").strip().lower()
    if purchase_intent == "listed for sale":
        clauses.append("listed_for_sale = TRUE")
    elif purchase_intent == "heloc intent":
        clauses.append("has_heloc_propensity_trigger = TRUE")
    elif purchase_intent == "both":
        clauses.append("listed_for_sale = TRUE")
        clauses.append("has_heloc_propensity_trigger = TRUE")

    relationship = (criteria.lender_relationship or "").strip().lower()
    if relationship == "current customer":
        clauses.append("is_current_customer = TRUE")
    elif relationship == "former customer":
        clauses.append("is_former_customer = TRUE")
    elif relationship in {"competitor customer", "competitor"}:
        clauses.append("is_competitor_lien = TRUE")

    target_lender_ref = (criteria.target_lender_ref or "").strip()
    if target_lender_ref and target_lender_ref.lower() != "all":
        clauses.append("current_lender_ref = :target_lender_ref")
        params["target_lender_ref"] = target_lender_ref

    if criteria.product and criteria.product != "All products":
        codes = product_codes.get(criteria.product.lower())
        if codes:
            placeholders = ", ".join(f":product_{i}" for i in range(len(codes)))
            clauses.append(f"recommended_offer_code IN ({placeholders})")
            for i, code in enumerate(codes):
                params[f"product_{i}"] = code

    # S1.6 loan product-type dimension. "Unknown" matches the NULL bucket
    # (missing Cotality loan type code); named labels match the frozen
    # fn_loan_product_type vocabulary exactly.
    loan_product = (criteria.loan_product or "").strip()
    if loan_product and loan_product != "All loan products":
        if loan_product == "Unknown":
            clauses.append("loan_product_type IS NULL")
        else:
            clauses.append("loan_product_type = :loan_product_type")
            params["loan_product_type"] = loan_product.lower()

    # S1.6 origination-channel dimension. "Unknown" matches borrowers with no
    # funded first-party application (NULL); named labels map to the governed
    # LOS feed vocabulary.
    origination_channel = (criteria.origination_channel or "").strip()
    if origination_channel and origination_channel != "All channels":
        if origination_channel == "Unknown":
            clauses.append("origination_channel IS NULL")
        else:
            clauses.append("origination_channel = :origination_channel")
            params["origination_channel"] = origination_channel.lower().replace(" ", "_")

    equity_floor: float | int | None = None
    if criteria.min_equity_pct is not None:
        equity_floor = criteria.min_equity_pct
    elif criteria.min_equity_pct_label:
        equity_floor = equity_thresholds.get(criteria.min_equity_pct_label)
    if equity_floor is not None and equity_floor > 0:
        clauses.append("equity_pct >= :equity_floor")
        params["equity_floor"] = equity_floor

    # S1.4: contactability predicates come from the single
    # EligibilityService module so a consent-source swap cannot fork the
    # set-based enforcement semantics from the row-level ones.
    marketing_eligibility = (criteria.marketing_eligibility or "").strip()
    if marketing_eligibility == "Eligible only":
        clauses.append(eligible_sql_predicate())
    elif marketing_eligibility == "Suppressed only":
        clauses.append(suppressed_sql_predicate())

    consent_status = (criteria.consent_status or "").strip()
    if consent_status == "Opt-in":
        clauses.append("consent_status = 'opt_in'")
    elif consent_status == "Opt-out":
        clauses.append("consent_status = 'opt_out'")
    elif consent_status == "Unknown":
        clauses.append("consent_status = 'unknown'")

    recency = (criteria.recency or "").strip()
    recency_days = {
        "Untouched 30d": 30,
        "Untouched 60d": 60,
        "Untouched 90d": 90,
    }.get(recency)
    if recency_days:
        clauses.append(
            f"(last_touch_at IS NULL OR last_touch_at < CURRENT_TIMESTAMP() - INTERVAL {recency_days} DAYS)"
        )

    if not clauses:
        return "", {}
    return "WHERE " + " AND ".join(clauses), params


def build_kpi_trend(points: list[tuple[str, float]]) -> KpiTrend:
    """Compute KpiTrend from oldest-first (date label, value) points."""
    notes: list[str] = []
    original_start = points[0][0] if points else None
    while len(points) > 1 and points[0][1] == 0 and any(p[1] != 0 for p in points[1:]):
        points = points[1:]
    if original_start and points and points[0][0] != original_start:
        notes.append(
            f"Comparison starts on {points[0][0]} because earlier snapshots predate this metric."
        )
    series = [value for _, value in points]
    comparison_label = f"vs {points[0][0]}" if len(points) >= 2 else None
    if len(series) < 2 or series[0] == 0:
        return KpiTrend(
            series=series,
            delta_pct=None,
            direction="flat",
            comparison_label=comparison_label,
            note=" ".join(notes) or None,
        )
    for index in range(1, len(points)):
        previous = points[index - 1][1]
        current = points[index][1]
        if previous == 0:
            continue
        step_pct = abs((current - previous) / previous) * 100.0
        if step_pct >= 8.0:
            notes.append(
                f"Trend includes a coverage or rules update on {points[index][0]}; counts remain source-backed and the comparison is shown for context."
            )
            break
    delta_pct = ((series[-1] - series[0]) / series[0]) * 100.0
    direction = "up" if delta_pct > 0.5 else "down" if delta_pct < -0.5 else "flat"
    return KpiTrend(
        series=series,
        delta_pct=round(delta_pct, 1),
        direction=direction,
        comparison_label=comparison_label,
        note=" ".join(notes) or None,
    )


def coerce_utc_datetime(value: Any) -> datetime | None:
    """Normalise warehouse timestamp-like values into tz-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    try:
        raw = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def preview_cache_key(prefix: str, where_clause: str, params: dict[str, Any]) -> str:
    """Build a deterministic bounded cache key for preview results."""
    canonical = json.dumps(
        {"where": where_clause, "params": params},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]  # noqa: S324
    return f"{prefix}:{digest}"


def json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def household_dedup_config_from_value(value: Any) -> HouseholdDedupConfig:
    if not isinstance(value, dict):
        return HouseholdDedupConfig()
    return HouseholdDedupConfig.model_validate(value)


def household_dedup_summary_from_value(value: Any) -> HouseholdDedupSummary:
    if not isinstance(value, dict):
        return HouseholdDedupSummary()
    return HouseholdDedupSummary.model_validate(value)


def campaign_summary_from_row(row: dict[str, Any]) -> CampaignSummary:
    criteria = json_value(row.get("criteria"), {})
    suppression_policy = json_value(row.get("suppression_policy"), {})
    message_variants = json_value(row.get("message_variants"), [])
    channel_cascade = json_value(row.get("channel_cascade"), [])
    send_window = json_value(row.get("send_window"), {})
    holdout = json_value(row.get("holdout"), None)
    roi_assumptions = json_value(row.get("roi_assumptions"), None)
    household_dedup = json_value(row.get("household_dedup"), {})
    household_summary = json_value(row.get("household_summary"), {})
    return CampaignSummary(
        campaign_id=str(row.get("campaign_id")),
        name=str(row.get("name") or "Campaign"),
        owner_email=str(row.get("owner_email") or "unknown"),
        status=str(row.get("status") or "draft"),  # type: ignore[arg-type]
        criteria=criteria if isinstance(criteria, dict) else {},
        suppression_policy=suppression_policy if isinstance(suppression_policy, dict) else {},
        message_variants=message_variants if isinstance(message_variants, list) else [],
        channel_cascade=channel_cascade if isinstance(channel_cascade, list) else [],
        send_window=send_window if isinstance(send_window, dict) else {},
        holdout=holdout if isinstance(holdout, dict) or holdout is None else None,
        roi_assumptions=(
            roi_assumptions
            if isinstance(roi_assumptions, dict) or roi_assumptions is None
            else None
        ),
        household_dedup=household_dedup_config_from_value(household_dedup),
        household_summary=household_dedup_summary_from_value(household_summary),
        created_at=coerce_utc_datetime(row.get("created_at")),
        updated_at=coerce_utc_datetime(row.get("updated_at")),
    )
