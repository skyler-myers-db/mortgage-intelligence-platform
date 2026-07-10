"""Reviewed workflow catalog and prompt router for the Mortgage Growth Agent."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import HTTPException

from backend.schemas.growth_agent import (
    GrowthAgentPromptRunRequest,
    GrowthAgentSpecialist,
    GrowthAgentWorkflow,
    GrowthAgentWorkflowId,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate


@dataclass(frozen=True)
class GrowthAgentWorkflowDef:
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
    specialist_agent: GrowthAgentSpecialist
    broad_label: str = "Broad opportunity"
    actionable_label: str = "Eligible subset"
    route_path: str = "/lead-queue"

    def schema(self) -> GrowthAgentWorkflow:
        return GrowthAgentWorkflow(
            id=self.id,
            title=self.title,
            objective=self.objective,
            trigger_label=self.trigger_label,
            action_label=self.action_label,
            source_assets=list(self.source_assets),
            default_route=build_growth_agent_route(self.route_filters, path=self.route_path),
            proof_points=list(self.proof_points),
        )


BORROWER_360 = qualify("gold", "borrower_360")
LEAD_POPULATION = qualify("gold", "lead_population")
BORROWER_DOSSIER = qualify("gold", "borrower_dossier")
EVIDENCE_EVENTS = qualify("gold", "evidence_events")
BORROWER_LIFECYCLE_STATE = qualify("gold", "borrower_lifecycle_state")
SOURCE_READINESS = qualify("gold", "source_readiness")

SEGMENT_LABELS: dict[str, str] = {
    "itm": "Prime Refi Candidates",
    "listed": "Listed for Sale",
    "permit": "HELOC Intent",
    "investor": "Investor / Multi-Property",
    "equity": "Home Equity Candidate",
    "retention": "Retention Risk",
}

CUSTOM_WORKFLOW_ID: GrowthAgentWorkflowId = "custom_segment_watch"
CUSTOM_WORKFLOW_TITLE = "Custom Segment Workflow"

WORKFLOWS: dict[GrowthAgentWorkflowId, GrowthAgentWorkflowDef] = {
    "daily_refi_brief": GrowthAgentWorkflowDef(
        id="daily_refi_brief",
        title="Daily Refi Opportunity Brief",
        objective="Find borrowers with rate-spread economics worth reviewing today.",
        trigger_label="Prime refinance economics",
        action_label="Open eligible refi subset",
        source_assets=(BORROWER_360, LEAD_POPULATION, EVIDENCE_EVENTS),
        proof_points=(
            "Broad count uses borrower_360.in_the_money.",
            "Actionable count requires Lead Queue eligibility and opt-in.",
            "The route opens only the eligible Prime Refi Candidate subset.",
        ),
        broad_predicate="b.in_the_money = TRUE",
        actionable_predicate="array_contains(b.segment_codes, 'itm')",
        route_filters={"segment": "itm", "marketing_eligibility": "Eligible only"},
        tool_detail="Screened rate-spread and equity thresholds, then reconciled to the eligible Lead Queue subset.",
        specialist_agent="structured_data_agent",
    ),
    "borrower_dossier_review": GrowthAgentWorkflowDef(
        id="borrower_dossier_review",
        title="Borrower Dossier Review",
        objective="Prepare the top-opportunity borrower story queue for human review without exposing raw identities.",
        trigger_label="Top opportunity dossier signals",
        action_label="Open top-opportunity dossier queue",
        source_assets=(BORROWER_360, BORROWER_DOSSIER, EVIDENCE_EVENTS),
        proof_points=(
            "Dossier evidence comes from the governed borrower_dossier and evidence_events assets.",
            "The handoff uses the existing high-opportunity Lead Queue stage.",
            "The agent does not open a raw borrower profile or expose owner names.",
        ),
        broad_predicate="b.opportunity_score >= 75",
        actionable_predicate="b.opportunity_score >= 75",
        route_filters={
            "funnel_stage": "high_opportunity",
            "marketing_eligibility": "Eligible only",
        },
        tool_detail="Prepared top-opportunity dossier review queue from governed borrower evidence.",
        specialist_agent="borrower_dossier_agent",
        broad_label="Top opportunities",
        actionable_label="Eligible dossier queue",
    ),
    "listing_watch": GrowthAgentWorkflowDef(
        id="listing_watch",
        title="Listed-for-Sale Purchase Watch",
        objective="Track Cotality MLS listing signals that can indicate next-home purchase intent.",
        trigger_label="Active or under-contract listing",
        action_label="Open eligible purchase subset",
        source_assets=(BORROWER_360, LEAD_POPULATION, EVIDENCE_EVENTS, SOURCE_READINESS),
        proof_points=(
            "Listing signals are gated by gold.source_readiness and come only from Cotality MLS rows surfaced in gold.",
            "Filed building-permit records are not inferred from listings.",
            "The route opens only eligible listed-for-sale leads.",
        ),
        broad_predicate="b.listed_for_sale = TRUE",
        actionable_predicate="array_contains(b.segment_codes, 'listed')",
        route_filters={"segment": "listed", "marketing_eligibility": "Eligible only"},
        tool_detail="Checked MLS-backed listed_for_sale flags and reconciled them to purchase-ready eligible leads.",
        specialist_agent="campaign_agent",
    ),
    "competitor_recapture_monitor": GrowthAgentWorkflowDef(
        id="competitor_recapture_monitor",
        title="Competitor Recapture Monitor",
        objective="Watch borrowers whose current lien appears to sit with a competitor lender.",
        trigger_label="Competitor lien signal",
        action_label="Open eligible recapture subset",
        source_assets=(BORROWER_360, LEAD_POPULATION, EVIDENCE_EVENTS),
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
        specialist_agent="compliance_agent",
    ),
    "high_equity_heloc_watch": GrowthAgentWorkflowDef(
        id="high_equity_heloc_watch",
        title="High-Equity / HELOC Watch",
        objective="Find equity-rich borrowers and Cotality HELOC-intent signals for lending review.",
        trigger_label="Equity and HELOC propensity",
        action_label="Open eligible HELOC/equity subset",
        source_assets=(BORROWER_360, LEAD_POPULATION, EVIDENCE_EVENTS, SOURCE_READINESS),
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
        specialist_agent="offer_agent",
    ),
    "branch_capacity_review": GrowthAgentWorkflowDef(
        id="branch_capacity_review",
        title="Branch Manager Capacity Review",
        objective="Find approved leads that are aging without outreach so a branch manager can rebalance LO capacity.",
        trigger_label="Stale approved queue",
        action_label="Open stale approved queue",
        source_assets=(BORROWER_360, LEAD_POPULATION, EVIDENCE_EVENTS, BORROWER_LIFECYCLE_STATE),
        proof_points=(
            "Counts use approval/outreach lifecycle state joined to borrower_360.",
            "The handoff opens approved leads aged at least 7 days with no outreach.",
            "This is a manager review workflow, not an automatic reassignment.",
        ),
        broad_predicate=eligible_sql_predicate("b"),
        actionable_predicate=eligible_sql_predicate("b"),
        route_filters={
            "approval_status": "approved",
            "aged_days": "7",
            "marketing_eligibility": "Eligible only",
        },
        tool_detail="Reviewed stale approved leads for capacity triage; no assignment is changed automatically.",
        specialist_agent="campaign_agent",
        broad_label="Approved queue",
        actionable_label="Aged review queue",
    ),
    "source_freshness_sentinel": GrowthAgentWorkflowDef(
        id="source_freshness_sentinel",
        title="Source/Freshness Sentinel",
        objective="Review live, demo, stale, pending, configured-empty, and blocked source feeds before a growth workflow is presented.",
        trigger_label="Global source readiness check",
        action_label="Open data operations",
        source_assets=(SOURCE_READINESS,),
        proof_points=(
            "Readiness comes from gold.source_readiness, not a static checklist.",
            "Pending, configured-empty, and demo feeds are disclosed instead of treated as live coverage.",
            "Operators refresh data from Admin; no source status is changed by the agent.",
        ),
        broad_predicate="TRUE",
        actionable_predicate="TRUE",
        route_filters={"panel": "data-operations"},
        tool_detail="Checked gold.source_readiness for live, demo, pending, stale, configured-empty, and blocked feed statuses.",
        specialist_agent="data_ops_agent",
        broad_label="Sources checked",
        actionable_label="Live source feeds",
        route_path="/admin-config",
    ),
}


def custom_workflow(segment_codes: Sequence[str], segment_mode: str) -> GrowthAgentWorkflowDef:
    if segment_mode not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="segment_mode must be 'any' or 'all'")
    invalid_codes = [str(code) for code in segment_codes if code not in SEGMENT_LABELS]
    if invalid_codes:
        raise HTTPException(status_code=422, detail="segment_codes must use reviewed segment codes")
    deduped = [code for idx, code in enumerate(segment_codes) if code not in segment_codes[:idx]]
    if not deduped:
        raise HTTPException(status_code=422, detail="segment_codes must include at least one reviewed segment")
    mode = segment_mode
    segment_label = _format_segment_labels(deduped, mode=mode)
    predicate = segment_predicate(deduped, mode)
    route_filters = (
        {
            "segment": deduped[0],
            "marketing_eligibility": "Eligible only",
        }
        if len(deduped) == 1
        else {
            "segment_codes": ",".join(deduped),
            "segment_mode": mode,
            "marketing_eligibility": "Eligible only",
        }
    )
    return GrowthAgentWorkflowDef(
        id=CUSTOM_WORKFLOW_ID,
        title=CUSTOM_WORKFLOW_TITLE,
        objective=f"Build a reviewed {segment_label} workflow from governed segment membership.",
        trigger_label=f"{segment_label} segment screen",
        action_label="Open eligible custom subset",
        source_assets=(BORROWER_360, LEAD_POPULATION, EVIDENCE_EVENTS),
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
        specialist_agent="campaign_agent",
    )


def segment_predicate(segment_codes: list[str], mode: str) -> str:
    joiner = " AND " if mode == "all" else " OR "
    clauses = [f"array_contains(b.segment_codes, '{code}')" for code in segment_codes]
    return "(" + joiner.join(clauses) + ")"


def build_growth_agent_route(filters: dict[str, str], *, path: str = "/lead-queue") -> str:
    query = urlencode(filters)
    return f"{path}?{query}" if query else path


def planned_workflow(payload: GrowthAgentPromptRunRequest) -> tuple[GrowthAgentWorkflowDef, str]:
    q = payload.prompt.lower()
    if payload.segment_codes:
        mode = "all" if payload.segment_mode == "all" else "any"
        return (
            custom_workflow(payload.segment_codes, mode),
            f"Custom reviewed segment workflow using {mode.upper()} semantics.",
        )
    if any(term in q for term in ("source", "fresh", "readiness", "stale data", "data ops", "refresh")):
        return WORKFLOWS["source_freshness_sentinel"], "Data operations lens selected the global source/freshness sentinel."
    if any(term in q for term in ("dossier", "borrower story", "customer 360", "borrower 360", "explain top")):
        return WORKFLOWS["borrower_dossier_review"], "Borrower dossier lens selected the dossier review workflow."
    detected_segments = _segments_from_prompt(q)
    if len(detected_segments) >= 2 and _requests_custom_segment_workflow(q):
        mode = "all" if _requests_all_segment_mode(q) else "any"
        return custom_workflow(detected_segments, mode), f"Campaign lens built a custom {mode.upper()} segment workflow."
    if any(term in q for term in ("heloc", "home equity line", "equity line")):
        return WORKFLOWS["high_equity_heloc_watch"], "Offer lens selected the high-equity HELOC watch."
    if len(detected_segments) >= 2:
        mode = "all" if _requests_all_segment_mode(q) else "any"
        return custom_workflow(detected_segments, mode), f"Campaign lens built a custom {mode.upper()} segment workflow."
    if (
        any(term in q for term in ("refi", "refinance", "rate spread", "economic incentive", "prime refinance"))
        and not any(term in q for term in ("listed", "listing", "for sale", "purchase", "heloc", "cash out", "cash-out", "home equity", "equity line"))
    ):
        return WORKFLOWS["daily_refi_brief"], "Structured data lens selected the daily refi opportunity brief."
    if any(term in q for term in ("capacity", "branch", "manager", "aging", "stale approved", "loan officer", "lo ")):
        return WORKFLOWS["branch_capacity_review"], "Campaign lens selected the branch-manager capacity review."
    if any(term in q for term in ("heloc", "cash out", "cash-out", "home equity", "equity line")):
        return WORKFLOWS["high_equity_heloc_watch"], "Offer lens selected the high-equity HELOC watch."
    if any(term in q for term in ("listed", "listing", "for sale", "purchase")):
        return WORKFLOWS["listing_watch"], "Campaign lens selected the listed-for-sale purchase watch."
    if any(term in q for term in ("competitor", "recapture", "retention", "current customer")):
        return WORKFLOWS["competitor_recapture_monitor"], "Compliance lens selected competitor recapture monitoring."
    return WORKFLOWS["daily_refi_brief"], "Structured data lens selected the daily refi opportunity brief."


_SEGMENT_PROMPT_TERMS: dict[str, str] = {
    "itm": "itm|in the money|refi|refinance|rate spread|economic incentive|prime refi",
    "listed": "listed|listing|for sale|purchase",
    "permit": "permit|heloc intent|heloc|home equity line",
    "investor": "investor|multi property|multi-property|owner link|portfolio owner",
    "equity": "equity|cash out|cash-out|high equity",
    "retention": "retention|recapture|current customer",
}
_ALL_SEGMENT_MODE_RE = re.compile(r"\b(?:both|all selected|intersection|and)\b")
_CUSTOM_SEGMENT_WORKFLOW_RE = re.compile(r"\b(?:custom|cohort|segment|segments|both|intersection)\b")


def _format_segment_labels(segment_codes: list[str], *, mode: str) -> str:
    labels = [SEGMENT_LABELS[code] for code in segment_codes]
    if len(labels) == 1:
        return labels[0]
    separator = " and " if mode == "all" else " or "
    return ", ".join(labels[:-1]) + separator + labels[-1]


def _segments_from_prompt(prompt: str) -> list[str]:
    found: list[str] = []
    for code, terms in _SEGMENT_PROMPT_TERMS.items():
        if any(term in prompt for term in terms.split("|")) and code not in found:
            found.append(code)
    return found


def _requests_all_segment_mode(prompt: str) -> bool:
    return bool(_ALL_SEGMENT_MODE_RE.search(prompt))


def _requests_custom_segment_workflow(prompt: str) -> bool:
    return bool(_CUSTOM_SEGMENT_WORKFLOW_RE.search(prompt))
