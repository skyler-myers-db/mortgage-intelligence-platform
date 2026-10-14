"""Per-step progress of a running composed plan (audit 2026-09-21 ``genie-09`` part 4).

``GET /api/growth-agent/agent/plan/progress?request_id=`` reads the caller's
own execute request's progress row: which step of how many is running, at the
approval gate, or being recorded. It is operational state, never an audit
record, and the read writes no audit row. ``not_started`` covers a request the
server has not reached yet, another actor's id and a failed read alike.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GrowthAgentPlanProgressState = Literal["not_started", "running", "gate", "recording"]


class GrowthAgentPlanProgressResponse(BaseModel):
    """Where the caller's own plan execute is: step k of N and its phase."""

    model_config = ConfigDict(extra="forbid")

    state: GrowthAgentPlanProgressState
    step_index: int | None = Field(
        default=None, ge=0, le=8, description="The 1-based step running or gated; the executed count while recording."
    )
    step_total: int | None = Field(default=None, ge=1, le=8, description="The plan's step count.")
    step_tool: str | None = Field(default=None, max_length=64, description="The reviewed tool of that step.")
    updated_at: datetime | None = None


NOT_STARTED = GrowthAgentPlanProgressResponse(state="not_started")

__all__ = ["NOT_STARTED", "GrowthAgentPlanProgressResponse", "GrowthAgentPlanProgressState"]
