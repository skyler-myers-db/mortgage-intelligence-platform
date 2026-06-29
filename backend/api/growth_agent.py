"""Mortgage Growth Agent workflows.

These endpoints make agentic automation visible without changing the
approval posture: the agent reads governed Unity Catalog assets, records an
audited Lakebase run/monitor, and deep-links to the eligible Lead Queue subset
for human review. It never sends outreach or activates a connector.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from backend.agents.mortgage_growth_copilot import (
    GrowthAgentCopilotEvidence,
    plan_growth_agent_prompt,
)
from backend.schemas.growth_agent import (
    GrowthAgentCustomRunRequest,
    GrowthAgentGovernanceChip,
    GrowthAgentHomeResponse,
    GrowthAgentMonitor,
    GrowthAgentPolicyCheck,
    GrowthAgentPromptRunRequest,
    GrowthAgentRunRequest,
    GrowthAgentRunResponse,
    GrowthAgentToolStep,
    GrowthAgentWorkflowId,
)
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import resolve_actor
from backend.services.capabilities import probe_capabilities
from backend.services.capability_request import collect_request_live_capability_statuses
from backend.services.databricks_sql import DatabricksSqlClient, get_sql_client
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import (
    MONITOR_LIST_SQL as _MONITOR_LIST_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    MONITOR_REFRESH_BY_ID_SQL as _MONITOR_REFRESH_BY_ID_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    MONITOR_SELECT_BY_ID_SQL as _MONITOR_SELECT_BY_ID_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    MONITOR_SELECT_BY_RUN_ID_SQL as _MONITOR_SELECT_BY_RUN_ID_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    MONITOR_UPSERT_SQL as _MONITOR_UPSERT_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    RUN_ATTACH_AUDIT_SQL as _RUN_ATTACH_AUDIT_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    RUN_INSERT_SQL as _RUN_INSERT_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    RUN_SELECT_BY_REQUEST_ID_SQL as _RUN_SELECT_BY_REQUEST_ID_SQL,
)
from backend.services.growth_agent_metrics import load_growth_agent_metrics
from backend.services.growth_agent_runtime import (
    criteria_for as _criteria_for,
)
from backend.services.growth_agent_runtime import (
    default_copilot_evidence as _default_copilot_evidence,
)
from backend.services.growth_agent_runtime import (
    governance_chips as _governance_chips,
)
from backend.services.growth_agent_runtime import (
    policy_checks as _policy_checks,
)
from backend.services.growth_agent_runtime import (
    tool_result_hash as _tool_result_hash,
)
from backend.services.growth_agent_runtime import (
    tool_steps as _tool_steps,
)
from backend.services.growth_agent_workflows import (
    WORKFLOWS as _WORKFLOWS,
)
from backend.services.growth_agent_workflows import (
    GrowthAgentWorkflowDef as _WorkflowDef,
)
from backend.services.growth_agent_workflows import (
    build_growth_agent_route as _route,
)
from backend.services.growth_agent_workflows import (
    custom_workflow as _custom_workflow,
)
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client

router = APIRouter(prefix="/growth-agent", tags=["growth-agent"])

SqlDep = Annotated[DatabricksSqlClient, Depends(get_sql_client)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


@router.get("", response_model=GrowthAgentHomeResponse)
def growth_agent_home(
    request: Request,
    lakebase: LakebaseDep,
    live_capabilities: bool = False,
) -> GrowthAgentHomeResponse:
    actor = resolve_actor(request)
    live_statuses = (
        collect_request_live_capability_statuses(request)
        if live_capabilities
        else None
    )
    return GrowthAgentHomeResponse(
        workflows=[workflow.schema() for workflow in _WORKFLOWS.values()],
        monitors=_list_monitors(lakebase, actor=actor),
        capabilities=[cap.to_dict() for cap in probe_capabilities(live_statuses=live_statuses)],
    )


@router.get("/monitors", response_model=list[GrowthAgentMonitor])
def growth_agent_monitors(request: Request, lakebase: LakebaseDep) -> list[GrowthAgentMonitor]:
    actor = resolve_actor(request)
    return _list_monitors(lakebase, actor=actor)


@router.post(
    "/monitors/{monitor_id}/run",
    response_model=GrowthAgentRunResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def rerun_growth_agent_monitor(
    monitor_id: UUID,
    payload: GrowthAgentRunRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
) -> GrowthAgentRunResponse:
    """Re-run a saved, reviewed watchlist on demand.

    This is the low-cost automation path: the user explicitly asks for a
    fresh run, the stored criteria are replayed, and the monitor's counts are
    refreshed. It does not create a scheduler, send outreach, or execute a raw
    natural-language prompt.
    """

    actor = resolve_actor(request)
    try:
        with lakebase.transaction() as conn:
            monitor_row = _txn_fetchone(
                conn,
                _MONITOR_SELECT_BY_ID_SQL,
                {"actor_email": actor, "monitor_id": str(monitor_id)},
            )
    except (LakebaseError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if monitor_row is None:
        raise HTTPException(status_code=404, detail="growth-agent monitor not found")
    workflow = _workflow_from_monitor(monitor_row)
    states = _states_from_monitor_criteria(monitor_row.get("criteria"))
    monitor_name = _stored_monitor_name(monitor_row, workflow=workflow)
    return _run_workflow(
        workflow=workflow,
        payload=GrowthAgentRunRequest(
            states=states,
            save_monitor=True,
            cadence=monitor_row["cadence"],
            request_id=payload.request_id,
        ),
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
        monitor_id_override=str(monitor_id),
        monitor_name_override=monitor_name,
        interpreted_intent=f"Saved watchlist re-run: {monitor_name}.",
        copilot_evidence=GrowthAgentCopilotEvidence(
            execution_mode="deterministic",
            trace_kind="local_hash",
            planner_label="Saved watchlist runner",
            interpreted_intent=f"Saved watchlist re-run: {monitor_name}.",
            reasoning_summary=(
                "The user re-ran a saved reviewed watchlist. Stored filters were "
                "replayed; no raw prompt, scheduler, outreach, or connector activation executed."
            ),
            fallback_reason="saved_monitor_rerun",
        ),
    )


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=GrowthAgentRunResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def run_growth_agent_workflow(
    workflow_id: GrowthAgentWorkflowId,
    payload: GrowthAgentRunRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
) -> GrowthAgentRunResponse:
    workflow = _WORKFLOWS.get(workflow_id)
    if workflow is None:  # Defensive; path type normally handles this.
        raise HTTPException(status_code=404, detail="unknown growth-agent workflow")
    return _run_workflow(
        workflow=workflow,
        payload=payload,
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
    )


@router.post("/custom/run", response_model=GrowthAgentRunResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def run_custom_growth_agent_workflow(
    payload: GrowthAgentCustomRunRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
) -> GrowthAgentRunResponse:
    workflow = _custom_workflow(payload.segment_codes, payload.segment_mode)
    return _run_workflow(
        workflow=workflow,
        payload=payload,
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
    )


@router.post("/agent/run", response_model=GrowthAgentRunResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def run_mortgage_growth_agent(
    payload: GrowthAgentPromptRunRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
) -> GrowthAgentRunResponse:
    """Route a natural-language co-pilot request to reviewed workflows.

    The current runtime uses reviewed deterministic routing only. It does not
    call the normal Genie answer path for prompt planning because that path can
    compile and execute SQL. The deterministic executor writes the Lakebase
    audit row and returns a reconciled Lead Queue/Admin handoff.
    """

    workflow, copilot_evidence = plan_growth_agent_prompt(payload)
    response = _run_workflow(
        workflow=workflow,
        payload=payload,
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
        interpreted_intent=copilot_evidence.interpreted_intent,
        copilot_evidence=copilot_evidence,
    )
    return response


def _run_workflow(
    *,
    workflow: _WorkflowDef,
    payload: GrowthAgentRunRequest,
    request: Request,
    sql_client: DatabricksSqlClient,
    lakebase: LakebaseClient,
    monitor_id_override: str | None = None,
    monitor_name_override: str | None = None,
    interpreted_intent: str | None = None,
    copilot_evidence: GrowthAgentCopilotEvidence | None = None,
) -> GrowthAgentRunResponse:
    actor = resolve_actor(request)
    copilot_evidence = copilot_evidence or _default_copilot_evidence(workflow)
    effective_states = [] if workflow.id == "source_freshness_sentinel" else payload.states
    criteria = _criteria_for(workflow, effective_states)
    route = _route(
        {**workflow.route_filters, **({"states": ",".join(effective_states)} if effective_states else {})},
        path=workflow.route_path,
    )
    request_id = payload.request_id or str(uuid4())
    if payload.request_id is not None:
        try:
            with lakebase.transaction() as conn:
                existing_row = _txn_fetchone(
                    conn,
                    _RUN_SELECT_BY_REQUEST_ID_SQL,
                    {"actor_email": actor, "request_id": request_id},
                )
                if existing_row is not None:
                    _assert_run_matches(existing_row, workflow=workflow, criteria=criteria)
                    replay_monitor_row = _txn_fetchone(
                        conn,
                        _MONITOR_SELECT_BY_RUN_ID_SQL,
                        {
                            "actor_email": actor,
                            "last_run_id": existing_row["run_id"],
                        },
                    )
                    return _run_response_from_row(
                        workflow=workflow,
                        run_row=existing_row,
                        monitor_row=replay_monitor_row,
                    )
        except HTTPException:
            raise
        except (LakebaseError, psycopg.Error) as exc:
            raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc

    metrics = load_growth_agent_metrics(sql_client, workflow=workflow, states=effective_states)
    trace_id = f"agent-trace-{uuid4()}"
    tool_result_hash = _tool_result_hash(workflow=workflow, metrics=metrics, criteria=criteria, route=route)
    tool_steps = _tool_steps(
        workflow,
        metrics,
        tool_result_hash=tool_result_hash,
        copilot_evidence=copilot_evidence,
    )
    policy_checks = _policy_checks(workflow, metrics, saved_monitor=payload.save_monitor)
    governance_chips = _governance_chips(
        workflow,
        metrics,
        policy_checks=policy_checks,
        trace_id=trace_id,
        audit_event_id=None,
        copilot_evidence=copilot_evidence,
    )
    source_assets = list(workflow.source_assets)
    try:
        with lakebase.transaction() as conn:
            run_row = _txn_fetchone(
                conn,
                _RUN_INSERT_SQL,
                {
                    "actor_email": actor,
                    "request_id": request_id,
                    "workflow_id": workflow.id,
                    "workflow_title": workflow.title,
                    "criteria": json.dumps(criteria),
                    "broad_total": metrics["broad_total"],
                    "actionable_total": metrics["actionable_total"],
                    "broad_avg_score": metrics.get("broad_avg_score"),
                    "actionable_avg_score": metrics.get("actionable_avg_score"),
                    "avg_rate_spread_bps": metrics.get("avg_rate_spread_bps"),
                    "avg_equity_pct": metrics.get("avg_equity_pct"),
                    "route": route,
                    "source_assets": source_assets,
                    "tool_steps": json.dumps([step.model_dump() for step in tool_steps]),
                    "policy_checks": json.dumps([check.model_dump() for check in policy_checks]),
                    "trace_id": trace_id,
                    "tool_result_hash": tool_result_hash,
                    "specialist_agent": workflow.specialist_agent,
                    "agent_evidence": json.dumps(copilot_evidence.criteria_json()),
                    "governance_chips": json.dumps([chip.model_dump() for chip in governance_chips]),
                },
            )
            if run_row is None:
                if payload.request_id is None:
                    raise RuntimeError("growth-agent run insert returned no row")
                existing_row = _txn_fetchone(
                    conn,
                    _RUN_SELECT_BY_REQUEST_ID_SQL,
                    {"actor_email": actor, "request_id": request_id},
                )
                if existing_row is None:
                    raise RuntimeError("growth-agent run conflict returned no row")
                _assert_run_matches(existing_row, workflow=workflow, criteria=criteria)
                replay_monitor_row = _txn_fetchone(
                    conn,
                    _MONITOR_SELECT_BY_RUN_ID_SQL,
                    {
                        "actor_email": actor,
                        "last_run_id": existing_row["run_id"],
                    },
                )
                return _run_response_from_row(
                    workflow=workflow,
                    run_row=existing_row,
                    monitor_row=replay_monitor_row,
                )
            _assert_run_matches(run_row, workflow=workflow, criteria=criteria)
            if run_row.get("audit_event_id") is None:
                audit_event = write_audit_event_in_transaction(
                    conn,
                    actor=actor,
                    action="growth_agent.run",
                    entity_type="growth_agent_workflow",
                    entity_id=workflow.id,
                    payload_json={
                        "workflow_id": workflow.id,
                        "workflow_title": workflow.title,
                        "run_status": "completed",
                        "broad_total": metrics["broad_total"],
                        "actionable_total": metrics["actionable_total"],
                        "route": route,
                        "result_filters": criteria["lead_queue_filters"],
                        "source_assets": source_assets,
                        "trace_id": trace_id,
                        "tool_result_hash": tool_result_hash,
                        "specialist_agent": workflow.specialist_agent,
                        "tool_steps": [step.model_dump() for step in tool_steps],
                        "policy_checks": [check.model_dump() for check in policy_checks],
                        "governance_chips": [chip.model_dump() for chip in governance_chips],
                    },
                    event_type="GROWTH_AGENT_RUN",
                    request_id=request_id,
                )
                run_row = _txn_fetchone(
                    conn,
                    _RUN_ATTACH_AUDIT_SQL,
                    {
                        "run_id": run_row["run_id"],
                        "audit_event_id": audit_event.event_id,
                    },
                )
                if run_row is None:
                    raise RuntimeError("growth-agent run audit attach returned no row")
            monitor_row: dict[str, Any] | None = None
            if payload.save_monitor:
                monitor_row = _upsert_monitor(
                    conn,
                    actor=actor,
                    workflow=workflow,
                    payload=payload,
                    criteria=criteria,
                    run_row=run_row,
                    monitor_id_override=monitor_id_override,
                    monitor_name_override=monitor_name_override,
                )
                if monitor_id_override and monitor_row is None:
                    raise HTTPException(status_code=409, detail="saved monitor could not be refreshed")
        monitor = _monitor_from_row(monitor_row) if monitor_row is not None else None
    except HTTPException:
        raise
    except (LakebaseError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return _run_response_from_row(
        workflow=workflow,
        run_row=run_row,
        monitor_row=monitor_row,
        monitor=monitor,
        interpreted_intent=interpreted_intent,
    )


def _upsert_monitor(
    conn: Any,
    *,
    actor: str,
    workflow: _WorkflowDef,
    payload: GrowthAgentRunRequest,
    criteria: dict[str, object],
    run_row: dict[str, Any],
    monitor_id_override: str | None = None,
    monitor_name_override: str | None = None,
) -> dict[str, Any] | None:
    statement = _MONITOR_REFRESH_BY_ID_SQL if monitor_id_override else _MONITOR_UPSERT_SQL
    return _txn_fetchone(
        conn,
        statement,
        {
            "actor_email": actor,
            "monitor_id": monitor_id_override,
            "workflow_id": workflow.id,
            "name": monitor_name_override or payload.monitor_name or workflow.title,
            "cadence": payload.cadence,
            "criteria": json.dumps(criteria),
            "route": str(run_row["route"]),
            "actionable_total": int(run_row.get("actionable_total") or 0),
            "source_assets": _source_assets_from_row(run_row),
            "last_run_id": run_row["run_id"],
        },
    )


def _assert_run_matches(
    run_row: dict[str, Any],
    *,
    workflow: _WorkflowDef,
    criteria: dict[str, object],
) -> None:
    if run_row.get("workflow_id") != workflow.id or not _json_equivalent(
        run_row.get("criteria"),
        criteria,
    ):
        raise HTTPException(
            status_code=409,
            detail="request_id already belongs to a different growth-agent run",
        )


def _run_response_from_row(
    *,
    workflow: _WorkflowDef,
    run_row: dict[str, Any],
    monitor_row: dict[str, Any] | None,
    monitor: GrowthAgentMonitor | None = None,
    interpreted_intent: str | None = None,
) -> GrowthAgentRunResponse:
    criteria = _json_object(run_row.get("criteria"))
    tool_steps = [
        GrowthAgentToolStep(**item)
        for item in _json_list(run_row.get("tool_steps"))
        if isinstance(item, dict)
    ]
    policy_checks = [
        GrowthAgentPolicyCheck(**item)
        for item in _json_list(run_row.get("policy_checks"))
        if isinstance(item, dict)
    ]
    governance_chips = [
        GrowthAgentGovernanceChip(**item)
        for item in _json_list(run_row.get("governance_chips"))
        if isinstance(item, dict)
    ]
    agent_evidence = _json_object(run_row.get("agent_evidence"))
    if monitor is None:
        monitor = _monitor_from_row(monitor_row) if monitor_row is not None else None
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
        broad_label=workflow.broad_label,
        actionable_label=workflow.actionable_label,
        broad_total=int(run_row.get("broad_total") or 0),
        actionable_total=int(run_row.get("actionable_total") or 0),
        broad_avg_score=_maybe_float(run_row.get("broad_avg_score")),
        actionable_avg_score=_maybe_float(run_row.get("actionable_avg_score")),
        avg_rate_spread_bps=_maybe_float(run_row.get("avg_rate_spread_bps")),
        avg_equity_pct=_maybe_float(run_row.get("avg_equity_pct")),
        route=str(run_row["route"]),
        criteria=criteria,
        source_assets=_source_assets_from_row(run_row),
        tool_steps=tool_steps,
        policy_checks=policy_checks,
        governance_chips=governance_chips,
        interpreted_intent=interpreted_intent or _maybe_str(agent_evidence.get("interpreted_intent")),
        agent_reasoning=_maybe_str(agent_evidence.get("reasoning_summary")),
        genie_conversation_id=_maybe_str(agent_evidence.get("conversation_id")),
        genie_message_id=_maybe_str(agent_evidence.get("message_id")),
        genie_question_hash=_maybe_str(agent_evidence.get("question_hash")),
        genie_sql_hash=_maybe_str(agent_evidence.get("sql_hash")),
        genie_row_count=_maybe_int(agent_evidence.get("row_count")),
        genie_trusted_assets=_str_list(agent_evidence.get("trusted_assets")),
        audit_event_id=str(run_row["audit_event_id"]) if run_row.get("audit_event_id") else None,
        created_at=run_row.get("created_at"),
    )


def _list_monitors(lakebase: LakebaseClient, *, actor: str) -> list[GrowthAgentMonitor]:
    try:
        rows = lakebase.fetchall(_MONITOR_LIST_SQL, {"actor_email": actor, "limit": 20}, limit=20)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return [_monitor_from_row(row) for row in rows]


def _workflow_from_monitor(row: dict[str, Any]) -> _WorkflowDef:
    workflow_id = str(row.get("workflow_id") or "")
    if workflow_id == "custom_segment_watch":
        criteria = _json_object(row.get("criteria"))
        lead_filters = criteria.get("lead_queue_filters")
        if not isinstance(lead_filters, dict):
            raise HTTPException(status_code=409, detail="saved monitor has malformed criteria")
        segment_codes = lead_filters.get("segment_codes")
        if not isinstance(segment_codes, list):
            raise HTTPException(status_code=409, detail="saved monitor has malformed segment criteria")
        segment_mode = str(lead_filters.get("segment_mode") or "any")
        return _custom_workflow([str(code) for code in segment_codes], segment_mode)
    workflow = _WORKFLOWS.get(cast(GrowthAgentWorkflowId, workflow_id))
    if workflow is None:
        raise HTTPException(status_code=409, detail="saved monitor references an unknown workflow")
    return workflow


def _states_from_monitor_criteria(criteria_value: Any) -> list[str]:
    criteria = _json_object(criteria_value)
    states = criteria.get("states")
    if not isinstance(states, list):
        return []
    return [str(state).strip().upper() for state in states if str(state).strip()]


def _stored_monitor_name(row: dict[str, Any], *, workflow: _WorkflowDef) -> str:
    name = str(row.get("name") or "").strip()
    if not name:
        return workflow.title
    try:
        request = GrowthAgentRunRequest(monitor_name=name)
    except ValidationError:
        return workflow.title
    return request.monitor_name or workflow.title


def _monitor_fallback_name(row: dict[str, Any]) -> str:
    workflow_id = str(row.get("workflow_id") or "")
    workflow = _WORKFLOWS.get(cast(GrowthAgentWorkflowId, workflow_id))
    if workflow is not None:
        return workflow.title
    if workflow_id == "custom_segment_watch":
        return "Custom Segment Workflow"
    return "Reviewed Growth Watchlist"


def _safe_monitor_name_from_row(row: dict[str, Any]) -> str:
    fallback = _monitor_fallback_name(row)
    name = str(row.get("name") or "").strip()
    if not name:
        return fallback
    try:
        request = GrowthAgentRunRequest(monitor_name=name)
    except ValidationError:
        return fallback
    return request.monitor_name or fallback


def _monitor_from_row(row: dict[str, Any]) -> GrowthAgentMonitor:
    criteria = row.get("criteria") or {}
    if isinstance(criteria, str):
        try:
            criteria = json.loads(criteria)
        except json.JSONDecodeError:
            criteria = {}
    return GrowthAgentMonitor(
        monitor_id=str(row["monitor_id"]),
        workflow_id=row["workflow_id"],
        name=_safe_monitor_name_from_row(row),
        cadence=row["cadence"],
        status=row.get("status") or "active",
        criteria=criteria,
        route=row["route"],
        actionable_total=int(row.get("actionable_total") or 0),
        source_assets=list(row.get("source_assets") or []),
        last_run_id=str(row["last_run_id"]) if row.get("last_run_id") else None,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _txn_fetchone(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    execute = getattr(conn, "execute", None)
    if callable(execute):
        row = execute(sql, params).fetchone()
        return dict(row) if row is not None else None
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None


def _json_equivalent(value: Any, expected: dict[str, object]) -> bool:
    return _json_object(value) == expected


def _json_object(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _source_assets_from_row(row: dict[str, Any]) -> list[str]:
    value = row.get("source_assets") or []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return []


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
