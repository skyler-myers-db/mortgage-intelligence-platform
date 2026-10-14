"""Contracts for "Watch this level" on the Rate Lever (audit 2026-09-21 ``wow-ai-2``).

``POST /api/growth-agent/rate-watch`` saves a reviewed ``rate_trigger_watch``
watchlist from ONE Rate Lever step. The client sends the step, a cadence and
an idempotency id only: never a rate and never a name. The server reads
today's scenario grid, takes the watched par level from it (2 dp), derives
the direction from the step's sign and names the watchlist itself. A run of
the watch compares the latest weekly 30-year print with that level and drafts
a review note only when it is reached; it never forecasts a rate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import validate_public_opaque_id
from backend.schemas.growth_agent import GrowthAgentCadence, GrowthAgentMonitor
from backend.schemas.growth_agent_watchlist import GrowthAgentSchedulerStatus

GrowthAgentRateWatchDirection = Literal["at_or_below", "at_or_above"]


class GrowthAgentRateWatchRequest(BaseModel):
    """Watch the par level of one Rate Lever step. No rate, no name."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    step_bps: int = Field(
        ge=-100,
        le=100,
        strict=True,
        description="The Rate Lever step in basis points of par move; not 0. The server reads its par level.",
    )
    cadence: GrowthAgentCadence = "daily"
    request_id: str | None = None

    @field_validator("step_bps")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("step_bps must move the par rate")
        return value

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        return None if value is None else validate_public_opaque_id(value)


class GrowthAgentRateWatchResponse(BaseModel):
    """The saved watch, the server's level and the latest weekly print."""

    model_config = ConfigDict(extra="forbid")

    monitor: GrowthAgentMonitor
    replayed: bool = Field(description="True when an identical active watch already existed: nothing was written.")
    watch_level_pct: float = Field(description="The watched 30-year par level in percent, from today's scenario grid.")
    watch_direction: GrowthAgentRateWatchDirection
    latest_print_pct: float | None = Field(
        default=None, description="The latest weekly 30-year print in percent; None when no print is available."
    )
    latest_print_week: str | None = Field(default=None, description="Week-starting Monday of that print (ISO date).")
    scheduler: GrowthAgentSchedulerStatus


__all__ = [
    "GrowthAgentRateWatchDirection",
    "GrowthAgentRateWatchRequest",
    "GrowthAgentRateWatchResponse",
]
