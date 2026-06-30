"""Saved-monitor helpers for Growth Agent API routes."""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import HTTPException
from pydantic import ValidationError

from backend.schemas.growth_agent import (
    GrowthAgentMonitor,
    GrowthAgentRunRequest,
    GrowthAgentWorkflowId,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import MONITOR_LIST_SQL
from backend.services.growth_agent_workflows import (
    WORKFLOWS,
    GrowthAgentWorkflowDef,
    custom_workflow,
)
from backend.services.lakebase import LakebaseClient, LakebaseError


def list_monitors(lakebase: LakebaseClient, *, actor: str) -> list[GrowthAgentMonitor]:
    try:
        rows = lakebase.fetchall(MONITOR_LIST_SQL, {"actor_email": actor, "limit": 20}, limit=20)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return [monitor_from_row(row) for row in rows]


def workflow_from_monitor(row: dict[str, Any]) -> GrowthAgentWorkflowDef:
    workflow_id = str(row.get("workflow_id") or "")
    if workflow_id == "custom_segment_watch":
        criteria = json_object(row.get("criteria"))
        lead_filters = criteria.get("lead_queue_filters")
        if not isinstance(lead_filters, dict):
            raise HTTPException(status_code=409, detail="saved monitor has malformed criteria")
        segment_codes = lead_filters.get("segment_codes")
        if not isinstance(segment_codes, list):
            raise HTTPException(status_code=409, detail="saved monitor has malformed segment criteria")
        segment_mode = str(lead_filters.get("segment_mode") or "any")
        return custom_workflow([str(code) for code in segment_codes], segment_mode)
    workflow = WORKFLOWS.get(cast(GrowthAgentWorkflowId, workflow_id))
    if workflow is None:
        raise HTTPException(status_code=409, detail="saved monitor references an unknown workflow")
    return workflow


def states_from_monitor_criteria(criteria_value: Any) -> list[str]:
    criteria = json_object(criteria_value)
    states = criteria.get("states")
    if not isinstance(states, list):
        return []
    return [str(state).strip().upper() for state in states if str(state).strip()]


def stored_monitor_name(row: dict[str, Any], *, workflow: GrowthAgentWorkflowDef) -> str:
    name = str(row.get("name") or "").strip()
    if not name:
        return workflow.title
    try:
        request = GrowthAgentRunRequest(monitor_name=name)
    except ValidationError:
        return workflow.title
    return request.monitor_name or workflow.title


def monitor_from_row(row: dict[str, Any]) -> GrowthAgentMonitor:
    criteria = json_object(row.get("criteria") or {})
    return GrowthAgentMonitor(
        monitor_id=str(row["monitor_id"]),
        workflow_id=row["workflow_id"],
        name=_safe_monitor_name_from_row(row),
        cadence=row["cadence"],
        status=row.get("status") or "active",
        criteria=criteria,
        route=row["route"],
        actionable_total=int(row.get("actionable_total") or 0),
        source_assets=list(row.get("source_assets") or []),
        last_run_id=str(row["last_run_id"]) if row.get("last_run_id") else None,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def json_object(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _monitor_fallback_name(row: dict[str, Any]) -> str:
    workflow_id = str(row.get("workflow_id") or "")
    workflow = WORKFLOWS.get(cast(GrowthAgentWorkflowId, workflow_id))
    if workflow is not None:
        return workflow.title
    if workflow_id == "custom_segment_watch":
        return "Custom Segment Workflow"
    return "Reviewed Growth Watchlist"


def _safe_monitor_name_from_row(row: dict[str, Any]) -> str:
    fallback = _monitor_fallback_name(row)
    name = str(row.get("name") or "").strip()
    if not name:
        return fallback
    try:
        request = GrowthAgentRunRequest(monitor_name=name)
    except ValidationError:
        return fallback
    return request.monitor_name or fallback
