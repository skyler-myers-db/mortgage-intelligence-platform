"""Reviewed Mortgage Growth Agent runtime helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from backend.agents.mortgage_growth_copilot import GrowthAgentCopilotEvidence
from backend.schemas.growth_agent import (
    GrowthAgentGovernanceChip,
    GrowthAgentPolicyCheck,
    GrowthAgentToolStep,
)
from backend.services.agent_tools import assert_tool_allowed_for_specialist
from backend.services.growth_agent_workflows import (
    CUSTOM_WORKFLOW_ID,
    SOURCE_READINESS,
    GrowthAgentWorkflowDef,
)


def default_copilot_evidence(workflow: GrowthAgentWorkflowDef) -> GrowthAgentCopilotEvidence:
    return GrowthAgentCopilotEvidence(
        execution_mode="deterministic",
        trace_kind="local_hash",
        planner_label="Reviewed workflow runner",
        interpreted_intent=f"Reviewed workflow selected: {workflow.title}.",
        reasoning_summary="The user selected a reviewed workflow card; no model planner was used.",
        fallback_reason="direct_workflow_run",
    )


def criteria_for(workflow: GrowthAgentWorkflowDef, states: list[str]) -> dict[str, object]:
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


def tool_steps(
    workflow: GrowthAgentWorkflowDef,
    metrics: dict[str, Any],
    *,
    tool_result_hash: str,
    copilot_evidence: GrowthAgentCopilotEvidence,
) -> list[GrowthAgentToolStep]:
    supervisor_override = bool(copilot_evidence.workflow_override_review_required)
    if supervisor_override:
        planner_detail = (
            "Supervisor selected a different allowlisted workflow than the deterministic fallback; "
            "human review is required before acting."
        )
    elif copilot_evidence.execution_mode == "agent_framework":
        planner_detail = (
            "Supervisor selected one allowlisted workflow; deterministic tools own counts, filters, audit, and handoff."
        )
    else:
        planner_detail = (
            "Reviewed planner selected an allowlisted workflow; deterministic tools own counts, filters, audit, and handoff."
        )
    planner_step = GrowthAgentToolStep(
        label="Interpret mortgage-growth objective",
        status="review_required" if supervisor_override else "completed",
        detail=planner_detail,
        source_asset=None,
        tool_name=None,
        result_hash=tool_result_hash,
    )
    if workflow.id == "source_freshness_sentinel":
        warning_total = int(metrics.get("warning_total") or 0)
        stale_total = int(metrics.get("stale_total") or 0)
        return [
            planner_step,
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
            planner_step,
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
            planner_step,
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
        planner_step,
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


def policy_checks(
    workflow: GrowthAgentWorkflowDef,
    metrics: dict[str, Any],
    *,
    saved_monitor: bool,
    copilot_evidence: GrowthAgentCopilotEvidence | None = None,
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
    if copilot_evidence and copilot_evidence.execution_mode == "agent_framework":
        selected = copilot_evidence.supervisor_workflow_id or workflow.id
        deterministic = copilot_evidence.deterministic_workflow_id or workflow.id
        override = bool(copilot_evidence.workflow_override_review_required)
        checks.append(
            GrowthAgentPolicyCheck(
                label="Supervisor workflow selection",
                status="review_required" if override else "passed",
                detail=(
                    f"Supervisor selected {selected}; deterministic fallback candidate was "
                    f"{deterministic}. Review the workflow choice before acting."
                    if override
                    else f"Supervisor and deterministic planner agreed on {selected}."
                ),
            )
        )
    if workflow.id == "high_equity_heloc_watch":
        checks.append(
            GrowthAgentPolicyCheck(
                label="Permit honesty",
                status="passed",
                detail="HELOC Intent is propensity-backed; filed building-permit records remain a separate pending feed.",
            )
        )
    if workflow.id == CUSTOM_WORKFLOW_ID:
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
                label="Watchlist saved to Lakebase",
                status="passed",
                detail=(
                    "The saved watchlist stores reviewed filters and counts only; "
                    "it does not create a scheduled run, outbound activation, borrower identity export, or raw prompt record."
                ),
            )
        )
    return checks


def governance_chips(
    workflow: GrowthAgentWorkflowDef,
    metrics: dict[str, Any],
    *,
    policy_checks: list[GrowthAgentPolicyCheck],
    trace_id: str,
    audit_event_id: str | None,
    copilot_evidence: GrowthAgentCopilotEvidence,
) -> list[GrowthAgentGovernanceChip]:
    blocked = any(check.status == "blocked" for check in policy_checks)
    review = any(check.status == "review_required" for check in policy_checks)
    policy_status: Literal["passed", "review_required"] = "review_required" if blocked or review else "passed"
    if copilot_evidence.workflow_override_review_required:
        framework_status: Literal["passed", "review_required", "not_attached"] = "review_required"
        framework_detail = (
            "Databricks Supervisor Agent selected a different reviewed workflow than the deterministic "
            "fallback; review the workflow choice before acting."
        )
    elif copilot_evidence.execution_mode == "agent_framework":
        framework_status = "passed"
        framework_detail = (
            "Databricks Supervisor Agent selected a reviewed workflow; reviewed deterministic tools executed the run."
        )
    else:
        framework_status = "not_attached"
        framework_detail = (
            "Mosaic/Agent Bricks orchestration is not used by this run; reviewed SQL workflows executed instead."
        )
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
        GrowthAgentGovernanceChip(
            label="Genie Conversation",
            status="passed" if copilot_evidence.execution_mode == "genie_conversation" else "not_attached",
            detail=(
                "Prompt interpretation is linked to a Genie Conversation message; deterministic tools executed the run."
                if copilot_evidence.execution_mode == "genie_conversation"
                else "This run used reviewed deterministic routing; no Genie Conversation proof was attached."
            ),
            evidence_ref=None,
        ),
        GrowthAgentGovernanceChip(
            label="Multi-agent framework",
            status=framework_status,
            detail=framework_detail,
            evidence_ref=copilot_evidence.question_hash if copilot_evidence.execution_mode == "agent_framework" else None,
        ),
        GrowthAgentGovernanceChip(
            label="MLflow trace/eval",
            status="not_attached",
            detail="No MLflow trace URL or Agent Evaluation result is attached to this run.",
            evidence_ref=None,
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
                evidence_ref=SOURCE_READINESS,
            )
        )
    return chips


def tool_result_hash(
    *,
    workflow: GrowthAgentWorkflowDef,
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


def _reviewed_tool_step(
    workflow: GrowthAgentWorkflowDef,
    tool_name: str,
    *,
    label: str,
    status: Literal["completed", "blocked", "review_required"],
    detail: str,
    result_hash: str,
) -> GrowthAgentToolStep:
    tool = assert_tool_allowed_for_specialist(tool_name, workflow.specialist_agent)
    if tool.writes_state:
        raise ValueError(f"{tool.name} writes state and cannot run inside reviewed read-only workflow")
    return GrowthAgentToolStep(
        label=label,
        status=status,
        detail=detail,
        source_asset=tool.source_asset,
        tool_name=tool.name,
        result_hash=result_hash,
    )
