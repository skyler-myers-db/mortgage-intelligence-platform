"""Immutable T0 campaign treatment materialization and manifest recovery.

The sibling ``databricks_campaign_treatment_preflight`` module counts the
exact post-policy, post-dedup cohort without writing anything. This module
is the write half: it assigns treatment/holdout, appends one immutable
member set plus a manifest row, and reads the result back at the pinned
Delta version so the returned manifest describes exactly what landed.

Recovery lives here too. A campaign build that loses its response is
ambiguous -- the MERGE may or may not have committed -- so
``load_campaign_treatment_manifest`` re-reads the append-only table by
(campaign, materialization, contract, payload) and returns the manifest
only if a complete one is already there.

Both paths funnel through ``validated_campaign_treatment_manifest``, which
is the integrity gate: exactly one manifest row, no duplicate members,
counts that agree with each other, and 64-hex digests. A manifest that
fails any of those raises rather than being returned as a proof.
"""

from __future__ import annotations

import re
from typing import Any

from backend.schemas.portfolio import (
    CAMPAIGN_BUILD_LIMIT,
    CAMPAIGN_TREATMENT_ALGORITHM_VERSION,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.repositories.databricks_campaign_treatment_preflight import (
    CampaignTreatmentBuildRejected,
    campaign_treatment_source_parts,
)

MAX_CAMPAIGN_TREATMENT_MEMBERS = CAMPAIGN_BUILD_LIMIT


def _manifest_select_sql(table: str, delta_version: int) -> str:
    """Read one campaign's manifest at a pinned Delta version.

    Materialization and recovery must agree exactly on what a manifest
    is, so they share this projection rather than keeping two copies of
    a 20-column aggregate in sync by hand.
    """

    return f"""
SELECT
  COUNT(CASE WHEN row_kind = 'manifest' THEN 1 END) AS manifest_rows,
  COUNT(CASE WHEN row_kind = 'member' THEN 1 END) AS member_rows,
  COUNT(DISTINCT CASE WHEN row_kind = 'member' THEN record_key END) AS distinct_member_rows,
  MAX(candidate_count) AS candidate_count,
  MAX(selected_primary_count) AS selected_primary_count,
  MAX(treatment_count) AS treatment_count,
  MAX(holdout_count) AS holdout_count,
  MAX(assignment_digest) AS assignment_digest,
  MAX(treatment_fingerprint) AS treatment_fingerprint,
  MAX(source_snapshot_id) AS source_snapshot_id,
  MAX(source_refreshed_at) AS source_refreshed_at,
  MAX(materialized_at) AS materialized_at,
  MAX(household_count) AS household_count,
  MAX(owner_link_household_count) AS owner_link_household_count,
  MAX(mailing_address_household_count) AS mailing_address_household_count,
  MAX(singleton_household_count) AS singleton_household_count
FROM {table} VERSION AS OF {delta_version}
WHERE campaign_id = :campaign_id
  AND materialization_id = :materialization_id
  AND contract_fingerprint = :contract_fingerprint
  AND request_payload_hash = :request_payload_hash
"""


def _delta_version(host: Any, table: str, *, failure_detail: str) -> int:
    history = host._client.execute_one(f"DESCRIBE HISTORY {table} LIMIT 1") or {}
    try:
        return int(history["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(failure_detail) from exc


def materialize_campaign_treatment(
    host: Any,
    filters: Any,
    *,
    campaign_id: str,
    materialization_id: str,
    request_payload_hash: str,
    contract_fingerprint: str,
    frequency_cap_days: int,
    holdout_basis_points: int,
    household_dedup_enabled: bool,
) -> dict[str, Any]:
    """Materialize one immutable T0 assignment set and return its manifest."""

    if not 0 <= holdout_basis_points <= 5_000:
        raise ValueError("campaign holdout must be between 0 and 5000 basis points")

    matched_sql, params, snapshot_ctes, eligible_candidates_cte = (
        campaign_treatment_source_parts(
            host,
            filters,
            frequency_cap_days=frequency_cap_days,
            household_dedup_enabled=household_dedup_enabled,
        )
    )
    preflight = host.campaign_treatment_preflight(
        filters,
        frequency_cap_days=frequency_cap_days,
        household_dedup_enabled=household_dedup_enabled,
    )
    params = {
        **params,
        "campaign_id": campaign_id,
        "materialization_id": materialization_id,
        "request_payload_hash": request_payload_hash,
        "contract_fingerprint": contract_fingerprint,
        "campaign_holdout_basis_points": holdout_basis_points,
    }
    table = qualify("audit", "campaign_treatment_snapshot")
    selected_primary_count = int(preflight.get("selected_primary_count") or 0)
    if selected_primary_count > MAX_CAMPAIGN_TREATMENT_MEMBERS:
        raise CampaignTreatmentBuildRejected(
            "Campaign treatment exceeds the 10,000-member synchronous build limit; "
            "refine the reviewed cohort before creating the campaign."
        )
    preflight_source_snapshot_id = str(preflight["source_snapshot_id"])
    params["campaign_member_limit"] = MAX_CAMPAIGN_TREATMENT_MEMBERS
    params["preflight_source_snapshot_id"] = preflight_source_snapshot_id
    statement = f"""
WITH matched AS (
  {matched_sql}
),
{snapshot_ctes},
{eligible_candidates_cte},
ranked AS (
  SELECT
    borrower_id,
    household_id,
    household_derivation_method,
    snapshot_id,
    source_refreshed_at,
    ROW_NUMBER() OVER (
      PARTITION BY household_id
      ORDER BY opportunity_score DESC, borrower_id ASC
    ) AS campaign_household_rank
  FROM eligible_candidates
),
treatment_units AS (
  SELECT
    borrower_id,
    household_id,
    household_derivation_method,
    snapshot_id,
    source_refreshed_at,
    CASE
      WHEN {str(household_dedup_enabled).upper()} THEN household_id
      ELSE borrower_id
    END AS treatment_unit_id
  FROM ranked
  WHERE NOT {str(household_dedup_enabled).upper()}
     OR campaign_household_rank = 1
),
assigned AS (
  SELECT
    *,
    CASE
      WHEN pmod(
             xxhash64(CONCAT(:campaign_id, ':', treatment_unit_id)),
             10000
           ) < :campaign_holdout_basis_points
      THEN 'holdout'
      ELSE 'treatment'
    END AS assignment
  FROM treatment_units
),
assignment_stats AS (
  SELECT
    COUNT(*) AS selected_primary_count,
    COALESCE(SUM(CASE WHEN assignment = 'treatment' THEN 1 ELSE 0 END), 0)
      AS treatment_count,
    COALESCE(SUM(CASE WHEN assignment = 'holdout' THEN 1 ELSE 0 END), 0)
      AS holdout_count,
    COALESCE(
      sha2(CONCAT_WS('|', SORT_ARRAY(COLLECT_LIST(
        sha2(CONCAT(borrower_id, ':', assignment), 256)
      ))), 256),
      sha2('', 256)
    ) AS assignment_digest,
    COUNT(DISTINCT household_id) AS household_count,
    COUNT(DISTINCT CASE WHEN household_derivation_method = 'owner_link' THEN household_id END)
      AS owner_link_household_count,
    COUNT(DISTINCT CASE WHEN household_derivation_method = 'mailing_address' THEN household_id END)
      AS mailing_address_household_count,
    COUNT(DISTINCT CASE WHEN household_derivation_method = 'singleton' THEN household_id END)
      AS singleton_household_count
  FROM assigned
),
candidate_stats AS (
  SELECT COUNT(*) AS candidate_count FROM eligible_candidates
),
build_guard AS (
  SELECT assignment_stats.selected_primary_count
  FROM assignment_stats
  CROSS JOIN snapshot_validation
  WHERE assignment_stats.selected_primary_count <= :campaign_member_limit
    AND snapshot_validation.snapshot_id = :preflight_source_snapshot_id
),
manifest_values AS (
  SELECT
    :campaign_id AS campaign_id,
    :materialization_id AS materialization_id,
    snapshot_validation.snapshot_id,
    snapshot_validation.source_refreshed_at,
    candidate_stats.candidate_count,
    assignment_stats.*,
    sha2(
      CONCAT(
        '{CAMPAIGN_TREATMENT_ALGORITHM_VERSION}:', :campaign_id, ':', :contract_fingerprint, ':',
        snapshot_validation.snapshot_id, ':', assignment_stats.assignment_digest, ':',
        CAST(candidate_stats.candidate_count AS STRING), ':',
        CAST(assignment_stats.selected_primary_count AS STRING), ':',
        CAST(assignment_stats.treatment_count AS STRING), ':',
        CAST(assignment_stats.holdout_count AS STRING)
      ),
      256
    ) AS treatment_fingerprint
  FROM snapshot_validation
  CROSS JOIN candidate_stats
  CROSS JOIN assignment_stats
  CROSS JOIN build_guard
  WHERE snapshot_validation.snapshot_id IS NOT NULL
),
source_rows AS (
  SELECT
    :campaign_id AS campaign_id,
    :materialization_id AS materialization_id,
    'member' AS row_kind,
    assigned.borrower_id AS record_key,
    assigned.borrower_id,
    assigned.assignment,
    sha2(CONCAT(:campaign_id, ':', assigned.treatment_unit_id), 256) AS treatment_unit_token,
    '{CAMPAIGN_TREATMENT_ALGORITHM_VERSION}' AS treatment_algorithm_version,
    :contract_fingerprint AS contract_fingerprint,
    :request_payload_hash AS request_payload_hash,
    assigned.snapshot_id AS source_snapshot_id,
    assigned.source_refreshed_at,
    CAST(NULL AS BIGINT) AS candidate_count,
    CAST(NULL AS BIGINT) AS selected_primary_count,
    CAST(NULL AS BIGINT) AS treatment_count,
    CAST(NULL AS BIGINT) AS holdout_count,
    CAST(NULL AS STRING) AS assignment_digest,
    manifest_values.treatment_fingerprint,
    CAST(NULL AS BIGINT) AS household_count,
    CAST(NULL AS BIGINT) AS owner_link_household_count,
    CAST(NULL AS BIGINT) AS mailing_address_household_count,
    CAST(NULL AS BIGINT) AS singleton_household_count,
    CURRENT_TIMESTAMP() AS materialized_at
  FROM assigned
  CROSS JOIN manifest_values
  UNION ALL
  SELECT
    campaign_id,
    materialization_id,
    'manifest',
    '__manifest__',
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    '{CAMPAIGN_TREATMENT_ALGORITHM_VERSION}',
    :contract_fingerprint,
    :request_payload_hash,
    snapshot_id,
    source_refreshed_at,
    candidate_count,
    selected_primary_count,
    treatment_count,
    holdout_count,
    assignment_digest,
    treatment_fingerprint,
    household_count,
    owner_link_household_count,
    mailing_address_household_count,
    singleton_household_count,
    CURRENT_TIMESTAMP()
  FROM manifest_values
)
MERGE INTO {table} AS target
USING source_rows AS source
ON target.campaign_id = source.campaign_id
 AND target.materialization_id = source.materialization_id
 AND target.row_kind = source.row_kind
 AND target.record_key = source.record_key
WHEN NOT MATCHED THEN INSERT *
"""
    host._client.execute(statement, params)
    delta_version = _delta_version(
        host,
        table,
        failure_detail="campaign treatment materialization did not return a Delta version",
    )
    manifest = host._client.execute_one(_manifest_select_sql(table, delta_version), params) or {}
    return validated_campaign_treatment_manifest(manifest, delta_version=delta_version)


def load_campaign_treatment_manifest(
    host: Any,
    *,
    campaign_id: str,
    materialization_id: str,
    request_payload_hash: str,
    contract_fingerprint: str,
) -> dict[str, Any] | None:
    """Recover a complete append-only manifest after an ambiguous response loss."""

    table = qualify("audit", "campaign_treatment_snapshot")
    delta_version = _delta_version(
        host,
        table,
        failure_detail="campaign treatment recovery returned no Delta version",
    )
    params = {
        "campaign_id": campaign_id,
        "materialization_id": materialization_id,
        "request_payload_hash": request_payload_hash,
        "contract_fingerprint": contract_fingerprint,
    }
    manifest = host._client.execute_one(_manifest_select_sql(table, delta_version), params) or {}
    if int(manifest.get("manifest_rows") or 0) == 0:
        return None
    return validated_campaign_treatment_manifest(manifest, delta_version=delta_version)


def validated_campaign_treatment_manifest(
    manifest: dict[str, Any],
    *,
    delta_version: int,
) -> dict[str, Any]:
    if int(manifest.get("manifest_rows") or 0) != 1:
        raise ValueError("campaign treatment materialization must contain exactly one manifest")
    member_rows = int(manifest.get("member_rows") or 0)
    if member_rows != int(manifest.get("distinct_member_rows") or 0):
        raise ValueError("campaign treatment materialization contains duplicate members")
    candidate_count = int(manifest.get("candidate_count") or 0)
    expected_members = int(manifest.get("selected_primary_count") or 0)
    if (
        candidate_count < 0
        or expected_members < 0
        or expected_members > candidate_count
        or expected_members > MAX_CAMPAIGN_TREATMENT_MEMBERS
    ):
        raise ValueError("campaign treatment manifest counts are invalid")
    if member_rows != expected_members:
        raise ValueError("campaign treatment manifest count does not match its members")
    treatment_count = int(manifest.get("treatment_count") or 0)
    holdout_count = int(manifest.get("holdout_count") or 0)
    if (
        treatment_count < 0
        or holdout_count < 0
        or expected_members != treatment_count + holdout_count
    ):
        raise ValueError("campaign treatment manifest assignments do not match its members")
    for field_name in (
        "assignment_digest",
        "treatment_fingerprint",
        "source_snapshot_id",
    ):
        if re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(field_name) or "")) is None:
            raise ValueError(f"campaign treatment manifest {field_name} is invalid")
    manifest["delta_version"] = delta_version
    return manifest


__all__ = [
    "MAX_CAMPAIGN_TREATMENT_MEMBERS",
    "load_campaign_treatment_manifest",
    "materialize_campaign_treatment",
    "validated_campaign_treatment_manifest",
]
