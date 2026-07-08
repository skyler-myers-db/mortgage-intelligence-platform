"""Deterministic executor for a validated, composed Growth Agent plan.

The composer produces a ``ComposedPlan`` whose every step names a reviewed tool.
This module runs those steps sequentially through the SAME deterministic tool
implementations the reviewed workflows lean on (governed bounded SQL reads and
the governed property-loan lookup), collects a per-step trace, writes a per-step
audit row plus a top-level compose audit row, and STOPS at the first
approval-gated tool exactly like the reviewed workflows do. Nothing here sends
outreach, activates a connector, or executes model-authored SQL.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import psycopg
from fastapi import HTTPException

from backend.schemas.agent_plan import ComposedPlan, PlanStep, PlanStepTrace
from backend.schemas.growth_agent import GrowthAgentToolStep
from backend.services.agent_tools import AgentTool, get_agent_tool
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import AuditStore
from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_workflows import (
    BORROWER_360,
    BORROWER_DOSSIER,
    SOURCE_READINESS,
)
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.property_lookup import lookup_property_loan


@dataclass(frozen=True)
class PlanStepResult:
    """Deterministic, PII-safe summary of one executed tool step."""

    detail: str
    source_asset: str
    row_summary: int | None = None


@dataclass
class ToolExecutionContext:
    sql_client: DatabricksSqlClient
    audit_store: AuditStore
    actor: str


ToolImpl = Callable[[ToolExecutionContext, dict[str, Any]], PlanStepResult]


@dataclass
class PlanExecution:
    plan_id: str
    trace: list[PlanStepTrace] = field(default_factory=list)
    audit_event_ids: list[str] = field(default_factory=list)
    approval_gate_step_id: str | None = None
    executed_step_count: int = 0


def execute_plan(
    plan: ComposedPlan,
    *,
    sql_client: DatabricksSqlClient,
    lakebase: LakebaseClient,
    audit_store: AuditStore,
    actor: str,
    request_id: str | None = None,
    tool_impls: dict[str, ToolImpl] | None = None,
) -> PlanExecution:
    """Execute a validated plan; return trace + audit ids, stopping at approval."""

    impls = tool_impls or default_tool_impls()
    ctx = ToolExecutionContext(sql_client=sql_client, audit_store=audit_store, actor=actor)
    plan_id = str(uuid4())
    execution = PlanExecution(plan_id=plan_id)

    executed: list[tuple[PlanStep, AgentTool, PlanStepResult, str, int]] = []
    for step in plan.steps:
        tool = get_agent_tool(step.tool)
        result_hash = _step_hash(step, tool)
        if tool.gates_run:
            execution.trace.append(
                PlanStepTrace(
                    step_id=step.step_id,
                    tool=tool.name,
                    label=tool.label,
                    status="review_required",
                    detail=(
                        f"{tool.label} prepares a reviewed human-review handoff and requires "
                        "approval. The run stops here; no outreach, activation, or write is executed."
                    ),
                    duration_ms=0,
                    row_summary=None,
                    result_hash=result_hash,
                    source_asset=tool.source_asset,
                    approval_gate=True,
                )
            )
            execution.approval_gate_step_id = step.step_id
            break

        impl = impls.get(tool.name)
        if impl is None:
            raise HTTPException(
                status_code=422,
                detail=f"composed plan references a non-executable tool: {tool.name}",
            )
        started = time.monotonic()
        result = impl(ctx, dict(step.params))
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        executed.append((step, tool, result, result_hash, duration_ms))
        execution.trace.append(
            PlanStepTrace(
                step_id=step.step_id,
                tool=tool.name,
                label=tool.label,
                status="completed",
                detail=result.detail,
                duration_ms=duration_ms,
                row_summary=result.row_summary,
                result_hash=result_hash,
                source_asset=result.source_asset,
                approval_gate=False,
            )
        )
    execution.executed_step_count = len(executed)

    _write_plan_audits(
        lakebase,
        plan=plan,
        plan_id=plan_id,
        executed=executed,
        approval_gate_step_id=execution.approval_gate_step_id,
        actor=actor,
        request_id=request_id,
        execution=execution,
    )
    return execution


def _write_plan_audits(
    lakebase: LakebaseClient,
    *,
    plan: ComposedPlan,
    plan_id: str,
    executed: list[tuple[PlanStep, AgentTool, PlanStepResult, str, int]],
    approval_gate_step_id: str | None,
    actor: str,
    request_id: str | None,
    execution: PlanExecution,
) -> None:
    try:
        with lakebase.transaction() as conn:
            step_index_to_audit: dict[str, str] = {}
            summary_steps: list[dict[str, Any]] = []
            for step, tool, result, result_hash, _duration in executed:
                tool_step = GrowthAgentToolStep(
                    label=tool.label,
                    status="completed",
                    detail=result.detail,
                    source_asset=tool.source_asset,
                    tool_name=tool.name,
                    result_hash=result_hash,
                )
                summary_steps.append(tool_step.model_dump())
                event = write_audit_event_in_transaction(
                    conn,
                    actor=actor,
                    action="growth_agent.plan_step",
                    entity_type="growth_agent_plan",
                    entity_id=plan_id,
                    payload_json={
                        "run_status": "completed",
                        "tool_result_hash": result_hash,
                        "specialist_agent": tool.primary_specialist(),
                        "source_assets": [tool.source_asset],
                        "tool_steps": [tool_step.model_dump()],
                    },
                    event_type="GROWTH_AGENT_PLAN_STEP",
                    request_id=request_id,
                )
                step_index_to_audit[step.step_id] = event.event_id
                execution.audit_event_ids.append(event.event_id)

            compose_event = write_audit_event_in_transaction(
                conn,
                actor=actor,
                action="growth_agent.compose",
                entity_type="growth_agent_plan",
                entity_id=plan_id,
                payload_json={
                    "run_status": "completed",
                    "specialist_agent": "campaign_agent",
                    "source_assets": _distinct_assets(summary_steps),
                    "tool_steps": summary_steps[:12],
                },
                event_type="GROWTH_AGENT_COMPOSE",
                request_id=request_id,
            )
            execution.audit_event_ids.append(compose_event.event_id)
    except (LakebaseError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc

    for trace in execution.trace:
        if trace.status == "completed" and trace.step_id in step_index_to_audit:
            trace.audit_event_id = step_index_to_audit[trace.step_id]
    _ = (plan, approval_gate_step_id)


def _distinct_assets(steps: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for item in steps:
        asset = str(item.get("source_asset") or "").strip()
        if asset and asset not in seen:
            seen.append(asset)
    return seen


def _step_hash(step: PlanStep, tool: AgentTool) -> str:
    payload = {"tool": tool.name, "params": step.params, "source_asset": tool.source_asset}
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------
# Deterministic tool implementations. Each runs one bounded governed read and
# returns a PII-safe count summary. These reuse the same gold assets and SQL
# client the reviewed workflows use; the model never authors SQL.
# --------------------------------------------------------------------------


def _summarize_count(row: dict[str, Any] | None) -> int:
    row = row or {}
    for key in ("row_count", "broad_total", "actionable_total", "n"):
        if key in row and row[key] is not None:
            try:
                return max(0, int(row[key]))
            except (TypeError, ValueError):
                continue
    return 0


def _state_clause(alias: str, states: list[str], params: dict[str, Any]) -> str:
    if not states:
        return ""
    names: list[str] = []
    for idx, state in enumerate(states):
        key = f"state_{idx}"
        params[key] = state
        names.append(f":{key}")
    return f" AND UPPER({alias}.state) IN ({', '.join(names)})"


def _execute_one(ctx: ToolExecutionContext, statement: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return ctx.sql_client.execute_one(statement, params) or {}
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("warehouse")) from exc


def _impl_build_cohort(ctx: ToolExecutionContext, params: dict[str, Any]) -> PlanStepResult:
    sql_params: dict[str, Any] = {}
    clause = _state_clause("b", list(params.get("states") or []), sql_params)
    row = _execute_one(
        ctx,
        f"SELECT COUNT(DISTINCT b.clip) AS row_count FROM {BORROWER_360} b WHERE TRUE{clause}",
        sql_params,
    )
    count = _summarize_count(row)
    return PlanStepResult(
        detail=f"Built the broad borrower cohort: {count:,} governed rows from {BORROWER_360}.",
        source_asset=BORROWER_360,
        row_summary=count,
    )


def _impl_segment_counts(ctx: ToolExecutionContext, params: dict[str, Any]) -> PlanStepResult:
    sql_params: dict[str, Any] = {}
    codes = [str(code) for code in (params.get("segment_codes") or [])]
    mode = str(params.get("segment_mode") or "any")
    segment_clause = ""
    if codes:
        joiner = " AND " if mode == "all" else " OR "
        clauses = [f"array_contains(b.segment_codes, '{code}')" for code in codes]
        segment_clause = " AND (" + joiner.join(clauses) + ")"
    state_clause = _state_clause("b", list(params.get("states") or []), sql_params)
    row = _execute_one(
        ctx,
        f"""
SELECT COUNT(DISTINCT b.clip) AS row_count
FROM {BORROWER_360} b
WHERE b.marketing_eligible = TRUE
  AND b.consent_status = 'opt_in'
  AND b.suppression_reason IS NULL{segment_clause}{state_clause}
""",
        sql_params,
    )
    count = _summarize_count(row)
    return PlanStepResult(
        detail=(
            f"Applied marketing-eligible, opt-in actionability gates: {count:,} eligible leads."
        ),
        source_asset=BORROWER_360,
        row_summary=count,
    )


def _impl_offer_compare(ctx: ToolExecutionContext, params: dict[str, Any]) -> PlanStepResult:
    sql_params: dict[str, Any] = {}
    state_clause = _state_clause("b", list(params.get("states") or []), sql_params)
    row = _execute_one(
        ctx,
        f"""
SELECT COUNT(DISTINCT b.clip) AS row_count
FROM {BORROWER_360} b
WHERE b.marketing_eligible = TRUE
  AND b.in_the_money = TRUE{state_clause}
""",
        sql_params,
    )
    count = _summarize_count(row)
    return PlanStepResult(
        detail=f"Compared offer fit against deterministic rules: {count:,} eligible offer candidates.",
        source_asset=BORROWER_360,
        row_summary=count,
    )


def _impl_dossier_evidence(ctx: ToolExecutionContext, params: dict[str, Any]) -> PlanStepResult:
    sql_params: dict[str, Any] = {"min_score": int(params.get("min_opportunity_score") or 75)}
    state_clause = _state_clause("d", list(params.get("states") or []), sql_params)
    row = _execute_one(
        ctx,
        f"""
SELECT COUNT(DISTINCT d.clip) AS row_count
FROM {BORROWER_DOSSIER} d
WHERE d.opportunity_score >= :min_score{state_clause}
""",
        sql_params,
    )
    count = _summarize_count(row)
    return PlanStepResult(
        detail=(
            f"Summarized governed dossier evidence at score >= {sql_params['min_score']}: "
            f"{count:,} borrowers, no identities exposed."
        ),
        source_asset=BORROWER_DOSSIER,
        row_summary=count,
    )


def _impl_source_readiness(ctx: ToolExecutionContext, _params: dict[str, Any]) -> PlanStepResult:
    row = _execute_one(ctx, f"SELECT COUNT(*) AS row_count FROM {SOURCE_READINESS}", {})
    count = _summarize_count(row)
    return PlanStepResult(
        detail=f"Read the governed source-readiness ledger: {count:,} feeds tracked.",
        source_asset=SOURCE_READINESS,
        row_summary=count,
    )


def _impl_source_rollup(ctx: ToolExecutionContext, _params: dict[str, Any]) -> PlanStepResult:
    row = _execute_one(
        ctx,
        f"SELECT COUNT_IF(status = 'live') AS row_count FROM {SOURCE_READINESS}",
        {},
    )
    count = _summarize_count(row)
    return PlanStepResult(
        detail=f"Classified source health: {count:,} feeds are live.",
        source_asset=SOURCE_READINESS,
        row_summary=count,
    )


def _impl_property_lookup(ctx: ToolExecutionContext, params: dict[str, Any]) -> PlanStepResult:
    response = lookup_property_loan(
        ctx.sql_client,
        ctx.audit_store,
        actor=ctx.actor,
        address_line=str(params["address_line"]),
        zip5=str(params["zip5"]),
    )
    detail = (
        "Resolved the property to a masked CLIP and governed loan facts."
        if response.matched
        else "No governed address match; the lookup was recorded without exposing any address."
    )
    return PlanStepResult(
        detail=detail,
        source_asset=qualify("gold", "address_lookup"),
        row_summary=1 if response.matched else 0,
    )


def default_tool_impls() -> dict[str, ToolImpl]:
    return {
        "fn_build_cohort": _impl_build_cohort,
        "fn_segment_counts": _impl_segment_counts,
        "fn_offer_compare": _impl_offer_compare,
        "fn_borrower_dossier_evidence": _impl_dossier_evidence,
        "fn_source_readiness": _impl_source_readiness,
        "source_readiness_status_rollup": _impl_source_rollup,
        "fn_property_loan_lookup": _impl_property_lookup,
    }


__all__ = [
    "PlanExecution",
    "PlanStepResult",
    "ToolExecutionContext",
    "ToolImpl",
    "default_tool_impls",
    "execute_plan",
]
