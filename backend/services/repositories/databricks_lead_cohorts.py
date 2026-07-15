"""Matched-cohort SQL and atomic identity reads for the lead repository."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead import LeadSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.growth_agent_handoff import (
    GrowthAgentHandoffInvalid as GrowthAgentHandoffInvalid,
)
from backend.services.growth_agent_handoff import (
    GrowthAgentHandoffProof as GrowthAgentHandoffProof,
)
from backend.services.growth_agent_handoff import (
    GrowthAgentHandoffStale as GrowthAgentHandoffStale,
)
from backend.services.growth_agent_handoff import (
    handoff_filters_fingerprint as handoff_filters_fingerprint,
)
from backend.services.growth_agent_handoff import (
    issue_growth_agent_handoff as issue_growth_agent_handoff,
)
from backend.services.growth_agent_handoff import (
    validate_growth_agent_handoff_identity as validate_growth_agent_handoff_identity,
)
from backend.services.growth_agent_handoff import (
    verify_growth_agent_handoff as verify_growth_agent_handoff,
)
from backend.services.pii_redaction import redact_lead_row
from backend.services.repositories.databricks_portfolio import build_preview_predicates
from backend.services.repositories.databricks_shared import (
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD
from backend.services.segment_predicates import (
    compose_segment_predicate,
    normalise_segment_codes,
)
from backend.services.state_footprint import get_state_footprint_resolver


@dataclass(frozen=True)
class LeadCohortFilters:
    """Inputs that determine the complete set behind a Lead Queue query."""

    segment: str | None
    state: str | None = None
    zip_code: str | None = None
    county_fips: str | None = None
    county_fipses: list[str] | None = None
    state_codes: list[str] | None = None
    zip_codes: list[str] | None = None
    borrower_ids: list[str] | None = None
    segment_codes: list[str] | None = None
    segment_mode: str = "any"
    target_lender_ref: str | None = None
    funnel_stage: str | None = None
    portfolio_criteria: PortfolioCriteria | None = None
    approval_status: str | None = None
    outreach_status: str | None = None
    aged_days: int | None = None

    @property
    def needs_lifecycle_snapshot(self) -> bool:
        return bool(self.approval_status or self.outreach_status or self.aged_days is not None)


class LeadCohortQueries:
    """Compose matched cohorts and bind their rows to one source snapshot."""

    _COUNT_BASE_SQL = (
        f"SELECT {{aggregate_select}} FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE 1=1 {lifecycle_clause}"
    )

    _COUNT_FILTERED_SQL_TEMPLATE = (
        f"SELECT {{aggregate_select}} FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE {segment_clause} {lifecycle_clause}"
    )

    _COUNT_BY_GEO_SQL_TEMPLATE = (
        f"SELECT {{aggregate_select}} FROM {qualify('gold', 'borrower_360')} b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = b.borrower_id "
        "WHERE 1=1 {state_clause} {zip_clause} {county_clause} {borrower_clause} "
        "{segment_clause} {funnel_stage_clause} {lender_clause} {portfolio_clause} "
        "{lifecycle_clause} {freshness_clause}"
    )

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache_ttl_s: float,
        clock: ModuleType = time,
    ) -> None:
        self._client = client
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock

    def aggregate_row(
        self,
        filters: LeadCohortFilters,
        *,
        aggregate_select: str,
    ) -> dict[str, Any]:
        matched_sql, params, uses_lead_population = self.matched_cohort_sql(
            filters,
            include_lead_columns=False,
        )
        rendered_aggregate = aggregate_select.format(alias="m")
        if "snapshot_id" in rendered_aggregate:
            rendered_aggregate = rendered_aggregate.replace(
                self._legacy_snapshot_select(),
                "MAX(snapshot_validation.snapshot_id) AS snapshot_id",
            )
        statement = f"""
WITH matched AS (
  {matched_sql}
),
{self._snapshot_ctes(
    uses_lead_population=uses_lead_population,
    needs_lifecycle_snapshot=filters.needs_lifecycle_snapshot,
)}
SELECT {rendered_aggregate}
FROM matched m
CROSS JOIN snapshot_validation
"""
        return self._client.execute_one(statement, params) or {}

    def cohort_identity(self, filters: LeadCohortFilters) -> dict[str, str | int]:
        row = self.aggregate_row(
            filters,
            aggregate_select=self._identity_aggregate_select("{alias}"),
        )
        return self._parse_identity(row)

    def list_with_identity(
        self,
        filters: LeadCohortFilters,
        *,
        limit: int,
    ) -> tuple[list[LeadSummary], dict[str, str | int]]:
        """Return page rows and complete-set identity from one uncached statement."""

        matched_sql, params, uses_lead_population = self.matched_cohort_sql(
            filters,
            include_lead_columns=True,
        )
        statement = f"""
WITH matched AS (
  {matched_sql}
),
{self._snapshot_ctes(
    uses_lead_population=uses_lead_population,
    needs_lifecycle_snapshot=filters.needs_lifecycle_snapshot,
)},
identity AS (
  SELECT
    COUNT(DISTINCT m.borrower_id) AS __identity_total,
    sha2(concat_ws('|', sort_array(collect_set(CAST(m.borrower_id AS STRING)))), 256)
      AS __cohort_digest,
    MAX(snapshot_validation.snapshot_id) AS __snapshot_id
  FROM matched m
  CROSS JOIN snapshot_validation
),
ranked AS (
  SELECT * FROM matched
  ORDER BY __rank_order DESC, borrower_id ASC
  LIMIT {limit}
)
SELECT ranked.*, identity.__identity_total, identity.__cohort_digest, identity.__snapshot_id
FROM identity
LEFT JOIN ranked ON TRUE
"""
        rows = self._client.execute(statement, params)
        if not rows:
            raise ValueError("Lead Queue cohort identity proof returned no metadata")
        metadata = rows[0]
        identity = self._parse_identity(
            metadata,
            total_key="__identity_total",
            digest_key="__cohort_digest",
            snapshot_key="__snapshot_id",
        )
        leads = [
            LeadSummary(
                **redact_lead_row(
                    {key: value for key, value in row.items() if not key.startswith("__")}
                )
            )
            for row in rows
            if row.get("borrower_id")
        ]
        return leads, identity

    def matched_cohort_sql(
        self,
        filters: LeadCohortFilters,
        *,
        include_lead_columns: bool,
    ) -> tuple[str, dict[str, object], bool]:
        segment_clause, segment_params = self.segment_filter_clause(
            segment=filters.segment,
            segment_codes=filters.segment_codes,
            segment_mode=filters.segment_mode,
        )
        normalised_states = self.normalise_states(filters.state, filters.state_codes)
        normalised_zips = self.normalise_zips(filters.zip_code, filters.zip_codes)
        normalised_county = self.normalise_county_fips(
            filters.county_fips,
            filters.county_fipses,
        )
        normalised_borrower_ids = self.normalise_borrower_ids(filters.borrower_ids)
        lifecycle_clause_geo, lifecycle_params_geo = self.lifecycle_filter_clause(
            source_alias="b",
            approval_status=filters.approval_status,
            outreach_status=filters.outreach_status,
            aged_days=filters.aged_days,
        )
        lender_clause = ""
        lender_params: dict[str, object] = {}
        if filters.target_lender_ref and filters.target_lender_ref.strip().lower() != "all":
            lender_clause = "AND b.current_lender_ref = :target_lender_ref"
            lender_params["target_lender_ref"] = filters.target_lender_ref.strip()
        portfolio_where, portfolio_params = build_preview_predicates(
            filters.portfolio_criteria,
            state_sets=(
                self.state_sets()
                if filters.portfolio_criteria and filters.portfolio_criteria.geography
                else {}
            ),
        )
        portfolio_clause = (
            "AND " + portfolio_where.removeprefix("WHERE ").strip() if portfolio_where else ""
        )
        if "target_lender_ref" in portfolio_params:
            lender_clause = ""
            lender_params = {}
        funnel_stage_clause = self.funnel_stage_filter_clause(filters.funnel_stage)
        freshness_clause = self.freshness_clause()
        geo_projection = (
            f"{_LEAD_POPULATION_SELECT_FROM_B360}, b.opportunity_score AS __rank_order"
            if include_lead_columns
            else "b.borrower_id"
        )
        lead_projection = (
            f"{_LEAD_POPULATION_SELECT_FROM_LP}, -lp.rank_overall AS __rank_order"
            if include_lead_columns
            else "lp.borrower_id"
        )
        if (
            normalised_states
            or normalised_zips
            or normalised_county
            or normalised_borrower_ids
            or filters.funnel_stage
            or lender_clause
            or portfolio_clause
        ):
            params: dict[str, object] = dict(segment_params)
            params.update(lender_params)
            params.update(portfolio_params)
            params.update(lifecycle_params_geo)
            sql = self._COUNT_BY_GEO_SQL_TEMPLATE.format(
                aggregate_select=geo_projection,
                state_clause=self.in_clause(
                    column="b.state",
                    prefix="state",
                    values=normalised_states,
                    params=params,
                ),
                zip_clause=self.in_clause(
                    column="b.zip",
                    prefix="zip",
                    values=normalised_zips,
                    params=params,
                ),
                county_clause=self.in_clause(
                    column="b.county_fips_5",
                    prefix="county",
                    values=normalised_county,
                    params=params,
                ),
                borrower_clause=self.in_clause(
                    column="b.borrower_id",
                    prefix="borrower_id",
                    values=normalised_borrower_ids,
                    params=params,
                ),
                segment_clause=f"AND {segment_clause}" if segment_clause else "",
                funnel_stage_clause=funnel_stage_clause,
                lender_clause=lender_clause,
                portfolio_clause=portfolio_clause,
                lifecycle_clause=lifecycle_clause_geo,
                freshness_clause=freshness_clause,
            )
            return sql, params, False
        lifecycle_clause, lifecycle_params = self.lifecycle_filter_clause(
            source_alias="lp",
            approval_status=filters.approval_status,
            outreach_status=filters.outreach_status,
            aged_days=filters.aged_days,
        )
        if segment_clause:
            segment_params = {**segment_params, **lifecycle_params}
            return (
                self._COUNT_FILTERED_SQL_TEMPLATE.format(
                    aggregate_select=lead_projection,
                    segment_clause=segment_clause,
                    lifecycle_clause=lifecycle_clause,
                ),
                segment_params,
                True,
            )
        return (
            self._COUNT_BASE_SQL.format(
                aggregate_select=lead_projection,
                lifecycle_clause=lifecycle_clause,
            ),
            lifecycle_params,
            True,
        )

    @staticmethod
    def state_sets() -> dict[str, list[str]]:
        """Build the active geography-label mapping used by portfolio criteria."""

        resolver = get_state_footprint_resolver()
        footprint_codes = resolver.state_codes()
        state_name_map = resolver.state_name_to_codes()
        all_key = f"all {len(footprint_codes)} states"
        return {
            **state_name_map,
            all_key: list(footprint_codes),
        }

    @staticmethod
    def normalise_states(
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
    def normalise_zips(
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
    def normalise_county_fips(
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
    def normalise_borrower_ids(borrower_ids: list[str] | None) -> list[str]:
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
    def lifecycle_filter_clause(
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
    def funnel_stage_filter_clause(funnel_stage: str | None) -> str:
        """Return the exact gold-funnel predicate used by analytics drilldowns."""

        stage = str(funnel_stage or "").strip().lower()
        if not stage or stage == "addressable":
            return ""
        if stage == "in_the_money":
            return "AND b.in_the_money = TRUE"
        if stage == "high_opportunity":
            return f"AND b.opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD}"
        if stage == "offer_recommended":
            return (
                "AND b.recommended_offer_code IS NOT NULL "
                "AND b.recommended_offer_code <> 'nurture'"
            )
        if stage == "approved":
            return "AND COALESCE(ls.approval_status, 'pending') = 'approved'"
        if stage == "actioned":
            return "AND COALESCE(ls.outreach_status, 'none') = 'actioned'"
        raise ValueError(f"unsupported funnel_stage: {funnel_stage}")

    @staticmethod
    def in_clause(
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
    def segment_filter_clause(
        *,
        segment: str | None,
        segment_codes: list[str] | None,
        segment_mode: str,
    ) -> tuple[str, dict[str, object]]:
        # S8: delegate to the canonical composer so the Lead Queue ranks the
        # exact cohort the Segment Intelligence cards previewed.
        return compose_segment_predicate(
            LeadCohortQueries.normalise_segment_codes(segment, segment_codes),
            mode=segment_mode,
        )

    @staticmethod
    def normalise_segment_codes(
        segment: str | None,
        segment_codes: list[str] | None,
    ) -> list[str]:
        raw = segment_codes if segment_codes else ([segment] if segment else [])
        return normalise_segment_codes(raw)

    def freshness_clause(self) -> str:
        """Bound warehouse result-cache reuse for interactive drilldowns.

        Drilldowns read ``gold.borrower_360`` joined to the lifecycle mirror,
        which can change during deploy-time syncs while query text stays
        otherwise identical. An app-generated no-op literal predicate prevents
        Databricks SQL from serving a stale exact-query result; the app's
        short ``TTLCache`` remains the bounded reuse layer.

        2026-06-11 audit P1-6 refinement (staleness wording corrected per
        re-audit): the marker is bucketed to the repository cache TTL
        instead of nanosecond-unique, so sibling app processes/workers
        (each with their own in-process TTLCache) can reuse the warehouse
        result cache instead of re-scanning borrower_360 (5.16M rows,
        3.6-6.6s measured live). Staleness bound: the LAYERS COMPOUND — a
        warehouse result computed at the start of a marker window can be
        stored into the app cache at the end of it, so worst-case data age
        is ~2x ``cache_ttl_s`` (~10 min at the 300s default), not 1x. That
        remains far below the gold-refresh cadence, which is the actual
        data-change rate. With app caching disabled (ttl <= 0) the marker
        stays nanosecond-unique, preserving the original always-fresh
        behaviour.
        """

        if self._cache_ttl_s <= 0:
            marker: int = self._clock.time_ns()
        else:
            marker = int(self._clock.time() // self._cache_ttl_s)
        return f"AND {marker} = {marker}"

    @staticmethod
    def _identity_aggregate_select(alias: str) -> str:
        return (
            f"COUNT(DISTINCT {alias}.borrower_id) AS n, "
            "sha2(concat_ws('|', sort_array(collect_set(CAST("
            f"{alias}.borrower_id AS STRING)))), 256) AS cohort_digest, "
            f"{LeadCohortQueries._legacy_snapshot_select()}"
        )

    @staticmethod
    def _legacy_snapshot_select() -> str:
        return (
            "(SELECT CAST(MAX(snapshot_src.refreshed_at) AS STRING) "
            f"FROM {qualify('gold', 'borrower_360')} snapshot_src) AS snapshot_id"
        )

    @staticmethod
    def _snapshot_ctes(*, uses_lead_population: bool, needs_lifecycle_snapshot: bool) -> str:
        version_columns = [
            f"(SELECT MAX(refreshed_at) FROM {qualify('gold', 'borrower_360')}) "
            "AS borrower_360_at"
        ]
        if uses_lead_population:
            version_columns.append(
                f"(SELECT MAX(refreshed_at) FROM {qualify('gold', 'lead_population')}) "
                "AS lead_population_at"
            )
        if needs_lifecycle_snapshot:
            version_columns.append(
                f"(SELECT MAX(refreshed_at) FROM {qualify('gold', 'borrower_lifecycle_state')}) "
                "AS lifecycle_at"
            )
        rendered_version_columns = ",\n    ".join(version_columns)
        lead_check = (
            "AND versions.lead_population_at = anchor.refresh_at" if uses_lead_population else ""
        )
        lifecycle_check = (
            "AND versions.lifecycle_at IS NOT NULL" if needs_lifecycle_snapshot else ""
        )
        lifecycle_token = (
            ", '|lifecycle:', CAST(versions.lifecycle_at AS STRING)"
            if needs_lifecycle_snapshot
            else ""
        )
        return f"""
refresh_anchor AS (
  SELECT run_id, refresh_at
  FROM {qualify('ref', 'refresh_run_state')}
  ORDER BY captured_at DESC
  LIMIT 1
),
source_versions AS (
  SELECT
    {rendered_version_columns}
),
snapshot_validation AS (
  SELECT CASE
    WHEN anchor.refresh_at IS NOT NULL
      AND anchor.run_id IS NOT NULL
      AND versions.borrower_360_at = anchor.refresh_at
      {lead_check}
      {lifecycle_check}
    THEN sha2(
      concat(
        'gold-refresh:', CAST(anchor.run_id AS STRING), '|',
        CAST(anchor.refresh_at AS STRING){lifecycle_token}
      ),
      256
    )
    ELSE NULL
  END AS snapshot_id
  FROM refresh_anchor anchor
  CROSS JOIN source_versions versions
)"""

    @staticmethod
    def _parse_identity(
        row: dict[str, Any],
        *,
        total_key: str = "n",
        digest_key: str = "cohort_digest",
        snapshot_key: str = "snapshot_id",
    ) -> dict[str, str | int]:
        digest = str(row.get(digest_key) or "").strip().lower()
        snapshot_id = str(row.get(snapshot_key) or "").strip()
        if len(digest) != 64 or not snapshot_id:
            raise ValueError("Lead Queue cohort identity proof is incomplete")
        return {
            "total": int(row.get(total_key) or 0),
            "cohort_digest": digest,
            "snapshot_id": snapshot_id,
        }


def normalise_growth_agent_handoff_filters(
    criteria: Mapping[str, object],
) -> dict[str, object]:
    """Canonicalize stored Growth Agent criteria to Lead Queue semantics."""

    raw_filters = criteria.get("lead_queue_filters", criteria)
    if not isinstance(raw_filters, Mapping):
        raise ValueError("Growth Agent handoff filters are invalid")
    if raw_filters.get("borrower_ids"):
        raise ValueError("Growth Agent handoffs cannot contain borrower ids")

    portfolio_raw = raw_filters.get("portfolio_criteria")
    if portfolio_raw is None:
        portfolio_criteria = None
    elif isinstance(portfolio_raw, Mapping):
        portfolio_criteria = PortfolioCriteria.model_validate(dict(portfolio_raw))
    else:
        raise ValueError("Growth Agent portfolio criteria are invalid")

    return normalise_lead_queue_handoff_filters(
        LeadCohortFilters(
            segment=_optional_text(raw_filters.get("segment")),
            state=_optional_text(raw_filters.get("state")),
            zip_code=_optional_text(raw_filters.get("zip")),
            county_fips=_optional_text(raw_filters.get("county")),
            county_fipses=_string_list(raw_filters.get("counties")),
            state_codes=_string_list(raw_filters.get("states")),
            zip_codes=_string_list(raw_filters.get("zips")),
            segment_codes=_string_list(raw_filters.get("segment_codes")),
            segment_mode=_optional_text(raw_filters.get("segment_mode")) or "any",
            target_lender_ref=_optional_text(raw_filters.get("target_lender_ref")),
            funnel_stage=_optional_text(raw_filters.get("funnel_stage")),
            portfolio_criteria=portfolio_criteria,
            approval_status=_optional_text(raw_filters.get("approval_status")),
            outreach_status=_optional_text(raw_filters.get("outreach_status")),
            aged_days=_optional_int(raw_filters.get("aged_days")),
        )
    )


def normalise_lead_queue_handoff_filters(
    filters: LeadCohortFilters,
) -> dict[str, object]:
    """Return stable, PII-free filters matching the SQL cohort semantics."""

    if LeadCohortQueries.normalise_borrower_ids(filters.borrower_ids):
        raise ValueError("Growth Agent handoffs cannot contain borrower ids")

    portfolio_payload = (
        filters.portfolio_criteria.model_dump(mode="json", exclude_none=True)
        if filters.portfolio_criteria is not None
        else {}
    )
    portfolio_states = LeadCohortQueries.normalise_states(
        None,
        _string_list(portfolio_payload.pop("states", None)),
    )
    top_states = LeadCohortQueries.normalise_states(filters.state, filters.state_codes)
    if top_states and portfolio_states:
        states = sorted(set(top_states) & set(portfolio_states))
    else:
        states = sorted(top_states or portfolio_states)

    normalized: dict[str, object] = {}
    segment_codes = sorted(
        LeadCohortQueries.normalise_segment_codes(filters.segment, filters.segment_codes)
    )
    if segment_codes:
        if filters.segment_mode not in {"any", "all"}:
            raise ValueError("Growth Agent handoff segment mode is invalid")
        normalized["segment_codes"] = segment_codes
        normalized["segment_mode"] = filters.segment_mode
    if states:
        normalized["states"] = states

    zip_codes = sorted(LeadCohortQueries.normalise_zips(filters.zip_code, filters.zip_codes))
    if zip_codes:
        normalized["zips"] = zip_codes
    county_fipses = sorted(
        LeadCohortQueries.normalise_county_fips(filters.county_fips, filters.county_fipses)
    )
    if county_fipses:
        normalized["counties"] = county_fipses
    if filters.target_lender_ref:
        normalized["target_lender_ref"] = filters.target_lender_ref.strip()
    if filters.funnel_stage:
        normalized["funnel_stage"] = filters.funnel_stage.strip().lower()
    if filters.approval_status:
        normalized["approval_status"] = filters.approval_status.strip().lower()
    if filters.outreach_status:
        normalized["outreach_status"] = filters.outreach_status.strip().lower()
    if filters.aged_days is not None:
        normalized["aged_days"] = max(1, min(int(filters.aged_days), 90))
    if portfolio_payload:
        normalized["portfolio_criteria"] = portfolio_payload
    return normalized


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Growth Agent handoff integer filter is invalid")
    return int(str(value))


def _string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list | tuple | set | frozenset):
        values = list(value)
    else:
        raise ValueError("Growth Agent handoff list filter is invalid")
    out = [str(item).strip() for item in values if str(item).strip()]
    return out or None

