"""Plan composer for the Mortgage Growth Agent co-pilot.

This is the composition upgrade over workflow *selection*: given a reviewed
natural-language objective, ask the configured Supervisor serving endpoint to
compose a multi-step plan whose every step names a tool from the governed
deterministic registry. The model proposes structure only; this module validates
hard before any execution:

  * unknown tool  -> ``invalid`` (an honest error surface, never a canned plan);
  * bad params    -> ``invalid`` (params validated against each tool's spec);
  * step count    -> capped at ``MAX_PLAN_STEPS``;
  * free text     -> scrubbed via ``scrub_free_text`` and length-clamped;
  * approval      -> ``requires_approval`` recomputed server-side from the
                     registry (any state-writing / handoff tool forces True).

When the Supervisor host is unavailable the composer returns a ``degraded``
outcome so the API can offer the reviewed catalog workflows as a *labelled*
fallback — it never silently substitutes a fabricated plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from backend.agents.mortgage_growth_copilot import (
    extract_response_text,
    parse_json_object,
    prompt_hash,
)
from backend.agents.mortgage_growth_copilot import (
    workspace_client as make_workspace_client,
)
from backend.config.settings import Settings, get_settings
from backend.schemas.agent_plan import (
    MAX_PLAN_STEPS,
    MAX_RATIONALE_LEN,
    ComposedPlan,
    ComposePlanRequest,
    PlanStep,
)
from backend.services.agent_tools import (
    AgentToolParamError,
    get_agent_tool,
    registered_agent_tool_names,
    tool_catalog_for_planner,
)
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.pii_redaction import scrub_free_text
from backend.services.supervisor_runtime import verify_supervisor_runtime

ComposeStatus = Literal["composed", "degraded", "invalid"]


@dataclass(frozen=True)
class ComposeOutcome:
    """Honest, discriminated result of a composition attempt."""

    status: ComposeStatus
    endpoint: str | None = None
    plan: ComposedPlan | None = None
    interpreted_intent: str | None = None
    reasoning_summary: str | None = None
    degraded_reason: str | None = None
    message: str | None = None


def composition_endpoint(settings: Settings) -> tuple[str | None, str | None]:
    """Return (endpoint, unavailable_reason).

    Availability is tied to the same gates as the ``agent_orchestrator``
    capability: the orchestrator flag must be on and both the Supervisor id and
    the serving endpoint must be configured. When any is missing the second
    element explains why composition is unavailable.
    """

    if not settings.mip_agent_orchestrator:
        return None, "orchestrator_disabled"
    endpoint = (settings.mip_agent_serving_endpoint or "").strip()
    supervisor_id = (settings.mip_agent_supervisor_id or "").strip()
    if not endpoint or not supervisor_id:
        return None, "orchestrator_not_configured"
    return endpoint, None


def compose_growth_agent_plan(
    payload: ComposePlanRequest,
    *,
    settings: Settings | None = None,
    serving_client: Any | None = None,
) -> ComposeOutcome:
    """Compose and validate a plan, or degrade/invalidate honestly."""

    settings = settings or get_settings()
    client = serving_client
    try:
        if client is None:
            client = make_workspace_client()
    except Exception:  # noqa: BLE001 - host flakiness must degrade, not crash
        return _degraded("orchestrator_client_failed")
    runtime, reason = verify_supervisor_runtime(client, settings)
    if runtime is None:
        endpoint = (settings.mip_agent_serving_endpoint or "").strip() or None
        return _degraded(reason or "orchestrator_unavailable", endpoint=endpoint)
    endpoint = runtime.endpoint
    task = runtime.task

    # One bounded repair turn, mirroring the Genie SQL-repair precedent: a
    # first invalid plan (hallucinated params, unknown tool, bad shape) is fed
    # back to the model verbatim with the validation error so it can
    # self-correct. Still-invalid after repair surfaces honestly as invalid —
    # never a silent canned fallback.
    repair_note: str | None = None
    outcome: ComposeOutcome | None = None
    for attempt in range(2):
        try:
            response = query_serving_endpoint(
                client,
                endpoint,
                task=task,
                prompt=composer_prompt(payload, repair_note=repair_note),
                client_request_id=(
                    f"mip-compose-{prompt_hash(payload.objective)[:18]}-{attempt}"
                ),
            )
        except Exception:  # noqa: BLE001 - a failed call degrades to the catalog
            return _degraded("orchestrator_call_failed", endpoint=endpoint)
        if not serving_response_has_payload(response):
            return _degraded("orchestrator_empty_response", endpoint=endpoint)

        text = extract_response_text(response)
        parsed = parse_json_object(text)
        if parsed is None:
            outcome = _invalid(
                "The planning model did not return a valid plan JSON object.",
                endpoint=endpoint,
            )
        else:
            outcome = build_validated_plan(parsed, payload, endpoint=endpoint)
        if outcome.status != "invalid":
            return outcome
        repair_note = outcome.message
    assert outcome is not None  # loop body always assigns; keeps mypy exact
    return outcome


def build_validated_plan(
    parsed: dict[str, Any],
    payload: ComposePlanRequest,
    *,
    endpoint: str | None,
) -> ComposeOutcome:
    """Validate a parsed model plan against the governed registry.

    Pure and side-effect-free so tests and the eval harness can score a raw
    model JSON blob without a live endpoint.
    """

    raw_steps = parsed.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return _invalid("The composed plan contained no steps.", endpoint=endpoint)
    if len(raw_steps) > MAX_PLAN_STEPS:
        return _invalid(
            f"The composed plan exceeded the {MAX_PLAN_STEPS}-step limit.",
            endpoint=endpoint,
        )

    known = registered_agent_tool_names()
    steps: list[PlanStep] = []
    gates_approval = False
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            return _invalid("A composed plan step was not an object.", endpoint=endpoint)
        tool_name = str(raw.get("tool") or "").strip()
        if tool_name not in known:
            return _invalid(
                f"The composed plan named an unregistered tool: {tool_name or '(empty)'}.",
                endpoint=endpoint,
            )
        tool = get_agent_tool(tool_name)
        if not tool.planner_exposed:
            return _invalid(
                f"{tool_name} is not available to the plan composer.",
                endpoint=endpoint,
            )
        try:
            params = tool.validate_params(raw.get("params"))
        except AgentToolParamError as exc:
            return _invalid(str(exc), endpoint=endpoint)
        gates_approval = gates_approval or tool.gates_run
        step_id = _clean_step_id(raw.get("step_id"), index)
        steps.append(
            PlanStep(
                step_id=step_id,
                tool=tool_name,
                params=params,
                rationale=_clamp(raw.get("rationale")),
            )
        )

    plan = ComposedPlan(
        objective_summary=_clamp(parsed.get("objective_summary")) or _fallback_summary(payload),
        steps=steps,
        expected_outcome=_clamp(parsed.get("expected_outcome")),
        risk_notes=_clamp(parsed.get("risk_notes")),
        requires_approval=gates_approval,
    )
    interpreted = (
        f"Supervisor composed a {len(steps)}-step plan across "
        f"{len({step.tool for step in steps})} governed tools."
    )
    reasoning = (
        "The plan was composed by the Supervisor serving endpoint and validated "
        "against the reviewed tool registry. Every step runs a deterministic tool; "
        "no model SQL, DML, outreach, or raw identity data is executed, and "
        "state-writing steps stop for human approval."
    )
    return ComposeOutcome(
        status="composed",
        endpoint=endpoint,
        plan=plan,
        interpreted_intent=interpreted,
        reasoning_summary=reasoning,
    )


def composer_prompt(payload: ComposePlanRequest, *, repair_note: str | None = None) -> str:
    """Build the strict-JSON planning prompt shown to the Supervisor model."""

    state_scope = ", ".join(payload.states) if payload.states else "current configured coverage"
    objective_hash = prompt_hash(payload.objective)
    repair_block = (
        "Your previous plan failed validation with this error: "
        f"{repair_note} — correct the plan. Params belong to the exact tool "
        "named in that step; never reuse another tool's params.\n\n"
        if repair_note
        else ""
    )
    return (
        "You are the Mortgage Intelligence Platform Growth Agent planner for Module 0 "
        "(top-of-funnel lead generation and borrower segmentation). Compose a specialized, "
        "multi-step plan that answers the operator's objective by chaining ONLY the reviewed "
        "deterministic tools below.\n\n"
        f"{repair_block}"
        "Hard rules:\n"
        "- Use ONLY tools from the registry; never invent a tool or write SQL.\n"
        "- Every step's params must match THAT step's tool param spec exactly; params from "
        "one tool are never valid on another (fn_build_cohort takes only `states`; "
        "segment filters belong to fn_segment_counts).\n"
        "- Do not name borrowers, expose identities, or activate outreach.\n"
        "- Tools marked REQUIRES HUMAN APPROVAL end the executable portion of the plan; "
        "place any such handoff step last.\n"
        f"- Use between 1 and {MAX_PLAN_STEPS} steps.\n\n"
        "Operator objective (already validated PII-free, protected-class-free, and "
        "injection-guarded by the request schema; compose the plan FOR this objective):\n"
        f"{scrub_free_text(payload.objective)[:500]}\n\n"
        f"Objective hash: {objective_hash}. State scope: {state_scope}.\n\n"
        "Reviewed tool registry:\n"
        f"{tool_catalog_for_planner()}\n\n"
        "Return STRICT JSON only, no prose, with exactly this shape:\n"
        '{"objective_summary":"<=300 chars",'
        '"steps":[{"step_id":"step-1","tool":"<registry tool name>",'
        '"params":{},"rationale":"<=300 chars, no PII"}],'
        '"expected_outcome":"<=300 chars","risk_notes":"<=300 chars"}'
    )


def _clean_step_id(value: Any, index: int) -> str:
    text = str(value or "").strip()
    if not text:
        return f"step-{index}"
    scrubbed = scrub_free_text(text)[:48].strip()
    return scrubbed or f"step-{index}"


def _clamp(value: Any) -> str:
    if value is None:
        return ""
    return scrub_free_text(str(value).strip())[:MAX_RATIONALE_LEN].strip()


def _fallback_summary(payload: ComposePlanRequest) -> str:
    scope = f" scoped to {', '.join(payload.states)}" if payload.states else ""
    return _clamp(f"Composed Module 0 growth plan{scope}.") or "Composed Module 0 growth plan."


def _degraded(reason: str, *, endpoint: str | None = None) -> ComposeOutcome:
    return ComposeOutcome(
        status="degraded",
        endpoint=endpoint,
        degraded_reason=reason,
        message=(
            "Plan composition is temporarily unavailable; the Supervisor planning model "
            "is not reachable. Reviewed catalog workflows remain available as a fallback."
        ),
    )


def _invalid(message: str, *, endpoint: str | None = None) -> ComposeOutcome:
    return ComposeOutcome(
        status="invalid",
        endpoint=endpoint,
        message=message,
    )


__all__ = [
    "ComposeOutcome",
    "ComposeStatus",
    "build_validated_plan",
    "compose_growth_agent_plan",
    "composer_prompt",
    "composition_endpoint",
]
