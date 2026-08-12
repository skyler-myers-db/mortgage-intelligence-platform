"""Matched-cohort SQL and atomic identity reads for the lead repository."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from backend.schemas.genie_geo_filters import GENIE_CITY_FILTER_KEY
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
from backend.services.repositories.databricks_campaign_treatment_materialize import (
    load_campaign_treatment_manifest,
    materialize_campaign_treatment,
)
from backend.services.repositories.databricks_campaign_treatment_preflight import (
    CampaignTreatmentBuildRejected as CampaignTreatmentBuildRejected,
)
from backend.services.repositories.databricks_campaign_treatment_preflight import (
    campaign_treatment_preflight,
)
from backend.services.repositories.databricks_campaign_treatment_preflight import (
    is_campaign_treatment_member as exact_campaign_treatment_member,
)
from backend.services.repositories.databricks_lead_cohort_support import (
    LeadCohortQuerySupport,
)
from backend.services.repositories.databricks_portfolio import build_preview_predicates
from backend.services.repositories.databricks_shared import (
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)


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
    # Reviewed ``CITY~ST`` pairs. A pair, never a bare name: see
    # ``backend/schemas/genie_geo_filters`` for the five names that span two
    # states and the 14,631x error a name-only cohort makes on the minority
    # side of ``CYPRESS``.
    city_states: list[str] | None = None
    borrower_ids: list[str] | None = None
    segment_codes: list[str] | None = None
    segment_mode: str = "any"
    target_lender_ref: str | None = None
    funnel_stage: str | None = None
    portfolio_criteria: PortfolioCriteria | None = None
    # Reviewed numeric floors. A governed Genie cohort sets these so the
    # queue replays the answer's population; ``min_equity_pct`` travels on
    # ``portfolio_criteria`` because that vocabulary already compiles it.
    min_opportunity_score: int | None = None
    min_rate_spread_bps: float | None = None
    min_heloc_propensity_score: float | None = None
    approval_status: str | None = None
    outreach_status: str | None = None
    aged_days: int | None = None

    @property
    def needs_lifecycle_snapshot(self) -> bool:
        return bool(self.approval_status or self.outreach_status or self.aged_days is not None)


class LeadCohortQueries(LeadCohortQuerySupport):
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
        "WHERE 1=1 {state_clause} {zip_clause} {county_clause} {city_clause} "
        "{borrower_clause} "
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

    def campaign_treatment_preflight(
        self,
        filters: LeadCohortFilters,
        *,
        frequency_cap_days: int,
        household_dedup_enabled: bool,
    ) -> dict[str, Any]:
        return campaign_treatment_preflight(
            self,
            filters,
            frequency_cap_days=frequency_cap_days,
            household_dedup_enabled=household_dedup_enabled,
        )

    def materialize_campaign_treatment(
        self,
        filters: LeadCohortFilters,
        *,
        campaign_id: str,
        materialization_id: str,
        request_payload_hash: str,
        contract_fingerprint: str,
        frequency_cap_days: int,
        holdout_basis_points: int,
        household_dedup_enabled: bool,
    ) -> dict[str, Any]:
        return materialize_campaign_treatment(
            self,
            filters,
            campaign_id=campaign_id,
            materialization_id=materialization_id,
            request_payload_hash=request_payload_hash,
            contract_fingerprint=contract_fingerprint,
            frequency_cap_days=frequency_cap_days,
            holdout_basis_points=holdout_basis_points,
            household_dedup_enabled=household_dedup_enabled,
        )

    def load_campaign_treatment_manifest(
        self,
        *,
        campaign_id: str,
        materialization_id: str,
        request_payload_hash: str,
        contract_fingerprint: str,
    ) -> dict[str, Any] | None:
        return load_campaign_treatment_manifest(
            self,
            campaign_id=campaign_id,
            materialization_id=materialization_id,
            request_payload_hash=request_payload_hash,
            contract_fingerprint=contract_fingerprint,
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
        return exact_campaign_treatment_member(
            self,
            borrower_id=borrower_id,
            campaign_id=campaign_id,
            materialization_id=materialization_id,
            delta_version=delta_version,
            treatment_fingerprint=treatment_fingerprint,
            frequency_cap_days=frequency_cap_days,
        )

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
        normalised_cities = self.normalise_city_states(filters.city_states)
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
        replay_clause, replay_params = self.numeric_floor_clause(
            min_opportunity_score=filters.min_opportunity_score,
            min_rate_spread_bps=filters.min_rate_spread_bps,
            min_heloc_propensity_score=filters.min_heloc_propensity_score,
        )
        if "target_lender_ref" in portfolio_params:
            lender_clause = ""
            lender_params = {}
        funnel_stage_clause = self.funnel_stage_filter_clause(filters.funnel_stage)
        freshness_clause = self.freshness_clause()
        geo_projection = (
            f"{_LEAD_POPULATION_SELECT_FROM_B360}, b.opportunity_score AS __rank_order"
            if include_lead_columns
            else "b.borrower_id, b.opportunity_score"
        )
        lead_projection = (
            f"{_LEAD_POPULATION_SELECT_FROM_LP}, -lp.rank_overall AS __rank_order"
            if include_lead_columns
            else "lp.borrower_id, lp.opportunity_score"
        )
        if (
            normalised_states
            or normalised_zips
            or normalised_county
            or normalised_cities
            or normalised_borrower_ids
            or filters.funnel_stage
            or lender_clause
            or portfolio_clause
            or replay_clause
        ):
            params: dict[str, object] = dict(segment_params)
            params.update(lender_params)
            params.update(portfolio_params)
            params.update(replay_params)
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
                city_clause=self.city_state_clause(
                    values=normalised_cities,
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
                portfolio_clause=f"{portfolio_clause} {replay_clause}".strip(),
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
            city_states=_string_list(raw_filters.get(GENIE_CITY_FILTER_KEY)),
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
    # Inside the fingerprint, like every other narrowing geography key. A
    # cohort filtered to CHICAGO~IL and one filtered to HOUSTON~TX are
    # different populations, so they must not sign the same handoff.
    city_states = sorted(LeadCohortQueries.normalise_city_states(filters.city_states))
    if city_states:
        normalized[GENIE_CITY_FILTER_KEY] = city_states
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
    # Numeric floors narrow the cohort, so they belong inside the fingerprint.
    # /leads refuses to combine a handoff with a persisted cohort, so these are
    # absent today; carrying them keeps a future caller from narrowing a signed
    # cohort without changing the proof it was signed against.
    if filters.min_opportunity_score is not None:
        normalized["min_opportunity_score"] = int(filters.min_opportunity_score)
    if filters.min_rate_spread_bps is not None:
        normalized["min_rate_spread_bps"] = int(filters.min_rate_spread_bps)
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
