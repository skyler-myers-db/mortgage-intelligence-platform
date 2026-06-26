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
    "borrower_dossier_review",
    "listing_watch",
    "competitor_recapture_monitor",
    "high_equity_heloc_watch",
    "branch_capacity_review",
    "source_freshness_sentinel",
    "custom_segment_watch",
]
GrowthAgentCadence = Literal["daily", "weekly"]
GrowthAgentSegmentCode = Literal["itm", "listed", "permit", "investor", "equity", "retention"]
GrowthAgentSegmentMode = Literal["any", "all"]
GrowthAgentSpecialist = Literal[
    "structured_data_agent",
    "borrower_dossier_agent",
    "offer_agent",
    "compliance_agent",
    "campaign_agent",
    "data_ops_agent",
]

_STATE_RE = re.compile(r"^[A-Z]{2}$")
_MONITOR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &.,:+/-]{0,79}$")
_RAW_IDENTIFIER_RE = re.compile(
    r"\b(?:clip_ref_[0-9a-f]{6,}|clip\s*[:#-]?\s*[A-Za-z0-9_-]{6,}|"
    r"owner[_\s-]?link(?:\s*[A-Za-z0-9_-]{3,})?|raw[_\s-]?clip|B-[A-Za-z0-9_-]{3,})\b",
    re.IGNORECASE,
)
_PROMPT_STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+\s+"
    r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|way)\b",
    re.IGNORECASE,
)
_PROMPT_STREET_ADDRESS_NO_SUFFIX_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z0-9.'-]{2,}\s+[A-Z][A-Za-z0-9.'-]{2,}"
    r"(?:\s+[A-Z][A-Za-z0-9.'-]{2,}){0,2}\b"
)
_PROMPT_HUMAN_NAME_RE = re.compile(
    r"\b(?:for|named|borrower|customer|prospect|contact|person|show|find|review)\s+"
    r"(?:(?:[A-Z][a-z]{1,30}|[A-Z]{2,30})\s+"
    r"(?:[A-Z]\s+)?(?:[A-Z][a-z]{1,30}|[A-Z]{2,30}))\b"
)
_PROMPT_LEADING_HUMAN_NAME_RE = re.compile(
    r"^(?!(?:find|show|list|run|build|open|review|count|check|create|save|how|what|which|"
    r"source|data|mortgage|growth|prime|home|high|daily|branch|heloc|HELOC|"
    r"genie|Genie|cotality|Cotality|databricks|Databricks)\b)"
    r"(?:[A-Z][a-z]{1,30}|[A-Z]{2,30})\s+"
    r"(?:[A-Z]\s+)?(?:[A-Z][a-z]{1,30}|[A-Z]{2,30})\b"
)
_WORKFLOW_MONITOR_TITLE_RE = re.compile(
	r"^(?:Daily Refi Opportunity Brief|Listed-for-Sale Purchase Watch|"
	r"Competitor Recapture Monitor|High-Equity / HELOC Watch|"
	r"Borrower Dossier Review|Branch Manager Capacity Review|Source/Freshness Sentinel|"
	r"Custom Segment Workflow|Mortgage Growth Agent)"
	r"(?: - [A-Z]{2}(?:, [A-Z]{2}){0,19})?$"
)
_CUSTOM_WORKFLOW_MONITOR_TITLE_RE = re.compile(
    r"^Custom Segment Workflow - (?:ITM|LISTED|PERMIT|INVESTOR|EQUITY|RETENTION)"
    r"(?:\+(?:ITM|LISTED|PERMIT|INVESTOR|EQUITY|RETENTION)){0,5}"
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
    tool_name: str | None = None
    result_hash: str | None = None


class GrowthAgentPolicyCheck(BaseModel):
    label: str
    status: Literal["passed", "review_required", "blocked"]
    detail: str


class GrowthAgentGovernanceChip(BaseModel):
    label: str
    status: Literal["passed", "review_required", "roadmap", "not_provisioned"]
    detail: str
    evidence_ref: str | None = None


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
        if _WORKFLOW_MONITOR_TITLE_RE.fullmatch(clean) or _CUSTOM_WORKFLOW_MONITOR_TITLE_RE.fullmatch(clean):
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


class GrowthAgentCustomRunRequest(GrowthAgentRunRequest):
    segment_codes: list[GrowthAgentSegmentCode] = Field(min_length=1, max_length=6)
    segment_mode: GrowthAgentSegmentMode = "any"

    @field_validator("segment_codes")
    @classmethod
    def _segment_codes(cls, values: list[GrowthAgentSegmentCode]) -> list[GrowthAgentSegmentCode]:
        out: list[GrowthAgentSegmentCode] = []
        for value in values:
            if value not in out:
                out.append(value)
        if not out:
            raise ValueError("segment_codes must include at least one reviewed segment")
        return out


class GrowthAgentPromptRunRequest(GrowthAgentRunRequest):
    prompt: str = Field(min_length=3, max_length=500)
    segment_codes: list[GrowthAgentSegmentCode] = Field(default_factory=list, max_length=6)
    segment_mode: GrowthAgentSegmentMode = "any"

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        clean = re.sub(r"\s+", " ", value.strip())
        if (
            contains_pii_marker(clean)
            or _RAW_IDENTIFIER_RE.search(clean)
            or _PROMPT_STREET_ADDRESS_RE.search(clean)
            or _PROMPT_STREET_ADDRESS_NO_SUFFIX_RE.search(clean)
            or _PROMPT_HUMAN_NAME_RE.search(clean)
            or _PROMPT_LEADING_HUMAN_NAME_RE.search(clean)
        ):
            raise ValueError("prompt must not include borrower PII or raw identifiers")
        return clean

    @field_validator("segment_codes")
    @classmethod
    def _prompt_segment_codes(cls, values: list[GrowthAgentSegmentCode]) -> list[GrowthAgentSegmentCode]:
        out: list[GrowthAgentSegmentCode] = []
        for value in values:
            if value not in out:
                out.append(value)
        return out


class GrowthAgentRunResponse(BaseModel):
    workflow: GrowthAgentWorkflow
    run_id: str
    monitor: GrowthAgentMonitor | None = None
    specialist_agent: GrowthAgentSpecialist
    trace_id: str
    tool_result_hash: str
    broad_label: str = "Broad opportunity"
    actionable_label: str = "Eligible subset"
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
    governance_chips: list[GrowthAgentGovernanceChip] = Field(default_factory=list)
    interpreted_intent: str | None = None
    audit_event_id: str | None = None
    created_at: datetime | str | None = None


class GrowthAgentHomeResponse(BaseModel):
    workflows: list[GrowthAgentWorkflow]
    monitors: list[GrowthAgentMonitor]
