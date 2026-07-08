"""Growth Agent plan-composition route — Supervisor-composed governed plans.

Split out of ``backend/api/growth_agent.py`` (2026-07-08) to keep that module
under the file-size gate, mirroring the ``genie_feedback_routes`` precedent.
Behavior is unchanged and pinned by the compose tests in
``tests/unit/test_growth_agent_api.py``; the route path stays
``POST /api/growth-agent/agent/compose`` because both routers share the
``/growth-agent`` prefix.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from backend.schemas.agent_plan import ComposePlanRequest, ComposePlanResponse
from backend.services.audit_store import get_audit_store, resolve_actor
from backend.services.databricks_sql import DatabricksSqlClient, get_sql_client
from backend.services.growth_agent_composer import compose_growth_agent_plan
from backend.services.growth_agent_plan_executor import execute_plan
from backend.services.growth_agent_workflows import WORKFLOWS as _WORKFLOWS
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, get_lakebase_client

router = APIRouter(prefix="/growth-agent", tags=["growth-agent"])

SqlDep = Annotated[DatabricksSqlClient, Depends(get_sql_client)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


@router.post("/agent/compose", response_model=ComposePlanResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def compose_mortgage_growth_agent_plan(
    payload: ComposePlanRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
    sql_client: SqlDep,
    lakebase: LakebaseDep,
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
        audit_store=get_audit_store(),
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
