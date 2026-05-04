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
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import (
    GenieMessageResponse,
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
        "  CAST(COALESCE(ROUND(AVG(opportunity_score)), 0) AS INT)               AS avg_score "
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
    def _build_trend(series: list[float]) -> KpiTrend:
        """Compute KpiTrend (series ascending + delta + direction) from an
        oldest-first numeric series."""
        if len(series) < 2 or series[0] == 0:
            return KpiTrend(series=series, delta_pct=None, direction="flat")
        delta_pct = ((series[-1] - series[0]) / series[0]) * 100.0
        direction = "up" if delta_pct > 0.5 else "down" if delta_pct < -0.5 else "flat"
        return KpiTrend(series=series, delta_pct=round(delta_pct, 1), direction=direction)

    def _load_funnel(self) -> tuple[dict[str, KpiTrend], dict[str, Any]]:
        """Query the 7-day funnel snapshot and return (trends, latest).

        `trends` is a dict keyed by KPI field; series are oldest-first.
        `latest` is the newest row (keys include `approved_count`,
        `in_outreach_count`, `snapshot_at`) — used by the preview to
        surface the real current counts + data_refreshed_at timestamp.

        Never raises — if the funnel table is empty (first deploy before
        any snapshot has been written) we return empty dict + {}.
        """
        try:
            rows = self._client.execute(self._TREND_SQL) or []
        except Exception:  # noqa: BLE001 -- resilience: empty trend is an acceptable fallback
            return {}, {}
        if not rows:
            return {}, {}
        # Query is DESC; the FIRST row is newest. Reverse for oldest-first
        # sparkline rendering.
        latest = rows[0]
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
            series = [float(r.get(key) or 0) for r in ordered]
            trends[key] = self._build_trend(series)
        return trends, latest

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
        trends, latest = self._load_funnel()
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
            avg_score=int(row.get("avg_score") or 0),
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

    def list(
        self,
        segment: str | None,
        portfolio_id: str | None,
        limit: int | None = None,
    ) -> list[LeadSummary]:
        _ = portfolio_id
        bounded = self._bound_limit(limit)
        if segment:
            sql = self._LIST_BY_SEGMENT_SQL_TEMPLATE.format(limit=bounded)
            rows = self._client.execute(sql, {"segment": segment})
        else:
            sql = self._LIST_BASE_SQL_TEMPLATE.format(limit=bounded)
            rows = self._client.execute(sql)
        return [LeadSummary(**redact_lead_row(r)) for r in rows]

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

    def state_rollups(self) -> StateRollupResponse:
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
    """Real Genie + honest degraded message gated on the ``genie`` breaker.

    The control flow is deliberately narrow and defensive:

    1. If the ``genie`` circuit breaker is OPEN, return the honest
       "warming up" message. We do NOT serve curated catalog answers
       any more — they contained hardcoded specific numbers (counts,
       dollar amounts, sample borrower IDs) that read as real Cotality
       data even though the source chip said "fallback". CLAUDE.md
       prohibits any mock fallback in the running app, so the only
       acceptable degraded response is an honest "Genie is warming up"
       message that contains zero fabricated content.
       (2026-05-04: user feedback flagged the prior `source="fallback"`
       answers as misleading.)
    2. Otherwise call ``ResilientGenieClient.ask(question)``. On
       success, adapt ``GenieResponse`` into the ``GenieMessageResponse``
       wire contract (``source="genie"``).
    3. If the call fails with ``DependencyDownError`` (breaker just
       opened on us), return the warming-up message — same path as (1).
    4. On any other exception, re-raise so the router's 503 translation
       engages — we never swallow to a mock answer.

    The ``genie_answers`` catalog file is NOT removed: it still serves
    follow-up-question lists (which are static UI suggestions, not
    fabricated data) and remains available for tests + future use if we
    ever land a fallback that computes from real UC tables on demand.
    But its hardcoded answer bodies + table_rows never reach the user
    at runtime through this path.
    """

    _WARMING_MESSAGE = (
        "Genie is warming up — try that question again in a few seconds. "
        "Live answers come straight from the Mortgage Lead Intelligence "
        "Genie space; no curated answers are served while it reconnects."
    )

    def __init__(self, genie: ResilientGenieClient) -> None:
        self._genie = genie

    def respond(self, question: str) -> GenieMessageResponse:
        breaker_state = self._genie.resilient.breaker.state
        if breaker_state == "open":
            return self._degraded(question)
        try:
            result = self._genie.ask(question)
        except DependencyDownError:
            # Breaker opened during this call -> honest degraded message.
            return self._degraded(question)
        except GenieClientError:
            # Underlying Genie surfaced an unrecoverable response (401,
            # 500, malformed JSON). Re-raise so the router translates
            # to 503 + degraded UI. No silent mock fallback.
            raise
        return _adapt_genie_response(question, result)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _degraded(self, question: str) -> GenieMessageResponse:
        """Return an honest "Genie is warming up" message.

        We pull the catalog match purely so we can carry over its
        ``follow_up_questions`` (static UI suggestions like "Which zips
        have the most in-the-money refi candidates?" — these are
        editorial prompt help, not fabricated data). The catalog's
        answer body and table_rows are intentionally discarded so no
        hardcoded numbers reach the wire when Genie is unreachable.
        """
        catalog_answer = genie_catalog_respond(question)
        return GenieMessageResponse(
            conversation_id=catalog_answer.conversation_id,
            question=question,
            answer=self._WARMING_MESSAGE,
            source="degraded",
            trusted_assets=[],
            follow_up_questions=catalog_answer.follow_up_questions,
        )


def _adapt_genie_response(
    question: str,
    result: GenieResponse,
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
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        question=question,
        answer=result.answer_text or "",
        source="genie",
        trusted_assets=trusted_assets,
        table_rows=_redact_genie_rows(result.sql_result_rows),
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
        redacted.append({k: v for k, v in row.items() if k not in _GENIE_PII_KEYS})
    return redacted


def _extract_asset_refs(sql: str | None) -> list[str]:
    """Pull ``mip.<schema>.<table>`` references out of a SQL
    string. Best-effort; returns the first three unique references so
    the evidence chip row stays readable.
    """
    if not sql:
        return []
    import re

    pattern = re.compile(r"\bmip\.[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\b")
    seen: list[str] = []
    for match in pattern.findall(sql):
        if match not in seen:
            seen.append(match)
        if len(seen) >= 3:
            break
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
