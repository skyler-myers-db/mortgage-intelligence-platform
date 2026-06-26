"""Contracts for the Mortgage Growth Agent workspace."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import (
    contains_pii_marker,
    validate_public_campaign_label,
    validate_public_opaque_id,
)

GrowthAgentWorkflowId = Literal[
    "daily_refi_brief",
    "listing_watch",
    "competitor_recapture_monitor",
    "high_equity_heloc_watch",
]
GrowthAgentCadence = Literal["daily", "weekly"]

_STATE_RE = re.compile(r"^[A-Z]{2}$")
_MONITOR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &.,:+/-]{0,79}$")
_RAW_IDENTIFIER_RE = re.compile(
    r"\b(?:clip_ref_[0-9a-f]{6,}|owner[_\s-]?link|raw[_\s-]?clip|B-[A-Za-z0-9_-]{3,})\b",
    re.IGNORECASE,
)
_WORKFLOW_MONITOR_TITLE_RE = re.compile(
    r"^(?:Daily Refi Opportunity Brief|Listed-for-Sale Purchase Watch|"
    r"Competitor Recapture Monitor|High-Equity / HELOC Watch)"
    r"(?: - [A-Z]{2}(?:, [A-Z]{2}){0,19})?$"
)


def default_growth_agent_cadences() -> list[GrowthAgentCadence]:
    return ["daily", "weekly"]


class GrowthAgentWorkflow(BaseModel):
    id: GrowthAgentWorkflowId
    title: str
    objective: str
    trigger_label: str
    action_label: str
    source_assets: list[str]
    default_route: str
    proof_points: list[str]
    cadence_options: list[GrowthAgentCadence] = Field(default_factory=default_growth_agent_cadences)


class GrowthAgentToolStep(BaseModel):
    label: str
    status: Literal["completed", "blocked", "review_required"]
    detail: str
    source_asset: str | None = None


class GrowthAgentPolicyCheck(BaseModel):
    label: str
    status: Literal["passed", "review_required", "blocked"]
    detail: str


class GrowthAgentMonitor(BaseModel):
    monitor_id: str
    workflow_id: GrowthAgentWorkflowId
    name: str
    cadence: GrowthAgentCadence
    status: Literal["active", "paused", "disabled"] = "active"
    criteria: dict[str, object]
    route: str
    actionable_total: int = Field(ge=0)
    source_assets: list[str]
    last_run_id: str | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class GrowthAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    states: list[str] = Field(default_factory=list, max_length=20)
    save_monitor: bool = False
    cadence: GrowthAgentCadence = "daily"
    monitor_name: str | None = Field(default=None, max_length=80)
    request_id: str | None = None

    @field_validator("states")
    @classmethod
    def _states(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            state = str(value).strip().upper()
            if not state:
                continue
            if not _STATE_RE.fullmatch(state):
                raise ValueError("states must contain 2-character USPS codes")
            if state not in out:
                out.append(state)
        return out

    @field_validator("monitor_name")
    @classmethod
    def _monitor_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = re.sub(r"\s+", " ", value.strip())
        if not clean:
            return None
        if contains_pii_marker(clean) or _RAW_IDENTIFIER_RE.search(clean):
            raise ValueError("monitor_name must be a public-safe workflow label")
        if _WORKFLOW_MONITOR_TITLE_RE.fullmatch(clean):
            return clean
        if not _MONITOR_NAME_RE.fullmatch(clean):
            raise ValueError("monitor_name must be a public-safe workflow label")
        try:
            validate_public_campaign_label(clean, field_name="monitor_name")
        except ValueError as exc:
            raise ValueError("monitor_name must be a public-safe workflow label") from exc
        return clean

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class GrowthAgentRunResponse(BaseModel):
    workflow: GrowthAgentWorkflow
    run_id: str
    monitor: GrowthAgentMonitor | None = None
    broad_total: int = Field(ge=0)
    actionable_total: int = Field(ge=0)
    broad_avg_score: float | None = None
    actionable_avg_score: float | None = None
    avg_rate_spread_bps: float | None = None
    avg_equity_pct: float | None = None
    route: str
    criteria: dict[str, object]
    source_assets: list[str]
    tool_steps: list[GrowthAgentToolStep]
    policy_checks: list[GrowthAgentPolicyCheck]
    audit_event_id: str | None = None
    created_at: datetime | str | None = None


class GrowthAgentHomeResponse(BaseModel):
    workflows: list[GrowthAgentWorkflow]
    monitors: list[GrowthAgentMonitor]
