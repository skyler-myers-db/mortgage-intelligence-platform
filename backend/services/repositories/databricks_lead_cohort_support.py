"""Shared normalization and snapshot helpers for lead-cohort queries."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from backend.schemas.common import validate_public_borrower_id
from backend.services.databricks_sql_helpers import qualify
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD
from backend.services.segment_predicates import (
    compose_segment_predicate,
    normalise_segment_codes,
)
from backend.services.state_footprint import get_state_footprint_resolver


class LeadCohortQuerySupport:
    """Pure query helpers split from the stateful cohort composer."""

    _cache_ttl_s: float
    _clock: ModuleType

    @staticmethod
    def state_sets() -> dict[str, list[str]]:
        """Build the active geography-label mapping used by portfolio criteria."""
        resolver = get_state_footprint_resolver()
        footprint_codes = resolver.state_codes()
        state_name_map = resolver.state_name_to_codes()
        all_key = f"all {len(footprint_codes)} states"
        return {**state_name_map, all_key: list(footprint_codes)}

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
        return compose_segment_predicate(
            LeadCohortQuerySupport.normalise_segment_codes(segment, segment_codes),
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
        """Bound warehouse result-cache reuse for interactive drilldowns."""
        if self._cache_ttl_s <= 0:
            marker: int = self._clock.time_ns()
        else:
            marker = int(self._clock.time() // self._cache_ttl_s)
        return f"AND {marker} = {marker}"

    # The ranked-queue quality floor, pinned to the lead_population CTAS
    # (sql/transformations/gold_lead_population.sql: opportunity_score >= 50).
    # Geo-filtered reads go to borrower_360 (2026-05-04 FIX beta, map-tile
    # promise), so the identity row carries BOTH populations: the geography
    # total and the ranked subset. 2026-08-07 audit C4: a state filter made
    # the single reported count 4.8x LARGER than the unfiltered queue.
    RANKED_SCORE_FLOOR = 50

    @staticmethod
    def _identity_aggregate_select(alias: str) -> str:
        return (
            f"COUNT(DISTINCT {alias}.borrower_id) AS n, "
            f"COUNT(DISTINCT CASE WHEN {alias}.opportunity_score >= "
            f"{LeadCohortQuerySupport.RANKED_SCORE_FLOOR} "
            f"THEN {alias}.borrower_id END) AS ranked_n, "
            "sha2(concat_ws('|', sort_array(collect_set(CAST("
            f"{alias}.borrower_id AS STRING)))), 256) AS cohort_digest, "
            f"{LeadCohortQuerySupport._legacy_snapshot_select()}"
        )

    @staticmethod
    def _legacy_snapshot_select() -> str:
        return (
            "(SELECT CAST(MAX(snapshot_src.refreshed_at) AS STRING) "
            f"FROM {qualify('gold', 'borrower_360')} snapshot_src) AS snapshot_id"
        )

    @staticmethod
    def _snapshot_ctes(
        *,
        uses_lead_population: bool,
        needs_lifecycle_snapshot: bool,
        needs_household_snapshot: bool = False,
    ) -> str:
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
        if needs_household_snapshot:
            version_columns.append(
                f"(SELECT MAX(refreshed_at) FROM {qualify('gold', 'household_rollup')}) "
                "AS household_at"
            )
        rendered_version_columns = ",\n    ".join(version_columns)
        lead_check = (
            "AND versions.lead_population_at = anchor.refresh_at"
            if uses_lead_population
            else ""
        )
        lifecycle_check = (
            "AND versions.lifecycle_at IS NOT NULL" if needs_lifecycle_snapshot else ""
        )
        household_check = (
            "AND versions.household_at = anchor.refresh_at"
            if needs_household_snapshot
            else ""
        )
        lifecycle_token = (
            ", '|lifecycle:', CAST(versions.lifecycle_at AS STRING)"
            if needs_lifecycle_snapshot
            else ""
        )
        return f"""
refresh_anchor AS (
  SELECT run_id, refresh_at, captured_at, source
  FROM {qualify('ref', 'refresh_run_state')}
  ORDER BY captured_at DESC
  LIMIT 1
),
source_versions AS (
  SELECT
    {rendered_version_columns}
),
snapshot_validation AS (
  SELECT
    CASE
    WHEN anchor.refresh_at IS NOT NULL
      AND anchor.captured_at IS NOT NULL
      AND anchor.source IN ('mip_refresh_scores', 'ad_hoc', 'backfill')
      AND versions.borrower_360_at = anchor.refresh_at
      {lead_check}
      {lifecycle_check}
      {household_check}
    THEN sha2(
      concat(
        'gold-refresh:',
        COALESCE(
          NULLIF(TRIM(CAST(anchor.run_id AS STRING)), ''),
          concat(anchor.source, ':', CAST(anchor.captured_at AS STRING))
        ),
        '|',
        CAST(anchor.refresh_at AS STRING){lifecycle_token}
      ),
      256
    )
    ELSE NULL
    END AS snapshot_id,
    CASE
      WHEN anchor.refresh_at IS NOT NULL
        AND versions.borrower_360_at = anchor.refresh_at
        {lead_check}
        {lifecycle_check}
        {household_check}
      THEN anchor.refresh_at
      ELSE NULL
    END AS source_refreshed_at
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
        total = int(row.get(total_key) or 0)
        return {
            "total": total,
            # Ranked subset (score >= RANKED_SCORE_FLOOR). Rows read from
            # lead_population are all ranked by construction, so a missing
            # key falls back to the total rather than a misleading zero.
            "ranked_total": int(row.get("ranked_n", total) or 0),
            "cohort_digest": digest,
            "snapshot_id": snapshot_id,
        }
