"""Contracts for saved-watchlist briefings, the scheduler state and Save as watchlist.

Audit 2026-09-21 ``wow-ai-4`` (backend), ``flow-08`` slice 2 and ``genie-09``
part 1:

- ``GrowthAgentSchedulerStatus`` says whether the saved-watchlist scheduler job
  runs (its schedule is read through the Jobs API, never assumed);
- ``GrowthAgentWatchlistBriefing`` is one saved watchlist with its run series
  read from the Lakebase ledger: the latest and previous completed run, the
  deltas between them, and up to eight recent eligible counts. It never
  carries the stored route (which can hold an expiring, actor-bound Lead
  Queue handoff proof), the criteria or the actor;
- ``GrowthAgentRunWatchlistRequest`` saves exactly an executed, completed run
  as a watchlist, bound to its stored ``tool_result_hash``. Nothing re-plans.

``GrowthAgentSchedulerState`` is declared in ``backend.schemas.growth_agent``
(``GrowthAgentHomeResponse.scheduler_state`` uses it) and re-exported here:
this module imports the cadence, workflow id and monitor-name validator from
that module, so declaring the Literal here would be a circular import.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.growth_agent import (
    GrowthAgentCadence,
    GrowthAgentSchedulerState,
    GrowthAgentWorkflowId,
)

GrowthAgentSchedulerReason = Literal["job_schedule", "no_schedule", "not_configured", "lookup_failed"]

MAX_SPARKLINE_POINTS = 8
DEFAULT_WATCHLIST_SUMMARY_LIMIT = 20
MAX_WATCHLIST_SUMMARY_LIMIT = 50


class GrowthAgentSchedulerStatus(BaseModel):
    """Whether scheduled watchlist runs are on, and why the App knows."""

    model_config = ConfigDict(extra="forbid")

    state: GrowthAgentSchedulerState
    reason: GrowthAgentSchedulerReason


class GrowthAgentWatchlistBriefing(BaseModel):
    """One saved watchlist with its run-over-run change from the ledger."""

    model_config = ConfigDict(extra="forbid")

    monitor_id: str
    workflow_id: GrowthAgentWorkflowId
    name: str
    cadence: GrowthAgentCadence
    status: Literal["active", "paused", "disabled"]
    run_count: int = Field(ge=0)
    last_run_at: datetime | None = None
    previous_run_at: datetime | None = None
    actionable_total: int | None = Field(default=None, ge=0)
    previous_actionable_total: int | None = Field(default=None, ge=0)
    actionable_delta: int | None = None
    actionable_avg_score: float | None = None
    previous_actionable_avg_score: float | None = None
    avg_score_delta: float | None = None
    # Oldest to newest, at most MAX_SPARKLINE_POINTS completed runs.
    recent_actionable_totals: list[int] = Field(default_factory=list, max_length=MAX_SPARKLINE_POINTS)


class GrowthAgentWatchlistSummaryResponse(BaseModel):
    """The caller's saved watchlists as briefings, with the scheduler state."""

    model_config = ConfigDict(extra="forbid")

    scheduler: GrowthAgentSchedulerStatus
    watchlists: list[GrowthAgentWatchlistBriefing]


__all__ = [
    "DEFAULT_WATCHLIST_SUMMARY_LIMIT",
    "MAX_SPARKLINE_POINTS",
    "MAX_WATCHLIST_SUMMARY_LIMIT",
    "GrowthAgentSchedulerReason",
    "GrowthAgentSchedulerState",
    "GrowthAgentSchedulerStatus",
    "GrowthAgentWatchlistBriefing",
    "GrowthAgentWatchlistSummaryResponse",
]
