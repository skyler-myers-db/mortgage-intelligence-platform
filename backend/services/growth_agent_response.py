"""Growth Agent response assembly and signed Lead Queue route handoffs."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException

from backend.schemas.growth_agent import (
    GrowthAgentGovernanceChip,
    GrowthAgentMonitor,
    GrowthAgentPolicyCheck,
    GrowthAgentRunResponse,
    GrowthAgentToolStep,
)
from backend.services.growth_agent_monitors import monitor_from_row
from backend.services.growth_agent_row_parsing import (
    json_list,
    json_object,
    maybe_float,
    maybe_int,
    maybe_str,
    source_assets,
    str_list,
)
from backend.services.growth_agent_workflows import GrowthAgentWorkflowDef
from backend.services.repositories.databricks_lead_cohorts import (
    issue_growth_agent_handoff,
    normalise_growth_agent_handoff_filters,
)


def run_response_from_row(
    *,
    workflow: GrowthAgentWorkflowDef,
    run_row: dict[str, Any],
    monitor_row: dict[str, Any] | None,
    actor: str,
    monitor: GrowthAgentMonitor | None = None,
    interpreted_intent: str | None = None,
) -> GrowthAgentRunResponse:
    criteria = json_object(run_row.get("criteria"))
    tool_steps = [
        GrowthAgentToolStep(**item)
        for item in json_list(run_row.get("tool_steps"))
        if isinstance(item, dict)
    ]
    policy_checks = [
        GrowthAgentPolicyCheck(**item)
        for item in json_list(run_row.get("policy_checks"))
        if isinstance(item, dict)
    ]
    governance_chips = [
        GrowthAgentGovernanceChip(**item)
        for item in json_list(run_row.get("governance_chips"))
        if isinstance(item, dict)
    ]
    agent_evidence = json_object(run_row.get("agent_evidence"))
    if monitor is None:
        monitor = monitor_from_row(monitor_row) if monitor_row is not None else None
    route = str(run_row["route"])
    actionable_cohort_fingerprint = maybe_str(
        agent_evidence.get("actionable_cohort_fingerprint")
    )
    actionable_snapshot_id = maybe_str(agent_evidence.get("actionable_snapshot_id"))
    if urlsplit(route).path == "/lead-queue":
        if not actionable_cohort_fingerprint or not actionable_snapshot_id:
            raise HTTPException(
                status_code=503,
                detail="Growth Agent handoff proof is unavailable",
            )
        try:
            normalized_filters = normalise_growth_agent_handoff_filters(criteria)
            handoff_token = issue_growth_agent_handoff(
                actor=actor,
                run_id=str(run_row["run_id"]),
                normalized_filters=normalized_filters,
                cohort_fingerprint=actionable_cohort_fingerprint,
                total=int(run_row.get("actionable_total") or 0),
                source_snapshot=actionable_snapshot_id,
                tool_result_hash=str(run_row.get("tool_result_hash") or ""),
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="Growth Agent handoff signing is unavailable",
            ) from exc
        route = _route_with_growth_handoff(route, handoff_token)
        if monitor is not None:
            monitor = monitor.model_copy(update={"route": route})
    return GrowthAgentRunResponse(
        workflow=workflow.schema(),
        run_id=str(run_row["run_id"]),
        monitor=monitor,
        specialist_agent=run_row.get("specialist_agent") or workflow.specialist_agent,
        execution_mode=str(agent_evidence.get("execution_mode") or "deterministic"),  # type: ignore[arg-type]
        trace_kind=str(agent_evidence.get("trace_kind") or "local_hash"),  # type: ignore[arg-type]
        planner_label=str(agent_evidence.get("planner_label") or "Reviewed deterministic planner"),
        trace_id=str(run_row.get("trace_id") or ""),
        tool_result_hash=str(run_row.get("tool_result_hash") or ""),
        actionable_cohort_fingerprint=actionable_cohort_fingerprint,
        actionable_snapshot_id=actionable_snapshot_id,
        broad_label=workflow.broad_label,
        actionable_label=workflow.actionable_label,
        broad_total=int(run_row.get("broad_total") or 0),
        actionable_total=int(run_row.get("actionable_total") or 0),
        broad_avg_score=maybe_float(run_row.get("broad_avg_score")),
        actionable_avg_score=maybe_float(run_row.get("actionable_avg_score")),
        avg_rate_spread_bps=maybe_float(run_row.get("avg_rate_spread_bps")),
        avg_equity_pct=maybe_float(run_row.get("avg_equity_pct")),
        route=route,
        criteria=criteria,
        source_assets=source_assets(run_row),
        tool_steps=tool_steps,
        policy_checks=policy_checks,
        governance_chips=governance_chips,
        interpreted_intent=interpreted_intent or maybe_str(agent_evidence.get("interpreted_intent")),
        agent_reasoning=maybe_str(agent_evidence.get("reasoning_summary")),
        genie_conversation_id=maybe_str(agent_evidence.get("conversation_id")),
        genie_message_id=maybe_str(agent_evidence.get("message_id")),
        genie_question_hash=maybe_str(agent_evidence.get("question_hash")),
        genie_sql_hash=maybe_str(agent_evidence.get("sql_hash")),
        genie_row_count=maybe_int(agent_evidence.get("row_count")),
        genie_trusted_assets=str_list(agent_evidence.get("trusted_assets")),
        audit_event_id=str(run_row["audit_event_id"]) if run_row.get("audit_event_id") else None,
        created_at=run_row.get("created_at"),
    )


def _route_with_growth_handoff(route: str, token: str) -> str:
    parts = urlsplit(route)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "growth_handoff"
    ]
    query.append(("growth_handoff", token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
