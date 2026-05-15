"""Databricks-backed lead repository.

Kept separate from ``databricks_repo`` so the lead-list/filter SQL can evolve
without making the monolithic repository module harder to review. The public
compatibility import remains ``backend.services.repositories.databricks_repo``.
"""

from __future__ import annotations

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead import LeadSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.pii_redaction import redact_lead_row
from backend.services.repositories.databricks_portfolio import build_preview_predicates
from backend.services.repositories.databricks_shared import (
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)
from backend.services.state_footprint import get_state_footprint_resolver


def _state_sets() -> dict[str, list[str]]:
    """Build the active geography-label mapping used by portfolio criteria."""
    resolver = get_state_footprint_resolver()
    footprint_codes = resolver.state_codes()
    state_name_map = resolver.state_name_to_codes()
    all_key = f"all {len(footprint_codes)} states"
    return {
        **state_name_map,
        all_key: list(footprint_codes),
    }


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
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE 1=1 {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
        "LIMIT {limit}"
    )

    _LIST_BY_SEGMENT_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE array_contains(segment_codes, :segment) {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
        "LIMIT {limit}"
    )

    _LIST_FILTERED_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE {segment_clause} {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
        "LIMIT {limit}"
    )

    _COUNT_BASE_SQL = (
        f"SELECT COUNT(*) AS n FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE 1=1 {lifecycle_clause}"
    )

    _COUNT_FILTERED_SQL_TEMPLATE = (
        f"SELECT COUNT(*) AS n FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE {segment_clause} {lifecycle_clause}"
    )

    _COUNT_BY_GEO_SQL_TEMPLATE = (
        f"SELECT COUNT(*) AS n FROM {qualify('gold', 'borrower_360')} b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = b.borrower_id "
        "WHERE 1=1 {state_clause} {zip_clause} {county_clause} {borrower_clause} "
        "{segment_clause} {lender_clause} {portfolio_clause} {lifecycle_clause}"
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
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_B360} "
        f"FROM {qualify('gold', 'borrower_360')} b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = b.borrower_id "
        "WHERE 1=1 {state_clause} {zip_clause} {county_clause} {borrower_clause} "
        "{segment_clause} {lender_clause} {portfolio_clause} {lifecycle_clause} "
        "ORDER BY b.opportunity_score DESC, b.borrower_id ASC "
        "LIMIT {limit}"
    )

    def list(
        self,
        segment: str | None,
        portfolio_id: str | None,
        limit: int | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        county_fips: str | None = None,
        county_fipses: list[str] | None = None,
        state_codes: list[str] | None = None,
        zip_codes: list[str] | None = None,
        borrower_ids: list[str] | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        target_lender_ref: str | None = None,
        cohort_id: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> list[LeadSummary]:
        _ = (portfolio_id, cohort_id)
        bounded = self._bound_limit(limit)
        segment_clause, segment_params = self._segment_filter_clause(
            segment=segment,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
        )

        # FIX β: geo-filtered path bypasses lead_population so the queue
        # row count matches the map tooltip. See the
        # _LIST_BY_GEO_SQL_TEMPLATE docstring above for the full rationale.
        normalised_states = self._normalise_states(state, state_codes)
        normalised_zips = self._normalise_zips(zip_code, zip_codes)
        normalised_county = self._normalise_county_fips(county_fips, county_fipses)
        normalised_borrower_ids = self._normalise_borrower_ids(borrower_ids)
        lifecycle_clause, lifecycle_params = self._lifecycle_filter_clause(
            source_alias="b",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        lender_clause = ""
        lender_params: dict[str, object] = {}
        if target_lender_ref and target_lender_ref.strip().lower() != "all":
            lender_clause = "AND b.current_lender_ref = :target_lender_ref"
            lender_params["target_lender_ref"] = target_lender_ref.strip()
        portfolio_where, portfolio_params = build_preview_predicates(
            portfolio_criteria,
            state_sets=_state_sets() if portfolio_criteria and portfolio_criteria.geography else {},
        )
        portfolio_clause = (
            "AND " + portfolio_where.removeprefix("WHERE ").strip() if portfolio_where else ""
        )
        if "target_lender_ref" in portfolio_params:
            lender_clause = ""
            lender_params = {}

        if (
            normalised_states
            or normalised_zips
            or normalised_county
            or normalised_borrower_ids
            or lender_clause
            or portfolio_clause
        ):
            params: dict[str, object] = dict(segment_params)
            params.update(lender_params)
            params.update(portfolio_params)
            params.update(lifecycle_params)
            state_clause = self._in_clause(
                column="b.state",
                prefix="state",
                values=normalised_states,
                params=params,
            )
            zip_clause = self._in_clause(
                column="b.zip",
                prefix="zip",
                values=normalised_zips,
                params=params,
            )
            county_clause = self._in_clause(
                column="b.county_fips_5",
                prefix="county",
                values=normalised_county,
                params=params,
            )
            borrower_clause = self._in_clause(
                column="b.borrower_id",
                prefix="borrower_id",
                values=normalised_borrower_ids,
                params=params,
            )
            geo_segment_clause = f"AND {segment_clause}" if segment_clause else ""
            sql = self._LIST_BY_GEO_SQL_TEMPLATE.format(
                state_clause=state_clause,
                zip_clause=zip_clause,
                county_clause=county_clause,
                borrower_clause=borrower_clause,
                segment_clause=geo_segment_clause,
                lender_clause=lender_clause,
                portfolio_clause=portfolio_clause,
                lifecycle_clause=lifecycle_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, params)
            return [LeadSummary(**redact_lead_row(r)) for r in rows]

        lifecycle_clause, lifecycle_params = self._lifecycle_filter_clause(
            source_alias="lp",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        if segment_clause:
            if lender_clause:
                segment_clause = f"{segment_clause} {lender_clause}"
                segment_params = {**segment_params, **lender_params}
            segment_params = {**segment_params, **lifecycle_params}
            sql = self._LIST_FILTERED_SQL_TEMPLATE.format(
                segment_clause=segment_clause,
                lifecycle_clause=lifecycle_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, segment_params)
        else:
            sql = self._LIST_BASE_SQL_TEMPLATE.format(
                lifecycle_clause=lifecycle_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, lifecycle_params)
        return [LeadSummary(**redact_lead_row(r)) for r in rows]

    def count(
        self,
        segment: str | None,
        portfolio_id: str | None,
        state: str | None = None,
        zip_code: str | None = None,
        county_fips: str | None = None,
        county_fipses: list[str] | None = None,
        state_codes: list[str] | None = None,
        zip_codes: list[str] | None = None,
        borrower_ids: list[str] | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        target_lender_ref: str | None = None,
        cohort_id: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> int:
        _ = (portfolio_id, cohort_id)
        segment_clause, segment_params = self._segment_filter_clause(
            segment=segment,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
        )
        normalised_states = self._normalise_states(state, state_codes)
        normalised_zips = self._normalise_zips(zip_code, zip_codes)
        normalised_county = self._normalise_county_fips(county_fips, county_fipses)
        normalised_borrower_ids = self._normalise_borrower_ids(borrower_ids)
        lifecycle_clause_geo, lifecycle_params_geo = self._lifecycle_filter_clause(
            source_alias="b",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        lender_clause = ""
        lender_params: dict[str, object] = {}
        if target_lender_ref and target_lender_ref.strip().lower() != "all":
            lender_clause = "AND b.current_lender_ref = :target_lender_ref"
            lender_params["target_lender_ref"] = target_lender_ref.strip()
        portfolio_where, portfolio_params = build_preview_predicates(
            portfolio_criteria,
            state_sets=_state_sets() if portfolio_criteria and portfolio_criteria.geography else {},
        )
        portfolio_clause = (
            "AND " + portfolio_where.removeprefix("WHERE ").strip() if portfolio_where else ""
        )
        if "target_lender_ref" in portfolio_params:
            lender_clause = ""
            lender_params = {}
        if (
            normalised_states
            or normalised_zips
            or normalised_county
            or normalised_borrower_ids
            or lender_clause
            or portfolio_clause
        ):
            params: dict[str, object] = dict(segment_params)
            params.update(lender_params)
            params.update(portfolio_params)
            params.update(lifecycle_params_geo)
            sql = self._COUNT_BY_GEO_SQL_TEMPLATE.format(
                state_clause=self._in_clause(
                    column="b.state", prefix="state", values=normalised_states, params=params
                ),
                zip_clause=self._in_clause(
                    column="b.zip", prefix="zip", values=normalised_zips, params=params
                ),
                county_clause=self._in_clause(
                    column="b.county_fips_5",
                    prefix="county",
                    values=normalised_county,
                    params=params,
                ),
                borrower_clause=self._in_clause(
                    column="b.borrower_id",
                    prefix="borrower_id",
                    values=normalised_borrower_ids,
                    params=params,
                ),
                segment_clause=f"AND {segment_clause}" if segment_clause else "",
                lender_clause=lender_clause,
                portfolio_clause=portfolio_clause,
                lifecycle_clause=lifecycle_clause_geo,
            )
            row = self._client.execute_one(sql, params)
            return int((row or {}).get("n") or 0)
        lifecycle_clause, lifecycle_params = self._lifecycle_filter_clause(
            source_alias="lp",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        if segment_clause:
            segment_params = {**segment_params, **lifecycle_params}
            row = self._client.execute_one(
                self._COUNT_FILTERED_SQL_TEMPLATE.format(
                    segment_clause=segment_clause,
                    lifecycle_clause=lifecycle_clause,
                ),
                segment_params,
            )
            return int((row or {}).get("n") or 0)
        row = self._client.execute_one(
            self._COUNT_BASE_SQL.format(lifecycle_clause=lifecycle_clause),
            lifecycle_params,
        )
        return int((row or {}).get("n") or 0)

    @staticmethod
    def _normalise_states(
        state: str | None,
        state_codes: list[str] | None,
    ) -> list[str]:
        raw = ([state] if state else []) + (state_codes or [])
        out: list[str] = []
        for value in raw:
            code = str(value or "").upper()[:2]
            if len(code) == 2 and code.isalpha() and code not in out:
                out.append(code)
        return out

    @staticmethod
    def _normalise_zips(
        zip_code: str | None,
        zip_codes: list[str] | None,
    ) -> list[str]:
        raw = ([zip_code] if zip_code else []) + (zip_codes or [])
        out: list[str] = []
        for value in raw:
            code = str(value or "").strip()[:5]
            if len(code) == 5 and code.isdigit() and code not in out:
                out.append(code)
        return out

    @staticmethod
    def _normalise_county_fips(
        county_fips: str | None,
        county_fipses: list[str] | None = None,
    ) -> list[str]:
        raw = ([county_fips] if county_fips else []) + (county_fipses or [])
        out: list[str] = []
        for value in raw:
            code = str(value or "").strip()[:5]
            if len(code) == 5 and code.isdigit() and code not in out:
                out.append(code)
        return out

    @staticmethod
    def _normalise_borrower_ids(
        borrower_ids: list[str] | None,
    ) -> list[str]:
        out: list[str] = []
        for value in borrower_ids or []:
            try:
                borrower_id = validate_public_borrower_id(str(value or ""))
            except ValueError:
                continue
            if borrower_id not in out:
                out.append(borrower_id)
        return out

    @staticmethod
    def _lifecycle_filter_clause(
        *,
        source_alias: str,
        approval_status: str | None,
        outreach_status: str | None,
        aged_days: int | None,
    ) -> tuple[str, dict[str, object]]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if approval_status:
            clauses.append(
                f"COALESCE(ls.approval_status, {source_alias}.approval_status, 'pending') = :approval_status"
            )
            params["approval_status"] = approval_status
        if outreach_status:
            clauses.append("COALESCE(ls.outreach_status, 'none') = :outreach_status")
            params["outreach_status"] = outreach_status
        if aged_days is not None:
            bounded_days = max(1, min(int(aged_days), 90))
            clauses.append(
                f"COALESCE(ls.approval_status, {source_alias}.approval_status, 'pending') = 'approved'"
            )
            clauses.append(
                "ls.approved_at <= current_timestamp() - INTERVAL " f"{bounded_days} DAYS"
            )
            clauses.append("ls.outreach_at IS NULL")
        if not clauses:
            return "", {}
        return "AND " + " AND ".join(clauses), params

    @staticmethod
    def _in_clause(
        *,
        column: str,
        prefix: str,
        values: list[str],
        params: dict[str, object],
    ) -> str:
        if not values:
            return ""
        placeholders: list[str] = []
        for i, value in enumerate(values):
            key = f"{prefix}_{i}"
            params[key] = value
            placeholders.append(f":{key}")
        if len(placeholders) == 1:
            return f"AND {column} = {placeholders[0]}"
        return f"AND {column} IN ({', '.join(placeholders)})"

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
                f"array_contains(segment_codes, :segment_{i})" for i in range(len(codes))
            )
            return clause, params
        params = {f"segment_{i}": code for i, code in enumerate(codes)}
        clause = " OR ".join(
            f"array_contains(segment_codes, :segment_{i})" for i in range(len(codes))
        )
        return f"({clause})", params

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
