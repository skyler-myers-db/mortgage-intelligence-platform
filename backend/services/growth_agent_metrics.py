"""Deterministic Databricks SQL metric loaders for Mortgage Growth Agent workflows."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_workflows import (
    BORROWER_360,
    BORROWER_DOSSIER,
    EVIDENCE_EVENTS,
    SOURCE_READINESS,
    GrowthAgentWorkflowDef,
)
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD

# S1.4: canonical fail-closed contactability predicates (single interface).
_B_ELIGIBLE = eligible_sql_predicate("b")
_D_ELIGIBLE = eligible_sql_predicate("d")


def load_growth_agent_metrics(
    sql_client: DatabricksSqlClient,
    *,
    workflow: GrowthAgentWorkflowDef,
    states: list[str],
) -> dict[str, Any]:
    if workflow.id == "source_freshness_sentinel":
        return _load_source_freshness_metrics(sql_client)
    if workflow.id == "borrower_dossier_review":
        return _load_borrower_dossier_metrics(sql_client, states=states)
    if workflow.id == "branch_capacity_review":
        return _load_branch_capacity_metrics(sql_client, states=states)
    return _load_borrower_screen_metrics(sql_client, workflow=workflow, states=states)


def _load_borrower_screen_metrics(
    sql_client: DatabricksSqlClient,
    *,
    workflow: GrowthAgentWorkflowDef,
    states: list[str],
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    broad_state_clause = _state_clause("b", states, params)
    actionable_state_clause = _state_clause("b", states, params)
    statement = f"""
WITH broad AS (
  SELECT
    COUNT(DISTINCT b.clip) AS broad_total,
    ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS broad_avg_score,
    ROUND(AVG(CAST(b.rate_spread_bps AS DOUBLE)), 1) AS avg_rate_spread_bps,
    ROUND(AVG(CAST(b.equity_pct AS DOUBLE)), 1) AS avg_equity_pct
  FROM {BORROWER_360} b
  WHERE {workflow.broad_predicate}
    {broad_state_clause}
),
    actionable AS (
      SELECT
        COUNT(DISTINCT b.borrower_id) AS actionable_total,
        ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS actionable_avg_score,
        sha2(
          concat_ws(
            '|',
            sort_array(collect_set(CAST(b.borrower_id AS STRING)))
          ),
          256
        ) AS actionable_cohort_digest
      FROM {BORROWER_360} b
      WHERE {workflow.actionable_predicate}
        AND {_B_ELIGIBLE}
        {actionable_state_clause}
    ),
{_snapshot_validation_ctes(primary_table=BORROWER_360)}
SELECT
  broad.broad_total,
  actionable.actionable_total,
  actionable.actionable_cohort_digest,
  source_snapshot.actionable_snapshot_id,
  broad.broad_avg_score,
  actionable.actionable_avg_score,
  broad.avg_rate_spread_bps,
  broad.avg_equity_pct
FROM broad CROSS JOIN actionable CROSS JOIN snapshot_validation source_snapshot
"""
    try:
        row = sql_client.execute_one(statement, params) or {}
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("warehouse")) from exc
    return _metric_row(row)


def _load_borrower_dossier_metrics(
    sql_client: DatabricksSqlClient,
    *,
    states: list[str],
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    broad_state_clause = _state_clause("d", states, params)
    actionable_state_clause = _state_clause("d", states, params)
    statement = f"""
WITH broad AS (
  SELECT
    COUNT(DISTINCT d.clip) AS broad_total,
    ROUND(AVG(CAST(d.opportunity_score AS DOUBLE)), 1) AS broad_avg_score,
    ROUND(AVG(CAST(d.rate_spread_bps AS DOUBLE)), 1) AS avg_rate_spread_bps,
    ROUND(AVG(CAST(d.equity_pct AS DOUBLE)), 1) AS avg_equity_pct,
    COUNT(DISTINCT CASE WHEN ev.clip IS NOT NULL THEN d.clip END) AS evidence_backed_total
  FROM {BORROWER_DOSSIER} d
  LEFT JOIN {EVIDENCE_EVENTS} ev
    ON ev.clip = d.clip
  WHERE d.opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD}
    {broad_state_clause}
),
actionable AS (
  SELECT
    COUNT(DISTINCT d.borrower_id) AS actionable_total,
    ROUND(AVG(CAST(d.opportunity_score AS DOUBLE)), 1) AS actionable_avg_score,
    sha2(
      concat_ws(
        '|',
        sort_array(collect_set(CAST(d.borrower_id AS STRING)))
      ),
      256
    ) AS actionable_cohort_digest
  FROM {BORROWER_DOSSIER} d
  WHERE d.opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD}
    AND {_D_ELIGIBLE}
    {actionable_state_clause}
),
{_snapshot_validation_ctes(primary_table=BORROWER_DOSSIER)}
SELECT
  broad.broad_total,
  actionable.actionable_total,
  actionable.actionable_cohort_digest,
  source_snapshot.actionable_snapshot_id,
  broad.broad_avg_score,
  actionable.actionable_avg_score,
  broad.avg_rate_spread_bps,
  broad.avg_equity_pct,
  broad.evidence_backed_total
FROM broad CROSS JOIN actionable CROSS JOIN snapshot_validation source_snapshot
"""
    try:
        row = sql_client.execute_one(statement, params) or {}
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("warehouse")) from exc
    metrics = _metric_row(row)
    metrics["evidence_backed_total"] = int(row.get("evidence_backed_total") or 0)
    return metrics


def _load_branch_capacity_metrics(
    sql_client: DatabricksSqlClient,
    *,
    states: list[str],
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    state_clause = _state_clause("b", states, params)
    statement = f"""
WITH {_snapshot_validation_ctes(primary_table=BORROWER_360, include_lifecycle=True)}
SELECT
  COUNT(DISTINCT b.clip) AS broad_total,
  COUNT(DISTINCT b.borrower_id) AS actionable_total,
  sha2(
    concat_ws(
      '|',
      sort_array(collect_set(CAST(b.borrower_id AS STRING)))
    ),
    256
  ) AS actionable_cohort_digest,
  MAX(source_snapshot.actionable_snapshot_id) AS actionable_snapshot_id,
  ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS broad_avg_score,
  ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS actionable_avg_score,
  ROUND(AVG(CAST(b.rate_spread_bps AS DOUBLE)), 1) AS avg_rate_spread_bps,
  ROUND(AVG(CAST(b.equity_pct AS DOUBLE)), 1) AS avg_equity_pct
FROM {BORROWER_360} b
CROSS JOIN snapshot_validation source_snapshot
LEFT JOIN {qualify("gold", "borrower_lifecycle_state")} ls
  ON ls.borrower_id = b.borrower_id
WHERE {_B_ELIGIBLE}
  AND COALESCE(ls.approval_status, 'pending') = 'approved'
  AND ls.approved_at <= current_timestamp() - INTERVAL 7 DAYS
  AND ls.outreach_at IS NULL
  {state_clause}
"""
    try:
        row = sql_client.execute_one(statement, params) or {}
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("warehouse")) from exc
    return _metric_row(row)


def _load_source_freshness_metrics(sql_client: DatabricksSqlClient) -> dict[str, Any]:
    statement = f"""
SELECT
  COUNT(*) AS broad_total,
  COUNT_IF(status = 'live') AS actionable_total,
  COUNT_IF(status IS NULL OR status <> 'live') AS warning_total,
  COUNT_IF(last_updated IS NOT NULL AND last_updated < current_timestamp() - INTERVAL 7 DAYS) AS stale_total
FROM {SOURCE_READINESS}
"""
    try:
        row = sql_client.execute_one(statement, {}) or {}
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("warehouse")) from exc
    metrics = _metric_row(row)
    metrics["warning_total"] = int(row.get("warning_total") or 0)
    metrics["stale_total"] = int(row.get("stale_total") or 0)
    return metrics


def _state_clause(alias: str, states: list[str], params: dict[str, Any]) -> str:
    if not states:
        return ""
    names: list[str] = []
    for idx, state in enumerate(states):
        key = f"state_{idx}"
        params[key] = state
        names.append(f":{key}")
    return f"AND UPPER({alias}.state) IN ({', '.join(names)})"


def _snapshot_validation_ctes(
    *,
    primary_table: str,
    include_lifecycle: bool = False,
) -> str:
    """Build a fail-closed refresh identity over every table used by a workflow."""

    lifecycle_table = qualify("gold", "borrower_lifecycle_state")
    lifecycle_version = (
        f"(SELECT MAX(refreshed_at) FROM {lifecycle_table})"
        if include_lifecycle
        else "CAST(NULL AS TIMESTAMP)"
    )
    lifecycle_check = "AND versions.lifecycle_at IS NOT NULL" if include_lifecycle else ""
    lifecycle_token = (
        ", '|lifecycle:', CAST(versions.lifecycle_at AS STRING)" if include_lifecycle else ""
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
    (SELECT MAX(refreshed_at) FROM {BORROWER_360}) AS borrower_360_at,
    (SELECT MAX(refreshed_at) FROM {primary_table}) AS primary_at,
    {lifecycle_version} AS lifecycle_at
),
snapshot_validation AS (
  SELECT CASE
    WHEN anchor.refresh_at IS NOT NULL
      AND anchor.captured_at IS NOT NULL
      AND anchor.source IN ('mip_refresh_scores', 'ad_hoc', 'backfill')
      AND versions.borrower_360_at = anchor.refresh_at
      AND versions.primary_at = anchor.refresh_at
      {lifecycle_check}
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
  END AS actionable_snapshot_id
  FROM refresh_anchor anchor
  CROSS JOIN source_versions versions
)"""


def _metric_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "broad_total": int(row.get("broad_total") or 0),
        "actionable_total": int(row.get("actionable_total") or 0),
        "broad_avg_score": _maybe_float(row.get("broad_avg_score")),
        "actionable_avg_score": _maybe_float(row.get("actionable_avg_score")),
        "avg_rate_spread_bps": _maybe_float(row.get("avg_rate_spread_bps")),
        "avg_equity_pct": _maybe_float(row.get("avg_equity_pct")),
    }
    cohort_digest = str(row.get("actionable_cohort_digest") or "").strip().lower()
    snapshot_id = str(row.get("actionable_snapshot_id") or "").strip()
    if cohort_digest:
        metrics["actionable_cohort_digest"] = cohort_digest
    if snapshot_id:
        metrics["actionable_snapshot_id"] = snapshot_id
    return metrics


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
