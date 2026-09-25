"""Growth Agent plan-composition route for governed plans.

Split out of ``backend/api/growth_agent.py`` (2026-07-08) to keep that module
under the file-size gate, mirroring the ``genie_feedback_routes`` precedent.
Behavior is unchanged and pinned by the compose tests in
``tests/unit/test_growth_agent_api.py``; the route path stays
``POST /api/growth-agent/agent/compose`` because both routers share the
``/growth-agent`` prefix.

Audit 2026-09-21 ``critic-01`` / ``genie-09``: compose signs every composed
plan (``plan_digest``); ``POST /growth-agent/agent/plan/execute`` runs exactly
that reviewed plan, and ``GET /growth-agent/runs`` lists the caller's own
reviewed-workflow runs. Both mount here because ``backend/api`` modules may
not import each other and this router is already registered.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.agent_plan import (
    ComposePlanRequest,
    ComposePlanResponse,
    ExecutePlanRequest,
)
from backend.schemas.growth_agent_run_history import GrowthAgentRunSummary
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.databricks_sql import DatabricksSqlClient, get_sql_client
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_composer import compose_growth_agent_plan
from backend.services.growth_agent_plan_digest import (
    PlanDigestConflict,
    PlanDigestConflictReason,
    PlanDigestUnavailable,
    issue_plan_digest,
)
from backend.services.growth_agent_plan_executor import execute_plan
from backend.services.growth_agent_reviewed_plan import execute_reviewed_plan
from backend.services.growth_agent_run_history import (
    DEFAULT_RUN_LIST_LIMIT,
    MAX_RUN_LIST_LIMIT,
    list_runs,
)
from backend.services.growth_agent_workflows import WORKFLOWS as _WORKFLOWS
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.observability import emit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/growth-agent", tags=["growth-agent"])

SqlDep = Annotated[DatabricksSqlClient, Depends(get_sql_client)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]

# One fixed, public-safe sentence per conflict reason. The UI keys off the
# 409 status, never this text; the reason itself goes to the operator log.
PLAN_CONFLICT_DETAILS: dict[PlanDigestConflictReason, str] = {
    "digest_mismatch": (
        "The reviewed plan does not match the plan that was composed; compose it again."
    ),
    "digest_expired": "The reviewed plan expired before it was run; compose it again.",
    "plan_revalidation_failed": "The reviewed plan no longer passes review; compose it again.",
    "plan_changed": "The reviewed plan changed when it was checked again; compose it again.",
}
_PLAN_EXECUTE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **JSON_CONTENT_TYPE_RESPONSE,
    409: {
        "description": (
            "The posted plan is not the reviewed plan: the digest does not verify, "
            "has expired, or the plan no longer passes review. Nothing ran."
        )
    },
}


@router.post("/agent/compose", response_model=ComposePlanResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def compose_mortgage_growth_agent_plan(
    payload: ComposePlanRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
    audit_store: Annotated[AuditStore, Depends(get_audit_store)],
) -> ComposePlanResponse:
    """Compose a specialized multi-step plan from the governed tool registry.

    Unlike ``/agent/run`` (which selects one reviewed workflow), this endpoint
    asks the Supervisor serving endpoint to *compose* a plan whose every step is
    validated against the reviewed deterministic tool registry, then — when
    ``execute`` is set — runs the validated steps deterministically with the
    existing per-step audit and approval rails. Every composed response is
    labelled ``planner="supervisor_composed"`` and carries the model endpoint, so
    nothing composed can masquerade as a reviewed catalog workflow. When the
    Supervisor host is unavailable the response degrades honestly and offers the
    reviewed catalog workflows as a labelled fallback; when the model answers but
    the plan fails validation the response is ``invalid`` with no canned plan.
    """

    actor = resolve_actor(request)
    outcome = compose_growth_agent_plan(payload)
    if outcome.status == "degraded":
        return ComposePlanResponse(
            status="degraded",
            model_endpoint=outcome.endpoint,
            degraded_reason=outcome.degraded_reason,
            message=outcome.message,
            fallback_workflows=[workflow.schema() for workflow in _WORKFLOWS.values()],
        )
    if outcome.status == "invalid" or outcome.plan is None:
        return ComposePlanResponse(
            status="invalid",
            model_endpoint=outcome.endpoint,
            message=outcome.message or "The composed plan failed governed validation.",
        )
    plan = outcome.plan
    response = ComposePlanResponse(
        status="composed",
        model_endpoint=outcome.endpoint,
        plan=plan,
        plan_digest=issue_plan_digest(
            actor=actor,
            objective=payload.objective,
            states=payload.states,
            plan=plan,
        ),
        approval_required=plan.requires_approval,
        interpreted_intent=outcome.interpreted_intent,
        reasoning_summary=outcome.reasoning_summary,
    )
    if not payload.execute:
        return response
    execution = execute_plan(
        plan,
        sql_client=sql_client,
        lakebase=lakebase,
        audit_store=audit_store,
        actor=actor,
        request_id=payload.request_id,
    )
    return response.model_copy(
        update={
            "executed": True,
            "trace": execution.trace,
            "plan_id": execution.plan_id,
            "approval_gate_step_id": execution.approval_gate_step_id,
            "audit_event_ids": execution.audit_event_ids,
        }
    )


@router.post(
    "/agent/plan/execute",
    response_model=ComposePlanResponse,
    responses=_PLAN_EXECUTE_RESPONSES,
)
def execute_reviewed_growth_agent_plan(
    payload: ExecutePlanRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
    audit_store: Annotated[AuditStore, Depends(get_audit_store)],
) -> ComposePlanResponse:
    """Run the composed plan the user reviewed, exactly as it was signed.

    The request carries the displayed plan and the compose response's
    ``plan_digest``. The server verifies the digest for this actor, objective
    and state scope, re-validates the plan against the reviewed tool registry
    and runs it deterministically with the same per-step audit rows and
    approval gate as a composed run. It never composes a new plan: a plan that
    changed, expired or no longer passes review is a 409 and nothing runs.
    """

    actor = resolve_actor(request)
    try:
        return execute_reviewed_plan(
            payload,
            actor=actor,
            sql_client=sql_client,
            lakebase=lakebase,
            audit_store=audit_store,
        )
    except PlanDigestConflict as exc:
        emit(
            log,
            "growth_agent_plan_execute_conflict",
            level=logging.WARNING,
            outcome="conflict",
            conflict_reason=exc.reason,
        )
        raise HTTPException(status_code=409, detail=PLAN_CONFLICT_DETAILS[exc.reason]) from exc
    except PlanDigestUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("plan signing"),
        ) from exc


@router.get("/runs", response_model=list[GrowthAgentRunSummary])
def list_growth_agent_runs(
    request: Request,
    lakebase: LakebaseDep,
    limit: Annotated[int, Query(ge=1, le=MAX_RUN_LIST_LIMIT)] = DEFAULT_RUN_LIST_LIMIT,
) -> list[GrowthAgentRunSummary]:
    """List the caller's own recent reviewed-workflow runs, newest first.

    Read-only Lakebase app state: it writes no audit row, and each summary
    omits the actor, the stored criteria and the stored route (which can hold
    an expiring, actor-bound Lead Queue handoff proof).
    """

    return list_runs(lakebase, actor=resolve_actor(request), limit=limit)
