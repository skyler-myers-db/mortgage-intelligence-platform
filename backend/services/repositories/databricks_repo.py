"""Live Databricks-backed repository implementations.

Each class below implements one Protocol from
``backend.services.repositories.protocols`` against the ``mip.gold.*``
tables materialised by the Slice-3 Lakeflow pipeline. The constructor
takes a ``DatabricksSqlClient`` so the same pool/keep-alive is reused
across every repository in the process.

PII posture (non-negotiable, enforced by ``backend/services/pii_redaction``):

- No raw owner names, mailing addresses, or street addresses leave
  these classes.
- The ``clip`` -> ``clip_id`` and ``delta_vs_prior`` -> ``delta`` renames
  documented in ``docs/data-contract-module0.md`` §12 happen here, at
  the repository boundary, not in the SQL view and not in the router.
- Every ``SELECT`` lists columns explicitly -- never ``SELECT *`` -- so
  a future gold schema expansion cannot silently surface new PII.
- Every WHERE clause uses named / positional parameters, never string
  interpolation. A caller-supplied borrower id is always bound.

Evidence ordering: ``ORDER BY signal_rank ASC`` per the data-contract
§3.4. Chronological order is a display concern handled in the UI; the
canonical order for the evidence drawer is the gold-defined priority.

Slice-7: ``DatabricksGenieRepository`` flips ``/api/genie`` onto the
real Mortgage Lead Intelligence Genie space. A deterministic safe
corpus (parsed from ``genie/sample_questions.md`` + the curated catalog
in ``backend.services.genie_answers``) only fires when the ``genie``
circuit breaker is OPEN -- otherwise every answer comes from the live
space. An open breaker on an unknown question returns a honest
"warming up" message rather than fabricating data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from backend.schemas.common import EvidenceEvent
from backend.schemas.geo import (
    CountyRollup,
    CountyRollupResponse,
    StateRollup,
    StateRollupResponse,
    ZipRollup,
    ZipRollupResponse,
)
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import (
    KpiTrend,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioCriteria,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.schemas.why import WhyPanel, WhyPanelSource
from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import (
    GenieActionSuggestion,
    GenieDataFreshness,
    GenieMessageResponse,
    GenieProof,
    GenieVisualizationSpec,
)
from backend.services.genie_answers import (
    respond as genie_catalog_respond,
)
from backend.services.genie_client import (
    GenieClientError,
    GenieResponse,
    ResilientGenieClient,
)
from backend.services.pii_redaction import (
    redact_borrower_row,
    redact_evidence_row,
    redact_lead_row,
)
from backend.services.resilience import DependencyDownError, TTLCache
from backend.services.scoring import (
    NBO_PRODUCT_LABELS,
    in_the_money,
    source_display_label,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column projections -- one source of truth per query. Explicitly enumerated
# so ``SELECT *`` drift cannot leak PII.
# ---------------------------------------------------------------------------

_BORROWER_360_COLUMNS: str = (
    "clip, borrower_id, owner_name_hash, city, state, zip, segment_codes, "
    "equity_estimate, equity_pct, rate_spread_bps, market_rate_fraction, "
    "opportunity_score, confidence, recommended_offer_code, recommended_offer, "
    "why_now, evidence_ids, approval_status, owner_link_id, subject_property, "
    "avm_value, current_lien_balance, current_rate, ltv, related_property_count, "
    "min_spread_bps_applied, min_equity_pct_applied, in_the_money"
)

# Slice13-accuracy perf: mip.gold.borrower_dossier is a pre-joined superset
# of borrower_360 + top-20 evidence events per CLIP. /api/borrowers/{id}
# reads one indexed row here instead of fanning out to two warehouse
# statements (borrower_360 + evidence_events). Keep this list in sync with
# sql/transformations/gold_borrower_dossier.sql's SELECT and with the DDL
# in sql/ddl/003_gold_tables.sql §10.
_BORROWER_DOSSIER_COLUMNS: str = (
    _BORROWER_360_COLUMNS
    + ", trigger_timeline_json, evidence_events, trigger_timeline"
)

_LEAD_POPULATION_COLUMNS: str = (
    # `clip` is projected first so the repository boundary can surface the
    # real Cotality CLIP on LeadSummary (fix for the cross-route CLIP
    # inconsistency blocker 2026-04-22). `redact_lead_row` passes it
    # through under the same key.
    "clip, borrower_id, display_name, city, state, zip, segment_codes, "
    "equity_estimate, rate_spread_bps, opportunity_score, confidence, "
    "recommended_offer, why_now, evidence_ids, approval_status, "
    # Secondary-filter fields (2026-04-23) -- carried through from
    # gold.borrower_360 into gold.lead_population so /segment-intelligence
    # can run real client-side predicates against occupancy, owner-link
    # (related properties), lien state, and purchase intent. Ordering
    # matches the gold DDL + CTAS (see sql/ddl/gold_lead_population.sql).
    "is_owner_occupied, is_investor, related_property_count, "
    "current_lien_balance, second_pos_amount, has_permit, listed_for_sale"
)

_EVIDENCE_COLUMNS: str = (
    "evidence_id, source_product, source_table, signal_type, signal_value, "
    "display_text, confidence, `timestamp`, signal_rank"
)

_SEGMENT_COLUMNS: str = (
    "segment_code, name, count, delta_vs_prior, avg_score, description, color"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timeline(raw: Any) -> list[EvidenceEvent]:
    """Parse the ``trigger_timeline_json`` ARRAY<STRUCT> payload.

    The warehouse emits it as a JSON string via ``to_json(collect_list
    (struct(...)))``. Upstream tests already pin the struct field set;
    we map 1:1 into ``EvidenceEvent``. Unknown / empty input -> [].
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, str):
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        return []
    events: list[EvidenceEvent] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        events.append(
            EvidenceEvent(
                evidence_id=r.get("evidence_id") or "",
                source_product=r.get("source_product") or "",
                source_table=r.get("source_table") or "",
                signal_type=r.get("signal_type") or "",
                signal_value=r.get("signal_value") or "",
                display_text=r.get("display_text") or "",
                confidence=float(r.get("confidence") or 0.0),
                timestamp=str(r.get("timestamp") or ""),
            )
        )
    return events


def _redact_evidence_list(raw: Any) -> list[EvidenceEvent]:
    """Redact + hydrate an ARRAY<STRUCT<...>> evidence column.

    The dossier carries ``evidence_events`` + ``trigger_timeline`` as
    pre-joined struct arrays. ``databricks_sql._coerce`` decodes them
    from the JSON_ARRAY disposition into Python ``list[dict]``. Each
    struct dict needs the same redaction pipeline the standalone
    ``/api/borrowers/{id}/evidence`` path runs so lender strings stay
    generalised.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        # Defensive fallback: if a future disposition change emits the
        # array as JSON text, still parse it.
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    events: list[EvidenceEvent] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        events.append(EvidenceEvent(**redact_evidence_row(r)))
    return events


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


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
    # Hole-finder #20: the "all N states" option used to hardcode
    # ["IL","CA","FL","TX","WA","CO"]. That literal was one of 5 copies
    # of the footprint that silently broke for tenants with a different
    # state mix. It's now computed from `mip.ref.state_footprint` via
    # `StateFootprintResolver` (see `_state_sets()` below). MSA
    # groupings ("Chicago MSA", "CA + FL + TX", "IL + CA + WA") remain
    # hardcoded here — they're lender-specific marketing labels and
    # orthogonal to the per-state list. A lender whose footprint diverges
    # would re-label them in the UI; do not over-build until that happens.
    #
    # Per-state-name entries ("florida", "california", ...) are injected
    # at lookup time from the live footprint via `_state_sets()` — see
    # that method for the merge rule. Keys are always lowercased so
    # callers can do a case-insensitive match.
    _STATIC_STATE_SETS: dict[str, list[str]] = {
        "chicago msa":     ["IL"],
        "texas":           ["TX"],
        "ca + fl + tx":    ["CA", "FL", "TX"],
        "il + ca + wa":    ["IL", "CA", "WA"],
    }

    @classmethod
    def _state_sets(cls) -> dict[str, list[str]]:
        """Build the active _STATE_SETS dict, injecting the live footprint.

        Merge order (later keys override earlier — intentional so a
        tenant-level override in MSA combos could in principle beat the
        per-state entry of the same name, though none currently collide):

          1. MSA / multi-state combos from ``_STATIC_STATE_SETS``.
          2. Per-state-name entries from ``state_name_to_codes()``
             (fixes the bug where "Florida" in the GEO dropdown
             returned the whole population because the key was missing).
          3. ``all N states`` computed from the footprint.
          4. ``all 6 states`` legacy alias so a deep-linked URL from the
             old UI still parses.

        Called once per `_build_preview_predicates` invocation; the
        resolver caches the UC result for 300s so this is cheap.
        """
        from backend.services.state_footprint import get_state_footprint_resolver

        resolver = get_state_footprint_resolver()
        footprint_codes = resolver.state_codes()
        state_name_map = resolver.state_name_to_codes()
        all_key = f"all {len(footprint_codes)} states"
        return {
            **cls._STATIC_STATE_SETS,
            **state_name_map,
            all_key: list(footprint_codes),
            # Keep the legacy "all 6 states" key active while the frontend
            # catches up, so a deep-linked URL from the old UI still parses.
            # Maps to whatever the current footprint is — a tenant with 3
            # states who opens an old bookmark sees "all 3 states"
            # semantics under the legacy label.
            "all 6 states": list(footprint_codes),
        }

    # Canonical `recommended_offer_code` values emitted by fn_next_best_offer
    # (see sql/uc_functions/fn_next_best_offer.sql). Keep in sync.
    _PRODUCT_CODES: dict[str, list[str]] = {
        "refi":       ["refi"],
        "heloc":      ["heloc"],
        "cash-out":   ["cashout"],
        "purchase":   ["purchase"],
        "retention":  ["retention"],
    }

    _EQUITY_THRESHOLDS: dict[str, int] = {
        "≥ 15%": 15,
        "≥ 25%": 25,
        "≥ 40%": 40,
    }

    @classmethod
    def _build_preview_predicates(
        cls, criteria: PortfolioCriteria | None,
    ) -> tuple[str, dict[str, Any]]:
        """Convert validated PortfolioCriteria into a (WHERE clause, params)
        pair. Returns `("", {})` when no predicates apply so the caller can
        run the criteria-free SELECT."""
        if criteria is None:
            return "", {}

        clauses: list[str] = []
        params: dict[str, Any] = {}

        # Geography — map display label to a list of state codes. The
        # "all N states" option is computed from mip.ref.state_footprint
        # (hole-finder #20); MSA groupings stay hardcoded for now.
        if criteria.geography:
            key = criteria.geography.lower()
            states = cls._state_sets().get(key)
            if states:
                placeholders = ", ".join(f":geo_state_{i}" for i in range(len(states)))
                clauses.append(f"state IN ({placeholders})")
                for i, s in enumerate(states):
                    params[f"geo_state_{i}"] = s

        # Occupancy.
        if criteria.occupancy == "Owner-occupied":
            clauses.append("is_owner_occupied = TRUE")
        elif criteria.occupancy == "Non-owner-occupied":
            clauses.append("is_owner_occupied = FALSE")

        # Lien status. We can cleanly discriminate "Free & clear"; the other
        # values map to "has an open lien" until we land richer lien_type
        # columns in gold (flagged in audit as STUB).
        if criteria.lien_status == "Free & clear":
            clauses.append("current_lien_balance = 0")
        elif criteria.lien_status in ("Open 1st lien", "Open HELOC"):
            clauses.append("current_lien_balance > 0")

        # Lender relationship.
        if criteria.lender_relationship == "Current customer":
            clauses.append("is_current_customer = TRUE")
        elif criteria.lender_relationship == "Former customer":
            clauses.append("is_current_customer = FALSE AND is_competitor_lien = FALSE")
        elif criteria.lender_relationship == "Competitor customer":
            clauses.append("is_competitor_lien = TRUE")

        # Product. "All products" / missing = no predicate.
        if criteria.product and criteria.product != "All products":
            codes = cls._PRODUCT_CODES.get(criteria.product.lower())
            if codes:
                placeholders = ", ".join(f":product_{i}" for i in range(len(codes)))
                clauses.append(f"recommended_offer_code IN ({placeholders})")
                for i, code in enumerate(codes):
                    params[f"product_{i}"] = code

        # Equity threshold. Prefer explicit float; fall back to the label.
        equity_floor: int | None = None
        if criteria.min_equity_pct is not None:
            equity_floor = int(criteria.min_equity_pct)
        elif criteria.min_equity_pct_label:
            equity_floor = cls._EQUITY_THRESHOLDS.get(criteria.min_equity_pct_label)
        if equity_floor is not None and equity_floor > 0:
            clauses.append("equity_pct >= :equity_floor")
            params["equity_floor"] = equity_floor

        if not clauses:
            return "", {}
        return "WHERE " + " AND ".join(clauses), params

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
        while len(points) > 1 and points[0][1] == 0 and any(p[1] != 0 for p in points[1:]):
            points = points[1:]
        series = [value for _, value in points]
        comparison_label = f"vs {points[0][0]}" if len(points) >= 2 else None
        if len(series) < 2 or series[0] == 0:
            return KpiTrend(
                series=series,
                delta_pct=None,
                direction="flat",
                comparison_label=comparison_label,
            )
        delta_pct = ((series[-1] - series[0]) / series[0]) * 100.0
        direction = "up" if delta_pct > 0.5 else "down" if delta_pct < -0.5 else "flat"
        return KpiTrend(
            series=series,
            delta_pct=round(delta_pct, 1),
            direction=direction,
            comparison_label=comparison_label,
        )

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
            log.warning("portfolio funnel snapshot query failed: %s", exc)
            return {}, {}, "unavailable", "Trend snapshots are unavailable; headline KPIs still come from live borrower_360."
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
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        # Defensive: a future connector change may emit an ISO string.
        try:
            # Accept the "...Z" suffix that some drivers emit.
            raw = str(value).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return None

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
        canonical = json.dumps(
            {"where": where_clause, "params": params},
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]  # noqa: S324 -- not a secret
        return f"{cls._PREVIEW_CACHE_KEY}:{digest}"

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
        cache_key = (
            self._preview_cache_key(where_clause, params)
            if caching_enabled
            else ""
        )
        if caching_enabled:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        sql = self._PREVIEW_SQL_TEMPLATE.format(where=where_clause)
        row = self._client.execute_one(sql, params) or {}
        trends, latest, trend_status, trend_note = self._load_funnel(
            include_trends=not bool(where_clause),
        )
        preview = PortfolioPreview(
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
            avg_score=(
                int(row["avg_score"])
                if row.get("avg_score") is not None
                else None
            ),
            approved_count=(
                int(latest["approved_count"])
                if latest.get("approved_count") is not None
                else None
            ),
            in_outreach_count=(
                int(latest["in_outreach_count"])
                if latest.get("in_outreach_count") is not None
                else None
            ),
            data_refreshed_at=self._coerce_datetime(latest.get("snapshot_at")),
            trends=trends,
            trend_status=trend_status,
            trend_note=trend_note,
            day_zero=self._load_day_zero(),
        )
        if caching_enabled:
            self._cache.set(cache_key, preview, self._cache_ttl_s)
        return preview

    def create(self, payload: PortfolioCreateRequest) -> PortfolioCreateResponse:
        preview = self.preview(None)
        return PortfolioCreateResponse(
            portfolio_id="module0-portfolio",
            name=payload.name,
            marketable_population=preview.marketable_population,
        )

    def get(self, portfolio_id: str) -> dict[str, object]:
        preview = self.preview(None)
        return {
            "portfolio_id": portfolio_id,
            "status": "ready",
            "marketable_population": preview.marketable_population,
        }


class DatabricksSegmentRepository:
    """Segment rollup serves the national ``state='_ALL'`` row.

    Slice-6: wraps the ``list`` read in a short-TTL cache. Segment
    counts are a national aggregate recomputed by the gold pipeline on
    a fixed cadence; a 30s stale read is fine and removes a
    per-request warehouse round-trip from the segment-intelligence
    route.
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

    _LIST_SQL = (
        f"SELECT {_SEGMENT_COLUMNS} "
        f"FROM {qualify('gold', 'segment_population')} "
        "WHERE state = '_ALL' "
        "ORDER BY count DESC"
    )

    # Canonical FE display order matching the prototype's seg-grid layout
    # (`design_files/Module 0 Prototype.html` lines 1546–1551 + the gold
    # `meta` VALUES table in `sql/transformations/gold_segment_population.sql`).
    # Used to re-sort the SQL result after fetch so that pending-source
    # segments (count=0 because of an upstream Cotality data dependency)
    # are NOT buried at the end of the list by `ORDER BY count DESC`.
    # Prototype-parity-audit P0-2 (2026-05-04): the gold rollup now always
    # emits 6 rows; this constant ensures the FE always renders them in the
    # same predictable order regardless of cardinality.
    _CANONICAL_ORDER: tuple[str, ...] = (
        "itm",
        "listed",
        "permit",
        "investor",
        "equity",
        "retention",
    )

    def _list_cache_key(self, portfolio_id: str | None) -> str:
        return f"segments.list.{portfolio_id or '_ALL'}"

    def list(self, portfolio_id: str | None) -> list[SegmentSummary]:
        key = self._list_cache_key(portfolio_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        rows = self._client.execute(self._LIST_SQL)
        segments = [
            SegmentSummary(
                code=row["segment_code"],
                name=row["name"],
                count=int(row.get("count") or 0),
                # Rename at boundary: gold column is delta_vs_prior.
                delta=row.get("delta_vs_prior") or "+0%",
                avg_score=int(row.get("avg_score") or 0),
                description=row.get("description") or "",
                color=row.get("color") or "#999999",
            )
            for row in rows
        ]
        # Re-sort into the canonical FE display order. Without this the SQL's
        # `ORDER BY count DESC` would push pending-source segments (count=0,
        # currently `listed` and `permit`) to the end of the list, which
        # defeats the "you always see 6 segments" UX promise. Unknown segment
        # codes (a future addition that landed in gold but not yet in this
        # constant) are appended after the canonical set in their original
        # SQL order so they're still visible.
        order_index: dict[str, int] = {
            code: i for i, code in enumerate(self._CANONICAL_ORDER)
        }
        unknown_tail_index = len(self._CANONICAL_ORDER)
        segments.sort(
            key=lambda s: order_index.get(s.code, unknown_tail_index)
        )
        self._cache.set(key, segments, self._cache_ttl_s)
        return segments


class DatabricksLeadRepository:
    """Ranked leads from ``gold.lead_population``.

    The per-request ``limit`` is bounded by ``MAX_LIMIT`` (5000) so a
    pathological caller can't pull the whole gold table onto one page.
    Default is 500 — the size a VP of Lending can scroll in one sitting
    and the threshold the LeadTable footer renders "Showing N of M" at.
    Hole-finder round 2 #24, 2026-04-23.
    """

    DEFAULT_LIMIT: int = 500
    MAX_LIMIT: int = 5000

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _LIST_BASE_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_COLUMNS} "
        f"FROM {qualify('gold', 'lead_population')} "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT {limit}"
    )

    _LIST_BY_SEGMENT_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_COLUMNS} "
        f"FROM {qualify('gold', 'lead_population')} "
        "WHERE array_contains(segment_codes, :segment) "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT {limit}"
    )

    _LIST_FILTERED_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_COLUMNS} "
        f"FROM {qualify('gold', 'lead_population')} "
        "WHERE {segment_clause} "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT {limit}"
    )

    # 2026-05-04 FIX β: when the caller filters by state and/or zip we
    # bypass lead_population (which has the score >= 50 quality floor
    # baked into its CTAS) and read borrower_360 directly. Rationale:
    # the home-page map ZIP/county/state tooltips report the FULL
    # addressable population per geo (no score filter), and the user
    # expectation is that drilling INTO a geo from the map shows
    # everyone the map counted. Pre-fix: ZIP 33073 showed 19 marketable
    # on the map, then 0 in the Lead Queue because the API returned
    # the top-500 nationally-ranked from lead_population and none of
    # those 500 lived in 33073. Post-fix: the same /api/leads call,
    # narrowed to (state='FL', zip='33073'), runs against borrower_360
    # and returns the 19 borrowers the map promised. ORDER BY score
    # still surfaces the highest-opportunity ones first; LIMIT applies
    # AFTER the geo filter so the cap doesn't pre-truncate.
    _LIST_BY_GEO_SQL_TEMPLATE = (
        # Project the lead_population columns directly off borrower_360.
        # Every column in _LEAD_POPULATION_COLUMNS exists in borrower_360
        # except `display_name` (synthesized in the lead_population CTAS).
        # We re-synthesize it here with the same formula so LeadSummary
        # rows stay shape-compatible whether they came from
        # lead_population or borrower_360.
        "SELECT "
        "  clip, borrower_id, "
        "  CONCAT('Owner ', SUBSTR(owner_name_hash, 1, 8)) AS display_name, "
        "  city, state, zip, segment_codes, "
        "  equity_estimate, rate_spread_bps, opportunity_score, confidence, "
        "  recommended_offer, why_now, evidence_ids, approval_status, "
        "  is_owner_occupied, is_investor, related_property_count, "
        "  current_lien_balance, second_pos_amount, has_permit, listed_for_sale "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE 1=1 {state_clause} {zip_clause} {segment_clause} "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT {limit}"
    )

    def list(
        self,
        segment: str | None,
        portfolio_id: str | None,
        limit: int | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
    ) -> list[LeadSummary]:
        _ = portfolio_id
        bounded = self._bound_limit(limit)
        segment_clause, segment_params = self._segment_filter_clause(
            segment=segment,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
        )

        # FIX β: geo-filtered path bypasses lead_population so the queue
        # row count matches the map tooltip. See the
        # _LIST_BY_GEO_SQL_TEMPLATE docstring above for the full rationale.
        if state or zip_code:
            params: dict[str, object] = dict(segment_params)
            state_clause = ""
            if state:
                state_clause = "AND state = :state"
                params["state"] = state.upper()[:2]
            zip_clause = ""
            if zip_code:
                zip_clause = "AND zip = :zip"
                params["zip"] = zip_code
            geo_segment_clause = f"AND {segment_clause}" if segment_clause else ""
            sql = self._LIST_BY_GEO_SQL_TEMPLATE.format(
                state_clause=state_clause,
                zip_clause=zip_clause,
                segment_clause=geo_segment_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, params)
            return [LeadSummary(**redact_lead_row(r)) for r in rows]

        if segment_clause:
            sql = self._LIST_FILTERED_SQL_TEMPLATE.format(
                segment_clause=segment_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, segment_params)
        else:
            sql = self._LIST_BASE_SQL_TEMPLATE.format(limit=bounded)
            rows = self._client.execute(sql)
        return [LeadSummary(**redact_lead_row(r)) for r in rows]

    @staticmethod
    def _normalise_segment_codes(
        segment: str | None,
        segment_codes: list[str] | None,
    ) -> list[str]:
        raw = segment_codes if segment_codes else ([segment] if segment else [])
        # Preserve caller order for deterministic parameter names while
        # dropping duplicates and blanks.
        out: list[str] = []
        seen: set[str] = set()
        for code in raw:
            normalised = str(code or "").strip()
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            out.append(normalised)
        return out

    @classmethod
    def _segment_filter_clause(
        cls,
        *,
        segment: str | None,
        segment_codes: list[str] | None,
        segment_mode: str,
    ) -> tuple[str, dict[str, object]]:
        codes = cls._normalise_segment_codes(segment, segment_codes)
        if not codes:
            return "", {}
        if len(codes) == 1:
            return "array_contains(segment_codes, :segment)", {"segment": codes[0]}
        if segment_mode == "all":
            params = {f"segment_{i}": code for i, code in enumerate(codes)}
            clause = " AND ".join(
                f"array_contains(segment_codes, :segment_{i})"
                for i in range(len(codes))
            )
            return clause, params
        return "arrays_overlap(segment_codes, :segment_codes)", {"segment_codes": codes}

    @classmethod
    def _bound_limit(cls, limit: int | None) -> int:
        """Clamp a caller-supplied ``limit`` to [1, MAX_LIMIT].

        ``None`` / 0 / negative values collapse to ``DEFAULT_LIMIT`` so the
        SQL stays a literal integer (no binding for LIMIT) and can't be
        spoofed into pulling the whole table.
        """
        if limit is None or limit <= 0:
            return cls.DEFAULT_LIMIT
        return min(int(limit), cls.MAX_LIMIT)


class DatabricksBorrowerRepository:
    """Borrower-360 + evidence reads from ``gold.borrower_dossier``.

    Slice13-accuracy perf: the dossier table pre-joins ``borrower_360`` +
    the top-20 ``evidence_events`` per CLIP into a single row keyed on
    ``borrower_id`` (Delta liquid cluster). The prior implementation fanned
    two warehouse statements out on a ``ThreadPoolExecutor`` to drop the
    p95 from ~4.6 s to ~3.3 s; folding them into one indexed row read
    collapses the warehouse round-trip count from 2 to 1 and pushes the
    p95 toward the 2-s load-test target.
    """

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _GET_SQL = (
        f"SELECT {_BORROWER_DOSSIER_COLUMNS} "
        f"FROM {qualify('gold', 'borrower_dossier')} "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    # Fallback evidence fetch — preserved so /api/borrowers/{id}/evidence
    # can still serve a borrower whose dossier row was rebuilt between
    # refreshes AND whose evidence_events array is somehow empty (schema
    # drift, upstream outage). The primary path reads from the dossier's
    # evidence array below.
    _EVIDENCE_SQL = (
        f"SELECT {_EVIDENCE_COLUMNS} "
        f"FROM {qualify('gold', 'evidence_events')} "
        "WHERE clip = ("
        f"  SELECT clip FROM {qualify('gold', 'borrower_dossier')} "
        "  WHERE borrower_id = :borrower_id LIMIT 1"
        ") "
        "ORDER BY signal_rank ASC"
    )

    def get(self, borrower_id: str) -> Borrower360 | None:
        # Single-statement indexed lookup on the dossier cluster key
        # (borrower_id). Evidence + trigger timeline are both pre-joined
        # as ARRAY<STRUCT> columns, so no fan-out is needed.
        row = self._client.execute_one(
            self._GET_SQL, {"borrower_id": borrower_id}
        )
        if row is None:
            return None

        redacted = redact_borrower_row(row)

        # Evidence: dossier carries up to 20 rows per CLIP as a parsed
        # ARRAY<STRUCT> (``_coerce`` in databricks_sql.py decodes JSON
        # arrays into list-of-dict). Apply the same redaction pipeline
        # used by the standalone evidence() endpoint so PII posture is
        # preserved.
        raw_evidence = row.get("evidence_events") or []
        evidence_events = _redact_evidence_list(raw_evidence)

        # Trigger timeline: dossier pre-materialised the top-3 as its own
        # ARRAY<STRUCT>. Prefer that; fall back to the JSON string form
        # (old borrower_360 path) then to the top-3 of full-evidence so
        # the dossier stays defensible against an empty-array column
        # (raised by Copilot 2026-04-22 — [:1] under-populated the UI).
        raw_timeline = row.get("trigger_timeline") or []
        timeline_events = _redact_evidence_list(raw_timeline)
        if not timeline_events:
            timeline_events = _parse_timeline(row.get("trigger_timeline_json"))
        if not timeline_events and evidence_events:
            timeline_events = evidence_events[:3]

        # Plain-English "why now" string for the dossier rationale box.
        # Updated 2026-04-22 (fix/copilot-batch-post-merge) to drop
        # rule-engine phrasing like "+246 bps spread (>= 75) AND 79%
        # equity (>= 15%)" in favour of language a VP of Lending or
        # compliance reviewer reads fluidly. The numbers still ground
        # the claim (an approver wants concrete detail) but without bps
        # or ">=" syntax.
        itm_flag = _coerce_bool(row.get("in_the_money"))
        spread_bps = int(row.get("rate_spread_bps") or 0)
        equity_pct = int(row.get("equity_pct") or 0)
        if itm_flag:
            # Translate bps to a qualitative phrase that still carries
            # the magnitude signal. Keeping the literal percentage for
            # equity because LOs / analysts naturally read "79% equity".
            if spread_bps >= 200:
                spread_descriptor = "well above market rates"
            elif spread_bps >= 100:
                spread_descriptor = "meaningfully above market rates"
            else:
                spread_descriptor = "above market rates"
            itm_reason = (
                f"Current rate sits {spread_descriptor} and the home has "
                f"{equity_pct}% equity -- both refinance triggers are met."
            )
        else:
            itm_reason = (
                f"Rate and equity (currently {equity_pct}%) have not yet "
                "cleared the refinance trigger; keep in nurture."
            )

        why_sources = [
            qualify("gold", "fn_rate_spread"),
            qualify("gold", "fn_in_the_money"),
            qualify("gold", "borrower_dossier"),
        ]
        why = WhyPanel(
            rate_spread_bps=spread_bps,
            market_rate=float(row.get("market_rate_fraction") or 0.0),
            equity_pct=equity_pct,
            in_the_money=itm_flag,
            in_the_money_reason=itm_reason,
            min_spread_bps=int(row.get("min_spread_bps_applied") or 75),
            min_equity_pct=int(row.get("min_equity_pct_applied") or 15),
            sources=why_sources,
            source_labels=[
                WhyPanelSource(name=s, display_label=source_display_label(s))
                for s in why_sources
            ],
        )

        # Enrich the redacted projection with the Borrower360 extras.
        # Note: every dependent construction works on the *redacted*
        # dict -- no raw PII is ever composed into the Pydantic object.
        borrower = Borrower360(
            **redacted,
            trigger_timeline=timeline_events,
            evidence_events=evidence_events,
            why_panel=why,
        )
        # Defence in depth: belt-and-suspenders ITM check against the
        # Python primitive so a row where gold drifted from Python
        # can't serialise without being caught in dev.
        _ = in_the_money(
            borrower.rate_spread_bps,
            int(row.get("equity_pct") or 0),
            why.min_spread_bps,
            why.min_equity_pct,
        )
        return borrower

    def evidence(self, borrower_id: str) -> list[EvidenceEvent] | None:
        # Prefer reading from the dossier's pre-joined evidence array —
        # one round-trip instead of two. If the dossier row carries
        # evidence (it almost always will; the CTAS emits an empty
        # ARRAY() only when a CLIP has zero live signals), serve it
        # directly. Otherwise fall back to the standalone gold.evidence_
        # events query so an empty-array dossier row (brand-new CLIP,
        # schema drift) still resolves.
        dossier_row = self._client.execute_one(
            f"SELECT clip, evidence_events FROM {qualify('gold', 'borrower_dossier')} "
            "WHERE borrower_id = :borrower_id LIMIT 1",
            {"borrower_id": borrower_id},
        )
        if dossier_row is None:
            return None  # router 404s for unknown borrower
        raw = dossier_row.get("evidence_events") or []
        if raw:
            return _redact_evidence_list(raw)
        # Dossier row present but evidence empty — fall back to direct
        # evidence_events lookup (belt-and-suspenders for upstream drift).
        rows = self._client.execute(self._EVIDENCE_SQL, {"borrower_id": borrower_id})
        if not rows:
            return []
        return [EvidenceEvent(**redact_evidence_row(r)) for r in rows]


class DatabricksOfferRepository:
    """Offer-input bundle used by the offers router to build a recommendation."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _SQL = (
        "SELECT "
        "  rate_spread_bps, equity_pct, has_permit, listed_for_sale, "
        "  is_investor, is_current_customer, is_competitor_lien, "
        "  recommended_offer_code "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
        row = self._client.execute_one(self._SQL, {"borrower_id": borrower_id})
        if row is None:
            return None
        code = row.get("recommended_offer_code") or "nurture"
        # Defence in depth: contract expects a known code; surface an
        # obvious label if gold drifted.
        if code not in NBO_PRODUCT_LABELS:
            code = "nurture"
        return {
            "rate_spread_bps": int(row.get("rate_spread_bps") or 0),
            "equity_pct": int(row.get("equity_pct") or 0),
            "has_permit": _coerce_bool(row.get("has_permit")),
            "listed_for_sale": _coerce_bool(row.get("listed_for_sale")),
            "is_investor": _coerce_bool(row.get("is_investor")),
            "is_current_customer": _coerce_bool(row.get("is_current_customer")),
            "is_competitor_lien": _coerce_bool(row.get("is_competitor_lien")),
            "offer_code": code,
        }


class DatabricksOutreachRepository:
    """Borrower lookup for outreach draft composition -- same projection
    as ``BorrowerRepository.get`` but carved separately so outreach can
    grow draft-specific columns (opt-out, channel-preference) without
    widening the borrower surface.
    """

    def __init__(self, client: DatabricksSqlClient, borrower_repo: DatabricksBorrowerRepository) -> None:
        self._client = client
        self._borrower_repo = borrower_repo

    def find_borrower(self, borrower_id: str) -> Borrower360 | None:
        return self._borrower_repo.get(borrower_id)


class DatabricksGeoRepository:
    """Geography rollups for the USChoroplethMap.

    Reads three gold tables:

    * ``mip.gold.funnel_snapshot_daily`` (state rollup) -- latest
      snapshot, per-state ``_ALL`` segment row. Powers the hover
      tooltip (addressable / in-the-money / top-tier / avg_score) plus
      the state-fill level on the choropleth.
    * ``mip.gold.state_top_segment`` -- LEFT JOIN on state, latest
      snapshot, to surface the dominant SegmentCode per state on the
      ``StateRollup.top_segment_code`` extension.
    * ``mip.gold.county_rollup`` -- filtered to the given state at the
      latest snapshot.
    * ``mip.gold.zip_rollup`` -- filtered to the given county FIPS at
      the latest snapshot.

    Short-TTL cached (60s default) per-method so a presenter clicking
    between segment-intelligence and home pays one warehouse round-trip
    per minute, not per navigation. The data refreshes daily upstream
    so 60s is a non-issue for correctness.
    """

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

    # State rollup: join the funnel snapshot (counts) with the top-segment
    # table (dominant SegmentCode). LEFT JOIN on state so an empty
    # state_top_segment (first deploy before the CTAS has run) still
    # returns state counts -- top_segment_code just stays NULL.
    _STATE_SQL = (
        "SELECT "
        "  f.state                         AS state, "
        "  f.addressable_borrowers         AS addressable, "
        "  f.in_the_money_borrowers        AS in_the_money, "
        "  f.high_opportunity_borrowers    AS top_tier_opportunities, "
        "  f.avg_opportunity_score         AS avg_score, "
        "  f.snapshot_date                 AS snapshot_date, "
        "  ts.top_segment_code             AS top_segment_code "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} AS f "
        "LEFT JOIN ( "
        "  SELECT state, top_segment_code "
        f"  FROM {qualify('gold', 'state_top_segment')} "
        f"  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'state_top_segment')}) "
        ") AS ts ON ts.state = f.state "
        "WHERE f.state <> '_ALL' "
        "  AND f.segment_code = '_ALL' "
        f"  AND f.snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'funnel_snapshot_daily')}) "
        "ORDER BY f.addressable_borrowers DESC"
    )

    _COUNTY_SQL = (
        "SELECT "
        "  fips_5, "
        "  state, "
        "  county_name, "
        "  addressable_borrowers, "
        "  in_the_money_borrowers, "
        "  high_opportunity_borrowers, "
        "  avg_opportunity_score, "
        "  top_segment_code, "
        "  snapshot_date "
        f"FROM {qualify('gold', 'county_rollup')} "
        "WHERE state = :state "
        f"  AND snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'county_rollup')}) "
        "ORDER BY addressable_borrowers DESC"
    )

    _ZIP_SQL = (
        "SELECT "
        "  zip, "
        "  state, "
        "  county_fips_5, "
        "  addressable_borrowers, "
        "  avg_opportunity_score, "
        "  top_segment_code, "
        "  sample_borrower_id, "
        "  snapshot_date "
        f"FROM {qualify('gold', 'zip_rollup')} "
        "WHERE county_fips_5 = :fips_5 "
        f"  AND snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'zip_rollup')}) "
        "ORDER BY addressable_borrowers DESC"
    )

    _STATE_CACHE_KEY = "geo.state_rollups"

    # 2026-05-04 (FIX G, round 3): segment-aware per-state counts.
    # Computed live off mip.gold.borrower_360 (the FULL addressable
    # population — same source the unfiltered funnel snapshot reads),
    # with `arrays_overlap` as the segment predicate so multi-segment
    # borrowers are distinct-counted exactly once even when the filter
    # selects two of their segments.
    #
    # Round-2 (FIX G) queried lead_population, which has a score >= 50
    # floor baked in. After round-3 reverted the rollup filters (FIX α)
    # the unfiltered map shows the FULL addressable count per state.
    # Querying lead_population for the filtered path would have been
    # inconsistent: filter ON = score-quality subset, filter OFF =
    # full population. Switching to borrower_360 here keeps both paths
    # at the same "addressable" definition, so toggling a segment
    # filter always cuts the count down from the same baseline.
    _STATE_SEGMENT_FILTER_SQL_TPL = (
        "SELECT "
        "  state                                        AS state, "
        "  CAST(COUNT(*) AS INT)                         AS addressable, "
        "  CAST(SUM(CASE WHEN opportunity_score >= 75 "
        "                THEN 1 ELSE 0 END) AS INT)      AS top_tier_opportunities, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)    AS avg_score "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE {segment_clause} "
        "GROUP BY state"
    )

    def state_rollups(
        self,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
    ) -> StateRollupResponse:
        if segment_codes:
            # Filtered path. Cache key includes the sorted tuple so two
            # callers asking the same filter share the cache while a
            # different filter doesn't poison the result. The unfiltered
            # `_ALL` path keeps its own _STATE_CACHE_KEY so the most
            # common request (no filter) stays warm.
            normalised = sorted({c.strip() for c in segment_codes if c.strip()})
            cache_key = (
                f"{self._STATE_CACHE_KEY}:filtered:{segment_mode}:"
                f"{','.join(normalised)}"
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            segment_clause, params = self._state_segment_filter_clause(
                normalised,
                segment_mode=segment_mode,
            )
            sql = self._STATE_SEGMENT_FILTER_SQL_TPL.format(
                segment_clause=segment_clause,
            )
            rows = self._client.execute(
                sql,
                params,
            ) or []
            # in_the_money + top_segment_code aren't carried by the
            # filtered query (they would require an extra join the
            # tooltip doesn't surface). Set sentinel zeros so the
            # response shape stays stable; the FE only reads
            # `addressable` for the filtered tooltip path.
            rollups = [
                StateRollup(
                    state=str(r.get("state") or "").upper()[:2],
                    addressable=int(r.get("addressable") or 0),
                    in_the_money=0,
                    top_tier_opportunities=int(r.get("top_tier_opportunities") or 0),
                    avg_score=int(r.get("avg_score") or 0),
                    top_segment_code=None,
                )
                for r in rows
                if r.get("state") and str(r.get("state")) != "_ALL"
            ]
            response = StateRollupResponse(rollups=rollups, snapshot_date=None)
            self._cache.set(cache_key, response, self._cache_ttl_s)
            return response

        # Unfiltered path — unchanged behaviour.
        cached = self._cache.get(self._STATE_CACHE_KEY)
        if cached is not None:
            return cached
        rows = self._client.execute(self._STATE_SQL) or []
        rollups = [
            StateRollup(
                state=str(r.get("state") or "").upper()[:2],
                addressable=int(r.get("addressable") or 0),
                in_the_money=int(r.get("in_the_money") or 0),
                top_tier_opportunities=int(r.get("top_tier_opportunities") or 0),
                avg_score=int(r.get("avg_score") or 0),
                top_segment_code=(
                    str(r["top_segment_code"]) if r.get("top_segment_code") else None
                ),
            )
            for r in rows
            if r.get("state") and str(r.get("state")) != "_ALL"
        ]
        snapshot_date: str | None = None
        if rows:
            raw = rows[0].get("snapshot_date")
            snapshot_date = str(raw) if raw is not None else None
        response = StateRollupResponse(rollups=rollups, snapshot_date=snapshot_date)
        self._cache.set(self._STATE_CACHE_KEY, response, self._cache_ttl_s)
        return response

    @staticmethod
    def _state_segment_filter_clause(
        segment_codes: list[str],
        *,
        segment_mode: str,
    ) -> tuple[str, dict[str, object]]:
        if len(segment_codes) == 1:
            return "array_contains(segment_codes, :segment)", {"segment": segment_codes[0]}
        if segment_mode == "all":
            params = {f"segment_{i}": code for i, code in enumerate(segment_codes)}
            clause = " AND ".join(
                f"array_contains(segment_codes, :segment_{i})"
                for i in range(len(segment_codes))
            )
            return clause, params
        return "arrays_overlap(segment_codes, :segment_codes)", {"segment_codes": segment_codes}

    def county_rollups(self, state: str) -> CountyRollupResponse:
        """Fetch per-county rollups for the given state.

        ``state`` is normalised to 2-char uppercase before the warehouse
        call so the response is stable regardless of UI casing. Returns
        an empty list + ``snapshot_date=None`` when the state is outside
        the 6-state footprint or the CTAS hasn't run yet.
        """
        normalised = str(state or "").upper()[:2]
        cache_key = f"geo.county_rollups:{normalised}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        rows = self._client.execute(self._COUNTY_SQL, {"state": normalised}) or []
        rollups = [
            CountyRollup(
                fips_5=str(r.get("fips_5") or "")[:5],
                state=str(r.get("state") or "").upper()[:2] or normalised,
                county_name=(
                    str(r["county_name"]) if r.get("county_name") else None
                ),
                addressable_borrowers=int(r.get("addressable_borrowers") or 0),
                in_the_money_borrowers=int(r.get("in_the_money_borrowers") or 0),
                high_opportunity_borrowers=int(r.get("high_opportunity_borrowers") or 0),
                avg_opportunity_score=int(r.get("avg_opportunity_score") or 0),
                top_segment_code=(
                    str(r["top_segment_code"]) if r.get("top_segment_code") else None
                ),
            )
            for r in rows
            if r.get("fips_5") and len(str(r.get("fips_5"))) == 5
        ]
        snapshot_date: str | None = None
        if rows:
            raw = rows[0].get("snapshot_date")
            snapshot_date = str(raw) if raw is not None else None
        response = CountyRollupResponse(
            state=normalised,
            rollups=rollups,
            snapshot_date=snapshot_date,
            scope_note=(
                "Cotality evaluation share: 1 anchor county per state"
                if rollups
                else None
            ),
        )
        self._cache.set(cache_key, response, self._cache_ttl_s)
        return response

    def zip_rollups(self, fips_5: str) -> ZipRollupResponse:
        """Fetch per-ZIP rollups for the given 5-char county FIPS."""
        normalised = str(fips_5 or "")[:5]
        cache_key = f"geo.zip_rollups:{normalised}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        rows = self._client.execute(self._ZIP_SQL, {"fips_5": normalised}) or []
        rollups = [
            ZipRollup(
                zip=str(r.get("zip") or "")[:5],
                state=str(r.get("state") or "").upper()[:2],
                county_fips_5=(
                    str(r["county_fips_5"]) if r.get("county_fips_5") else None
                ),
                addressable_borrowers=int(r.get("addressable_borrowers") or 0),
                avg_opportunity_score=int(r.get("avg_opportunity_score") or 0),
                top_segment_code=(
                    str(r["top_segment_code"]) if r.get("top_segment_code") else None
                ),
                sample_borrower_id=(
                    str(r["sample_borrower_id"]) if r.get("sample_borrower_id") else None
                ),
            )
            for r in rows
            if r.get("zip") and len(str(r.get("zip"))) == 5
        ]
        snapshot_date: str | None = None
        if rows:
            raw = rows[0].get("snapshot_date")
            snapshot_date = str(raw) if raw is not None else None
        response = ZipRollupResponse(
            fips_5=normalised,
            rollups=rollups,
            snapshot_date=snapshot_date,
        )
        self._cache.set(cache_key, response, self._cache_ttl_s)
        return response


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _coerce_bool(value: Any) -> bool:
    """Warehouse booleans arrive as Python bool via the coercion in
    ``databricks_sql._coerce`` -- but defend against raw strings just
    in case a future API change emits 'true' / 'false' literals.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "t", "yes")


class DatabricksGenieRepository:
    """Real Genie, then an honest "warming up" degraded message.

    Control flow:

    1. If the ``genie`` circuit breaker is OPEN, return the honest
       "Genie is warming up — try again in a few seconds" message.
    2. Otherwise call ``ResilientGenieClient.ask(question)``. On
       success, adapt ``GenieResponse`` into the ``GenieMessageResponse``
       wire contract (``source="genie"``).
    3. If the call fails with ``DependencyDownError`` (breaker just
       opened on us), return the warming-up message — same path as (1).
    4. On any other exception, re-raise so the router's 503 translation
       engages — we never swallow to a mock answer.

    No path ever fabricates data. There used to be a "computed_fallback"
    path that ran a small Python-templated SQL query when Genie was
    down, but that was removed 2026-05-04 per user feedback ("we don't
    want this app to be gimmicky at all"). When Genie is unreachable we
    show the user a single honest message and let them retry; we don't
    half-impersonate Genie with a hand-written intent matcher + SQL
    template. Live Genie does conversational analytics; nothing else
    pretends to.

    The ``genie_answers`` catalog file remains in-tree for its
    ``follow_up_questions`` lists (static UI prompt suggestions, not
    data), and would be the natural seed if someone ever wires a
    proper LLM-backed fallback. None of its hardcoded numeric content
    reaches the wire.
    """

    _WARMING_MESSAGE = (
        "Genie is warming up — try that question again in a few seconds. "
        "Live answers come straight from the Mortgage Lead Intelligence "
        "Genie space; no curated answers are served while it reconnects."
    )

    def __init__(
        self,
        genie: ResilientGenieClient,
        sql_client: DatabricksSqlClient | None = None,
    ) -> None:
        self._genie = genie
        self._sql_client = sql_client

    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        breaker_state = self._genie.resilient.breaker.state
        if breaker_state == "open":
            return self._degraded(question)
        try:
            result = self._genie.ask(question, conversation_id=conversation_id)
        except DependencyDownError:
            return self._degraded(question)
        except GenieClientError:
            # Underlying Genie surfaced an unrecoverable response (401,
            # 500, malformed JSON). Re-raise so the router translates
            # to 503 + degraded UI. No silent mock fallback.
            raise
        return _adapt_genie_response(question, result, sql_client=self._sql_client)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _degraded(self, question: str) -> GenieMessageResponse:
        """Honest "Genie is warming up" message with no fabricated content.

        We pull the catalog match purely so we can carry over its
        ``follow_up_questions`` (static UI suggestions like "Which zips
        have the most in-the-money refi candidates?" — these are
        editorial prompt help, not data). The catalog's answer body
        and table_rows are intentionally discarded.
        """
        catalog_answer = genie_catalog_respond(question)
        return GenieMessageResponse(
            conversation_id=catalog_answer.conversation_id,
            question=question,
            answer=self._WARMING_MESSAGE,
            source="degraded",
            trusted_assets=[],
            question_hash=_genie_question_hash(question),
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                known_data_gaps=_known_data_gaps(question, []),
                conversation_id=catalog_answer.conversation_id,
                generated_at=datetime.now(UTC).isoformat(),
            ),
            follow_up_questions=catalog_answer.follow_up_questions,
        )


def _adapt_genie_response(
    question: str,
    result: GenieResponse,
    *,
    sql_client: DatabricksSqlClient | None = None,
) -> GenieMessageResponse:
    """Wrap a live ``GenieResponse`` into the wire contract the UI
    already consumes. We derive ``trusted_assets`` from the SQL query
    when one is available (best-effort regex for ``mip.*``
    references); empty otherwise -- the UI tolerates an empty list.

    PII posture (Genie audit finding, 2026-04-23): Genie's Space ``instructions``
    block forbids returning PII columns, but that's model-compliance, not a
    guaranteed output-side filter. The repository boundary enforces defence-
    in-depth by stripping any row keys that match the governance denylist
    (owner names, raw CLIP, owner_link_id, owner_name_hash, street addresses)
    regardless of what the model decided to select. Customers see zero PII
    columns in Ask Genie results even if the Space drifts.
    """
    trusted_assets = _extract_asset_refs(result.sql_query)
    rows = _redact_genie_rows(result.sql_result_rows)
    trusted_sql = _trusted_sql_policy(result.sql_query, trusted_assets)
    question_hash = _genie_question_hash(question)
    canonical = _canonical_genie_answer(
        question=question,
        result=result,
        sql_client=sql_client,
    )
    if canonical is not None:
        return canonical
    text_contains_pii = _answer_text_contains_pii(result.answer_text)
    if text_contains_pii or (result.sql_query and not trusted_sql):
        proof = _build_genie_proof(
            sql_query=None,
            trusted_assets=trusted_assets,
            rows=[],
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=(
                "Genie returned content outside the trusted Module 0 policy, "
                "so the app did not display the result. Ask a scoped question "
                "over the trusted mortgage lead assets without PII or protected-class criteria."
            ),
            source="policy_blocked",
            trusted_assets=trusted_assets,
            sql_query=None,
            row_count=0,
            proof=proof,
            table_rows=[],
        )
    if result.sql_query and trusted_sql and not rows and sql_client is not None:
        rows = _redact_genie_rows(_execute_trusted_genie_sql(sql_client, result.sql_query))
    proof = _build_genie_proof(
        sql_query=result.sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
    )
    visualization = _plan_genie_visualization(question, rows)
    actions = _suggest_genie_actions(
        question=question,
        rows=rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        question_hash=question_hash,
    )
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=result.answer_text or "",
        source="genie",
        trusted_assets=trusted_assets,
        sql_query=result.sql_query,
        row_count=len(rows) if rows else 0,
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


_CANONICAL_ITM_COUNT_SQL = """
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE in_the_money = TRUE
""".strip()

_CANONICAL_ITM_COUNT_BY_STATE_SQL = """
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE in_the_money = TRUE
  AND state = :state
""".strip()

_CANONICAL_ITM_COUNT_BY_CITY_SQL = """
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE in_the_money = TRUE
  AND LOWER(city) = LOWER(:city)
""".strip()

_CANONICAL_MSA_SCORE_SQL = """
WITH borrower_markets AS (
  SELECT situs_cbsa_code
       , COALESCE(NULLIF(city, ''), 'Unknown') AS city
       , state
       , opportunity_score
       , refreshed_at
  FROM mip.gold.borrower_360
  WHERE situs_cbsa_code IS NOT NULL
    AND TRIM(situs_cbsa_code) <> ''
),
market_scores AS (
  SELECT situs_cbsa_code AS msa_cbsa_code
       , CAST(COUNT(*) AS BIGINT) AS borrowers
       , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
       , MAX(refreshed_at) AS refreshed_at
  FROM borrower_markets
  GROUP BY situs_cbsa_code
),
city_counts AS (
  SELECT situs_cbsa_code
       , city
       , state
       , COUNT(*) AS city_borrowers
  FROM borrower_markets
  GROUP BY situs_cbsa_code, city, state
),
city_ranked AS (
  SELECT situs_cbsa_code
       , city
       , state
       , city_borrowers
       , ROW_NUMBER() OVER (
           PARTITION BY situs_cbsa_code
           ORDER BY city_borrowers DESC, city ASC, state ASC
         ) AS rn
  FROM city_counts
)
SELECT CONCAT(cr.city, ', ', cr.state, ' (CBSA ', ms.msa_cbsa_code, ')') AS market
     , ms.msa_cbsa_code
     , ms.borrowers
     , ms.avg_score
     , ms.refreshed_at
FROM market_scores AS ms
LEFT JOIN city_ranked AS cr
  ON cr.situs_cbsa_code = ms.msa_cbsa_code
 AND cr.rn = 1
ORDER BY ms.borrowers DESC, ms.avg_score DESC, ms.msa_cbsa_code ASC
LIMIT 5
""".strip()

_US_STATE_FILTERS: tuple[tuple[str, str], ...] = (
    ("alabama", "AL"), ("alaska", "AK"), ("arizona", "AZ"), ("arkansas", "AR"),
    ("california", "CA"), ("colorado", "CO"), ("connecticut", "CT"), ("delaware", "DE"),
    ("florida", "FL"), ("georgia", "GA"), ("hawaii", "HI"), ("idaho", "ID"),
    ("illinois", "IL"), ("indiana", "IN"), ("iowa", "IA"), ("kansas", "KS"),
    ("kentucky", "KY"), ("louisiana", "LA"), ("maine", "ME"), ("maryland", "MD"),
    ("massachusetts", "MA"), ("michigan", "MI"), ("minnesota", "MN"),
    ("mississippi", "MS"), ("missouri", "MO"), ("montana", "MT"), ("nebraska", "NE"),
    ("nevada", "NV"), ("new hampshire", "NH"), ("new jersey", "NJ"),
    ("new mexico", "NM"), ("new york", "NY"), ("north carolina", "NC"),
    ("north dakota", "ND"), ("ohio", "OH"), ("oklahoma", "OK"), ("oregon", "OR"),
    ("pennsylvania", "PA"), ("rhode island", "RI"), ("south carolina", "SC"),
    ("south dakota", "SD"), ("tennessee", "TN"), ("texas", "TX"), ("utah", "UT"),
    ("vermont", "VT"), ("virginia", "VA"), ("washington", "WA"),
    ("west virginia", "WV"), ("wisconsin", "WI"), ("wyoming", "WY"),
)


def _canonical_itm_state_scope(question: str) -> tuple[str, str] | None:
    q = question.lower()
    for name, code in _US_STATE_FILTERS:
        name_pattern = r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
        code_pattern = r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])"
        if re.search(name_pattern, q) or re.search(code_pattern, question):
            return name.title(), code
    return None


def _canonical_in_the_money_count_scope(question: str) -> tuple[str, str] | None | bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    if not any(phrase in q for phrase in ("in-the-money", "in the money")):
        return False
    if "borrower" not in q:
        return False
    if not any(term in q for term in ("how many", "count", "total number", "number of")):
        return False
    breakdown_terms = (
        " by ",
        "break down",
        "broken down",
        " by state",
        "by-state",
        "state by state",
        "top ",
        "rank",
        "list",
        "zip",
        "county",
        "msa",
        "market",
        "average",
        "avg",
        "mean",
    )
    if any(term in q for term in breakdown_terms):
        return None
    state_scope = _canonical_itm_state_scope(question)
    if state_scope is not None:
        return state_scope
    if re.search(
        r"\bborrowers?\b(?:\s+[a-z0-9-]+){0,6}\s+"
        r"(?:in|for|near|around|within)\s+(?!the\b|the-money\b)[a-z]",
        q,
    ):
        return None
    if re.search(r"\bin[- ]the[- ]money\s+in\s+[a-z]", q):
        return None
    return True


def _canonical_itm_city_scope(question: str) -> str | None:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"[-]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if "in the money" not in q or "borrower" not in q:
        return None
    if not any(term in q for term in ("how many", "count", "total number", "number of")):
        return None
    city_start = q.rfind(" in ")
    if city_start <= q.find("in the money"):
        return None
    city = q[city_start + 4 :].strip()
    city = re.sub(r"\b(?:right now|currently|today|this week|this month)\b.*$", "", city)
    city = city.strip()
    if not city:
        return None
    blocked_geo_terms = {"state", "states", "zip", "zips", "msa", "market", "markets", "county"}
    if any(term in city.split() for term in blocked_geo_terms):
        return None
    state_names = {name for name, _code in _US_STATE_FILTERS}
    state_codes = {code.lower() for _name, code in _US_STATE_FILTERS}
    if city in state_names or city.lower() in state_codes:
        return None
    return " ".join(part.capitalize() for part in city.split())


def _canonical_msa_score_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    score_terms = (
        "lead score",
        "opportunity score",
        "avg score",
        "average score",
        "mean score",
        "mean lead score",
    )
    geo_terms = ("msa", "cbsa", "market", "markets")
    top_terms = ("top five", "top 5", "five markets", "5 markets")
    return (
        "compare" in q
        and any(term in q for term in score_terms)
        and any(term in q for term in geo_terms)
        and any(term in q for term in top_terms)
    )


def _canonical_genie_answer(
    *,
    question: str,
    result: GenieResponse,
    sql_client: DatabricksSqlClient | None,
) -> GenieMessageResponse | None:
    """Return hard-gated trusted answers for known grain-sensitive metrics.

    The Genie space is allowed to read metric views, but
    ``borrower_opportunity_metric_view`` is exploded by segment. A plain
    ``COUNT(*)`` there double-counts multi-segment borrowers. For the exact
    executive question "how many borrowers are currently in-the-money", the
    app replays the canonical gold-grain query and displays that proof.
    """
    if sql_client is None:
        return None
    if _canonical_msa_score_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_MSA_SCORE_SQL)
        except DatabricksSqlError as exc:
            log.warning("canonical_genie_msa_score_failed: %s", exc, exc_info=True)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_MSA_SCORE_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, rows)
        actions = _suggest_genie_actions(
            question=question,
            rows=rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
        )
        if rows:
            answer = (
                "I used Cotality's `situs_cbsa_code` as the MSA identifier and "
                "ranked the top five markets by borrower volume, then calculated "
                "mean lead score at the unique borrower grain from mip.gold.borrower_360."
            )
        else:
            answer = (
                "The current gold borrower table did not return CBSA-coded market rows. "
                "Module 0 has `situs_cbsa_code` for MSA-style grouping, but no separate "
                "MSA-name lookup is loaded."
            )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="genie",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_MSA_SCORE_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            table_rows=rows,
        )

    scope = _canonical_in_the_money_count_scope(question)
    if not scope:
        city_scope = _canonical_itm_city_scope(question)
        if not city_scope:
            return None
        try:
            row = sql_client.execute_one(
                _CANONICAL_ITM_COUNT_BY_CITY_SQL,
                {"city": city_scope},
            ) or {}
        except DatabricksSqlError as exc:
            log.warning("canonical_genie_metric_failed: %s", exc, exc_info=True)
            return None
        count = row.get("in_the_money_borrowers")
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            log.warning("canonical_genie_metric_bad_count: %r", count)
            return None
        rows = [
            {
                "city": city_scope,
                "in_the_money_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, rows)
        actions = _suggest_genie_actions(
            question=question,
            rows=rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
        )
        answer = (
            f"There are {count_int:,} borrowers currently in-the-money in {city_scope} "
            "within the current IL / CA / FL / TX / WA / CO share footprint. "
            "This is a city-scoped unique borrower count from mip.gold.borrower_360; "
            "it is not the overall share total."
        )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="genie",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            metric_value=f"{count_int:,}",
            table_rows=rows,
        )
    state_scope = scope if isinstance(scope, tuple) else None
    sql_query = _CANONICAL_ITM_COUNT_BY_STATE_SQL if state_scope else _CANONICAL_ITM_COUNT_SQL
    params = {"state": state_scope[1]} if state_scope else None
    try:
        row = sql_client.execute_one(sql_query, params) or {}
    except DatabricksSqlError as exc:
        log.warning("canonical_genie_metric_failed: %s", exc, exc_info=True)
        return None
    count = row.get("in_the_money_borrowers")
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        log.warning("canonical_genie_metric_bad_count: %r", count)
        return None

    rows = [{"in_the_money_borrowers": count_int, "refreshed_at": row.get("refreshed_at")}]
    if state_scope:
        rows[0]["state"] = state_scope[1]
    trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
    )
    visualization = _plan_genie_visualization(question, rows)
    actions = _suggest_genie_actions(
        question=question,
        rows=rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        question_hash=question_hash,
    )
    geo_text = f" in {state_scope[0]} ({state_scope[1]})" if state_scope else ""
    answer = (
        f"There are {count_int:,} borrowers currently in-the-money{geo_text}. "
        "This is a unique borrower count from mip.gold.borrower_360 at the "
        "gold borrower grain, so multi-segment borrowers are counted once."
    )
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="genie",
        trusted_assets=trusted_assets,
        sql_query=sql_query,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=f"{count_int:,}",
        table_rows=rows,
    )


# Governance denylist applied to every Genie table-row before the response
# leaves the repository boundary. Matches the ``_FORBIDDEN_OUTPUT_KEYS`` set
# in ``backend/services/pii_redaction`` (keep in sync). These keys are PII
# per the governance contract and must never ship to the frontend.
_GENIE_PII_KEYS: frozenset[str] = frozenset({
    "owner_name",
    "owner_names",
    "owner_full_name",
    "primary_owner",
    "owner_name_hash",      # hashed, but still a stable identifier — not exported
    "owner_link_id",        # raw Cotality identifier — replaced with a display surrogate elsewhere
    "clip",                 # raw CLIP — evidence drawer surfaces a short form only
    "raw_clip",
    "street_address",
    "site_address",
    "mailing_address",
    "tax_mailing_address",
    "subject_property",     # carries synthesized city + ZIP; synthesized upstream, but redacted here too
    "owner_email",
    "borrower_email",
    "email",
    "phone",
    "phone_number",
    "ssn",
})


def _redact_genie_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Strip PII keys from Genie's result set before returning to the UI.

    Never raises; if ``rows`` is falsy we pass it through. Applied to every
    response path that sets ``table_rows`` on ``GenieMessageResponse``.
    """
    if not rows:
        return rows
    redacted: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        redacted.append(
            {
                k: v
                for k, v in row.items()
                if _normalise_genie_key(str(k)) not in _GENIE_PII_KEYS
            }
        )
    return redacted


def _normalise_genie_key(key: str) -> str:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return re.sub(r"[^a-z0-9]+", "_", split_camel.lower()).strip("_")


_PII_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)


def _answer_text_contains_pii(text: str | None) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in _PII_TEXT_PATTERNS)


def _trusted_genie_asset_names() -> frozenset[str]:
    """Return explicit trusted assets for the configured and demo catalogs."""

    pairs = (
        ("gold", "lead_population"),
        ("gold", "segment_population"),
        ("gold", "lead_scores"),
        ("gold", "borrower_360"),
        ("gold", "borrower_dossier"),
        ("gold", "evidence_events"),
        ("gold", "lockin_cohort"),
        ("semantics", "lead_generation_metric_view"),
        ("semantics", "segment_performance_metric_view"),
        ("semantics", "borrower_opportunity_metric_view"),
    )
    assets = {qualify(schema, table) for schema, table in pairs}
    assets.update(qualify(schema, table, catalog="mip") for schema, table in pairs)
    return frozenset(assets)


_TRUSTED_GENIE_ASSETS: frozenset[str] = _trusted_genie_asset_names()


def _genie_question_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def _is_select_only(sql: str | None) -> bool:
    if not sql:
        return False
    policy_sql = _scrub_sql_for_policy(sql)
    if policy_sql is None:
        return False
    stripped = policy_sql.strip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return False
    blocked = "alter|create|delete|drop|grant|insert|merge|revoke|set|truncate|update|use"
    return re.search(rf"\b(?:{blocked})\b", stripped) is None


def _trusted_sql_policy(sql: str | None, trusted_assets: list[str]) -> bool:
    return bool(trusted_assets) and all(
        asset in _TRUSTED_GENIE_ASSETS for asset in trusted_assets
    ) and _is_select_only(sql) and not _sql_mentions_pii_columns(sql)


def _bounded_genie_sql(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    return f"SELECT * FROM ({stripped}) AS genie_result LIMIT 500"


def _execute_trusted_genie_sql(
    sql_client: DatabricksSqlClient,
    sql: str,
) -> list[dict[str, Any]] | None:
    try:
        return sql_client.execute(_bounded_genie_sql(sql))
    except DatabricksSqlError as exc:
        log.warning("trusted_genie_sql_replay_failed: %s", exc, exc_info=True)
        return None


def _extract_filters(sql: str | None) -> list[str]:
    if not sql:
        return []
    import re

    match = re.search(
        r"\bwhere\b(?P<where>.*?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    where = re.sub(r"\s+", " ", match.group("where")).strip()
    if not where:
        return []
    return [where[:500]]


def _row_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    if not rows:
        return []
    cols: list[str] = []
    for row in rows[:5]:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


_GENIE_IDENTIFIER_COLUMNS = {
    "zip",
    "zip_code",
    "zipcode",
    "postal_code",
    "fips",
    "fips_5",
    "county_fips",
    "county_fips_5",
    "msa_cbsa_code",
    "cbsa_code",
    "census_tract",
    "tract",
    "borrower_id",
    "clip",
    "id",
}


def _is_genie_identifier_column(column: str) -> bool:
    lower = column.lower()
    return lower in _GENIE_IDENTIFIER_COLUMNS or lower.endswith("_id")


def _numeric_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for col in _row_columns(rows):
        if _is_genie_identifier_column(col):
            continue
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(isinstance(v, int | float) for v in values):
            out.append(col)
    return out


def _text_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for col in _row_columns(rows):
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(isinstance(v, str) for v in values):
            out.append(col)
    return out


def _dateish_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    names = []
    for col in _row_columns(rows):
        lower = col.lower()
        if lower.endswith("_date") or lower.endswith("_at") or "snapshot" in lower:
            names.append(col)
    return names


def _freshness_from_rows(
    assets: list[str],
    rows: list[dict[str, Any]] | None,
) -> list[GenieDataFreshness]:
    freshness_cols = [
        col
        for col in _row_columns(rows)
        if col.lower() in {"refreshed_at", "snapshot_at", "data_refreshed_at"}
        or col.lower().endswith("_refreshed_at")
    ]
    values: list[str] = []
    for col in freshness_cols:
        for row in rows or []:
            value = row.get(col)
            if value is not None:
                values.append(str(value))
    refreshed_at = max(values) if values else None
    if refreshed_at:
        return [
            GenieDataFreshness(
                asset=asset,
                refreshed_at=refreshed_at,
                status="live",
                note="freshness returned by the generated SQL result",
            )
            for asset in assets
        ]
    return [
        GenieDataFreshness(
            asset=asset,
            status="source-cited",
            note="generated SQL did not return refreshed_at/snapshot_at; inspect SQL or query MAX(refreshed_at) for this asset",
        )
        for asset in assets
    ]


def _known_data_gaps(question: str, assets: list[str]) -> list[str]:
    material = " ".join([question, *assets]).lower()
    gaps: list[str] = []
    if any(token in material for token in ("permit", "building permit")):
        gaps.append(
            "Cotality Building Permits feed is pending; permit flags are blocked false today."
        )
    if any(token in material for token in ("listing", "listed", "mls")):
        gaps.append(
            "Cotality MLS/listing feed is pending; listed-for-sale flags are blocked false today."
        )
    return gaps


def _build_genie_proof(
    *,
    sql_query: str | None,
    trusted_assets: list[str],
    rows: list[dict[str, Any]] | None,
    question: str,
    conversation_id: str,
    message_id: str,
    elapsed_ms: int,
) -> GenieProof:
    trusted = _trusted_sql_policy(sql_query, trusted_assets)
    return GenieProof(
        sql_query=sql_query,
        source_assets=trusted_assets,
        data_freshness=_freshness_from_rows(trusted_assets, rows),
        row_count=len(rows) if rows else 0,
        filters=_extract_filters(sql_query),
        trusted=trusted,
        known_data_gaps=_known_data_gaps(question, trusted_assets),
        conversation_id=conversation_id,
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        generated_at=datetime.now(UTC).isoformat(),
    )


def _label_column(rows: list[dict[str, Any]] | None, question: str) -> str | None:
    cols = _row_columns(rows)
    preferred = [
        "state",
        "zip",
        "zip_code",
        "county",
        "county_name",
        "msa",
        "market",
        "segment",
        "segment_code",
        "recommended_offer",
        "offer_code",
        "product_label",
    ]
    q = question.lower()
    if "zip" in q:
        preferred = ["zip", "zip_code", *preferred]
    if "state" in q or "map" in q:
        preferred = ["state", *preferred]
    for col in preferred:
        if col in cols:
            return col
    texts = _text_columns(rows)
    return texts[0] if texts else None


def _value_column(rows: list[dict[str, Any]] | None, question: str) -> str | None:
    nums = _numeric_columns(rows)
    if not nums:
        return None
    q = question.lower()
    preferred = [
        "borrowers",
        "borrower_count",
        "count",
        "marketable_borrowers",
        "addressable_borrowers",
        "in_the_money_borrowers",
        "high_opportunity_borrowers",
        "opportunity_score",
        "avg_score",
        "approval_rate",
        "conversion_rate",
        "rate_spread_bps",
        "equity_pct",
    ]
    if "score" in q:
        preferred = ["opportunity_score", "avg_score", *preferred]
    if "rate" in q:
        preferred = ["approval_rate", "conversion_rate", "rate_spread_bps", *preferred]
    for col in preferred:
        if col in nums:
            return col
    return nums[0]


def _plan_genie_visualization(
    question: str,
    rows: list[dict[str, Any]] | None,
) -> GenieVisualizationSpec | None:
    q = question.lower()
    row_count = len(rows) if rows else 0
    label = _label_column(rows, question)
    value = _value_column(rows, question)
    date_col = (_dateish_columns(rows) or [None])[0]
    cols = set(_row_columns(rows))

    if "borrower_id" in cols and row_count > 0:
        return GenieVisualizationSpec(
            kind="borrower_list",
            title="Borrower drill-down",
            x="borrower_id",
            y=value,
            reason="result includes borrower_id rows",
        )
    if ("strategy" in q or "10,000" in q or "outreach touches" in q) and row_count > 0:
        return GenieVisualizationSpec(
            kind="strategy_board",
            title="Strategy board",
            x=label,
            y=value,
            reason="strategy-oriented prompt with returned rows",
        )
    if ("map" in q or "geo" in q or "where" in q) and label in {"state", "zip", "zip_code"} and value:
        return GenieVisualizationSpec(
            kind="map",
            title=f"{value} by {label}",
            x=label,
            y=value,
            reason="geography prompt with state or ZIP column",
        )
    if ("trend" in q or "over time" in q or "by week" in q or "daily" in q) and date_col and value:
        return GenieVisualizationSpec(
            kind="line",
            title=f"{value} trend",
            x=date_col,
            y=value,
            reason="time-oriented prompt with date/snapshot column",
        )
    if row_count == 1 and value:
        return GenieVisualizationSpec(
            kind="metric",
            title=value,
            y=value,
            reason="single-row numeric result",
        )
    if label and value and row_count >= 2:
        return GenieVisualizationSpec(
            kind="bar",
            title=f"{value} by {label}",
            x=label,
            y=value,
            reason="categorical result with numeric measure",
        )
    if row_count > 0:
        return GenieVisualizationSpec(kind="table", title="Query result")
    return None


def _borrower_ids_from_rows(rows: list[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    for row in rows or []:
        value = row.get("borrower_id")
        if isinstance(value, str) and value.startswith("B-") and value not in ids:
            ids.append(value)
    return ids[:50]


def _suggest_genie_actions(
    *,
    question: str,
    rows: list[dict[str, Any]] | None,
    trusted_assets: list[str],
    visualization: GenieVisualizationSpec | None,
    conversation_id: str | None,
    message_id: str | None,
    question_hash: str | None,
) -> list[GenieActionSuggestion]:
    actions: list[GenieActionSuggestion] = []
    borrower_ids = _borrower_ids_from_rows(rows)
    row_count = len(rows) if rows else 0
    q = question.lower()
    base_criteria: dict[str, Any] = {
        "source": "genie",
        "source_assets": trusted_assets,
        "visualization_kind": visualization.kind if visualization else None,
        "row_count": row_count,
    }
    if borrower_ids:
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="save-borrowers",
                label=f"Save {len(borrower_ids)} borrower{'' if len(borrower_ids) == 1 else 's'}",
                action_type="save_borrowers",
                description="Add returned borrowers to the governed saved workspace.",
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        route = f"/borrower-360/{borrower_ids[0]}"
        actions.append(
            GenieActionSuggestion(
                id="show-first-rationale",
                label="Show why first borrower is ranked",
                action_type="show_rationale",
                description="Open Borrower 360 for the top returned borrower.",
                route=route,
                borrower_ids=[borrower_ids[0]],
                criteria=criteria,
            )
        )
    if row_count > 0:
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="open-cohort",
                label="Open this cohort in Lead Queue",
                action_type="open_cohort",
                description="Navigate into the lead queue with this Genie result audited.",
                route="/lead-queue",
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="create-campaign-draft",
                label="Create draft campaign",
                action_type="create_draft_campaign",
                description="Create a Lakebase draft campaign from this governed Genie result.",
                route="/lead-queue",
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
    if "offer" in q or "strategy" in q or "10,000" in q:
        criteria = dict(base_criteria)
        route = f"/offer-orchestrator/{borrower_ids[0]}" if borrower_ids else "/offer-orchestrator"
        actions.append(
            GenieActionSuggestion(
                id="compare-offers",
                label="Compare offer strategies",
                action_type="compare_offer_strategies",
                description="Audit this strategy comparison and open the offer surface.",
                route=route,
                borrower_ids=borrower_ids[:1],
                criteria=criteria,
            )
        )
    criteria = dict(base_criteria)
    actions.append(
        GenieActionSuggestion(
            id="export-insight",
            label="Export demo-ready insight",
            action_type="export_insight",
            description="Record an audited insight export for this Genie answer.",
            borrower_ids=borrower_ids,
            criteria=criteria,
        )
    )
    return actions[:5]


_SQL_IDENT_RE = r"(?:`[^`]+`|[a-zA-Z_][a-zA-Z0-9_]*)"


def _scrub_sql_for_policy(sql: str | None) -> str | None:
    """Remove literals and reject SQL shapes the proof gate cannot trust.

    Genie is allowed to generate one read-only statement over curated UC
    assets. Comments, semicolons, and double-quoted identifiers are rejected
    fail-closed: they are unnecessary for Module 0 questions and make a
    regex-backed verifier easy to spoof.
    """
    if not sql:
        return None
    out: list[str] = []
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if ch == ";":
            return None
        if ch == "-" and nxt == "-":
            return None
        if ch == "/" and nxt == "*":
            return None
        if ch == '"':
            return None
        if ch == "'":
            out.append(" '' ")
            i += 1
            while i < len(sql):
                if sql[i] == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if ch == "`":
            start = i
            i += 1
            while i < len(sql) and sql[i] != "`":
                i += 1
            if i >= len(sql):
                return None
            out.append(sql[start : i + 1])
            i += 1
            continue
        out.append(ch)
        i += 1
    scrubbed = "".join(out).strip()
    return scrubbed or None


def _normalise_sql_ref(ref: str) -> str:
    parts = [part.strip().strip("`").lower() for part in ref.split(".")]
    return ".".join(part for part in parts if part)


_GENIE_PII_SQL_COLUMNS: frozenset[str] = frozenset({
    "clip",
    "owner_link_id",
    "owner_name",
    "owner_names",
    "owner_full_name",
    "owner_name_hash",
    "primary_owner",
    "raw_clip",
    "street_address",
    "site_address",
    "mailing_address",
    "tax_mailing_address",
    "subject_property",
    "owner_email",
    "borrower_email",
    "email",
    "phone",
    "phone_number",
    "ssn",
})


def _sql_mentions_pii_columns(sql: str | None) -> bool:
    scrubbed = _scrub_sql_for_policy(sql)
    if not scrubbed:
        return False
    normalised = re.sub(r"[^a-z0-9_]+", " ", scrubbed.lower())
    tokens = set(normalised.split())
    return bool(tokens & _GENIE_PII_SQL_COLUMNS)


def _extract_asset_refs(sql: str | None) -> list[str]:
    """Pull three-part UC references out of a SQL string.

    Best-effort, but intentionally returns every unique reference it
    sees. UI callers can choose how many chips to render; trust
    enforcement must evaluate the whole query.
    """
    if not sql:
        return []
    policy_sql = _scrub_sql_for_policy(sql)
    if not policy_sql:
        return []

    table_pattern = re.compile(
        rf"\b(?:from|join)\s+({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{1,2}})(?!\s*\.)",
        flags=re.IGNORECASE,
    )
    seen: list[str] = []
    for match in table_pattern.findall(policy_sql):
        ref = _normalise_sql_ref(match)
        if ref and ref not in seen:
            seen.append(ref)
    return seen


__all__ = [
    "DatabricksBorrowerRepository",
    "DatabricksGenieRepository",
    "DatabricksGeoRepository",
    "DatabricksLeadRepository",
    "DatabricksOfferRepository",
    "DatabricksOutreachRepository",
    "DatabricksPortfolioRepository",
    "DatabricksSegmentRepository",
]
