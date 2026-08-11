"""Databricks-backed segment and geography repositories.

Every headline these repositories serve is an *addressable* count — the
whole population the Cotality share describes. The Lead Queue behind each
card and tile applies the contact-eligibility predicate, so it is always a
strict subset (live 2026-08-11: 216,403 of 5,156,184 borrower_360 rows,
4.2%). Both numbers are correct; the bug was that neither surface stated
the relationship, so a reader took one number and landed on the other.

So both rollups now carry ``contactable`` next to the addressable count,
computed from ``eligibility.eligible_sql_predicate`` — the single module
that owns contactability semantics. See ``databricks_geo_sql`` for the
statements and for why this is not a gold column.
"""

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
from backend.services.geography_scope import GeographyScope, load_geography_scope
from backend.services.observability import emit
from backend.services.repositories import databricks_geo_sql as geo_sql
from backend.services.repositories.databricks_portfolio import DatabricksPortfolioRepository
from backend.services.repositories.databricks_segment_gates import apply_source_gates
from backend.services.repositories.databricks_shared import _parse_facet_mix
from backend.services.resilience import TTLCache
from backend.services.segment_predicates import (
    compose_segment_predicate,
    normalise_segment_codes,
)

log = logging.getLogger("backend.services.repositories.databricks_repo")


def _contactable(row: dict[str, object], addressable: int) -> int | None:
    """Coerce the ``contactable`` aggregate, clamped to ``addressable``.

    ``None`` when the row has no such key: the field is optional on the
    wire, and a caller reading a pre-change cached frame should see "not
    reported" rather than a fabricated zero, which would read as "nobody
    is contactable".

    The clamp is load-bearing on the two unfiltered paths, where a
    *precomputed* rollup row (``gold.segment_population`` /
    ``gold.funnel_snapshot_daily``) is joined to a *live* aggregate over
    ``gold.borrower_360``. A snapshot that lags the base table could make
    the subset out-count its superset, and the UI states the two as a
    relationship ("N contactable of M"). Clamping keeps that sentence
    true; the same reasoning as ``GREATEST(0, ...)`` on ``zip_unassigned``.
    """
    raw = row.get("contactable")
    if raw is None:
        return None
    return max(0, min(addressable, int(raw)))  # type: ignore[arg-type]


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

    # SQL text lives in `databricks_geo_sql`; these aliases keep the
    # class-attribute access the tests pin (`_LIST_SQL`, `_LIST_FILTERED_SQL_TPL`).
    # Both statements now report `contactable` alongside `count` — see that
    # module's docstring for why the predicate is Python-owned.
    _LIST_SQL = geo_sql.SEGMENT_LIST_SQL
    _FACET_MIX_EXPR = geo_sql.FACET_MIX_EXPR
    _LIST_FILTERED_SQL_TPL = geo_sql.SEGMENT_LIST_FILTERED_SQL_TPL

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
                    # The contact-eligible subset of `count`. Both paths
                    # emit it, so a card that says "N of M" is stating one
                    # segment's two aggregates, not two cohorts.
                    contactable=_contactable(row, int(row.get("count") or 0)),
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

    # SQL text lives in `databricks_geo_sql`; these aliases keep the
    # class-attribute access the tests pin (e.g. `_STATE_SQL`,
    # `_STATE_FILTER_SQL_TPL`). Both state statements now report
    # `contactable` alongside `addressable` — see that module's docstring
    # for why the predicate is Python-owned and not a gold column.
    _STATE_SQL = geo_sql.STATE_SQL
    _COUNTY_SQL = geo_sql.COUNTY_SQL
    _ZIP_SELECT = geo_sql.ZIP_SELECT
    _ZIP_SNAPSHOT_AND_ORDER = geo_sql.ZIP_SNAPSHOT_AND_ORDER
    _ZIP_SQL = geo_sql.ZIP_SQL
    _ZIP_STATE_SQL = geo_sql.ZIP_STATE_SQL
    _STATE_FILTER_SQL_TPL = geo_sql.STATE_FILTER_SQL_TPL
    _COUNTY_FILTER_SQL_TPL = geo_sql.COUNTY_FILTER_SQL_TPL
    _ZIP_FILTER_SQL_TPL = geo_sql.ZIP_FILTER_SQL_TPL

    _STATE_CACHE_KEY = "geo.state_rollups"
    _SCOPE_CACHE_KEY = "geo.scope"

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
                        contactable=_contactable(r, int(r.get("addressable") or 0)),
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
                    contactable=_contactable(r, int(r.get("addressable") or 0)),
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
