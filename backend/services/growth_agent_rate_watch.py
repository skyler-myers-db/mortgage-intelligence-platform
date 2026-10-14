"""Save "Watch this level" from the Rate Lever (audit 2026-09-21 ``wow-ai-2``, W5d).

``POST /api/growth-agent/rate-watch`` turns ONE Rate Lever step into a
reviewed ``rate_trigger_watch`` watchlist. The client sends the step only; the
server:

1. reads today's scenario grid (``gold.rate_sensitivity_rollup`` through the
   shared repository): not built -> 409, the step no longer on the grid -> 409;
2. takes the watched par level from the grid at that step (2 dp), the
   direction from the step's sign, and names the watchlist itself (the name
   spells "percent": the reviewed monitor-name validator refuses ``%``);
3. in one Lakebase transaction returns an active watch with identical criteria
   and cadence as-is (``replayed``, no audit row), or upserts the watchlist
   with no run (``last_run_id`` NULL) and writes one
   ``GROWTH_AGENT_MONITOR_SAVE`` row.

No run is created and nothing is sent. Each later run compares the weekly
30-year print with the level and drafts a review note only when it is reached
(``growth_agent_drafts``); it never forecasts a rate.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from fastapi import HTTPException

from backend.schemas.analytics_rate_window import RateWindowResponse
from backend.schemas.geo_rate_sensitivity import RateSensitivityResponse
from backend.schemas.growth_agent_rate_watch import (
    GrowthAgentRateWatchRequest,
    GrowthAgentRateWatchResponse,
)
from backend.schemas.growth_agent_watchlist import GrowthAgentSchedulerStatus
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import MONITOR_SELECT_BY_KEY_SQL, MONITOR_UPSERT_SQL
from backend.services.growth_agent_monitors import json_object, monitor_from_row
from backend.services.growth_agent_rate_trigger import direction_for_step, latest_print, valid_level
from backend.services.growth_agent_runtime import criteria_for
from backend.services.growth_agent_workflows import (
    RateTrigger,
    build_growth_agent_route,
    rate_trigger_workflow,
)
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit

log = logging.getLogger(__name__)

NOT_BUILT_DETAIL = "Rate scenarios are not built yet."
GRID_CHANGED_DETAIL = "The Rate Lever grid changed; move the slider again."


def rate_watch_name(trigger: RateTrigger) -> str:
    """The server's watchlist label; it passes the reviewed monitor-name validator unchanged."""

    words = "at or below" if trigger.direction == "at_or_below" else "at or above"
    return f"Rate watch - 30-year par {words} {trigger.level_pct:.2f} percent"


def trigger_from_grid(grid: RateSensitivityResponse, step_bps: int) -> RateTrigger:
    """The watched level at ``step_bps`` on today's grid, or the governed 409."""

    if not grid.built or not grid.steps_bps or grid.base_market_rate_pct is None:
        raise HTTPException(status_code=409, detail=NOT_BUILT_DETAIL)
    if step_bps not in grid.steps_bps:
        raise HTTPException(status_code=409, detail=GRID_CHANGED_DETAIL)
    index = grid.steps_bps.index(step_bps)
    if index >= len(grid.scenario_market_rate_pct):
        raise HTTPException(status_code=409, detail=GRID_CHANGED_DETAIL)
    level = round(float(grid.scenario_market_rate_pct[index]), 2)
    base = round(float(grid.base_market_rate_pct), 2)
    if not valid_level(level) or not valid_level(base):
        raise HTTPException(status_code=409, detail=GRID_CHANGED_DETAIL)
    return RateTrigger(
        step_bps=step_bps,
        level_pct=level,
        direction=direction_for_step(step_bps),
        base_market_rate_pct=base,
    )


def save_rate_watch(
    lakebase: LakebaseClient,
    *,
    actor: str,
    payload: GrowthAgentRateWatchRequest,
    grid: RateSensitivityResponse,
    window: RateWindowResponse,
    scheduler: GrowthAgentSchedulerStatus,
) -> GrowthAgentRateWatchResponse:
    """Save (or replay) the caller's watch of one Rate Lever step."""

    try:
        trigger = trigger_from_grid(grid, payload.step_bps)
    except HTTPException as exc:
        emit(log, "growth_agent_rate_watch_saved", outcome=f"refused_{exc.status_code}")
        raise
    workflow = rate_trigger_workflow(trigger)
    criteria = criteria_for(workflow, [])
    route = build_growth_agent_route(workflow.route_filters, path=workflow.route_path)
    source_assets = list(workflow.source_assets)
    key = {"actor_email": actor, "workflow_id": workflow.id, "name": rate_watch_name(trigger)}
    try:
        with lakebase.transaction() as conn:
            existing = _fetchone(conn, MONITOR_SELECT_BY_KEY_SQL, key)
            replayed = (
                existing is not None
                and existing.get("status") == "active"
                and existing.get("cadence") == payload.cadence
                and json_object(existing.get("criteria")) == criteria
            )
            if replayed and existing is not None:
                row = existing
            else:
                row = _upsert(
                    conn, key=key, payload=payload, trigger=trigger, criteria=criteria,
                    route=route, source_assets=source_assets,
                )
    except (LakebaseError, psycopg.Error) as exc:
        emit(log, "growth_agent_rate_watch_saved", level=logging.WARNING, outcome="failed")
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    emit(log, "growth_agent_rate_watch_saved", outcome="replayed" if replayed else "saved")
    print_pct, print_week = latest_print(window)
    return GrowthAgentRateWatchResponse(
        monitor=monitor_from_row(row),
        replayed=replayed,
        watch_level_pct=trigger.level_pct,
        watch_direction=trigger.direction,
        latest_print_pct=print_pct,
        latest_print_week=print_week,
        scheduler=scheduler,
    )


def _upsert(
    conn: Any,
    *,
    key: dict[str, Any],
    payload: GrowthAgentRateWatchRequest,
    trigger: RateTrigger,
    criteria: dict[str, object],
    route: str,
    source_assets: list[str],
) -> dict[str, Any]:
    row = _fetchone(
        conn,
        MONITOR_UPSERT_SQL,
        {
            **key,
            "cadence": payload.cadence,
            "criteria": json.dumps(criteria),
            "route": route,
            "actionable_total": 0,
            "source_assets": source_assets,
            "last_run_id": None,
        },
    )
    if row is None:
        raise LakebaseError("growth-agent rate watch upsert returned no row")
    write_audit_event_in_transaction(
        conn,
        actor=key["actor_email"],
        action="growth_agent.monitor_save",
        entity_type="growth_agent_monitor",
        entity_id=str(row["monitor_id"]),
        payload_json={
            "workflow_id": key["workflow_id"],
            "rate_step_bps": trigger.step_bps,
            "watch_level_pct": trigger.level_pct,
            "watch_direction": trigger.direction,
            "route": route,
            "source_assets": source_assets,
        },
        event_type="GROWTH_AGENT_MONITOR_SAVE",
        request_id=payload.request_id,
    )
    return row


def _fetchone(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    execute = getattr(conn, "execute", None)
    if callable(execute):
        row = execute(sql, params).fetchone()
        return dict(row) if row is not None else None
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None


__all__ = [
    "GRID_CHANGED_DETAIL",
    "NOT_BUILT_DETAIL",
    "rate_watch_name",
    "save_rate_watch",
    "trigger_from_grid",
]
