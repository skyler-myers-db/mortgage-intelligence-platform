"""Canonical exact treatment preflight shared by preview and materialization."""

from __future__ import annotations

from typing import Any

from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate


class CampaignTreatmentBuildRejected(ValueError):
    """Deterministic cohort-size/snapshot rejection before a ready proof exists."""


def campaign_treatment_source_parts(
    host: Any,
    filters: Any,
    *,
    frequency_cap_days: int,
    household_dedup_enabled: bool,
) -> tuple[str, dict[str, Any], str, str]:
    """Compile the one canonical eligible/dedup treatment source."""

    if not 30 <= frequency_cap_days <= 365:
        raise ValueError("campaign frequency cap must be between 30 and 365 days")
    matched_sql, params, uses_lead_population = host.matched_cohort_sql(
        filters,
        include_lead_columns=False,
    )
    snapshot_ctes = host._snapshot_ctes(
        uses_lead_population=uses_lead_population,
        needs_lifecycle_snapshot=filters.needs_lifecycle_snapshot,
        needs_household_snapshot=household_dedup_enabled,
    )
    eligible_candidates_cte = f"""
eligible_candidates AS (
  SELECT DISTINCT
    m.borrower_id,
    b.opportunity_score,
    COALESCE(
      h.household_id,
      CONCAT('HH-', SUBSTR(sha2(CONCAT('singleton:', m.borrower_id), 256), 1, 16))
    ) AS household_id,
    COALESCE(h.household_derivation_method, 'singleton') AS household_derivation_method,
    snapshot_validation.snapshot_id,
    snapshot_validation.source_refreshed_at
  FROM matched m
  INNER JOIN {qualify('gold', 'borrower_360')} b
    ON b.borrower_id = m.borrower_id
  LEFT JOIN {qualify('gold', 'household_rollup')} h
    ON h.borrower_id = m.borrower_id
  CROSS JOIN snapshot_validation
  WHERE {eligible_sql_predicate('b')}
    AND snapshot_validation.snapshot_id IS NOT NULL
    AND COALESCE(b.has_unresolved_owner, FALSE) = FALSE
    AND (
      b.last_touch_at IS NULL
      OR b.last_touch_at < CURRENT_TIMESTAMP() - INTERVAL '{frequency_cap_days}' DAYS
    )
)"""
    return matched_sql, params, snapshot_ctes, eligible_candidates_cte


def campaign_treatment_preflight(
    host: Any,
    filters: Any,
    *,
    frequency_cap_days: int,
    household_dedup_enabled: bool,
) -> dict[str, Any]:
    """Count exact post-policy, post-dedup contacts without materializing them."""

    matched_sql, params, snapshot_ctes, eligible_candidates_cte = campaign_treatment_source_parts(
        host,
        filters,
        frequency_cap_days=frequency_cap_days,
        household_dedup_enabled=household_dedup_enabled,
    )
    preflight_sql = f"""
WITH matched AS (
  {matched_sql}
),
{snapshot_ctes},
{eligible_candidates_cte},
ranked AS (
  SELECT
    borrower_id,
    household_id,
    ROW_NUMBER() OVER (
      PARTITION BY household_id
      ORDER BY opportunity_score DESC, borrower_id ASC
    ) AS campaign_household_rank
  FROM eligible_candidates
),
treatment_units AS (
  SELECT borrower_id
  FROM ranked
  WHERE NOT {str(household_dedup_enabled).upper()}
     OR campaign_household_rank = 1
)
SELECT COUNT(*) AS selected_primary_count,
       (SELECT COUNT(*) FROM eligible_candidates) AS candidate_count,
       (SELECT MAX(snapshot_id) FROM snapshot_validation) AS source_snapshot_id
FROM treatment_units
"""
    row = host._client.execute_one(preflight_sql, params) or {}
    selected_primary_count = int(row.get("selected_primary_count") or 0)
    candidate_count = int(row.get("candidate_count") or selected_primary_count)
    if candidate_count < 0 or selected_primary_count < 0 or selected_primary_count > candidate_count:
        raise CampaignTreatmentBuildRejected("Campaign treatment preflight counts are invalid")
    source_snapshot_id = str(row.get("source_snapshot_id") or "")
    if not source_snapshot_id:
        raise CampaignTreatmentBuildRejected(
            "Campaign treatment preflight returned no governed source snapshot"
        )
    return {
        "candidate_count": candidate_count,
        "selected_primary_count": selected_primary_count,
        "source_snapshot_id": source_snapshot_id,
    }


def is_campaign_treatment_member(
    host: Any,
    *,
    borrower_id: str,
    campaign_id: str,
    materialization_id: str,
    delta_version: int,
    treatment_fingerprint: str,
    frequency_cap_days: int,
) -> bool:
    """Intersect immutable T0 treatment membership with live eligibility only."""

    if not 30 <= frequency_cap_days <= 365:
        raise ValueError("campaign frequency cap must be between 30 and 365 days")
    if delta_version < 0:
        raise ValueError("campaign treatment Delta version is invalid")
    table = qualify("audit", "campaign_treatment_snapshot")
    statement = f"""
SELECT COUNT(*) AS n
FROM {table} VERSION AS OF {int(delta_version)} AS treatment
INNER JOIN {qualify('gold', 'borrower_360')} AS b
  ON b.borrower_id = treatment.borrower_id
WHERE treatment.campaign_id = :campaign_id
  AND treatment.materialization_id = :materialization_id
  AND treatment.borrower_id = :campaign_borrower_id
  AND treatment.row_kind = 'member'
  AND treatment.assignment = 'treatment'
  AND treatment.treatment_fingerprint = :treatment_fingerprint
  AND {eligible_sql_predicate('b')}
  AND COALESCE(b.has_unresolved_owner, FALSE) = FALSE
  AND (
    b.last_touch_at IS NULL
    OR b.last_touch_at < CURRENT_TIMESTAMP() - INTERVAL '{frequency_cap_days}' DAYS
  )
"""
    row = (
        host._client.execute_one(
            statement,
            {
                "campaign_id": campaign_id,
                "materialization_id": materialization_id,
                "campaign_borrower_id": borrower_id,
                "treatment_fingerprint": treatment_fingerprint,
            },
        )
        or {}
    )
    return int(row.get("n") or 0) == 1


__all__ = [
    "CampaignTreatmentBuildRejected",
    "campaign_treatment_preflight",
    "campaign_treatment_source_parts",
    "is_campaign_treatment_member",
]
