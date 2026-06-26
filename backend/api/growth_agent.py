"""Mortgage Growth Agent workflows.

These endpoints make agentic automation visible without changing the
approval posture: the agent reads governed Unity Catalog assets, records an
audited Lakebase run/monitor, and deep-links to the eligible Lead Queue subset
for human review. It never sends outreach or activates a connector.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

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
from backend.services.agent_tools import assert_tool_allowed_for_specialist
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import resolve_actor
from backend.services.databricks_sql import DatabricksSqlClient, get_sql_client
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import (
    MONITOR_LIST_SQL as _MONITOR_LIST_SQL,
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
from backend.services.growth_agent_workflows import (
    CUSTOM_WORKFLOW_ID as _CUSTOM_WORKFLOW_ID,
)
from backend.services.growth_agent_workflows import (
    SOURCE_READINESS as _SOURCE_READINESS,
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
from backend.services.growth_agent_workflows import (
    planned_workflow as _planned_workflow,
)
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client

router = APIRouter(prefix="/growth-agent", tags=["growth-agent"])
_JSON_CONTENT_TYPE_RESPONSE = {415: {"description": "Unsupported content type"}}

SqlDep = Annotated[DatabricksSqlClient, Depends(get_sql_client)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def _require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Unsupported content type")


@router.get("", response_model=GrowthAgentHomeResponse)
def growth_agent_home(request: Request, lakebase: LakebaseDep) -> GrowthAgentHomeResponse:
    actor = resolve_actor(request)
    return GrowthAgentHomeResponse(
        workflows=[workflow.schema() for workflow in _WORKFLOWS.values()],
        monitors=_list_monitors(lakebase, actor=actor),
    )


@router.get("/monitors", response_model=list[GrowthAgentMonitor])
def growth_agent_monitors(request: Request, lakebase: LakebaseDep) -> list[GrowthAgentMonitor]:
    actor = resolve_actor(request)
    return _list_monitors(lakebase, actor=actor)


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=GrowthAgentRunResponse,
    responses=_JSON_CONTENT_TYPE_RESPONSE,
)
def run_growth_agent_workflow(
    workflow_id: GrowthAgentWorkflowId,
    payload: GrowthAgentRunRequest,
    request: Request,
    _: Annotated[None, Depends(_require_json_content_type)],
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


@router.post("/custom/run", response_model=GrowthAgentRunResponse, responses=_JSON_CONTENT_TYPE_RESPONSE)
def run_custom_growth_agent_workflow(
    payload: GrowthAgentCustomRunRequest,
    request: Request,
    _: Annotated[None, Depends(_require_json_content_type)],
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


@router.post("/agent/run", response_model=GrowthAgentRunResponse, responses=_JSON_CONTENT_TYPE_RESPONSE)
def run_mortgage_growth_agent(
    payload: GrowthAgentPromptRunRequest,
    request: Request,
    _: Annotated[None, Depends(_require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
) -> GrowthAgentRunResponse:
    """Route a natural-language agent request to reviewed deterministic tools.

    The prompt is used only for bounded routing. The LLM/agent layer never
    executes SQL, DML, or outreach. It selects one reviewed workflow or one
    reviewed custom segment screen, then the same deterministic executor writes
    the Lakebase audit row and returns a reconciled Lead Queue/Admin handoff.
    """

    workflow, interpreted_intent = _planned_workflow(payload)
    response = _run_workflow(
        workflow=workflow,
        payload=payload,
        request=request,
        sql_client=sql_client,
        lakebase=lakebase,
        interpreted_intent=interpreted_intent,
    )
    return response


def _run_workflow(
    *,
    workflow: _WorkflowDef,
    payload: GrowthAgentRunRequest,
    request: Request,
    sql_client: DatabricksSqlClient,
    lakebase: LakebaseClient,
    interpreted_intent: str | None = None,
) -> GrowthAgentRunResponse:
    actor = resolve_actor(request)
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
    tool_steps = _tool_steps(workflow, metrics, tool_result_hash=tool_result_hash)
    policy_checks = _policy_checks(workflow, metrics, saved_monitor=payload.save_monitor)
    governance_chips = _governance_chips(
        workflow,
        metrics,
        policy_checks=policy_checks,
        trace_id=trace_id,
        audit_event_id=None,
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
                )
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


def _criteria_for(workflow: _WorkflowDef, states: list[str]) -> dict[str, object]:
    lead_queue_filters: dict[str, object] = {
        "source": "trusted_sql",
    }
    portfolio_criteria: dict[str, object] = {"marketing_eligibility": "Eligible only"}
    if "segment_codes" in workflow.route_filters:
        lead_queue_filters["segment_codes"] = workflow.route_filters["segment_codes"].split(",")
        lead_queue_filters["segment_mode"] = workflow.route_filters.get("segment_mode", "any")
    elif "segment" in workflow.route_filters:
        lead_queue_filters["segment_codes"] = [workflow.route_filters["segment"]]
        lead_queue_filters["segment_mode"] = "any"
    if "lender_relationship" in workflow.route_filters:
        portfolio_criteria["lender_relationship"] = workflow.route_filters["lender_relationship"]
    if "target_lender_ref" in workflow.route_filters:
        lead_queue_filters["target_lender_ref"] = workflow.route_filters["target_lender_ref"]
    for key in ("approval_status", "outreach_status", "aged_days", "funnel_stage"):
        if key in workflow.route_filters:
            lead_queue_filters[key] = workflow.route_filters[key]
    if states:
        lead_queue_filters["states"] = states
        portfolio_criteria["states"] = states
    lead_queue_filters["portfolio_criteria"] = portfolio_criteria
    return {
        "states": states,
        "lead_queue_filters": lead_queue_filters,
        "marketing_eligibility": "Eligible only",
        "workflow_id": workflow.id,
    }


def _tool_steps(
    workflow: _WorkflowDef,
    metrics: dict[str, Any],
    *,
    tool_result_hash: str,
) -> list[GrowthAgentToolStep]:
    if workflow.id == "source_freshness_sentinel":
        warning_total = int(metrics.get("warning_total") or 0)
        stale_total = int(metrics.get("stale_total") or 0)
        return [
            _reviewed_tool_step(
                workflow,
                "fn_source_readiness",
                label="Read source readiness",
                status="completed",
                detail=f"Checked {metrics['broad_total']:,} governed source-readiness rows.",
                result_hash=tool_result_hash,
            ),
            _reviewed_tool_step(
                workflow,
                "source_readiness_status_rollup",
                label="Classify source health",
                status="review_required" if warning_total or stale_total else "completed",
                detail=(
                    f"{metrics['actionable_total']:,} feeds are live; "
                    f"{warning_total:,} non-live and {stale_total:,} stale feeds need review."
                ),
                result_hash=tool_result_hash,
            ),
            _reviewed_tool_step(
                workflow,
                "open_admin_data_operations",
                label="Prepare operator handoff",
                status="review_required",
                detail=workflow.tool_detail,
                result_hash=tool_result_hash,
            ),
        ]
    if workflow.id == "borrower_dossier_review":
        return [
            _reviewed_tool_step(
                workflow,
                "fn_borrower_dossier_evidence",
                label="Read dossier evidence",
                status="completed",
                detail=f"Found {metrics['broad_total']:,} borrowers in the top-opportunity screen.",
                result_hash=tool_result_hash,
            ),
            _reviewed_tool_step(
                workflow,
                "fn_borrower_dossier_evidence",
                label="Summarize dossier evidence",
                status="completed",
                detail=(
                    f"Joined borrower dossier rows to evidence_events; "
                    f"{int(metrics.get('evidence_backed_total') or 0):,} top opportunities have evidence rows."
                ),
                result_hash=tool_result_hash,
            ),
            _reviewed_tool_step(
                workflow,
                "fn_lead_queue_url",
                label="Prepare governed next step",
                status="review_required",
                detail=workflow.tool_detail,
                result_hash=tool_result_hash,
            ),
        ]
    if workflow.id == "high_equity_heloc_watch":
        return [
            _reviewed_tool_step(
                workflow,
                "fn_build_cohort",
                label="Read equity and propensity signals",
                status="completed",
                detail=f"Found {metrics['broad_total']:,} borrowers in the broad HELOC/equity screen.",
                result_hash=tool_result_hash,
            ),
            _reviewed_tool_step(
                workflow,
                "fn_offer_compare",
                label="Compare offer fit",
                status="completed",
                detail=(
                    f"Reconciled HELOC/equity signals to {metrics['actionable_total']:,} "
                    "eligible offer candidates."
                ),
                result_hash=tool_result_hash,
            ),
            _reviewed_tool_step(
                workflow,
                "fn_lead_queue_url",
                label="Prepare governed next step",
                status="review_required",
                detail=workflow.tool_detail,
                result_hash=tool_result_hash,
            ),
        ]
    return [
        _reviewed_tool_step(
            workflow,
            "fn_build_cohort",
            label="Read trusted borrower signals",
            status="completed",
            detail=f"Found {metrics['broad_total']:,} borrowers in the broad opportunity screen.",
            result_hash=tool_result_hash,
        ),
        _reviewed_tool_step(
            workflow,
            "fn_segment_counts",
            label="Apply actionability gates",
            status="completed",
            detail=(
                f"Reconciled to {metrics['actionable_total']:,} marketing-eligible, "
                "opt-in leads for human review."
            ),
            result_hash=tool_result_hash,
        ),
        _reviewed_tool_step(
            workflow,
            "fn_lead_queue_url",
            label="Prepare governed next step",
            status="review_required",
            detail=workflow.tool_detail,
            result_hash=tool_result_hash,
        ),
    ]


def _reviewed_tool_step(
    workflow: _WorkflowDef,
    tool_name: str,
    *,
    label: str,
    status: Literal["completed", "blocked", "review_required"],
    detail: str,
    result_hash: str,
) -> GrowthAgentToolStep:
    tool = assert_tool_allowed_for_specialist(tool_name, workflow.specialist_agent)
    return GrowthAgentToolStep(
        label=label,
        status=status,
        detail=detail,
        source_asset=tool.source_asset,
        tool_name=tool.name,
        result_hash=result_hash,
    )


def _policy_checks(
    workflow: _WorkflowDef,
    metrics: dict[str, Any],
    *,
    saved_monitor: bool,
) -> list[GrowthAgentPolicyCheck]:
    broad_total = int(metrics["broad_total"])
    actionable_total = int(metrics["actionable_total"])
    reconciliation_status: Literal["passed", "review_required"] = "passed"
    reconciliation_detail = (
        f"{broad_total:,} broad opportunities reconcile to "
        f"{actionable_total:,} eligible leads."
    )
    if actionable_total > broad_total:
        reconciliation_status = "review_required"
        reconciliation_detail = (
            f"{actionable_total:,} eligible leads exceeds {broad_total:,} broad opportunities; "
            "review source filters before acting."
        )
    checks = [
        GrowthAgentPolicyCheck(
            label="No raw PII exposed",
            status="passed",
            detail="The workflow returns counts, public route filters, and governed source assets only.",
        ),
        GrowthAgentPolicyCheck(
            label="No outbound activation",
            status="passed",
            detail="The agent opens a reviewed Lead Queue subset; email, SMS, and CRM activation still require approval.",
        ),
        GrowthAgentPolicyCheck(
            label="Broad vs actionable reconciliation",
            status=reconciliation_status,
            detail=reconciliation_detail,
        ),
    ]
    if workflow.id == "high_equity_heloc_watch":
        checks.append(
            GrowthAgentPolicyCheck(
                label="Permit honesty",
                status="passed",
                detail="HELOC Intent is propensity-backed; filed building-permit records remain a separate pending feed.",
            )
        )
    if workflow.id == _CUSTOM_WORKFLOW_ID:
        checks.append(
            GrowthAgentPolicyCheck(
                label="Reviewed custom workflow",
                status="passed",
                detail=(
                    "Custom workflow criteria are reviewed segment codes and explicit Any/All mode only; "
                    "no arbitrary SQL or outbound activation is stored."
                ),
            )
        )
    if workflow.id == "branch_capacity_review":
        checks.append(
            GrowthAgentPolicyCheck(
                label="Manager review only",
                status="passed",
                detail="The workflow surfaces stale approved leads; it does not reassign LOs or change outreach state.",
            )
        )
    if workflow.id == "borrower_dossier_review":
        checks.append(
            GrowthAgentPolicyCheck(
                label="Dossier privacy",
                status="passed",
                detail="The handoff opens a scored queue for review; borrower dossier details still require row-level user action.",
            )
        )
    if workflow.id == "source_freshness_sentinel":
        warning_total = int(metrics.get("warning_total") or 0)
        stale_total = int(metrics.get("stale_total") or 0)
        checks.append(
            GrowthAgentPolicyCheck(
                label="Source freshness",
                status="passed" if warning_total == 0 and stale_total == 0 else "review_required",
                detail=(
                    f"{warning_total:,} feeds are demo, pending, configured-empty, error, roadmap, not configured, "
                    "or permission denied; "
                    f"{stale_total:,} feeds are older than 7 days."
                ),
            )
        )
    if saved_monitor:
        checks.append(
            GrowthAgentPolicyCheck(
                label="Monitor saved to Lakebase",
                status="passed",
                detail="The saved monitor stores reviewed filters and counts, not borrower identities or raw prompts.",
            )
        )
    return checks


def _governance_chips(
    workflow: _WorkflowDef,
    metrics: dict[str, Any],
    *,
    policy_checks: list[GrowthAgentPolicyCheck],
    trace_id: str,
    audit_event_id: str | None,
) -> list[GrowthAgentGovernanceChip]:
    blocked = any(check.status == "blocked" for check in policy_checks)
    review = any(check.status == "review_required" for check in policy_checks)
    policy_status: Literal["passed", "review_required"] = "review_required" if blocked or review else "passed"
    chips: list[GrowthAgentGovernanceChip] = [
        GrowthAgentGovernanceChip(
            label="PII-safe output",
            status="passed",
            detail="The run returns counts, source assets, hashes, and route filters only.",
            evidence_ref=trace_id,
        ),
        GrowthAgentGovernanceChip(
            label="Human approval required",
            status="passed",
            detail="No outreach, CRM activation, assignment change, or source refresh is executed by this run.",
            evidence_ref=workflow.id,
        ),
        GrowthAgentGovernanceChip(
            label="Policy checks",
            status=policy_status,
            detail=f"{len(policy_checks):,} policy checks evaluated for this workflow.",
            evidence_ref=audit_event_id,
        ),
    ]
    if workflow.id == "source_freshness_sentinel":
        chips.append(
            GrowthAgentGovernanceChip(
                label="Freshness signal",
                status=(
                    "passed"
                    if int(metrics.get("warning_total") or 0) == 0 and int(metrics.get("stale_total") or 0) == 0
                    else "review_required"
                ),
                detail="Backed by global gold.source_readiness rows.",
                evidence_ref=_SOURCE_READINESS,
            )
        )
    return chips


def _upsert_monitor(
    conn: Any,
    *,
    actor: str,
    workflow: _WorkflowDef,
    payload: GrowthAgentRunRequest,
    criteria: dict[str, object],
    run_row: dict[str, Any],
) -> dict[str, Any] | None:
    return _txn_fetchone(
        conn,
        _MONITOR_UPSERT_SQL,
        {
            "actor_email": actor,
            "workflow_id": workflow.id,
            "name": payload.monitor_name or workflow.title,
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
    if monitor is None:
        monitor = _monitor_from_row(monitor_row) if monitor_row is not None else None
    return GrowthAgentRunResponse(
        workflow=workflow.schema(),
        run_id=str(run_row["run_id"]),
        monitor=monitor,
        specialist_agent=run_row.get("specialist_agent") or workflow.specialist_agent,
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
        interpreted_intent=interpreted_intent,
        audit_event_id=str(run_row["audit_event_id"]) if run_row.get("audit_event_id") else None,
        created_at=run_row.get("created_at"),
    )


def _tool_result_hash(
    *,
    workflow: _WorkflowDef,
    metrics: dict[str, Any],
    criteria: dict[str, object],
    route: str,
) -> str:
    payload = {
        "workflow_id": workflow.id,
        "metrics": metrics,
        "criteria": criteria,
        "route": route,
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _list_monitors(lakebase: LakebaseClient, *, actor: str) -> list[GrowthAgentMonitor]:
    try:
        rows = lakebase.fetchall(_MONITOR_LIST_SQL, {"actor_email": actor, "limit": 20}, limit=20)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return [_monitor_from_row(row) for row in rows]


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
        name=row["name"],
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
