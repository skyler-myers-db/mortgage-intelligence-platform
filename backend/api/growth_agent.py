"""Mortgage Growth Agent workflows.

These endpoints make agentic automation visible without changing the
approval posture: the agent reads governed Unity Catalog assets, records an
audited Lakebase run/monitor, and deep-links to the eligible Lead Queue subset
for human review. It never sends outreach or activates a connector.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.growth_agent import (
    GrowthAgentCustomRunRequest,
    GrowthAgentHomeResponse,
    GrowthAgentMonitor,
    GrowthAgentPolicyCheck,
    GrowthAgentRunRequest,
    GrowthAgentRunResponse,
    GrowthAgentToolStep,
    GrowthAgentWorkflow,
    GrowthAgentWorkflowId,
)
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import resolve_actor
from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError, get_sql_client
from backend.services.databricks_sql_helpers import qualify
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client

router = APIRouter(prefix="/growth-agent", tags=["growth-agent"])

SqlDep = Annotated[DatabricksSqlClient, Depends(get_sql_client)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def _require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Unsupported content type")


@dataclass(frozen=True)
class _WorkflowDef:
    id: GrowthAgentWorkflowId
    title: str
    objective: str
    trigger_label: str
    action_label: str
    source_assets: tuple[str, ...]
    proof_points: tuple[str, ...]
    broad_predicate: str
    actionable_predicate: str
    route_filters: dict[str, str]
    tool_detail: str

    def schema(self) -> GrowthAgentWorkflow:
        return GrowthAgentWorkflow(
            id=self.id,
            title=self.title,
            objective=self.objective,
            trigger_label=self.trigger_label,
            action_label=self.action_label,
            source_assets=list(self.source_assets),
            default_route=_route(self.route_filters),
            proof_points=list(self.proof_points),
        )


_BORROWER_360 = qualify("gold", "borrower_360")
_LEAD_POPULATION = qualify("gold", "lead_population")
_EVIDENCE_EVENTS = qualify("gold", "evidence_events")
_SOURCE_READINESS = qualify("gold", "source_readiness")

_SEGMENT_LABELS: dict[str, str] = {
    "itm": "Prime Refi Candidates",
    "listed": "Listed for Sale",
    "permit": "HELOC Intent",
    "investor": "Investor / Multi-Property",
    "equity": "Home Equity Candidate",
    "retention": "Retention Risk",
}

_CUSTOM_WORKFLOW_ID: GrowthAgentWorkflowId = "custom_segment_watch"
_CUSTOM_WORKFLOW_TITLE = "Custom Segment Workflow"

_WORKFLOWS: dict[GrowthAgentWorkflowId, _WorkflowDef] = {
    "daily_refi_brief": _WorkflowDef(
        id="daily_refi_brief",
        title="Daily Refi Opportunity Brief",
        objective="Find borrowers with rate-spread economics worth reviewing today.",
        trigger_label="Prime refinance economics",
        action_label="Open eligible refi subset",
        source_assets=(_BORROWER_360, _LEAD_POPULATION, _EVIDENCE_EVENTS),
        proof_points=(
            "Broad count uses borrower_360.in_the_money.",
            "Actionable count requires Lead Queue eligibility and opt-in.",
            "The route opens only the eligible Prime Refi Candidate subset.",
        ),
        broad_predicate="b.in_the_money = TRUE",
        actionable_predicate="array_contains(b.segment_codes, 'itm')",
        route_filters={"segment": "itm", "marketing_eligibility": "Eligible only"},
        tool_detail="Screened rate-spread and equity thresholds, then reconciled to the eligible Lead Queue subset.",
    ),
    "listing_watch": _WorkflowDef(
        id="listing_watch",
        title="Listed-for-Sale Purchase Watch",
        objective="Track Cotality MLS listing signals that can indicate next-home purchase intent.",
        trigger_label="Active or under-contract listing",
        action_label="Open eligible purchase subset",
        source_assets=(_BORROWER_360, _LEAD_POPULATION, _EVIDENCE_EVENTS, _SOURCE_READINESS),
        proof_points=(
            "Listing signals come from live Cotality MLS rows surfaced in gold.",
            "Filed building-permit records are not inferred from listings.",
            "The route opens only eligible listed-for-sale leads.",
        ),
        broad_predicate="b.listed_for_sale = TRUE",
        actionable_predicate="array_contains(b.segment_codes, 'listed')",
        route_filters={"segment": "listed", "marketing_eligibility": "Eligible only"},
        tool_detail="Checked live MLS listing flags and reconciled them to purchase-ready eligible leads.",
    ),
    "competitor_recapture_monitor": _WorkflowDef(
        id="competitor_recapture_monitor",
        title="Competitor Recapture Monitor",
        objective="Watch borrowers whose current lien appears to sit with a competitor lender.",
        trigger_label="Competitor lien signal",
        action_label="Open eligible recapture subset",
        source_assets=(_BORROWER_360, _LEAD_POPULATION, _EVIDENCE_EVENTS),
        proof_points=(
            "Competitor lien is a governed public-record alias, never a raw lender string.",
            "The actionable subset keeps marketing eligibility and opt-in gates closed.",
            "Human review is required before any outreach draft or activation.",
        ),
        broad_predicate="b.is_competitor_lien = TRUE",
        actionable_predicate="b.is_competitor_lien = TRUE",
        route_filters={
            "lender_relationship": "Competitor customer",
            "marketing_eligibility": "Eligible only",
        },
        tool_detail="Found competitor-lien signals and constrained them to eligible recapture candidates.",
    ),
    "high_equity_heloc_watch": _WorkflowDef(
        id="high_equity_heloc_watch",
        title="High-Equity / HELOC Watch",
        objective="Find equity-rich borrowers and Cotality HELOC-intent signals for lending review.",
        trigger_label="Equity and HELOC propensity",
        action_label="Open eligible HELOC/equity subset",
        source_assets=(_BORROWER_360, _LEAD_POPULATION, _EVIDENCE_EVENTS, _SOURCE_READINESS),
        proof_points=(
            "HELOC Intent uses Cotality propensity, not filed building-permit rows.",
            "High-equity counts use the governed HELOC equity threshold applied during gold refresh.",
            "The route uses OR semantics and Lead Queue eligibility.",
        ),
        broad_predicate=(
            "((b.has_permit = TRUE OR b.has_heloc_propensity_trigger = TRUE) "
            "OR (b.equity_pct >= b.heloc_equity_min_applied "
            "AND COALESCE(b.second_pos_amount, 0) = 0))"
        ),
        actionable_predicate=(
            "(array_contains(b.segment_codes, 'permit') "
            "OR array_contains(b.segment_codes, 'equity'))"
        ),
        route_filters={
            "segment_codes": "permit,equity",
            "segment_mode": "any",
            "marketing_eligibility": "Eligible only",
        },
        tool_detail="Combined HELOC propensity and high-equity signals with de-duplicated OR semantics.",
    ),
}


def _custom_workflow(segment_codes: Sequence[str], segment_mode: str) -> _WorkflowDef:
    deduped = [code for code in segment_codes if code in _SEGMENT_LABELS]
    if not deduped:
        raise HTTPException(status_code=422, detail="segment_codes must include at least one reviewed segment")
    mode = "all" if segment_mode == "all" else "any"
    segment_label = _format_segment_labels(deduped, mode=mode)
    predicate = _segment_predicate(deduped, mode)
    route_filters = {
        "segment_codes": ",".join(deduped),
        "segment_mode": mode,
        "marketing_eligibility": "Eligible only",
    }
    return _WorkflowDef(
        id=_CUSTOM_WORKFLOW_ID,
        title=_CUSTOM_WORKFLOW_TITLE,
        objective=f"Build a reviewed {segment_label} workflow from governed segment membership.",
        trigger_label=f"{segment_label} segment screen",
        action_label="Open eligible custom subset",
        source_assets=(_BORROWER_360, _LEAD_POPULATION, _EVIDENCE_EVENTS),
        proof_points=(
            "Custom workflows are limited to reviewed Module 0 segment codes.",
            f"Segment mode is {mode.upper()}: borrowers are counted once after matching {segment_label}.",
            "The route opens only the eligible Lead Queue subset for human review.",
        ),
        broad_predicate=predicate,
        actionable_predicate=predicate,
        route_filters=route_filters,
        tool_detail=(
            f"Applied {mode.upper()} segment semantics across {segment_label}; "
            "the result is de-duplicated at borrower grain before human review."
        ),
    )


def _segment_predicate(segment_codes: list[str], mode: str) -> str:
    joiner = " AND " if mode == "all" else " OR "
    clauses = [f"array_contains(b.segment_codes, '{code}')" for code in segment_codes]
    return "(" + joiner.join(clauses) + ")"


def _format_segment_labels(segment_codes: list[str], *, mode: str) -> str:
    labels = [_SEGMENT_LABELS[code] for code in segment_codes]
    if len(labels) == 1:
        return labels[0]
    separator = " and " if mode == "all" else " or "
    return ", ".join(labels[:-1]) + separator + labels[-1]

_RUN_INSERT_SQL = """
INSERT INTO mip_app.growth_agent_runs (
  actor_email, request_id, workflow_id, workflow_title, criteria,
  broad_total, actionable_total, broad_avg_score, actionable_avg_score,
  avg_rate_spread_bps, avg_equity_pct, route, source_assets,
  tool_steps, policy_checks
) VALUES (
  %(actor_email)s, %(request_id)s, %(workflow_id)s, %(workflow_title)s, %(criteria)s::jsonb,
  %(broad_total)s, %(actionable_total)s, %(broad_avg_score)s, %(actionable_avg_score)s,
  %(avg_rate_spread_bps)s, %(avg_equity_pct)s, %(route)s, %(source_assets)s,
  %(tool_steps)s::jsonb, %(policy_checks)s::jsonb
)
ON CONFLICT (actor_email, request_id) WHERE request_id IS NOT NULL DO NOTHING
RETURNING run_id, workflow_id, criteria, broad_total, actionable_total,
          broad_avg_score, actionable_avg_score, avg_rate_spread_bps, avg_equity_pct,
          route, source_assets, tool_steps, policy_checks, audit_event_id, created_at
"""

_RUN_ATTACH_AUDIT_SQL = """
UPDATE mip_app.growth_agent_runs
SET audit_event_id = %(audit_event_id)s
WHERE run_id = %(run_id)s
RETURNING run_id, workflow_id, criteria, broad_total, actionable_total,
          broad_avg_score, actionable_avg_score, avg_rate_spread_bps, avg_equity_pct,
          route, source_assets, tool_steps, policy_checks, audit_event_id, created_at
"""

_RUN_SELECT_BY_REQUEST_ID_SQL = """
SELECT run_id, workflow_id, criteria, broad_total, actionable_total,
       broad_avg_score, actionable_avg_score, avg_rate_spread_bps, avg_equity_pct,
       route, source_assets, tool_steps, policy_checks, audit_event_id, created_at
FROM mip_app.growth_agent_runs
WHERE actor_email = %(actor_email)s
  AND request_id = %(request_id)s
LIMIT 1
"""

_MONITOR_UPSERT_SQL = """
INSERT INTO mip_app.growth_agent_monitors (
  actor_email, workflow_id, name, cadence, criteria, route,
  actionable_total, source_assets, last_run_id, updated_at
) VALUES (
  %(actor_email)s, %(workflow_id)s, %(name)s, %(cadence)s, %(criteria)s::jsonb, %(route)s,
  %(actionable_total)s, %(source_assets)s, %(last_run_id)s, now()
)
ON CONFLICT (actor_email, workflow_id, name) DO UPDATE SET
  cadence = EXCLUDED.cadence,
  criteria = EXCLUDED.criteria,
  route = EXCLUDED.route,
  actionable_total = EXCLUDED.actionable_total,
  source_assets = EXCLUDED.source_assets,
  last_run_id = EXCLUDED.last_run_id,
  status = 'active',
  updated_at = now()
RETURNING monitor_id, workflow_id, name, cadence, status, criteria, route,
          actionable_total, source_assets, last_run_id, created_at, updated_at
"""

_MONITOR_LIST_SQL = """
SELECT monitor_id, workflow_id, name, cadence, status, criteria, route,
       actionable_total, source_assets, last_run_id, created_at, updated_at
FROM mip_app.growth_agent_monitors
WHERE actor_email = %(actor_email)s
ORDER BY updated_at DESC
LIMIT %(limit)s
"""

_MONITOR_SELECT_BY_RUN_ID_SQL = """
SELECT monitor_id, workflow_id, name, cadence, status, criteria, route,
       actionable_total, source_assets, last_run_id, created_at, updated_at
FROM mip_app.growth_agent_monitors
WHERE actor_email = %(actor_email)s
  AND last_run_id = %(last_run_id)s
ORDER BY updated_at DESC
LIMIT 1
"""


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


@router.post("/workflows/{workflow_id}/run", response_model=GrowthAgentRunResponse)
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


@router.post("/custom/run", response_model=GrowthAgentRunResponse)
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


def _run_workflow(
    *,
    workflow: _WorkflowDef,
    payload: GrowthAgentRunRequest,
    request: Request,
    sql_client: DatabricksSqlClient,
    lakebase: LakebaseClient,
) -> GrowthAgentRunResponse:
    actor = resolve_actor(request)
    criteria = _criteria_for(workflow, payload.states)
    route = _route({**workflow.route_filters, **({"states": ",".join(payload.states)} if payload.states else {})})
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

    metrics = _load_workflow_metrics(sql_client, workflow=workflow, states=payload.states)
    tool_steps = _tool_steps(workflow, metrics)
    policy_checks = _policy_checks(workflow, metrics, saved_monitor=payload.save_monitor)
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
                        "tool_steps": [step.model_dump() for step in tool_steps],
                        "policy_checks": [check.model_dump() for check in policy_checks],
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
    )


def _load_workflow_metrics(
    sql_client: DatabricksSqlClient,
    *,
    workflow: _WorkflowDef,
    states: list[str],
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    broad_state_clause = _state_clause("b", states, params)
    actionable_state_clause = _state_clause("b", states, params)
    statement = f"""
WITH broad AS (
  SELECT
    COUNT(DISTINCT b.clip) AS broad_total,
    ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS broad_avg_score,
    ROUND(AVG(CAST(b.rate_spread_bps AS DOUBLE)), 1) AS avg_rate_spread_bps,
    ROUND(AVG(CAST(b.equity_pct AS DOUBLE)), 1) AS avg_equity_pct
  FROM {_BORROWER_360} b
  WHERE {workflow.broad_predicate}
    {broad_state_clause}
),
    actionable AS (
      SELECT
        COUNT(DISTINCT b.clip) AS actionable_total,
        ROUND(AVG(CAST(b.opportunity_score AS DOUBLE)), 1) AS actionable_avg_score
      FROM {_BORROWER_360} b
      WHERE {workflow.actionable_predicate}
        AND b.marketing_eligible = TRUE
        AND b.consent_status = 'opt_in'
        AND b.suppression_reason IS NULL
        {actionable_state_clause}
    )
SELECT
  broad.broad_total,
  actionable.actionable_total,
  broad.broad_avg_score,
  actionable.actionable_avg_score,
  broad.avg_rate_spread_bps,
  broad.avg_equity_pct
FROM broad CROSS JOIN actionable
"""
    try:
        row = sql_client.execute_one(statement, params) or {}
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("warehouse")) from exc
    return {
        "broad_total": int(row.get("broad_total") or 0),
        "actionable_total": int(row.get("actionable_total") or 0),
        "broad_avg_score": _maybe_float(row.get("broad_avg_score")),
        "actionable_avg_score": _maybe_float(row.get("actionable_avg_score")),
        "avg_rate_spread_bps": _maybe_float(row.get("avg_rate_spread_bps")),
        "avg_equity_pct": _maybe_float(row.get("avg_equity_pct")),
    }


def _state_clause(alias: str, states: list[str], params: dict[str, Any]) -> str:
    if not states:
        return ""
    names: list[str] = []
    for idx, state in enumerate(states):
        key = f"state_{idx}"
        params[key] = state
        names.append(f":{key}")
    return f"AND UPPER({alias}.state) IN ({', '.join(names)})"


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


def _tool_steps(workflow: _WorkflowDef, metrics: dict[str, Any]) -> list[GrowthAgentToolStep]:
    return [
        GrowthAgentToolStep(
            label="Read trusted borrower signals",
            status="completed",
            detail=f"Found {metrics['broad_total']:,} borrowers in the broad opportunity screen.",
            source_asset=_BORROWER_360,
        ),
        GrowthAgentToolStep(
            label="Apply actionability gates",
            status="completed",
            detail=(
                f"Reconciled to {metrics['actionable_total']:,} marketing-eligible, "
                "opt-in leads for human review."
            ),
            source_asset=_LEAD_POPULATION,
        ),
        GrowthAgentToolStep(
            label="Prepare governed next step",
            status="review_required",
            detail=workflow.tool_detail,
            source_asset=_EVIDENCE_EVENTS,
        ),
    ]


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
    if saved_monitor:
        checks.append(
            GrowthAgentPolicyCheck(
                label="Monitor saved to Lakebase",
                status="passed",
                detail="The saved monitor stores reviewed filters and counts, not borrower identities or raw prompts.",
            )
        )
    return checks


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
    if monitor is None:
        monitor = _monitor_from_row(monitor_row) if monitor_row is not None else None
    return GrowthAgentRunResponse(
        workflow=workflow.schema(),
        run_id=str(run_row["run_id"]),
        monitor=monitor,
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
        audit_event_id=str(run_row["audit_event_id"]) if run_row.get("audit_event_id") else None,
        created_at=run_row.get("created_at"),
    )


def _route(filters: dict[str, str]) -> str:
    return "/lead-queue?" + urlencode(filters)


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
