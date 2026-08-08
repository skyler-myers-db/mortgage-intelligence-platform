"""Databricks-backed segment and geography repositories."""

from __future__ import annotations

import json
import logging

from backend.schemas.geo import (
    CountyRollup,
    CountyRollupResponse,
    StateRollup,
    StateRollupResponse,
    ZipRollup,
    ZipRollupResponse,
)
from backend.schemas.lead import SegmentSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.county_names import county_name_for_fips
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.geography_scope import GeographyScope, load_geography_scope
from backend.services.observability import emit
from backend.services.repositories.databricks_portfolio import DatabricksPortfolioRepository
from backend.services.repositories.databricks_segment_gates import apply_source_gates
from backend.services.repositories.databricks_shared import _SEGMENT_COLUMNS, _parse_facet_mix
from backend.services.resilience import TTLCache
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD
from backend.services.segment_predicates import (
    compose_segment_predicate,
    normalise_segment_codes,
)

log = logging.getLogger("backend.services.repositories.databricks_repo")

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

    # S1.6: facet-mix lambda shared by the two dynamic mix CTEs below. Same
    # sort contract as the gold_segment_population CTAS: count desc then value.
    _FACET_MIX_EXPR = (
        "TRANSFORM(ARRAY_SORT(COLLECT_LIST(STRUCT(cnt, value)), "
        "(a, b) -> CASE WHEN a.cnt > b.cnt THEN -1 WHEN a.cnt < b.cnt THEN 1 "
        "WHEN a.value < b.value THEN -1 WHEN a.value > b.value THEN 1 ELSE 0 END), "
        "x -> STRUCT(x.value AS value, x.cnt AS count))"
    )

    _LIST_FILTERED_SQL_TPL = (
        "WITH meta AS ( "
        f"  SELECT {_SEGMENT_COLUMNS} "
        f"  FROM {qualify('gold', 'segment_population')} "
        "  WHERE state = '_ALL' "
        "), base AS ( "
        "  SELECT clip, segment_codes, opportunity_score, loan_product_type, origination_channel "
        f"  FROM {qualify('gold', 'borrower_360')} "
        "  WHERE {filter_clause} "
        "), exploded_segments AS ( "
        "  SELECT DISTINCT clip, sc AS segment_code, opportunity_score, "
        "    loan_product_type, origination_channel "
        "  FROM base "
        "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
        "  WHERE sc IS NOT NULL "
        "), rollup AS ( "
        "  SELECT "
        "    segment_code, "
        "    CAST(COUNT(DISTINCT clip) AS INT) AS count, "
        "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_score "
        "  FROM exploded_segments "
        "  GROUP BY segment_code "
        "), product_mix AS ( "
        f"  SELECT segment_code, {_FACET_MIX_EXPR} AS loan_product_mix "
        "  FROM ( "
        "    SELECT segment_code, COALESCE(loan_product_type, 'unknown') AS value, "
        "      CAST(COUNT(DISTINCT clip) AS INT) AS cnt "
        "    FROM exploded_segments "
        "    GROUP BY segment_code, COALESCE(loan_product_type, 'unknown') "
        "  ) GROUP BY segment_code "
        "), channel_mix AS ( "
        f"  SELECT segment_code, {_FACET_MIX_EXPR} AS origination_channel_mix "
        "  FROM ( "
        "    SELECT segment_code, COALESCE(origination_channel, 'unknown') AS value, "
        "      CAST(COUNT(DISTINCT clip) AS INT) AS cnt "
        "    FROM exploded_segments "
        "    GROUP BY segment_code, COALESCE(origination_channel, 'unknown') "
        "  ) GROUP BY segment_code "
        ") "
        "SELECT "
        "  m.segment_code, "
        "  m.name, "
        "  COALESCE(r.count, 0) AS count, "
        "  m.delta_vs_prior, "
        "  COALESCE(r.avg_score, 0) AS avg_score, "
        "  m.description, "
        "  m.color, "
        "  COALESCE(pm.loan_product_mix, CAST(ARRAY() AS ARRAY<STRUCT<value: STRING, count: INT>>)) AS loan_product_mix, "
        "  COALESCE(cm.origination_channel_mix, CAST(ARRAY() AS ARRAY<STRUCT<value: STRING, count: INT>>)) AS origination_channel_mix "
        "FROM meta AS m "
        "LEFT JOIN rollup AS r ON r.segment_code = m.segment_code "
        "LEFT JOIN product_mix AS pm ON pm.segment_code = m.segment_code "
        "LEFT JOIN channel_mix AS cm ON cm.segment_code = m.segment_code"
    )

    # Canonical FE display order matching the prototype's seg-grid layout
    # (`design_files/Module 0 Prototype.html` lines 1546–1551 + the gold
    # `meta` VALUES table in `sql/transformations/gold_segment_population.sql`).
    # Used to re-sort the SQL result after fetch so that pending-source
    # segments (count=0 because of an upstream Cotality data dependency)
    # are NOT buried at the end of the list by `ORDER BY count DESC`.
    # Prototype-parity-audit P0-2 (2026-05-04): the gold rollup always
    # emits one row per registered segment; this constant ensures the FE
    # always renders them in the same predictable order regardless of
    # cardinality. The six core segments keep the prototype order; the
    # S1.3 overlays follow in registry order.
    _CANONICAL_ORDER: tuple[str, ...] = (
        "itm",
        "listed",
        "permit",
        "investor",
        "equity",
        "retention",
        "second_lien_itm",
        "heloc_draw_to_payback",
        "home_equity_history",
        "refi_propensity",
        "itm_on_related_property",
        "payoff_loss_leads",
        "permit_activity",
    )

    def _list_cache_key(
        self,
        portfolio_id: str | None,
        *,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> str:
        portfolio_key = DatabricksGeoRepository._portfolio_cache_key(portfolio_criteria)
        segment_key = ",".join(segment_codes or [])
        return (
            f"segments.list.{portfolio_id or '_ALL'}:{segment_mode}:{segment_key}:{portfolio_key}"
        )

    def list(
        self,
        portfolio_id: str | None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> list[SegmentSummary]:
        normalised_segments = DatabricksGeoRepository._normalise_geo_segments(segment_codes)
        key = self._list_cache_key(
            portfolio_id,
            segment_codes=normalised_segments,
            segment_mode=segment_mode,
            portfolio_criteria=portfolio_criteria,
        )

        def build() -> list[SegmentSummary]:
            if normalised_segments or portfolio_criteria is not None:
                filter_clause, params = DatabricksGeoRepository._geo_filter_clause(
                    normalised_segments,
                    segment_mode=segment_mode,
                    portfolio_criteria=portfolio_criteria,
                )
                rows = self._client.execute(
                    self._LIST_FILTERED_SQL_TPL.format(filter_clause=filter_clause),
                    params,
                )
            else:
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
                    loan_product_mix=_parse_facet_mix(row.get("loan_product_mix")),
                    origination_channel_mix=_parse_facet_mix(row.get("origination_channel_mix")),
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
            order_index: dict[str, int] = {code: i for i, code in enumerate(self._CANONICAL_ORDER)}
            unknown_tail_index = len(self._CANONICAL_ORDER)
            segments.sort(key=lambda s: order_index.get(s.code, unknown_tail_index))
            return apply_source_gates(
                segments,
                client=self._client,
                cache=self._cache,
                cache_ttl_s=self._cache_ttl_s,
            )

        return self._cache.get_or_set(
            key,
            build,
            ttl_s=self._cache_ttl_s,
        )


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
    * ``mip.gold.zip_rollup`` -- filtered to the given state (the live
      drill) or county FIPS (reserved for licensed county data) at the
      latest snapshot.

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
    # 2026-08-08 UX walk: the ZIP layer is keyed on a 5-digit ZIP and
    # gold.zip_rollup filters `LENGTH(zip) = 5`, so borrowers whose share row
    # carries no usable ZIP vanish between the state tile and the ZIP tiles
    # (live: CO 8.7%, WA 5.8%). `zip_unassigned` is derived as the difference
    # between the state total and what the ZIP layer will actually render, so
    # the disclosed number IS the drill gap by construction — it cannot drift
    # from the tiles the way a separately-computed count would. Both sides
    # come from the same refresh anchor, so there is no snapshot skew to
    # absorb. GREATEST(0, ...) keeps the field non-negative if a partial
    # zip_rollup rebuild ever overshoots.
    _STATE_SQL = (
        "SELECT "
        "  f.state                         AS state, "
        "  f.addressable_borrowers         AS addressable, "
        "  f.in_the_money_borrowers        AS in_the_money, "
        "  f.high_opportunity_borrowers    AS top_tier_opportunities, "
        "  f.avg_opportunity_score         AS avg_score, "
        "  f.snapshot_date                 AS snapshot_date, "
        "  ts.top_segment_code             AS top_segment_code, "
        "  CAST(GREATEST(0, f.addressable_borrowers - COALESCE(zc.zip_covered, 0)) AS INT) "
        "    AS zip_unassigned "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} AS f "
        "LEFT JOIN ( "
        "  SELECT state, top_segment_code "
        f"  FROM {qualify('gold', 'state_top_segment')} "
        f"  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'state_top_segment')}) "
        ") AS ts ON ts.state = f.state "
        "LEFT JOIN ( "
        "  SELECT state, CAST(SUM(addressable_borrowers) AS BIGINT) AS zip_covered "
        f"  FROM {qualify('gold', 'zip_rollup')} "
        f"  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'zip_rollup')}) "
        "  GROUP BY state "
        ") AS zc ON zc.state = f.state "
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

    # 2026-08-08: the ZIP layer is keyed on STATE, not county. The Cotality
    # share carries exactly one county FIPS per state (audit C2), so the
    # fabricated county keys were removed and `county_fips_5` is NULL on
    # every zip_rollup row — a county-keyed read returns zero rows for every
    # county, always. `_ZIP_SQL` (FIPS-keyed) stays for a future licensed
    # county dataset; `_ZIP_STATE_SQL` is what the map actually drills.
    _ZIP_SELECT = (
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
    )
    _ZIP_SNAPSHOT_AND_ORDER = (
        f"  AND snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'zip_rollup')}) "
        "ORDER BY addressable_borrowers DESC"
    )

    _ZIP_SQL = _ZIP_SELECT + "WHERE county_fips_5 = :fips_5 " + _ZIP_SNAPSHOT_AND_ORDER

    _ZIP_STATE_SQL = _ZIP_SELECT + "WHERE state = :state " + _ZIP_SNAPSHOT_AND_ORDER

    _STATE_CACHE_KEY = "geo.state_rollups"
    _SCOPE_CACHE_KEY = "geo.scope"

    # 2026-05-04 (FIX G, round 3): segment-aware per-state counts.
    # Computed live off mip.gold.borrower_360 (the FULL addressable
    # population — same source the unfiltered funnel snapshot reads),
    # with scalar `array_contains` predicates so Databricks SQL parameter
    # binding never has to coerce a Python list into ARRAY<STRING>.
    # Multi-segment borrowers are still distinct-counted exactly once even
    # when the filter selects two of their segments.
    #
    # Round-2 (FIX G) queried lead_population, which has a score >= 50
    # floor baked in. After round-3 reverted the rollup filters (FIX α)
    # the unfiltered map shows the FULL addressable count per state.
    # Querying lead_population for the filtered path would have been
    # inconsistent: filter ON = score-quality subset, filter OFF =
    # full population. Switching to borrower_360 here keeps both paths
    # at the same "addressable" definition, so toggling a segment
    # filter always cuts the count down from the same baseline.
    _STATE_FILTER_SQL_TPL = (
        "SELECT "
        "  state                                        AS state, "
        "  CAST(COUNT(*) AS INT)                         AS addressable, "
        "  CAST(SUM(CASE WHEN in_the_money "
        "                THEN 1 ELSE 0 END) AS INT)      AS in_the_money, "
        "  CAST(SUM(CASE WHEN opportunity_score "
        f"                >= {HIGH_OPPORTUNITY_THRESHOLD} "
        "                THEN 1 ELSE 0 END) AS INT)      AS top_tier_opportunities, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)    AS avg_score, "
        # Same ZIP-coverage disclosure as _STATE_SQL, computed against the
        # same filtered universe the tiles will render — the ZIP layer
        # applies the identical `zip IS NOT NULL AND LENGTH(zip) = 5` gate.
        "  CAST(SUM(CASE WHEN zip IS NULL OR LENGTH(zip) <> 5 "
        "                THEN 1 ELSE 0 END) AS INT)      AS zip_unassigned "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE {filter_clause} "
        "GROUP BY state"
    )

    _COUNTY_FILTER_SQL_TPL = (
        "WITH base AS ( "
        "  SELECT "
        "    county_fips_5 AS fips_5, "
        "    state, "
        "    opportunity_score, "
        "    in_the_money, "
        "    segment_codes "
        f"  FROM {qualify('gold', 'borrower_360')} "
        "  WHERE state = :state "
        "    AND county_fips_5 IS NOT NULL "
        "    AND LENGTH(county_fips_5) = 5 "
        "    AND {filter_clause} "
        "), aggregates AS ( "
        "  SELECT "
        "    fips_5, "
        "    ANY_VALUE(state) AS state, "
        "    CAST(COUNT(*) AS INT) AS addressable_borrowers, "
        "    CAST(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS INT) AS in_the_money_borrowers, "
        f"    CAST(SUM(CASE WHEN opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD} THEN 1 ELSE 0 END) AS INT) AS high_opportunity_borrowers, "
        "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_opportunity_score "
        "  FROM base "
        "  GROUP BY fips_5 "
        "), exploded_segments AS ( "
        "  SELECT fips_5, sc AS segment_code "
        "  FROM base "
        "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
        "  WHERE sc IS NOT NULL "
        "), segment_counts AS ( "
        "  SELECT "
        "    fips_5, "
        "    segment_code, "
        "    COUNT(*) AS cnt, "
        "    ROW_NUMBER() OVER (PARTITION BY fips_5 ORDER BY COUNT(*) DESC, segment_code ASC) AS rn "
        "  FROM exploded_segments "
        "  GROUP BY fips_5, segment_code "
        "), top_segment_per_county AS ( "
        "  SELECT fips_5, segment_code AS top_segment_code "
        "  FROM segment_counts "
        "  WHERE rn = 1 "
        ") "
        "SELECT "
        "  a.fips_5, "
        "  a.state, "
        "  CAST(NULL AS STRING) AS county_name, "
        "  a.addressable_borrowers, "
        "  a.in_the_money_borrowers, "
        "  a.high_opportunity_borrowers, "
        "  a.avg_opportunity_score, "
        "  ts.top_segment_code, "
        "  CAST(NULL AS STRING) AS snapshot_date "
        "FROM aggregates AS a "
        "LEFT JOIN top_segment_per_county AS ts ON ts.fips_5 = a.fips_5 "
        "ORDER BY a.addressable_borrowers DESC"
    )

    _ZIP_FILTER_SQL_TPL = (
        "WITH base AS ( "
        "  SELECT "
        "    zip, "
        "    state, "
        "    county_fips_5, "
        "    borrower_id, "
        "    opportunity_score, "
        "    segment_codes "
        f"  FROM {qualify('gold', 'borrower_360')} "
        # {geo_predicate} is `state = :state` (the live drill) or
        # `county_fips_5 = :fips_5` (reserved for licensed county data).
        # Both sides are repo-owned literals — never caller text.
        "  WHERE {geo_predicate} "
        "    AND zip IS NOT NULL "
        "    AND LENGTH(zip) = 5 "
        "    AND {filter_clause} "
        "), aggregates AS ( "
        "  SELECT "
        "    state, "
        "    county_fips_5, "
        "    zip, "
        "    CAST(COUNT(*) AS INT) AS addressable_borrowers, "
        "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_opportunity_score "
        "  FROM base "
        "  GROUP BY state, county_fips_5, zip "
        "), exploded_segments AS ( "
        "  SELECT state, county_fips_5, zip, sc AS segment_code "
        "  FROM base "
        "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
        "  WHERE sc IS NOT NULL "
        "), segment_counts AS ( "
        "  SELECT "
        "    state, "
        "    county_fips_5, "
        "    zip, "
        "    segment_code, "
        "    COUNT(*) AS cnt, "
        "    ROW_NUMBER() OVER (PARTITION BY state, county_fips_5, zip ORDER BY COUNT(*) DESC, segment_code ASC) AS rn "
        "  FROM exploded_segments "
        "  GROUP BY state, county_fips_5, zip, segment_code "
        "), top_segment_per_zip AS ( "
        "  SELECT state, county_fips_5, zip, segment_code AS top_segment_code "
        "  FROM segment_counts "
        "  WHERE rn = 1 "
        "), ranked_borrowers AS ( "
        "  SELECT "
        "    state, "
        "    county_fips_5, "
        "    zip, "
        "    borrower_id, "
        "    ROW_NUMBER() OVER (PARTITION BY state, county_fips_5, zip ORDER BY opportunity_score DESC, borrower_id ASC) AS rn "
        "  FROM base "
        "), sample_borrower_per_zip AS ( "
        "  SELECT state, county_fips_5, zip, borrower_id AS sample_borrower_id "
        "  FROM ranked_borrowers "
        "  WHERE rn = 1 "
        ") "
        "SELECT "
        "  a.zip, "
        "  a.state, "
        "  a.county_fips_5, "
        "  a.addressable_borrowers, "
        "  a.avg_opportunity_score, "
        "  ts.top_segment_code, "
        "  sb.sample_borrower_id, "
        "  CAST(NULL AS STRING) AS snapshot_date "
        "FROM aggregates AS a "
        "LEFT JOIN top_segment_per_zip AS ts ON ts.state = a.state AND COALESCE(ts.county_fips_5, '') = COALESCE(a.county_fips_5, '') AND ts.zip = a.zip "
        "LEFT JOIN sample_borrower_per_zip AS sb ON sb.state = a.state AND COALESCE(sb.county_fips_5, '') = COALESCE(a.county_fips_5, '') AND sb.zip = a.zip "
        "ORDER BY a.addressable_borrowers DESC"
    )

    def state_rollups(
        self,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> StateRollupResponse:
        if segment_codes or portfolio_criteria is not None:
            # Filtered path. Cache key includes the sorted tuple so two
            # callers asking the same filter share the cache while a
            # different filter doesn't poison the result. The unfiltered
            # `_ALL` path keeps its own _STATE_CACHE_KEY so the most
            # common request (no filter) stays warm.
            normalised = self._normalise_geo_segments(segment_codes)
            portfolio_key = self._portfolio_cache_key(portfolio_criteria)
            cache_key = (
                f"{self._STATE_CACHE_KEY}:filtered:{segment_mode}:"
                f"{','.join(normalised)}:{portfolio_key}"
            )
            def build_filtered() -> StateRollupResponse:
                filter_clause, params = self._geo_filter_clause(
                    normalised,
                    segment_mode=segment_mode,
                    portfolio_criteria=portfolio_criteria,
                )
                sql = self._STATE_FILTER_SQL_TPL.format(
                    filter_clause=filter_clause,
                )
                rows = (
                    self._client.execute(
                        sql,
                        params,
                    )
                    or []
                )
                # in_the_money + top_segment_code aren't carried by the
                # filtered query (they would require an extra join the
                # tooltip doesn't surface). Set sentinel zeros so the
                # response shape stays stable; the FE only reads
                # `addressable` for the filtered tooltip path.
                rollups = [
                    StateRollup(
                        state=str(r.get("state") or "").upper()[:2],
                        addressable=int(r.get("addressable") or 0),
                        in_the_money=int(r.get("in_the_money") or 0),
                        top_tier_opportunities=int(r.get("top_tier_opportunities") or 0),
                        avg_score=int(r.get("avg_score") or 0),
                        top_segment_code=None,
                        zip_unassigned_count=max(0, int(r.get("zip_unassigned") or 0)),
                    )
                    for r in rows
                    if r.get("state") and str(r.get("state")) != "_ALL"
                ]
                return StateRollupResponse(rollups=rollups, snapshot_date=None)

            # Single-flight + stale-if-error: a burst of map interactions
            # hitting an expired key runs ONE warehouse query while the
            # followers wait; a warehouse flap serves the last-good frame
            # instead of blanking the map. Read-only aggregate => safe.
            return self._cache.get_or_set(
                cache_key,
                build_filtered,
                ttl_s=self._cache_ttl_s,
                stale_if_error=True,
            )

        # Unfiltered path — same data shape, plus snapshot_date carried.
        def build_unfiltered() -> StateRollupResponse:
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
                    zip_unassigned_count=max(0, int(r.get("zip_unassigned") or 0)),
                )
                for r in rows
                if r.get("state") and str(r.get("state")) != "_ALL"
            ]
            snapshot_date: str | None = None
            if rows:
                raw = rows[0].get("snapshot_date")
                snapshot_date = str(raw) if raw is not None else None
            return StateRollupResponse(rollups=rollups, snapshot_date=snapshot_date)

        return self._cache.get_or_set(
            self._STATE_CACHE_KEY,
            build_unfiltered,
            ttl_s=self._cache_ttl_s,
            stale_if_error=True,
        )

    @staticmethod
    def _state_segment_filter_clause(
        segment_codes: list[str],
        *,
        segment_mode: str,
    ) -> tuple[str, dict[str, object]]:
        # S8: delegate to the canonical composer so map rollups, card
        # cohorts, and the Lead Queue all compose one predicate per segment.
        return compose_segment_predicate(segment_codes, mode=segment_mode)

    @staticmethod
    def _portfolio_cache_key(portfolio_criteria: PortfolioCriteria | None) -> str:
        if portfolio_criteria is None:
            return "_none"
        return json.dumps(portfolio_criteria.model_dump(exclude_none=True), sort_keys=True)

    @classmethod
    def _geo_filter_clause(
        cls,
        segment_codes: list[str],
        *,
        segment_mode: str,
        portfolio_criteria: PortfolioCriteria | None,
    ) -> tuple[str, dict[str, object]]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if segment_codes:
            segment_clause, segment_params = cls._state_segment_filter_clause(
                segment_codes,
                segment_mode=segment_mode,
            )
            clauses.append(segment_clause)
            params.update(segment_params)
        portfolio_where, portfolio_params = DatabricksPortfolioRepository._build_preview_predicates(
            portfolio_criteria,
        )
        if portfolio_where:
            clauses.append(portfolio_where.removeprefix("WHERE ").strip())
            params.update(portfolio_params)
        return (" AND ".join(clauses) if clauses else "1 = 1"), params

    @staticmethod
    def _normalise_geo_segments(segment_codes: list[str] | None) -> list[str]:
        # Sorted (unlike the lead repository's caller-order contract) so two
        # map interactions selecting the same set share one cache entry.
        return sorted(normalise_segment_codes(segment_codes))

    @staticmethod
    def _filtered_geo_cache_key(
        prefix: str,
        grain: str,
        *,
        segment_codes: list[str],
        segment_mode: str,
    ) -> str:
        return f"{prefix}:{grain}:filtered:{segment_mode}:" f"{','.join(segment_codes)}"

    def county_rollups(
        self,
        state: str,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> CountyRollupResponse:
        """Fetch per-county rollups for the given state.

        ``state`` is normalised to 2-char uppercase before the warehouse
        call so the response is stable regardless of UI casing. Returns
        an empty list + ``snapshot_date=None`` when the state has no
        Cotality-backed county coverage or the CTAS hasn't run yet.
        """
        normalised = str(state or "").upper()[:2]
        normalised_segments = self._normalise_geo_segments(segment_codes)
        use_filtered_path = bool(normalised_segments) or portfolio_criteria is not None
        portfolio_key = self._portfolio_cache_key(portfolio_criteria)
        cache_key = (
            self._filtered_geo_cache_key(
                "geo.county_rollups",
                normalised,
                segment_codes=normalised_segments,
                segment_mode=segment_mode,
            )
            + f":{portfolio_key}"
            if use_filtered_path
            else f"geo.county_rollups:{normalised}"
        )
        def build() -> CountyRollupResponse:
            if use_filtered_path:
                filter_clause, params = self._geo_filter_clause(
                    normalised_segments,
                    segment_mode=segment_mode,
                    portfolio_criteria=portfolio_criteria,
                )
                params["state"] = normalised
                sql = self._COUNTY_FILTER_SQL_TPL.format(
                    filter_clause=filter_clause,
                )
                rows = self._client.execute(sql, params) or []
            else:
                rows = self._client.execute(self._COUNTY_SQL, {"state": normalised}) or []
            rollups = [
                CountyRollup(
                    fips_5=str(r.get("fips_5") or "")[:5],
                    state=str(r.get("state") or "").upper()[:2] or normalised,
                    county_name=(str(r["county_name"]) if r.get("county_name") else None)
                    or county_name_for_fips(str(r.get("fips_5") or "")[:5]),
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
            scope_note = self._scope_note_for_state(
                normalised,
                returned_count=len(rollups),
            )
            return CountyRollupResponse(
                state=normalised,
                rollups=rollups,
                snapshot_date=snapshot_date,
                scope_note=scope_note,
            )

        # Single-flight + stale-if-error: county drill is the hottest map
        # interaction; one expired key must not fan out N identical
        # warehouse queries, and a flap serves last-good (read-only).
        return self._cache.get_or_set(
            cache_key,
            build,
            ttl_s=self._cache_ttl_s,
            stale_if_error=True,
        )

    def _geography_scope(self) -> GeographyScope | None:
        try:
            # Single-flight + stale-if-error: every county/zip rollup calls
            # this; without coalescing an expired scope key multiplies the
            # discovery query per concurrent drill. Stale scope copy is
            # cosmetically fine (it labels coverage notes only).
            return self._cache.get_or_set(
                self._SCOPE_CACHE_KEY,
                lambda: load_geography_scope(self._client),
                ttl_s=self._cache_ttl_s,
                stale_if_error=True,
            )
        except Exception as exc:  # noqa: BLE001 -- scope copy is non-critical
            emit(
                log,
                "geo_scope_discovery_failed",
                level=logging.WARNING,
                dependency="warehouse",
                outcome="degraded",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            return None

    def _scope_note_for_state(self, state: str, *, returned_count: int) -> str | None:
        scope = self._geography_scope()
        if scope is not None:
            return scope.state_scope_label(state, returned_count=returned_count)
        if returned_count <= 0:
            return None
        county_word = "county" if returned_count == 1 else "counties"
        state_uc = str(state or "").upper()[:2]
        return f"Cotality data coverage for {state_uc}: {returned_count:,} {county_word} returned"

    def zip_rollups(
        self,
        fips_5: str | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
        *,
        state: str | None = None,
    ) -> ZipRollupResponse:
        """Fetch per-ZIP rollups keyed by state (live) or county FIPS.

        Callers pass exactly one key. ``state`` is the drill the map uses
        — ``mip.gold.zip_rollup`` is grained on state + ZIP and its
        ``county_fips_5`` is NULL for every row in the current Cotality
        share, so a FIPS-keyed read returns nothing. ``fips_5`` stays
        wired for a future licensed county dataset.
        """
        normalised_state = str(state or "").upper()[:2]
        normalised_fips = str(fips_5 or "")[:5]
        by_state = bool(normalised_state)
        # Distinct cache grains: `state:WA` can never collide with a FIPS.
        grain = f"state:{normalised_state}" if by_state else f"fips:{normalised_fips}"
        normalised_segments = self._normalise_geo_segments(segment_codes)
        use_filtered_path = bool(normalised_segments) or portfolio_criteria is not None
        portfolio_key = self._portfolio_cache_key(portfolio_criteria)
        cache_key = (
            self._filtered_geo_cache_key(
                "geo.zip_rollups",
                grain,
                segment_codes=normalised_segments,
                segment_mode=segment_mode,
            )
            + f":{portfolio_key}"
            if use_filtered_path
            else f"geo.zip_rollups:{grain}"
        )
        def build() -> ZipRollupResponse:
            if use_filtered_path:
                filter_clause, params = self._geo_filter_clause(
                    normalised_segments,
                    segment_mode=segment_mode,
                    portfolio_criteria=portfolio_criteria,
                )
                if by_state:
                    params["state"] = normalised_state
                else:
                    params["fips_5"] = normalised_fips
                sql = self._ZIP_FILTER_SQL_TPL.format(
                    geo_predicate="state = :state" if by_state else "county_fips_5 = :fips_5",
                    filter_clause=filter_clause,
                )
                rows = self._client.execute(sql, params) or []
            elif by_state:
                rows = self._client.execute(
                    self._ZIP_STATE_SQL, {"state": normalised_state}
                ) or []
            else:
                rows = self._client.execute(self._ZIP_SQL, {"fips_5": normalised_fips}) or []
            rollups = [
                ZipRollup(
                    zip=str(r.get("zip") or "")[:5],
                    state=str(r.get("state") or "").upper()[:2],
                    county_fips_5=(str(r["county_fips_5"]) if r.get("county_fips_5") else None),
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
            return ZipRollupResponse(
                fips_5=None if by_state else normalised_fips,
                state=normalised_state or None,
                rollups=rollups,
                snapshot_date=snapshot_date,
            )

        # Single-flight + stale-if-error, same rationale as county_rollups.
        return self._cache.get_or_set(
            cache_key,
            build,
            ttl_s=self._cache_ttl_s,
            stale_if_error=True,
        )
