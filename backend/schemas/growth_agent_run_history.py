"""Contract for the Growth Agent run-history list (audit 2026-09-21 genie-09).

A summary of one reviewed-workflow run from the Lakebase run ledger, scoped to
the caller. It carries counts, the reviewed workflow and its source assets
only: never the actor, the stored criteria or the stored Lead Queue route,
because a stored route can carry an expiring, actor-bound handoff proof.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.growth_agent import GrowthAgentWorkflowId


class GrowthAgentRunSummary(BaseModel):
    """One of the caller's reviewed-workflow runs, newest first."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_id: GrowthAgentWorkflowId
    workflow_title: str
    status: Literal["completed", "failed"]
    broad_total: int = Field(ge=0)
    actionable_total: int = Field(ge=0)
    actionable_avg_score: float | None = None
    source_assets: list[str] = Field(default_factory=list)
    audit_event_id: str | None = None
    created_at: datetime | str | None = None


__all__ = ["GrowthAgentRunSummary"]
