"""Contracts for the Mortgage Growth Agent plan-composition surface.

Composition upgrades the co-pilot from selecting one reviewed workflow to
composing a multi-step plan from the governed deterministic tool registry. The
model proposes structure; the app validates every step against the registry and
executes only reviewed, deterministic tool implementations. These schemas carry
the composed plan and its deterministic execution trace — never raw prompt text,
borrower identities, or model-authored SQL.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import validate_public_opaque_id
from backend.schemas.growth_agent import (
    GrowthAgentWorkflow,
    assert_reviewed_growth_objective,
)
from backend.schemas.usps import is_usps_state_code

ComposePlanStatus = Literal["composed", "degraded", "invalid"]
PlanStepStatus = Literal["pending", "completed", "review_required", "blocked"]

MAX_PLAN_STEPS = 8
MAX_RATIONALE_LEN = 300


class ComposePlanRequest(BaseModel):
    """Ask the co-pilot to compose (and optionally execute) a governed plan."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    objective: str = Field(min_length=3, max_length=500)
    execute: bool = False
    states: list[str] = Field(default_factory=list, max_length=20)
    request_id: str | None = None

    @field_validator("objective")
    @classmethod
    def _objective(cls, value: str) -> str:
        return assert_reviewed_growth_objective(value)

    @field_validator("states")
    @classmethod
    def _states(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            state = str(value).strip().upper()
            if not state:
                continue
            if len(state) != 2 or not is_usps_state_code(state):
                raise ValueError("states must contain valid USPS state codes")
            if state not in out:
                out.append(state)
        return out

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class PlanStep(BaseModel):
    """One validated, deterministic step of a composed plan."""

    step_id: str = Field(min_length=1, max_length=48)
    tool: str = Field(min_length=1, max_length=64)
    params: dict[str, object] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=MAX_RATIONALE_LEN)


class ComposedPlan(BaseModel):
    """A model-composed, app-validated multi-step plan.

    ``requires_approval`` is always computed server-side from the registry: any
    step whose tool writes state or hands off to outreach/campaign forces it to
    ``True`` regardless of what the model returned.
    """

    objective_summary: str = Field(max_length=MAX_RATIONALE_LEN)
    steps: list[PlanStep] = Field(min_length=1, max_length=MAX_PLAN_STEPS)
    expected_outcome: str = Field(default="", max_length=MAX_RATIONALE_LEN)
    risk_notes: str = Field(default="", max_length=MAX_RATIONALE_LEN)
    requires_approval: bool = False


class PlanStepTrace(BaseModel):
    """Deterministic execution record for one plan step."""

    step_id: str
    tool: str
    label: str
    status: PlanStepStatus
    detail: str
    duration_ms: int = Field(ge=0)
    row_summary: int | None = Field(default=None, ge=0)
    result_hash: str | None = None
    source_asset: str | None = None
    approval_gate: bool = False
    audit_event_id: str | None = None


class ComposePlanResponse(BaseModel):
    """Response for the plan-composition endpoint.

    ``status`` discriminates the three honest outcomes: a validated
    ``composed`` plan, a ``degraded`` response when the Supervisor host is
    unavailable (reviewed catalog workflows are offered as a labelled
    fallback), or an ``invalid`` response when the Supervisor answered but the
    plan failed validation (no silent canned substitution).
    """

    status: ComposePlanStatus
    planner: str = "supervisor_composed"
    model_endpoint: str | None = None
    plan: ComposedPlan | None = None
    trace: list[PlanStepTrace] = Field(default_factory=list)
    approval_required: bool = False
    approval_gate_step_id: str | None = None
    executed: bool = False
    plan_id: str | None = None
    interpreted_intent: str | None = None
    reasoning_summary: str | None = None
    degraded_reason: str | None = None
    message: str | None = None
    fallback_workflows: list[GrowthAgentWorkflow] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)


__all__ = [
    "MAX_PLAN_STEPS",
    "MAX_RATIONALE_LEN",
    "ComposePlanRequest",
    "ComposePlanResponse",
    "ComposePlanStatus",
    "ComposedPlan",
    "PlanStep",
    "PlanStepStatus",
    "PlanStepTrace",
]
