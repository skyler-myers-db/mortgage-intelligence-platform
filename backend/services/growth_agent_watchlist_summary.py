"""Audit-free saved-watchlist briefings (audit 2026-09-21 wow-ai-4 backend).

``GET /api/growth-agent/monitors/summary`` turns each of the caller's saved
watchlists into a briefing: its latest completed run, the run before it, the
change between them and up to eight recent eligible counts for a sparkline.
The series comes from the Lakebase run ledger (``WATCHLIST_SUMMARY_SQL``,
runs tagged with the watchlist plus its seed and last run); deltas are
computed here.

A passive read: it writes no audit row, never starts a run, never touches the
SQL warehouse, and never returns a stored route (which can carry an expiring,
actor-bound Lead Queue handoff proof), criteria or actor. A Lakebase failure
is a 503 with the safe dependency detail.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException

from backend.schemas.growth_agent_watchlist import (
    MAX_SPARKLINE_POINTS,
    MAX_WATCHLIST_SUMMARY_LIMIT,
    GrowthAgentSchedulerStatus,
    GrowthAgentWatchlistBriefing,
    GrowthAgentWatchlistSummaryResponse,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import WATCHLIST_SUMMARY_SQL
from backend.services.lakebase import LakebaseClient, LakebaseError


def watchlist_summary(
    lakebase: LakebaseClient,
    *,
    actor: str,
    limit: int,
    scheduler: GrowthAgentSchedulerStatus,
) -> GrowthAgentWatchlistSummaryResponse:
    """Return up to ``limit`` (1..50) of ``actor``'s watchlists as briefings."""

    bounded = max(1, min(int(limit), MAX_WATCHLIST_SUMMARY_LIMIT))
    try:
        rows = lakebase.fetchall(
            WATCHLIST_SUMMARY_SQL,
            {"actor_email": actor, "limit": bounded, "points": MAX_SPARKLINE_POINTS},
            limit=bounded * MAX_SPARKLINE_POINTS,
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return GrowthAgentWatchlistSummaryResponse(scheduler=scheduler, watchlists=briefings_from_rows(rows))


def briefings_from_rows(rows: Iterable[dict[str, Any]]) -> list[GrowthAgentWatchlistBriefing]:
    """Group the summary rows (one per watchlist and recent run) into briefings.

    Rows arrive ordered by watchlist, then newest run first (``recency`` 1).
    A watchlist with no completed run arrives once with NULL run columns.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["monitor_id"]), []).append(row)
    return [_briefing(monitor_id, series) for monitor_id, series in grouped.items()]


def _briefing(monitor_id: str, series: list[dict[str, Any]]) -> GrowthAgentWatchlistBriefing:
    head = series[0]
    runs = [row for row in series if row.get("recency") is not None]
    latest = runs[0] if runs else {}
    totals = [_int(row.get("actionable_total")) for row in reversed(runs)]
    actionable = _int(latest.get("actionable_total"))
    previous_actionable = _int(latest.get("previous_actionable_total"))
    score = _float(latest.get("actionable_avg_score"))
    previous_score = _float(latest.get("previous_actionable_avg_score"))
    return GrowthAgentWatchlistBriefing(
        monitor_id=monitor_id,
        workflow_id=head["workflow_id"],
        name=str(head["name"]),
        cadence=head["cadence"],
        status=head.get("status") or "active",
        run_count=_int(latest.get("run_count")) or 0,
        last_run_at=latest.get("run_at"),
        previous_run_at=latest.get("previous_run_at"),
        actionable_total=actionable,
        previous_actionable_total=previous_actionable,
        actionable_delta=(
            actionable - previous_actionable
            if actionable is not None and previous_actionable is not None
            else None
        ),
        actionable_avg_score=score,
        previous_actionable_avg_score=previous_score,
        avg_score_delta=(
            round(score - previous_score, 1) if score is not None and previous_score is not None else None
        ),
        recent_actionable_totals=[total for total in totals if total is not None][-MAX_SPARKLINE_POINTS:],
    )


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


__all__ = ["briefings_from_rows", "watchlist_summary"]
