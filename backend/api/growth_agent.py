"""Mortgage Growth Agent workflows.

These endpoints make agentic automation visible without changing the
approval posture: the agent reads governed Unity Catalog assets, records an
audited Lakebase run/monitor, and deep-links to the eligible Lead Queue subset
for human review. It never sends outreach or activates a connector.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Annotated, Any
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.agents.mortgage_growth_copilot import (
    GrowthAgentCopilotEvidence,
    plan_growth_agent_prompt,
)
from backend.schemas.growth_agent import (
    GrowthAgentCustomRunRequest,
    GrowthAgentDueMonitorRunRequest,
    GrowthAgentDueMonitorRunResponse,
    GrowthAgentGovernanceChip,
    GrowthAgentHomeResponse,
    GrowthAgentMonitor,
    GrowthAgentMonitorDraftRequest,
    GrowthAgentNotificationDraft,
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
from backend.services.databricks_sql import DatabricksSqlClient, get_sql_client
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_drafts import create_notification_drafts
from backend.services.growth_agent_ledger_sql import (
    DUE_MONITOR_LIST_ALL_SQL as _DUE_MONITOR_LIST_ALL_SQL,
)
from backend.services.growth_agent_ledger_sql import (
    DUE_MONITOR_LIST_SQL as _DUE_MONITOR_LIST_SQL,
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
from backend.services.growth_agent_monitors import (
    list_monitors,
    monitor_from_row,
    states_from_monitor_criteria,
    stored_monitor_name,
    workflow_from_monitor,
)
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
from backend.services.rbac import require_admin

router = APIRouter(prefix="/growth-agent", tags=["growth-agent"])

SqlDep = Annotated[DatabricksSqlClient, Depends(get_sql_client)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


@router.get("", response_model=GrowthAgentHomeResponse)
def growth_agent_home(
    request: Request,
    lakebase: LakebaseDep,
) -> GrowthAgentHomeResponse:
    actor = resolve_actor(request)
    return GrowthAgentHomeResponse(
        workflows=[workflow.schema() for workflow in _WORKFLOWS.values()],
        monitors=list_monitors(lakebase, actor=actor),
        capabilities=[cap.to_dict() for cap in probe_capabilities()],
    )


@router.get("/monitors", response_model=list[GrowthAgentMonitor])
def growth_agent_monitors(request: Request, lakebase: LakebaseDep) -> list[GrowthAgentMonitor]:
    actor = resolve_actor(request)
    return list_monitors(lakebase, actor=actor)


@router.post(
    "/monitors/run-due",
    response_model=GrowthAgentDueMonitorRunResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def run_due_growth_agent_monitors(
    payload: GrowthAgentDueMonitorRunRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
) -> GrowthAgentDueMonitorRunResponse:
    """Run active saved watchlists whose cadence window has elapsed.

    This endpoint is scheduler-safe but not a sender: it replays reviewed saved
    filters, refreshes counts, and writes Slack/Teams review drafts only.
    """

    actor = resolve_actor(request)
    try:
        rows = lakebase.fetchall(
            _DUE_MONITOR_LIST_SQL,
            {"actor_email": actor, "limit": payload.limit},
            limit=payload.limit,
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    runs, drafts = _run_due_monitor_rows(
        rows,
        actor=actor,
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
        channels=payload.channels,
        request_id=payload.request_id,
    )
    return GrowthAgentDueMonitorRunResponse(
        runs=runs,
        drafts=drafts,
        due_count=len(rows),
        actor_count=1 if rows else 0,
    )


@router.post(
    "/monitors/run-due-all",
    response_model=GrowthAgentDueMonitorRunResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def run_due_growth_agent_monitors_all_actors(
    payload: GrowthAgentDueMonitorRunRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    _admin_actor: Annotated[str, Depends(require_admin)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
) -> GrowthAgentDueMonitorRunResponse:
    """Run all due saved watchlists for the scheduled Databricks job.

    This is the only all-actor runner. It is admin-gated, replays only stored
    reviewed monitor criteria, attributes each run/draft to the monitor owner,
    and creates review drafts only. It never sends outreach or connector
    notifications.
    """

    try:
        rows = lakebase.fetchall(
            _DUE_MONITOR_LIST_ALL_SQL,
            {"limit": payload.limit},
            limit=payload.limit,
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    actor_count = len({str(row.get("actor_email") or "").lower() for row in rows if row.get("actor_email")})
    runs, drafts = _run_due_monitor_rows(
        rows,
        actor=None,
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
        channels=payload.channels,
        request_id=payload.request_id,
    )
    return GrowthAgentDueMonitorRunResponse(
        runs=runs,
        drafts=drafts,
        due_count=len(rows),
        actor_count=actor_count,
    )


@router.post(
    "/monitors/{monitor_id}/notification-drafts",
    response_model=list[GrowthAgentNotificationDraft],
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def create_growth_agent_monitor_notification_drafts(
    monitor_id: UUID,
    payload: GrowthAgentMonitorDraftRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    lakebase: LakebaseDep,
) -> list[GrowthAgentNotificationDraft]:
    """Create Slack/Teams review drafts for a saved watchlist's latest run."""

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
    monitor = monitor_from_row(monitor_row)
    if not monitor.last_run_id:
        raise HTTPException(status_code=409, detail="saved monitor has not been run yet")
    return create_notification_drafts(
        lakebase,
        actor=actor,
        monitor=monitor,
        run_id=monitor.last_run_id,
        route=monitor.route,
        actionable_total=monitor.actionable_total,
        channels=payload.channels,
        request_id=payload.request_id,
    )


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
    return _run_monitor_row(
        monitor_row,
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
        request_id=payload.request_id,
        evidence_label="Saved watchlist re-run",
        planner_label="Saved watchlist runner",
        fallback_reason="saved_monitor_rerun",
    )


def _run_monitor_row(
    monitor_row: dict[str, Any],
    *,
    request: Request,
    sql_client: DatabricksSqlClient,
    lakebase: LakebaseClient,
    request_id: str | None = None,
    evidence_label: str,
    planner_label: str,
    fallback_reason: str,
    actor_override: str | None = None,
) -> GrowthAgentRunResponse:
    workflow = workflow_from_monitor(monitor_row)
    states = states_from_monitor_criteria(monitor_row.get("criteria"))
    monitor_name = stored_monitor_name(monitor_row, workflow=workflow)
    return _run_workflow(
        workflow=workflow,
        payload=GrowthAgentRunRequest(
            states=states,
            save_monitor=True,
            cadence=monitor_row["cadence"],
            request_id=request_id,
        ),
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
        monitor_id_override=str(monitor_row["monitor_id"]),
        monitor_name_override=monitor_name,
        actor_override=actor_override,
        interpreted_intent=f"{evidence_label}: {monitor_name}.",
        copilot_evidence=GrowthAgentCopilotEvidence(
            execution_mode="deterministic",
            trace_kind="local_hash",
            planner_label=planner_label,
            interpreted_intent=f"{evidence_label}: {monitor_name}.",
            reasoning_summary=(
                "A saved reviewed watchlist was replayed from stored filters. No raw prompt, outreach, "
                "or connector activation executed."
            ),
            fallback_reason=fallback_reason,
        ),
    )


def _run_due_monitor_rows(
    rows: list[dict[str, Any]],
    *,
    actor: str | None,
    request: Request,
    sql_client: DatabricksSqlClient,
    lakebase: LakebaseClient,
    channels: Sequence[str],
    request_id: str | None,
) -> tuple[list[GrowthAgentRunResponse], list[GrowthAgentNotificationDraft]]:
    runs: list[GrowthAgentRunResponse] = []
    drafts: list[GrowthAgentNotificationDraft] = []
    for row in rows:
        row_actor = actor or str(row.get("actor_email") or "").strip().lower()
        if not row_actor:
            raise HTTPException(status_code=409, detail="saved monitor is missing an owner")
        run = _run_monitor_row(
            row,
            request=request,
            sql_client=sql_client,
            lakebase=lakebase,
            evidence_label="Scheduled watchlist run",
            planner_label="Scheduled watchlist runner",
            fallback_reason="scheduled_monitor_run",
            actor_override=row_actor,
        )
        runs.append(run)
        if run.monitor is not None:
            drafts.extend(
                create_notification_drafts(
                    lakebase,
                    actor=row_actor,
                    monitor=run.monitor,
                    run_id=run.run_id,
                    route=run.route,
                    actionable_total=run.actionable_total,
                    channels=channels,
                    request_id=request_id,
                )
            )
    return runs, drafts


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

    When the Databricks Supervisor Agent is configured, it may choose exactly
    one reviewed workflow id from the app allowlist. The endpoint still does
    not call the normal Genie answer path for prompt planning because that path
    can compile and execute SQL. Reviewed deterministic tools own SQL, counts,
    filters, Lakebase audit rows, and the reconciled Lead Queue/Admin handoff.
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
    actor_override: str | None = None,
    interpreted_intent: str | None = None,
    copilot_evidence: GrowthAgentCopilotEvidence | None = None,
) -> GrowthAgentRunResponse:
    actor = actor_override or resolve_actor(request)
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
        monitor = monitor_from_row(monitor_row) if monitor_row is not None else None
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
        monitor = monitor_from_row(monitor_row) if monitor_row is not None else None
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
