"""Read-only live-analysis fallback for the Growth Agent co-pilot.

When no reviewed workflow matches the operator's objective, the objective can
still run as a governed Ask Genie turn: prompt guard battery, live SQL trust,
claims verification, PII redaction — analysis with proof, never state. Moved
out of the router module when it crossed the size gate (2026-08-08); behavior
unchanged. Routers stay thin; this is service logic.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import psycopg
from fastapi import HTTPException, Request

from backend.schemas.growth_agent import (
    GrowthAgentPolicyCheck,
    GrowthAgentPromptRunRequest,
    GrowthAgentRunResponse,
    GrowthAgentToolStep,
    GrowthAgentWorkflow,
)
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.repositories.factory import get_genie_answer_repository


def live_analysis_fallback(
    payload: GrowthAgentPromptRunRequest,
    request: Request,
    lakebase: LakebaseClient,
) -> GrowthAgentRunResponse | None:
    """Read-only live-analysis fallback when no reviewed workflow matches.

    The objective runs through the full Genie policy pipeline (which can plan
    its own multi-part decomposition), exactly like an Ask Genie turn: prompt
    guard battery first, live SQL trust, claims verification, PII redaction.
    Nothing here writes campaign/monitor state — the response is analysis with
    proof, and the audit trail records that a read-only analysis ran.
    """

    from backend.services.repositories.databricks_genie_sweep import (
        _planned_question_guard_hit,
    )

    if _planned_question_guard_hit(payload.prompt) is not None:
        return None
    repo = get_genie_answer_repository()
    try:
        analysis = repo.respond(payload.prompt)
    except Exception:  # noqa: BLE001 - fall back to the honest 422
        return None
    if analysis.source not in ("genie", "trusted_sql") or not (analysis.answer or "").strip():
        return None
    actor = resolve_actor(request)
    run_id = payload.request_id or str(uuid4())
    result_hash = hashlib.sha256(
        f"{analysis.question_hash}|{analysis.sql_query or ''}".encode()
    ).hexdigest()[:32]
    try:
        with lakebase.transaction() as conn:
            write_audit_event_in_transaction(
                conn,
                actor=actor,
                action="growth_agent.live_analysis",
                entity_type="growth_agent_run",
                entity_id=run_id,
                # Keys come from the reviewed audit-metadata allowlist
                # (backend/services/audit_store.py::_ALLOWED_METADATA_KEYS).
                payload_json={
                    "question_hash": analysis.question_hash,
                    "source_assets": analysis.trusted_assets,
                    "conversation_id": analysis.conversation_id or None,
                    "message_id": analysis.message_id,
                },
            )
    except (LakebaseError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    workflow = GrowthAgentWorkflow(
        id="live_analysis",
        title="Live analysis",
        objective=payload.prompt[:280],
        trigger_label="No reviewed workflow matched; the live space analyzed the objective itself.",
        action_label="Review the governed analysis; no state was written.",
        source_assets=list(analysis.trusted_assets),
        proof_points=[
            "Every figure comes from Genie's own governed SQL over trusted assets.",
            "The full answer, generated SQL, and process trace are in Ask Genie.",
        ],
        default_route="/ask-genie",
    )
    tool_steps = [
        GrowthAgentToolStep(
            label=step.kind,
            status="completed",
            detail=step.content,
        )
        for step in (analysis.reasoning_trace or [])[:8]
    ] or [
        GrowthAgentToolStep(
            label="live",
            status="completed",
            detail="The objective ran as a governed live Genie analysis.",
        )
    ]
    return GrowthAgentRunResponse(
        workflow=workflow,
        run_id=run_id,
        specialist_agent="structured_data_agent",
        execution_mode="genie_conversation",
        trace_kind="genie_conversation",
        planner_label="Live Genie analysis (read-only fallback)",
        trace_id=f"agent-trace-{uuid4()}",
        tool_result_hash=result_hash,
        broad_total=analysis.row_count or 0,
        actionable_total=0,
        route="/ask-genie",
        criteria={"prompt": payload.prompt[:280]},
        source_assets=list(analysis.trusted_assets),
        tool_steps=tool_steps,
        policy_checks=[
            GrowthAgentPolicyCheck(
                label="Prompt guard battery",
                status="passed",
                detail="Fair-lending, PII, scope, and injection screens cleared.",
            ),
            GrowthAgentPolicyCheck(
                label="Governed answer pipeline",
                status="passed",
                detail="SQL trust policy, claims verification, and PII redaction applied.",
            ),
            GrowthAgentPolicyCheck(
                label="No state written",
                status="passed",
                detail="Read-only analysis; campaigns, monitors, and Lead Queue were not modified.",
            ),
        ],
        interpreted_intent="Read-only live analysis (no reviewed workflow matched).",
        agent_reasoning=analysis.answer,
        genie_conversation_id=analysis.conversation_id or None,
        genie_message_id=analysis.message_id,
        genie_question_hash=analysis.question_hash,
    )


