"""Databricks-backed lead repository.

Kept separate from ``databricks_repo`` so the lead-list/filter SQL can evolve
without making the monolithic repository module harder to review. The public
compatibility import remains ``backend.services.repositories.databricks_repo``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.config.settings import settings
from backend.schemas.lead import LeadSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.pii_redaction import redact_lead_row
from backend.services.repositories.databricks_lead_cohorts import (
    LeadCohortFilters,
    LeadCohortQueries,
)
from backend.services.repositories.databricks_portfolio import build_preview_predicates
from backend.services.repositories.databricks_shared import (
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)
from backend.services.resilience import TTLCache


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

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float | None = None,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = settings.mip_cache_ttl_s if cache_ttl_s is None else cache_ttl_s
        self._cohort_queries = LeadCohortQueries(
            client,
            cache_ttl_s=self._cache_ttl_s,
            clock=time,
        )

    _LIST_BASE_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE 1=1 {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
        "LIMIT {limit}"
    )

    # S8 cross-review B1: the old single-segment template died when the
    # canonical composer took over clause building; _LIST_FILTERED_SQL_TEMPLATE
    # covers one segment and many alike, so no SQL here may hardcode a
    # segment bind-parameter name.
    _LIST_FILTERED_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE {segment_clause} {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
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
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_B360} "
        f"FROM {qualify('gold', 'borrower_360')} b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = b.borrower_id "
        "WHERE 1=1 {state_clause} {zip_clause} {county_clause} {borrower_clause} "
        "{segment_clause} {funnel_stage_clause} {lender_clause} {portfolio_clause} "
        "{lifecycle_clause} {freshness_clause} "
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
        funnel_stage: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> list[LeadSummary]:
        _ = (portfolio_id, cohort_id)
        bounded = self._bound_limit(limit)
        sql_limit = self._sql_fetch_limit(bounded)
        cache_key = self._cache_key(
            "lead_list",
            {
                "segment": segment,
                "portfolio_id": portfolio_id,
                "limit": bounded,
                "state": state,
                "zip_code": zip_code,
                "county_fips": county_fips,
                "county_fipses": county_fipses,
                "state_codes": state_codes,
                "zip_codes": zip_codes,
                "borrower_ids": borrower_ids,
                "segment_codes": segment_codes,
                "segment_mode": segment_mode,
                "target_lender_ref": target_lender_ref,
                "cohort_id": cohort_id,
                "funnel_stage": funnel_stage,
                "portfolio_criteria": self._criteria_key(portfolio_criteria),
                "approval_status": approval_status,
                "outreach_status": outreach_status,
                "aged_days": aged_days,
            },
        )
        cached = self._get_cached_leads(cache_key)
        if cached is not None:
            return cached
        segment_clause, segment_params = self._cohort_queries.segment_filter_clause(
            segment=segment,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
        )

        # FIX β: geo-filtered path bypasses lead_population so the queue
        # row count matches the map tooltip. See the
        # _LIST_BY_GEO_SQL_TEMPLATE docstring above for the full rationale.
        normalised_states = self._cohort_queries.normalise_states(state, state_codes)
        normalised_zips = self._cohort_queries.normalise_zips(zip_code, zip_codes)
        normalised_county = self._cohort_queries.normalise_county_fips(
            county_fips,
            county_fipses,
        )
        normalised_borrower_ids = self._cohort_queries.normalise_borrower_ids(borrower_ids)
        lifecycle_clause, lifecycle_params = self._cohort_queries.lifecycle_filter_clause(
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
            state_sets=(
                self._cohort_queries.state_sets()
                if portfolio_criteria and portfolio_criteria.geography
                else {}
            ),
        )
        portfolio_clause = (
            "AND " + portfolio_where.removeprefix("WHERE ").strip() if portfolio_where else ""
        )
        if "target_lender_ref" in portfolio_params:
            lender_clause = ""
            lender_params = {}
        funnel_stage_clause = self._cohort_queries.funnel_stage_filter_clause(funnel_stage)
        freshness_clause = self._cohort_queries.freshness_clause()

        if (
            normalised_states
            or normalised_zips
            or normalised_county
            or normalised_borrower_ids
            or funnel_stage
            or lender_clause
            or portfolio_clause
        ):
            params: dict[str, object] = dict(segment_params)
            params.update(lender_params)
            params.update(portfolio_params)
            params.update(lifecycle_params)
            state_clause = self._cohort_queries.in_clause(
                column="b.state",
                prefix="state",
                values=normalised_states,
                params=params,
            )
            zip_clause = self._cohort_queries.in_clause(
                column="b.zip",
                prefix="zip",
                values=normalised_zips,
                params=params,
            )
            county_clause = self._cohort_queries.in_clause(
                column="b.county_fips_5",
                prefix="county",
                values=normalised_county,
                params=params,
            )
            borrower_clause = self._cohort_queries.in_clause(
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
                funnel_stage_clause=funnel_stage_clause,
                lender_clause=lender_clause,
                portfolio_clause=portfolio_clause,
                lifecycle_clause=lifecycle_clause,
                freshness_clause=freshness_clause,
                limit=sql_limit,
            )
            rows = self._client.execute(sql, params)
            return self._store_cached_leads(
                cache_key,
                [LeadSummary(**redact_lead_row(r)) for r in rows[:bounded]],
            )

        lifecycle_clause, lifecycle_params = self._cohort_queries.lifecycle_filter_clause(
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
                limit=sql_limit,
            )
            rows = self._client.execute(sql, segment_params)
        else:
            sql = self._LIST_BASE_SQL_TEMPLATE.format(
                lifecycle_clause=lifecycle_clause,
                limit=sql_limit,
            )
            rows = self._client.execute(sql, lifecycle_params)
        return self._store_cached_leads(
            cache_key,
            [LeadSummary(**redact_lead_row(r)) for r in rows[:bounded]],
        )

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
        funnel_stage: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> int:
        cache_key = self._cache_key(
            "lead_count",
            {
                "segment": segment,
                "portfolio_id": portfolio_id,
                "state": state,
                "zip_code": zip_code,
                "county_fips": county_fips,
                "county_fipses": county_fipses,
                "state_codes": state_codes,
                "zip_codes": zip_codes,
                "borrower_ids": borrower_ids,
                "segment_codes": segment_codes,
                "segment_mode": segment_mode,
                "target_lender_ref": target_lender_ref,
                "cohort_id": cohort_id,
                "funnel_stage": funnel_stage,
                "portfolio_criteria": self._criteria_key(portfolio_criteria),
                "approval_status": approval_status,
                "outreach_status": outreach_status,
                "aged_days": aged_days,
            },
        )
        if self._cache_ttl_s > 0:
            cached = self._cache.get(cache_key)
            if isinstance(cached, int):
                return cached
        row = self._cohort_queries.aggregate_row(
            LeadCohortFilters(
                segment=segment,
                state=state,
                zip_code=zip_code,
                county_fips=county_fips,
                county_fipses=county_fipses,
                state_codes=state_codes,
                zip_codes=zip_codes,
                borrower_ids=borrower_ids,
                segment_codes=segment_codes,
                segment_mode=segment_mode,
                target_lender_ref=target_lender_ref,
                funnel_stage=funnel_stage,
                portfolio_criteria=portfolio_criteria,
                approval_status=approval_status,
                outreach_status=outreach_status,
                aged_days=aged_days,
            ),
            aggregate_select="COUNT(*) AS n",
        )
        return self._store_cached_count(cache_key, int(row.get("n") or 0))

    def cohort_identity(
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
        funnel_stage: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> dict[str, str | int]:
        """Return complete-set identity proof for an explicitly requested audit."""

        return self._cohort_queries.cohort_identity(
            LeadCohortFilters(
                segment=segment,
                state=state,
                zip_code=zip_code,
                county_fips=county_fips,
                county_fipses=county_fipses,
                state_codes=state_codes,
                zip_codes=zip_codes,
                borrower_ids=borrower_ids,
                segment_codes=segment_codes,
                segment_mode=segment_mode,
                target_lender_ref=target_lender_ref,
                funnel_stage=funnel_stage,
                portfolio_criteria=portfolio_criteria,
                approval_status=approval_status,
                outreach_status=outreach_status,
                aged_days=aged_days,
            )
        )

    def is_campaign_treatment_member(
        self,
        *,
        borrower_id: str,
        campaign_id: str,
        materialization_id: str,
        delta_version: int,
        treatment_fingerprint: str,
        frequency_cap_days: int,
    ) -> bool:
        return self._cohort_queries.is_campaign_treatment_member(
            borrower_id=borrower_id,
            campaign_id=campaign_id,
            materialization_id=materialization_id,
            delta_version=delta_version,
            treatment_fingerprint=treatment_fingerprint,
            frequency_cap_days=frequency_cap_days,
        )

    def list_with_identity(
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
        funnel_stage: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> tuple[list[LeadSummary], dict[str, str | int]]:
        """Return page rows and complete-set identity from one uncached statement."""

        return self._cohort_queries.list_with_identity(
            LeadCohortFilters(
                segment=segment,
                state=state,
                zip_code=zip_code,
                county_fips=county_fips,
                county_fipses=county_fipses,
                state_codes=state_codes,
                zip_codes=zip_codes,
                borrower_ids=borrower_ids,
                segment_codes=segment_codes,
                segment_mode=segment_mode,
                target_lender_ref=target_lender_ref,
                funnel_stage=funnel_stage,
                portfolio_criteria=portfolio_criteria,
                approval_status=approval_status,
                outreach_status=outreach_status,
                aged_days=aged_days,
            ),
            limit=self._bound_limit(limit),
        )

    @staticmethod
    def _criteria_key(criteria: PortfolioCriteria | None) -> dict[str, Any] | None:
        if criteria is None:
            return None
        return criteria.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _cache_key(prefix: str, payload: dict[str, Any]) -> str:
        stable = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return f"{prefix}:{stable}"

    @staticmethod
    def _copy_leads(rows: list[LeadSummary]) -> list[LeadSummary]:
        return [row.model_copy(deep=True) for row in rows]

    def _get_cached_leads(self, cache_key: str) -> list[LeadSummary] | None:
        if self._cache_ttl_s <= 0:
            return None
        cached = self._cache.get(cache_key)
        if isinstance(cached, list) and all(isinstance(row, LeadSummary) for row in cached):
            return self._copy_leads(cached)
        return None

    def _store_cached_leads(
        self,
        cache_key: str,
        rows: list[LeadSummary],
    ) -> list[LeadSummary]:
        if self._cache_ttl_s > 0:
            self._cache.set(cache_key, self._copy_leads(rows), self._cache_ttl_s)
        return self._copy_leads(rows)

    def _store_cached_count(self, cache_key: str, value: int) -> int:
        if self._cache_ttl_s > 0:
            self._cache.set(cache_key, value, self._cache_ttl_s)
        return value

    @staticmethod
    def _normalise_states(
        state: str | None,
        state_codes: list[str] | None,
    ) -> list[str]:
        return LeadCohortQueries.normalise_states(state, state_codes)

    @staticmethod
    def _normalise_zips(
        zip_code: str | None,
        zip_codes: list[str] | None,
    ) -> list[str]:
        return LeadCohortQueries.normalise_zips(zip_code, zip_codes)

    @staticmethod
    def _normalise_county_fips(
        county_fips: str | None,
        county_fipses: list[str] | None = None,
    ) -> list[str]:
        return LeadCohortQueries.normalise_county_fips(county_fips, county_fipses)

    @staticmethod
    def _normalise_borrower_ids(borrower_ids: list[str] | None) -> list[str]:
        return LeadCohortQueries.normalise_borrower_ids(borrower_ids)

    @staticmethod
    def _lifecycle_filter_clause(
        *,
        source_alias: str,
        approval_status: str | None,
        outreach_status: str | None,
        aged_days: int | None,
    ) -> tuple[str, dict[str, object]]:
        return LeadCohortQueries.lifecycle_filter_clause(
            source_alias=source_alias,
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )

    @staticmethod
    def _funnel_stage_filter_clause(funnel_stage: str | None) -> str:
        return LeadCohortQueries.funnel_stage_filter_clause(funnel_stage)

    @staticmethod
    def _in_clause(
        *,
        column: str,
        prefix: str,
        values: list[str],
        params: dict[str, object],
    ) -> str:
        return LeadCohortQueries.in_clause(
            column=column,
            prefix=prefix,
            values=values,
            params=params,
        )

    @staticmethod
    def _normalise_segment_codes(
        segment: str | None,
        segment_codes: list[str] | None,
    ) -> list[str]:
        return LeadCohortQueries.normalise_segment_codes(segment, segment_codes)

    @classmethod
    def _segment_filter_clause(
        cls,
        *,
        segment: str | None,
        segment_codes: list[str] | None,
        segment_mode: str,
    ) -> tuple[str, dict[str, object]]:
        return LeadCohortQueries.segment_filter_clause(
            segment=segment,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
        )

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

    @classmethod
    def _sql_fetch_limit(cls, bounded_limit: int) -> int:
        """Fetch one extra row while preserving the public row cap.

        The route still returns at most ``bounded_limit`` rows. Internally
        selecting one additional row avoids reusing stale warehouse result
        cache entries keyed to a previous exact ``LIMIT 500`` query during a
        gold/lifecycle refresh window.
        """

        return min(int(bounded_limit) + 1, cls.MAX_LIMIT + 1)

    def _freshness_clause(self) -> str:
        return self._cohort_queries.freshness_clause()
