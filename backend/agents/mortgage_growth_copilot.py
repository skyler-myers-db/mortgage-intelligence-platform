"""Safe Mortgage Growth co-pilot planner.

The co-pilot accepts a natural-language objective and, when a Supervisor Agent
endpoint is configured and live, invokes it for bounded planning evidence. The
Supervisor is advisory only: workflow choice, criteria, SQL, counts, routes,
audit rows, and watchlist writes stay in the reviewed deterministic executor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

from backend.config.settings import Settings, get_settings
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.growth_agent_workflows import (
    GrowthAgentWorkflowDef,
    planned_workflow,
)

ExecutionMode = Literal["deterministic", "genie_conversation", "agent_framework"]
TraceKind = Literal["local_hash", "genie_conversation", "agent_framework", "mlflow_trace"]


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

    The normal Genie answer path can compile and execute SQL, so this planner
    does not call it. A configured Supervisor Agent endpoint may be invoked for
    prompt-interpretation evidence, but its response cannot alter the reviewed
    workflow, SQL predicates, or handoff route.
    """

    deterministic_workflow, deterministic_intent = planned_workflow(payload)
    settings = settings or get_settings()
    framework_evidence = _agent_framework_evidence(
        payload,
        workflow=deterministic_workflow,
        deterministic_intent=deterministic_intent,
        settings=settings,
    )
    if framework_evidence is not None:
        return deterministic_workflow, framework_evidence
    return deterministic_workflow, GrowthAgentCopilotEvidence(
        execution_mode="deterministic",
        trace_kind="local_hash",
        planner_label="Reviewed deterministic planner",
        interpreted_intent=deterministic_intent,
        reasoning_summary=(
            "Growth objectives are routed through reviewed workflow rules. "
            "No model-generated SQL, DML, outreach, or unreviewed workflow choice was executed."
        ),
        fallback_reason=_planner_fallback_reason(settings),
    )


def _agent_framework_evidence(
    payload: GrowthAgentPromptRunRequest,
    *,
    workflow: GrowthAgentWorkflowDef,
    deterministic_intent: str,
    settings: Settings,
) -> GrowthAgentCopilotEvidence | None:
    if not settings.mip_agent_orchestrator:
        return None
    endpoint = (settings.mip_agent_serving_endpoint or "").strip()
    supervisor_id = (settings.mip_agent_supervisor_id or "").strip()
    if not endpoint or not supervisor_id:
        return None
    try:
        workspace_client = _workspace_client()
        task = _agent_task_if_ready(workspace_client, endpoint)
        if task is None:
            return None
        response = query_serving_endpoint(
            workspace_client,
            endpoint,
            task=task,
            prompt=_supervisor_prompt(payload, workflow=workflow, interpreted_intent=deterministic_intent),
            client_request_id=f"mip-growth-agent-{_prompt_hash(payload.prompt)[:20]}",
        )
        if not serving_response_has_payload(response):
            return None
    except Exception:  # noqa: BLE001 - framework failure must not block reviewed fallback
        return None
    return GrowthAgentCopilotEvidence(
        execution_mode="agent_framework",
        trace_kind="agent_framework",
        planner_label="Databricks Supervisor Agent",
        interpreted_intent=deterministic_intent,
        reasoning_summary=(
            "Databricks Supervisor Agent accepted the bounded planning prompt. "
            "Reviewed workflow rules still selected the workflow and deterministic "
            "tools produced counts, filters, and the human-review handoff."
        ),
        question_hash=_prompt_hash(payload.prompt),
        trusted_assets=(
            f"databricks.serving_endpoint.{endpoint}",
            f"databricks.supervisor_agent.{supervisor_id}",
        ),
    )


def _supervisor_prompt(
    payload: GrowthAgentPromptRunRequest,
    *,
    workflow: GrowthAgentWorkflowDef,
    interpreted_intent: str,
) -> str:
    state_scope = ", ".join(payload.states) if payload.states else "current configured coverage"
    segment_scope = (
        f"{payload.segment_mode.upper()} over {', '.join(payload.segment_codes)}"
        if payload.segment_codes
        else "reviewed workflow defaults"
    )
    prompt_hash = _prompt_hash(payload.prompt)
    return (
        "You are the Mortgage Growth Agent Supervisor for Module 0 lead generation. "
        "A validated user objective was classified by the app, but the raw prompt "
        "is not exported. Do not write SQL, do not name borrowers, do not expose "
        "identities, and do not activate outreach. "
        f"Objective hash: {prompt_hash}. "
        f"Reviewed workflow selected by the app contract: {workflow.title} ({workflow.id}). "
        f"Interpreted intent: {interpreted_intent}. "
        f"State scope: {state_scope}. "
        f"Segment scope: {segment_scope}. "
        "Reply with a concise acknowledgement that the reviewed workflow and human "
        "approval gates should remain in control."
    )


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()


def _planner_fallback_reason(settings: Settings) -> str:
    if not settings.mip_agent_orchestrator:
        return "model_sql_planning_not_enabled"
    if not settings.mip_agent_serving_endpoint or not settings.mip_agent_supervisor_id:
        return "agent_orchestrator_not_configured"
    return "agent_orchestrator_unavailable"


def _agent_task_if_ready(workspace_client: object, endpoint: str) -> str | None:
    serving_endpoints = getattr(workspace_client, "serving_endpoints", None)
    get = getattr(serving_endpoints, "get", None)
    if not callable(get):
        return None
    details = get(endpoint)
    ready = _enum_value(getattr(getattr(details, "state", None), "ready", None))
    if ready != "READY":
        return None
    task = str(getattr(details, "task", "") or "")
    if not _is_agent_responses_task(task):
        return None
    return task


def _is_agent_responses_task(task: object) -> bool:
    raw = getattr(task, "value", task)
    normalized = str(raw or "").strip().lower()
    canonical = normalized.replace("-", "_").replace("/", "_")
    return canonical == "agent_v1_responses"


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").upper()


def _workspace_client() -> object:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()
