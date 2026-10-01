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

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.schemas.growth_agent import GrowthAgentSchedulerState

GrowthAgentSchedulerReason = Literal["job_schedule", "no_schedule", "not_configured", "lookup_failed"]


class GrowthAgentSchedulerStatus(BaseModel):
    """Whether scheduled watchlist runs are on, and why the App knows."""

    model_config = ConfigDict(extra="forbid")

    state: GrowthAgentSchedulerState
    reason: GrowthAgentSchedulerReason


__all__ = [
    "GrowthAgentSchedulerReason",
    "GrowthAgentSchedulerState",
    "GrowthAgentSchedulerStatus",
]
