"""Run the Growth Agent plan the user reviewed, and nothing else.

Audit 2026-09-21 ``critic-01``: 'Execute plan' used to compose a NEW plan
with the Supervisor model and run that one, so the plan that ran was never
the plan the lender reviewed. This path runs the posted plan only after, in
this order:

1. the request schema re-ran the objective guards and the size bound (a
   refused objective is a 422 with its refusal audit row; it never gets here);
2. the server's plan digest verifies for this actor, objective, scope and plan;
3. ``build_validated_plan`` re-validates the posted plan against the CURRENT
   tool registry (registry and planner exposure, per-tool params, narrative
   guards, server-computed ``requires_approval``);
4. the re-validated plan is a fixed point: its canonical JSON equals the
   posted plan's, so what runs is byte-for-byte what was reviewed;
5. ``execute_plan`` runs it unchanged: read-only tools, stopping at the first
   approval-gated step, with the same per-step and compose audit rows.

No model is called here: this module imports nothing that composes a plan or
reaches a serving endpoint (a test pins that).

Retry note: the client sends a ``request_id`` and the transport may re-send
after a warming 503. That is safe, because ``execute_plan`` writes all of its
audit rows in one Lakebase transaction after the reads, so a failed attempt
leaves no partial rows behind.
"""

from __future__ import annotations

from backend.schemas.agent_plan import (
    ComposePlanRequest,
    ComposePlanResponse,
    ExecutePlanRequest,
)
from backend.services.audit_store import AuditStore
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.growth_agent_composer import build_validated_plan
from backend.services.growth_agent_plan_digest import (
    PlanDigestConflict,
    canonical_plan_json,
    verify_plan_digest,
)
from backend.services.growth_agent_plan_executor import execute_plan
from backend.services.lakebase import LakebaseClient


def execute_reviewed_plan(
    payload: ExecutePlanRequest,
    *,
    actor: str,
    sql_client: DatabricksSqlClient,
    lakebase: LakebaseClient,
    audit_store: AuditStore,
) -> ComposePlanResponse:
    """Verify, re-validate and run the reviewed plan; raise on any mismatch.

    Raises ``PlanDigestConflict`` (HTTP 409) when the digest does not verify,
    has expired, the plan no longer validates, or re-validation changes it;
    ``PlanDigestUnavailable`` (HTTP 503) when no signing key resolves. Nothing
    executes in either case.
    """

    verify_plan_digest(
        payload.plan_digest,
        actor=actor,
        objective=payload.objective,
        states=payload.states,
        plan=payload.plan,
    )

    # The objective and states already passed the same validators on
    # ExecutePlanRequest; build_validated_plan reads only the state scope
    # (for a fallback summary), so the reviewed values are passed through
    # rather than normalized a second time.
    scope = ComposePlanRequest.model_construct(
        objective=payload.objective,
        states=list(payload.states),
        execute=False,
        request_id=None,
    )
    outcome = build_validated_plan(payload.plan.model_dump(mode="json"), scope, endpoint=None)
    if outcome.status != "composed" or outcome.plan is None:
        raise PlanDigestConflict("plan_revalidation_failed")
    revalidated = outcome.plan
    if canonical_plan_json(revalidated) != canonical_plan_json(payload.plan):
        raise PlanDigestConflict("plan_changed")

    execution = execute_plan(
        revalidated,
        sql_client=sql_client,
        lakebase=lakebase,
        audit_store=audit_store,
        actor=actor,
        request_id=payload.request_id,
    )
    return ComposePlanResponse(
        status="composed",
        model_endpoint=None,
        plan=revalidated,
        plan_digest=payload.plan_digest,
        executed=True,
        trace=execution.trace,
        plan_id=execution.plan_id,
        approval_gate_step_id=execution.approval_gate_step_id,
        audit_event_ids=execution.audit_event_ids,
        approval_required=revalidated.requires_approval,
    )


__all__ = ["execute_reviewed_plan"]
