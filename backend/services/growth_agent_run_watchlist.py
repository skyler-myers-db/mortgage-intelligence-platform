"""Save exactly the executed run as a watchlist (audit 2026-09-21 genie-09 part 1).

``POST /api/growth-agent/runs/{run_id}/monitors``. The old "Save reviewed
watchlist" button re-posted the objective to ``/agent/run`` with
``save_monitor=true``: that re-planned (the model could pick a different
workflow) and saved a run the lender never saw. This path re-plans nothing.
In one Lakebase transaction it:

1. reads the caller's run by ``(actor, run_id)`` (another actor's run is the
   same 404 as a missing one, so existence never leaks);
2. refuses (409, nothing written) a run that is not ``completed``, has no
   audit row, or whose stored ``tool_result_hash`` differs from the one the
   card showed;
3. returns an active watchlist that already points at this run with the same
   cadence as-is (a replay writes no second audit row);
4. otherwise upserts the watchlist from the STORED workflow, criteria, route,
   eligible count and source assets, with ``last_run_id`` = the run, and
   writes one ``GROWTH_AGENT_MONITOR_SAVE`` audit row.

Never called: the planner, the metrics loader, the SQL warehouse, the
composer.

Series gap (wow-ai-4, for the W5b briefing card): runs are append-only, so a
run saved here into an EXISTING watchlist name cannot be tagged with that
watchlist's ``monitor_id``. ``WATCHLIST_SUMMARY_SQL`` still counts it while it
is the watchlist's ``last_run_id``; after the next Run now it leaves the
series, so "change since the previous run" skips it. Closing that needs a
link record, not an UPDATE of the run.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import psycopg
from fastapi import HTTPException

from backend.schemas.growth_agent import GrowthAgentMonitor
from backend.schemas.growth_agent_watchlist import GrowthAgentRunWatchlistRequest
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import (
    MONITOR_SELECT_BY_KEY_SQL,
    MONITOR_UPSERT_SQL,
    RUN_SELECT_FOR_SAVE_SQL,
)
from backend.services.growth_agent_monitors import (
    json_object,
    monitor_from_row,
    states_from_monitor_criteria,
)
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit

log = logging.getLogger(__name__)

RUN_NOT_FOUND_DETAIL = "growth-agent run not found"
RUN_SAVE_CONFLICT_DETAIL = "This run changed or did not complete; run it again before saving."
_CUSTOM_WORKFLOW_ID = "custom_segment_watch"


def save_run_as_watchlist(
    lakebase: LakebaseClient,
    *,
    actor: str,
    run_id: UUID,
    payload: GrowthAgentRunWatchlistRequest,
) -> GrowthAgentMonitor:
    """Save the caller's completed, audited run as a watchlist, exactly as stored."""

    try:
        with lakebase.transaction() as conn:
            run = _fetchone(conn, RUN_SELECT_FOR_SAVE_SQL, {"actor_email": actor, "run_id": str(run_id)})
            if run is None:
                raise HTTPException(status_code=404, detail=RUN_NOT_FOUND_DETAIL)
            if (
                run.get("status") != "completed"
                or run.get("audit_event_id") is None
                or str(run.get("tool_result_hash") or "") != payload.tool_result_hash
            ):
                raise HTTPException(status_code=409, detail=RUN_SAVE_CONFLICT_DETAIL)
            key = {
                "actor_email": actor,
                "workflow_id": run["workflow_id"],
                "name": payload.monitor_name or default_watchlist_name(run),
            }
            replay = _replay_row(_fetchone(conn, MONITOR_SELECT_BY_KEY_SQL, key), run_id=run_id, payload=payload)
            monitor_row = replay if replay is not None else _upsert(
                conn, key=key, run=run, payload=payload, run_id=run_id
            )
    except HTTPException as exc:
        emit(log, "growth_agent_run_watchlist_saved", outcome=f"refused_{exc.status_code}")
        raise
    except (LakebaseError, psycopg.Error) as exc:
        emit(log, "growth_agent_run_watchlist_saved", level=logging.WARNING, outcome="failed")
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    emit(log, "growth_agent_run_watchlist_saved", outcome="replayed" if replay is not None else "saved")
    return monitor_from_row(monitor_row)


def default_watchlist_name(run: dict[str, Any]) -> str:
    """The stored workflow's reviewed title plus its state scope.

    A custom segment run also names its mode and segments (the same label the
    custom workflow's own Save uses), so two custom watchlists over different
    segments never share a name and overwrite each other.
    """

    states = states_from_monitor_criteria(run.get("criteria"))
    state_suffix = f" - {', '.join(states)}" if states else ""
    title = str(run.get("workflow_title") or "")
    if run.get("workflow_id") == _CUSTOM_WORKFLOW_ID:
        filters = json_object(run.get("criteria")).get("lead_queue_filters")
        filters = filters if isinstance(filters, dict) else {}
        codes = filters.get("segment_codes")
        if isinstance(codes, list) and codes:
            mode = str(filters.get("segment_mode") or "any").upper()
            segments = "+".join(str(code).upper() for code in codes)
            return f"{title} - {mode} - {segments}{state_suffix}"
    return f"{title}{state_suffix}"


def _replay_row(
    existing: dict[str, Any] | None, *, run_id: UUID, payload: GrowthAgentRunWatchlistRequest
) -> dict[str, Any] | None:
    """The active watchlist already saved from this run with this cadence, if any."""

    if (
        existing is not None
        and existing.get("status") == "active"
        and str(existing.get("last_run_id")) == str(run_id)
        and existing.get("cadence") == payload.cadence
    ):
        return existing
    return None


def _upsert(
    conn: Any,
    *,
    key: dict[str, Any],
    run: dict[str, Any],
    payload: GrowthAgentRunWatchlistRequest,
    run_id: UUID,
) -> dict[str, Any]:
    source_assets = [str(asset) for asset in (run.get("source_assets") or [])]
    actionable_total = int(run.get("actionable_total") or 0)
    monitor_row = _fetchone(
        conn,
        MONITOR_UPSERT_SQL,
        {
            **key,
            "cadence": payload.cadence,
            "criteria": json.dumps(json_object(run.get("criteria"))),
            "route": str(run["route"]),
            "actionable_total": actionable_total,
            "source_assets": source_assets,
            "last_run_id": str(run_id),
        },
    )
    if monitor_row is None:
        raise RuntimeError("growth-agent watchlist upsert returned no row")
    write_audit_event_in_transaction(
        conn,
        actor=key["actor_email"],
        action="growth_agent.monitor_save",
        entity_type="growth_agent_monitor",
        entity_id=str(monitor_row["monitor_id"]),
        payload_json={
            "workflow_id": run["workflow_id"],
            "run_id": str(run_id),
            "actionable_total": actionable_total,
            "tool_result_hash": payload.tool_result_hash,
            "route": str(run["route"]),
            "source_assets": source_assets,
        },
        event_type="GROWTH_AGENT_MONITOR_SAVE",
        request_id=payload.request_id,
    )
    return monitor_row


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
    "RUN_NOT_FOUND_DETAIL",
    "RUN_SAVE_CONFLICT_DETAIL",
    "default_watchlist_name",
    "save_run_as_watchlist",
]
