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
        COUNT(DISTINCT b.clip) AS actionable_total,
        ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS actionable_avg_score
      FROM {BORROWER_360} b
      WHERE {workflow.actionable_predicate}
        AND {_B_ELIGIBLE}
        AND b.consent_status = 'opt_in'
        AND b.suppression_reason IS NULL
        {actionable_state_clause}
    )
SELECT
  broad.broad_total,
  actionable.actionable_total,
  broad.broad_avg_score,
  actionable.actionable_avg_score,
  broad.avg_rate_spread_bps,
  broad.avg_equity_pct
FROM broad CROSS JOIN actionable
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
  WHERE d.opportunity_score >= 75
    {broad_state_clause}
),
actionable AS (
  SELECT
    COUNT(DISTINCT d.clip) AS actionable_total,
    ROUND(AVG(CAST(d.opportunity_score AS DOUBLE)), 1) AS actionable_avg_score
  FROM {BORROWER_DOSSIER} d
  WHERE d.opportunity_score >= 75
    AND {_D_ELIGIBLE}
    AND d.consent_status = 'opt_in'
    AND d.suppression_reason IS NULL
    {actionable_state_clause}
)
SELECT
  broad.broad_total,
  actionable.actionable_total,
  broad.broad_avg_score,
  actionable.actionable_avg_score,
  broad.avg_rate_spread_bps,
  broad.avg_equity_pct,
  broad.evidence_backed_total
FROM broad CROSS JOIN actionable
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
SELECT
  COUNT(DISTINCT b.clip) AS broad_total,
  COUNT(DISTINCT b.clip) AS actionable_total,
  ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS broad_avg_score,
  ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS actionable_avg_score,
  ROUND(AVG(CAST(b.rate_spread_bps AS DOUBLE)), 1) AS avg_rate_spread_bps,
  ROUND(AVG(CAST(b.equity_pct AS DOUBLE)), 1) AS avg_equity_pct
FROM {BORROWER_360} b
LEFT JOIN {qualify("gold", "borrower_lifecycle_state")} ls
  ON ls.borrower_id = b.borrower_id
WHERE {_B_ELIGIBLE}
  AND b.consent_status = 'opt_in'
  AND b.suppression_reason IS NULL
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


def _metric_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "broad_total": int(row.get("broad_total") or 0),
        "actionable_total": int(row.get("actionable_total") or 0),
        "broad_avg_score": _maybe_float(row.get("broad_avg_score")),
        "actionable_avg_score": _maybe_float(row.get("actionable_avg_score")),
        "avg_rate_spread_bps": _maybe_float(row.get("avg_rate_spread_bps")),
        "avg_equity_pct": _maybe_float(row.get("avg_equity_pct")),
    }


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
