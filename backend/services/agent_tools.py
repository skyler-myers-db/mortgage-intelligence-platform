"""Reviewed deterministic tool registry for the Mortgage Growth Agent.

The Growth Agent may *name* tools in its trace, but it must never invent a
tool at runtime or execute arbitrary SQL from natural language. This registry
is the reviewed allowlist used by the API and tests: every displayed tool has a
fixed source asset, specialist ownership, and no outbound side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.schemas.growth_agent import GrowthAgentSpecialist
from backend.services.audit_metadata_value_policy import validate_source_assets
from backend.services.databricks_sql_helpers import qualify

AgentToolName = Literal[
    "fn_build_cohort",
    "fn_segment_counts",
    "fn_lead_queue_url",
    "fn_offer_compare",
    "fn_borrower_dossier_evidence",
    "fn_property_loan_lookup",
    "fn_source_readiness",
    "source_readiness_status_rollup",
    "open_admin_data_operations",
]

_BORROWER_360 = qualify("gold", "borrower_360")
_BORROWER_DOSSIER = qualify("gold", "borrower_dossier")
_EVIDENCE_EVENTS = qualify("gold", "evidence_events")
_ADDRESS_LOOKUP = qualify("gold", "address_lookup")
_SOURCE_READINESS = qualify("gold", "source_readiness")


@dataclass(frozen=True)
class AgentTool:
    name: AgentToolName
    label: str
    source_asset: str
    specialists: tuple[GrowthAgentSpecialist, ...]
    description: str
    requires_human_review: bool = False
    writes_state: bool = False

    @property
    def deterministic(self) -> bool:
        return True


_ALL_GROWTH_SPECIALISTS: tuple[GrowthAgentSpecialist, ...] = (
    "structured_data_agent",
    "borrower_dossier_agent",
    "offer_agent",
    "compliance_agent",
    "campaign_agent",
    "data_ops_agent",
)

_TOOLS: dict[AgentToolName, AgentTool] = {
    "fn_build_cohort": AgentTool(
        name="fn_build_cohort",
        label="Build borrower cohort",
        source_asset=_BORROWER_360,
        specialists=_ALL_GROWTH_SPECIALISTS,
        description="Counts the broad borrower population from governed gold borrower signals.",
    ),
    "fn_segment_counts": AgentTool(
        name="fn_segment_counts",
        label="Apply actionability gates",
        source_asset=_BORROWER_360,
        specialists=(
            "structured_data_agent",
            "borrower_dossier_agent",
            "offer_agent",
            "compliance_agent",
            "campaign_agent",
        ),
        description="Reconciles a broad screen to marketing-eligible, opt-in Lead Queue rows.",
    ),
    "fn_lead_queue_url": AgentTool(
        name="fn_lead_queue_url",
        label="Prepare Lead Queue handoff",
        source_asset=_EVIDENCE_EVENTS,
        specialists=(
            "structured_data_agent",
            "borrower_dossier_agent",
            "offer_agent",
            "compliance_agent",
            "campaign_agent",
        ),
        description="Creates a reviewed route for human review; never sends outreach.",
        requires_human_review=True,
    ),
    "fn_offer_compare": AgentTool(
        name="fn_offer_compare",
        label="Compare offer fit",
        source_asset=_BORROWER_360,
        specialists=("offer_agent",),
        description="Compares eligible borrower signals to deterministic offer rules.",
    ),
    "fn_borrower_dossier_evidence": AgentTool(
        name="fn_borrower_dossier_evidence",
        label="Summarize borrower evidence",
        source_asset=_BORROWER_DOSSIER,
        specialists=("borrower_dossier_agent",),
        description="Reads the pre-joined borrower dossier evidence surface without exposing identities.",
    ),
    "fn_property_loan_lookup": AgentTool(
        name="fn_property_loan_lookup",
        label="Look up property loan by address",
        source_asset=_ADDRESS_LOOKUP,
        specialists=("borrower_dossier_agent",),
        description="Resolves an address + ZIP to a masked CLIP and loan facts via the governed address_lookup spine. Share-scoped exact match; no fuzzy resolution, no raw address exposure.",
    ),
    "fn_source_readiness": AgentTool(
        name="fn_source_readiness",
        label="Read source readiness",
        source_asset=_SOURCE_READINESS,
        specialists=("data_ops_agent",),
        description="Reads the governed source-readiness ledger.",
    ),
    "source_readiness_status_rollup": AgentTool(
        name="source_readiness_status_rollup",
        label="Classify source health",
        source_asset=_SOURCE_READINESS,
        specialists=("data_ops_agent",),
        description="Classifies live, stale, pending, configured-empty, and blocked feeds.",
    ),
    "open_admin_data_operations": AgentTool(
        name="open_admin_data_operations",
        label="Prepare Data Ops handoff",
        source_asset=_SOURCE_READINESS,
        specialists=("data_ops_agent",),
        description="Creates a reviewed Admin/Data Operations route; does not refresh data.",
        requires_human_review=True,
    ),
}

for tool in _TOOLS.values():
    validate_source_assets([tool.source_asset])


def get_agent_tool(name: str) -> AgentTool:
    try:
        return _TOOLS[name]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"unknown reviewed growth-agent tool: {name}") from exc


def registered_agent_tools() -> tuple[AgentTool, ...]:
    return tuple(_TOOLS.values())


def registered_agent_tool_names() -> set[str]:
    return set(_TOOLS)


def assert_tool_allowed_for_specialist(name: str, specialist: GrowthAgentSpecialist) -> AgentTool:
    tool = get_agent_tool(name)
    if specialist not in tool.specialists:
        raise ValueError(f"{name} is not reviewed for {specialist}")
    return tool
