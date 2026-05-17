"""Pure Portfolio repository helpers for the Databricks implementation."""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.schemas.portfolio import (
    CampaignListResponse,
    CampaignStatusPatchRequest,
    CampaignSummary,
    KpiTrend,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioCriteria,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.lakebase import (
    LakebaseError,
)
from backend.services.lakebase import (
    get_lakebase_client as _get_lakebase_client_default,
)
from backend.services.observability import emit, get_correlation_id
from backend.services.resilience import TTLCache

log = logging.getLogger(__name__)


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

    _PREVIEW_SQL_TEMPLATE = (
        "SELECT "
        "  COUNT(*)                                                              AS marketable_population, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END)                         AS high_intent_leads, "
        "  SUM(CASE WHEN opportunity_score >= 75 THEN 1 ELSE 0 END)              AS top_tier_opportunities, "
        "  SUM(CASE WHEN recommended_offer_code <> 'nurture' THEN 1 ELSE 0 END)  AS offers_recommended, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)                            AS avg_score "
        f"FROM {qualify('gold', 'borrower_360')} "
        "{where}"
    )

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
        roi_assumptions, updated_at
      )
      VALUES (
        %(name)s, %(owner_email)s, 'draft', %(criteria)s::jsonb,
        %(suppression_policy)s::jsonb, %(message_variants)s::jsonb,
        %(channel_cascade)s::jsonb, %(send_window)s::jsonb,
        %(holdout)s::jsonb, %(roi_assumptions)s::jsonb, now()
      )
      RETURNING campaign_id
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
    )
    SELECT
      inserted_campaign.campaign_id,
      inserted_audit.audit_id
    FROM inserted_campaign
    LEFT JOIN inserted_audit ON TRUE
    """

    _CAMPAIGN_VARIANT_UPSERT_SQL = """
    INSERT INTO mip_app.campaign_message_variants (
      campaign_id, variant_name, channel, subject, body, weight_pct
    ) VALUES (
      %(campaign_id)s, %(variant_name)s, %(channel)s, %(subject)s, %(body)s, %(weight_pct)s
    )
    ON CONFLICT (campaign_id, variant_name, channel)
    DO UPDATE SET
      subject = EXCLUDED.subject,
      body = EXCLUDED.body,
      weight_pct = EXCLUDED.weight_pct
    """

    _CAMPAIGN_LIST_SQL = """
    SELECT campaign_id::text, name, owner_email, status, criteria,
           suppression_policy, message_variants, channel_cascade, send_window,
           holdout, roi_assumptions, created_at, updated_at
    FROM mip_app.campaigns
    WHERE (%(owner_email)s::text IS NULL OR owner_email = %(owner_email)s::text)
      AND (%(status)s::text IS NULL OR status = %(status)s::text)
    ORDER BY updated_at DESC, created_at DESC
    LIMIT %(limit)s
    """

    _CAMPAIGN_GET_SQL = """
    SELECT campaign_id::text, name, owner_email, status, criteria,
           suppression_policy, message_variants, channel_cascade, send_window,
           holdout, roi_assumptions, created_at, updated_at
    FROM mip_app.campaigns
    WHERE campaign_id = %(campaign_id)s::uuid
    LIMIT 1
    """

    _CAMPAIGN_PATCH_SQL = """
    WITH updated_campaign AS (
      UPDATE mip_app.campaigns
      SET status = %(status)s, updated_at = now()
      WHERE campaign_id = %(campaign_id)s::uuid
      RETURNING campaign_id::text, name, owner_email, status, criteria,
                suppression_policy, message_variants, channel_cascade, send_window,
                holdout, roi_assumptions, created_at, updated_at
    ),
    inserted_audit AS (
      INSERT INTO mip_app.action_audit (
        event_type, actor_email, entity_type, entity_id,
        correlation_id, evidence_ids, metadata
      )
      SELECT
        'CAMPAIGN_STATUS_UPDATE',
        %(actor)s,
        'campaign',
        updated_campaign.campaign_id,
        %(correlation_id)s,
        ARRAY[]::TEXT[],
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
                avg_score=(int(row["avg_score"]) if row.get("avg_score") is not None else None),
                approved_count=(
                    int(latest["approved_count"])
                    if not where_clause and latest.get("approved_count") is not None
                    else None
                ),
                in_outreach_count=(
                    int(latest["in_outreach_count"])
                    if not where_clause and latest.get("in_outreach_count") is not None
                    else None
                ),
                data_refreshed_at=self._coerce_datetime(latest.get("snapshot_at")),
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

    def create(
        self,
        payload: PortfolioCreateRequest,
        *,
        actor: str | None = None,
    ) -> PortfolioCreateResponse:
        preview = self.preview(PortfolioPreviewRequest(criteria=payload.criteria))
        row = _get_lakebase_client().fetchone(
            self._CAMPAIGN_INSERT_SQL,
            {
                "name": payload.name,
                "owner_email": actor or "unknown",
                "criteria": json.dumps(payload.criteria.model_dump(exclude_none=True)),
                "suppression_policy": json.dumps(payload.suppression_policy, sort_keys=True),
                "message_variants": json.dumps(payload.message_variants, sort_keys=True),
                "channel_cascade": json.dumps(payload.channel_cascade, sort_keys=True),
                "send_window": json.dumps(payload.send_window, sort_keys=True),
                "holdout": json.dumps(payload.holdout, sort_keys=True)
                if payload.holdout is not None
                else "null",
                "roi_assumptions": json.dumps(payload.roi_assumptions, sort_keys=True)
                if payload.roi_assumptions is not None
                else "null",
                "request_id": f"portfolio-create-{uuid.uuid4()}",
                "correlation_id": get_correlation_id(),
                "metadata": json.dumps(
                    {
                        "source": "portfolio_builder",
                        "criteria": payload.criteria.model_dump(exclude_none=True),
                        "suppression_policy": payload.suppression_policy,
                        "channel_cascade": payload.channel_cascade,
                        "send_window": payload.send_window,
                        "holdout": payload.holdout,
                        "roi_assumptions": payload.roi_assumptions,
                        "marketable_population": preview.marketable_population,
                    },
                    sort_keys=True,
                ),
            },
        )
        if row is None or not row.get("campaign_id"):
            raise LakebaseError("campaign insert returned no row")
        campaign_id = str(row["campaign_id"])
        variant_rows = [
            {
                "campaign_id": campaign_id,
                "variant_name": str(
                    variant.get("variant_name") or variant.get("name") or "default"
                )[:64],
                "channel": str(variant.get("channel") or "email"),
                "subject": variant.get("subject"),
                "body": str(variant.get("body") or ""),
                "weight_pct": variant.get("weight_pct"),
            }
            for variant in payload.message_variants
            if str(variant.get("body") or "").strip()
        ]
        if variant_rows:
            _get_lakebase_client().executemany(self._CAMPAIGN_VARIANT_UPSERT_SQL, variant_rows)
        return PortfolioCreateResponse(
            portfolio_id=campaign_id,
            campaign_id=campaign_id,
            name=payload.name,
            marketable_population=preview.marketable_population,
            audit_event_id=str(row["audit_id"]) if row.get("audit_id") else None,
        )

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
        row = _get_lakebase_client().fetchone(
            self._CAMPAIGN_PATCH_SQL,
            {
                "campaign_id": portfolio_id,
                "status": payload.status,
                "actor": actor or "unknown",
                "correlation_id": get_correlation_id(),
                "metadata": json.dumps(
                    {
                        "action": "campaign.status_update",
                        "status": payload.status,
                        "rationale": payload.rationale,
                    },
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
    elif purchase_intent == "recent permit activity":
        clauses.append("has_permit = TRUE")
    elif purchase_intent == "both":
        clauses.append("listed_for_sale = TRUE")
        clauses.append("has_permit = TRUE")

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

    equity_floor: int | None = None
    if criteria.min_equity_pct is not None:
        equity_floor = int(criteria.min_equity_pct)
    elif criteria.min_equity_pct_label:
        equity_floor = equity_thresholds.get(criteria.min_equity_pct_label)
    if equity_floor is not None and equity_floor > 0:
        clauses.append("equity_pct >= :equity_floor")
        params["equity_floor"] = equity_floor

    marketing_eligibility = (criteria.marketing_eligibility or "").strip()
    if marketing_eligibility == "Eligible only":
        clauses.append("marketing_eligible = TRUE")
    elif marketing_eligibility == "Suppressed only":
        clauses.append("marketing_eligible = FALSE")

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
                f"Material step change on {points[index][0]}; verify rules or refresh context before presenting this as market movement."
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


def campaign_summary_from_row(row: dict[str, Any]) -> CampaignSummary:
    criteria = json_value(row.get("criteria"), {})
    suppression_policy = json_value(row.get("suppression_policy"), {})
    message_variants = json_value(row.get("message_variants"), [])
    channel_cascade = json_value(row.get("channel_cascade"), [])
    send_window = json_value(row.get("send_window"), {})
    holdout = json_value(row.get("holdout"), None)
    roi_assumptions = json_value(row.get("roi_assumptions"), None)
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
        created_at=coerce_utc_datetime(row.get("created_at")),
        updated_at=coerce_utc_datetime(row.get("updated_at")),
    )
