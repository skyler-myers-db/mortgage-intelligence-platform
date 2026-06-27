"""Safe Mortgage Growth co-pilot planner.

The co-pilot accepts a natural-language objective, but the current runtime does
not call a model-generated SQL path to interpret it. Until a no-SQL planner
endpoint is explicitly provisioned and live-probed, prompt routing remains a
reviewed deterministic classifier over the workflow catalog. All counts, routes,
audit rows, and watchlist writes stay in the deterministic executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from backend.config.settings import Settings
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.growth_agent_workflows import (
    GrowthAgentWorkflowDef,
    planned_workflow,
)

ExecutionMode = Literal["deterministic", "genie_conversation", "agent_framework"]
TraceKind = Literal["local_hash", "genie_conversation", "mlflow_trace"]


@dataclass(frozen=True)
class GrowthAgentCopilotEvidence:
    """Non-PII proof of how a prompt was interpreted."""

    execution_mode: ExecutionMode
    trace_kind: TraceKind
    planner_label: str
    interpreted_intent: str
    reasoning_summary: str
    conversation_id: str | None = None
    message_id: str | None = None
    question_hash: str | None = None
    sql_hash: str | None = None
    row_count: int | None = None
    trusted_assets: tuple[str, ...] = field(default_factory=tuple)
    thoughts: tuple[str, ...] = field(default_factory=tuple)
    fallback_reason: str | None = None

    def criteria_json(self) -> dict[str, object]:
        """Bounded, non-PII representation safe for Lakebase criteria."""

        payload: dict[str, object] = {
            "execution_mode": self.execution_mode,
            "trace_kind": self.trace_kind,
            "planner_label": self.planner_label,
            "interpreted_intent": self.interpreted_intent,
            "reasoning_summary": self.reasoning_summary,
        }
        optional: dict[str, object | None] = {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "question_hash": self.question_hash,
            "sql_hash": self.sql_hash,
            "row_count": self.row_count,
            "trusted_assets": list(self.trusted_assets),
            "thoughts": list(self.thoughts[:3]),
            "fallback_reason": self.fallback_reason,
        }
        payload.update({key: value for key, value in optional.items() if value not in (None, [], "")})
        return payload


def plan_growth_agent_prompt(
    payload: GrowthAgentPromptRunRequest,
    *,
    settings: Settings | None = None,
) -> tuple[GrowthAgentWorkflowDef, GrowthAgentCopilotEvidence]:
    """Return a reviewed workflow plus honest co-pilot evidence.

    ``settings`` is accepted for forward-compatible call sites, but it is not
    used to enable model planning. The normal Genie Conversation answer API can
    compile and execute SQL; this planner must not invoke it.
    """

    _ = settings
    deterministic_workflow, deterministic_intent = planned_workflow(payload)
    return deterministic_workflow, GrowthAgentCopilotEvidence(
        execution_mode="deterministic",
        trace_kind="local_hash",
        planner_label="Reviewed deterministic planner",
        interpreted_intent=deterministic_intent,
        reasoning_summary=(
            "Growth objectives are routed through reviewed workflow rules. "
            "No model-generated SQL, DML, outreach, or unreviewed workflow choice was executed."
        ),
        fallback_reason="model_sql_planning_not_enabled",
    )
