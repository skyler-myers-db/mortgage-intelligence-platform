"""Safe Mortgage Growth co-pilot planner.

The co-pilot accepts a natural-language objective and, when a Supervisor Agent
endpoint is configured and live, may ask it to choose one reviewed workflow id.
The selection is untrusted until it matches the local allowlist; criteria, SQL,
counts, routes, audit rows, and watchlist writes stay in the reviewed
deterministic executor.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from backend.config.settings import Settings, get_settings
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest, GrowthAgentWorkflowId
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.growth_agent_workflows import (
    CUSTOM_WORKFLOW_ID,
    WORKFLOWS,
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
    does not call it. A configured Supervisor Agent endpoint may choose exactly
    one reviewed workflow id from the allowlist. Deterministic code still owns
    SQL predicates, counts, handoff routes, Lakebase writes, audit rows, and
    fallbacks.
    """

    deterministic_workflow, deterministic_intent = planned_workflow(payload)
    settings = settings or get_settings()
    framework_plan = _agent_framework_plan(
        payload,
        deterministic_workflow=deterministic_workflow,
        deterministic_intent=deterministic_intent,
        settings=settings,
    )
    if framework_plan is not None:
        return framework_plan
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


def _agent_framework_plan(
    payload: GrowthAgentPromptRunRequest,
    *,
    deterministic_workflow: GrowthAgentWorkflowDef,
    deterministic_intent: str,
    settings: Settings,
) -> tuple[GrowthAgentWorkflowDef, GrowthAgentCopilotEvidence] | None:
    if not settings.mip_agent_orchestrator:
        return None
    if deterministic_workflow.id == CUSTOM_WORKFLOW_ID:
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
            prompt=_supervisor_prompt(
                payload,
                deterministic_workflow=deterministic_workflow,
                interpreted_intent=deterministic_intent,
            ),
            client_request_id=f"mip-growth-agent-{_prompt_hash(payload.prompt)[:20]}",
        )
        if not serving_response_has_payload(response):
            return None
        decision = _supervisor_decision_from_response(response)
        selected = WORKFLOWS.get(decision.workflow_id) if decision is not None else None
        if selected is None:
            return None
    except Exception:  # noqa: BLE001 - framework failure must not block reviewed fallback
        return None
    return selected, GrowthAgentCopilotEvidence(
        execution_mode="agent_framework",
        trace_kind="agent_framework",
        planner_label="Databricks Supervisor Agent",
        interpreted_intent=(
            f"Databricks Supervisor Agent selected reviewed workflow: {selected.title}."
        ),
        reasoning_summary=(
            f"Databricks Supervisor Agent selected the reviewed {selected.title} workflow "
            "from an allowlist. Deterministic tools produced counts, filters, audit, "
            "and the human-review handoff; no model SQL, DML, outreach, or raw "
            "identity data was executed."
        ),
        question_hash=_prompt_hash(payload.prompt),
        trusted_assets=(
            f"databricks.serving_endpoint.{endpoint}",
            f"databricks.supervisor_agent.{supervisor_id}",
        ),
        thoughts=(
            f"supervisor_workflow_id={selected.id}",
            f"deterministic_candidate={deterministic_workflow.id}",
            f"deterministic_intent_hash={_text_hash(deterministic_intent)}",
        ),
    )


def _supervisor_prompt(
    payload: GrowthAgentPromptRunRequest,
    *,
    deterministic_workflow: GrowthAgentWorkflowDef,
    interpreted_intent: str,
) -> str:
    state_scope = ", ".join(payload.states) if payload.states else "current configured coverage"
    segment_scope = (
        f"{payload.segment_mode.upper()} over {', '.join(payload.segment_codes)}"
        if payload.segment_codes
        else "reviewed workflow defaults"
    )
    prompt_hash = _prompt_hash(payload.prompt)
    workflow_rows = "\n".join(
        f"- {workflow.id}: {workflow.title}. {workflow.objective}"
        for workflow in WORKFLOWS.values()
    )
    return (
        "You are the Mortgage Growth Agent Supervisor for Module 0 lead generation. "
        "The user objective has already passed app-side PII, protected-class, raw "
        "identifier, and SQL-injection validation. You may choose exactly one "
        "reviewed workflow id below. Do not write SQL, do not name borrowers, do "
        "not expose identities, and do not activate outreach. "
        f"Objective hash: {prompt_hash}. "
        f"Reviewed objective signals: {_objective_signal_summary(payload)}. "
        f"Deterministic fallback candidate: {deterministic_workflow.title} ({deterministic_workflow.id}). "
        f"Deterministic interpreted intent: {interpreted_intent}. "
        f"State scope: {state_scope}. "
        f"Segment scope: {segment_scope}. "
        "Reviewed workflow allowlist:\n"
        f"{workflow_rows}\n"
        "Return JSON only with this shape: "
        '{"workflow_id":"daily_refi_brief","confidence":0.0,"reason":"brief non-PII reason"}. '
        "The workflow_id must be one of the reviewed ids exactly."
    )


def _objective_signal_summary(payload: GrowthAgentPromptRunRequest) -> str:
    q = payload.prompt.lower()
    signals: list[str] = []
    signal_terms: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("refinance economics", ("refi", "refinance", "rate spread", "economic incentive", "prime")),
        ("listed purchase", ("listed", "listing", "for sale", "purchase")),
        ("HELOC or high equity", ("heloc", "home equity", "equity line", "cash out", "cash-out")),
        ("investor or owner link", ("investor", "multi property", "multi-property", "owner link")),
        ("retention or recapture", ("retention", "recapture", "current customer", "competitor")),
        ("borrower dossier", ("dossier", "borrower story", "customer 360", "borrower 360")),
        ("branch capacity", ("capacity", "aging", "stale approved", "loan officer")),
        ("source freshness", ("source", "fresh", "readiness", "stale data", "data ops", "refresh")),
        ("custom segment workflow", ("custom", "cohort", "segment", "segments", "both", "intersection")),
    )
    for label, terms in signal_terms:
        if any(term in q for term in terms):
            signals.append(label)
    if payload.segment_codes:
        signals.append(f"explicit segment codes: {', '.join(payload.segment_codes)}")
        signals.append(f"explicit segment mode: {payload.segment_mode}")
    return "; ".join(signals[:10]) if signals else "default mortgage growth objective"


@dataclass(frozen=True)
class _SupervisorDecision:
    workflow_id: GrowthAgentWorkflowId


def _supervisor_decision_from_response(response: Any) -> _SupervisorDecision | None:
    text = _extract_response_text(response).strip()
    if not text:
        return None
    parsed = _parse_json_object(text)
    if parsed is None:
        return None
    workflow_id = str(parsed.get("workflow_id") or "").strip()
    if workflow_id not in WORKFLOWS:
        return None
    return _SupervisorDecision(workflow_id=workflow_id)


def _extract_response_text(value: Any) -> str:
    for method in ("as_dict", "to_dict"):
        converter = getattr(value, method, None)
        if callable(converter):
            try:
                value = converter()
                break
            except Exception:  # noqa: BLE001 - fall through to generic extraction
                pass
    found: list[str] = []

    def visit(node: Any) -> None:
        if len(found) >= 16:
            return
        if isinstance(node, str):
            if node.strip():
                found.append(node.strip())
            return
        if isinstance(node, dict):
            for key in ("text", "content", "output_text", "message", "response"):
                if key in node:
                    visit(node[key])
            for key in ("output", "outputs", "choices", "messages", "predictions"):
                if key in node:
                    visit(node[key])
            return
        if isinstance(node, list | tuple):
            for item in node:
                visit(item)
            return
        content = getattr(node, "content", None)
        if content is not None:
            visit(content)
        output = getattr(node, "output", None)
        if output is not None:
            visit(output)

    visit(value)
    return "\n".join(found)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = "\n".join(line for line in candidate.splitlines() if not line.strip().startswith("```")).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


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
