"""Reviewed deterministic tool registry for the Mortgage Growth Agent.

The Growth Agent may *name* tools in its trace, but it must never invent a
tool at runtime or execute arbitrary SQL from natural language. This registry
is the reviewed allowlist used by the API and tests: every displayed tool has a
fixed source asset, specialist ownership, and no outbound side effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.schemas.growth_agent import GrowthAgentSpecialist
from backend.schemas.usps import is_usps_state_code
from backend.services.audit_metadata_value_policy import validate_source_assets
from backend.services.databricks_sql_helpers import qualify
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD

AgentToolParamKind = Literal[
    "state_list",
    "segment_list",
    "segment_mode",
    "int_range",
    "address_line",
    "zip5",
]

_REVIEWED_SEGMENT_CODES: frozenset[str] = frozenset(
    {"itm", "listed", "permit", "investor", "equity", "retention"}
)
_STATE_RE = re.compile(r"^[A-Z]{2}$")
_ZIP5_RE = re.compile(r"^[0-9]{5}$")


class AgentToolParamError(ValueError):
    """Raised when a composed plan step supplies params a tool does not accept."""


@dataclass(frozen=True)
class AgentToolParam:
    """Lightweight, reviewed param spec for a composable agent tool.

    The spec is deliberately narrow: every kind maps to a deterministic,
    non-PII-shaped validator so a model-composed plan can only ever hand the
    executor governed, bounded values. Unknown param keys and out-of-domain
    values are rejected, never silently coerced.
    """

    name: str
    kind: AgentToolParamKind
    required: bool = False
    min_value: int | None = None
    max_value: int | None = None
    description: str = ""

    def spec_summary(self) -> str:
        req = "required" if self.required else "optional"
        if self.kind == "int_range":
            bounds = f"{self.min_value}..{self.max_value}"
            return f"{self.name} (int {bounds}, {req}): {self.description}"
        return f"{self.name} ({self.kind}, {req}): {self.description}"

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
    # Composed-plan exposure. False keeps a tool OUT of the planner catalog
    # and out of validated plans while staying fully available to specialists
    # and the direct API surface. Use for tools whose params could carry PII
    # a reviewed objective can never legitimately supply (external audit
    # 2026-07-08: fn_property_loan_lookup's address_line was a composed-plan
    # PII egress path — objectives are validated address-free, so any address
    # in a plan is hallucinated or injected, never legitimate).
    planner_exposed: bool = True
    params: tuple[AgentToolParam, ...] = field(default_factory=tuple)

    @property
    def deterministic(self) -> bool:
        return True

    @property
    def gates_run(self) -> bool:
        """True when a plan must stop for human approval at this tool."""

        return self.requires_human_review or self.writes_state

    def primary_specialist(self) -> GrowthAgentSpecialist:
        return self.specialists[0]

    def validate_params(self, raw: Any) -> dict[str, Any]:
        """Validate a model-supplied params object against this tool's spec.

        Rejects unknown keys, missing required params, and out-of-domain
        values. Returns a cleaned, deterministic dict safe to pass to the
        executor. Never coerces PII-shaped free text through.
        """

        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise AgentToolParamError(f"{self.name} params must be an object")
        allowed = {spec.name: spec for spec in self.params}
        unknown = set(map(str, raw.keys())) - set(allowed)
        if unknown:
            raise AgentToolParamError(
                f"{self.name} does not accept params: {', '.join(sorted(unknown))}"
            )
        cleaned: dict[str, Any] = {}
        for spec in self.params:
            if spec.name not in raw:
                if spec.required:
                    raise AgentToolParamError(f"{self.name} requires param {spec.name}")
                continue
            cleaned[spec.name] = _validate_param_value(self.name, spec, raw[spec.name])
        return cleaned


def _validate_param_value(tool_name: str, spec: AgentToolParam, value: Any) -> Any:
    if spec.kind == "state_list":
        return _clean_state_list(tool_name, value)
    if spec.kind == "segment_list":
        return _clean_segment_list(tool_name, value)
    if spec.kind == "segment_mode":
        mode = str(value).strip().lower()
        if mode not in {"any", "all"}:
            raise AgentToolParamError(f"{tool_name}.{spec.name} must be 'any' or 'all'")
        return mode
    if spec.kind == "int_range":
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise AgentToolParamError(f"{tool_name}.{spec.name} must be an integer") from exc
        if spec.min_value is not None and parsed < spec.min_value:
            raise AgentToolParamError(f"{tool_name}.{spec.name} is below the reviewed minimum")
        if spec.max_value is not None and parsed > spec.max_value:
            raise AgentToolParamError(f"{tool_name}.{spec.name} is above the reviewed maximum")
        return parsed
    if spec.kind == "zip5":
        digits = re.sub(r"[^0-9]", "", str(value))[:5]
        if not _ZIP5_RE.fullmatch(digits):
            raise AgentToolParamError(f"{tool_name}.{spec.name} must be a 5-digit ZIP")
        return digits
    if spec.kind == "address_line":
        text = str(value).strip()
        if not text or len(text) > 120:
            raise AgentToolParamError(f"{tool_name}.{spec.name} must be a bounded address line")
        return text
    raise AgentToolParamError(f"{tool_name}.{spec.name} has an unsupported param kind")


def _clean_state_list(tool_name: str, value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        raise AgentToolParamError(f"{tool_name} states must be a list of USPS codes")
    out: list[str] = []
    for item in value:
        state = str(item).strip().upper()
        if not state:
            continue
        if not _STATE_RE.fullmatch(state) or not is_usps_state_code(state):
            raise AgentToolParamError(f"{tool_name} states must be valid USPS codes")
        if state not in out:
            out.append(state)
    return out[:20]


def _clean_segment_list(tool_name: str, value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        raise AgentToolParamError(f"{tool_name} segment_codes must be a list")
    out: list[str] = []
    for item in value:
        code = str(item).strip().lower()
        if code not in _REVIEWED_SEGMENT_CODES:
            raise AgentToolParamError(f"{tool_name} segment_codes must use reviewed segments")
        if code not in out:
            out.append(code)
    if not out:
        raise AgentToolParamError(f"{tool_name} segment_codes must include a reviewed segment")
    return out[:6]


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
        params=(
            AgentToolParam(
                name="states",
                kind="state_list",
                description="Optional USPS state codes to scope the cohort.",
            ),
        ),
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
        params=(
            AgentToolParam(
                name="segment_codes",
                kind="segment_list",
                description="Reviewed segment codes to gate on (itm, listed, permit, investor, equity, retention).",
            ),
            AgentToolParam(
                name="segment_mode",
                kind="segment_mode",
                description="ANY (union) or ALL (intersection) over the segment codes.",
            ),
            AgentToolParam(name="states", kind="state_list", description="Optional USPS state scope."),
        ),
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
        description="Creates a reviewed route for human review; never sends outreach. Requires human approval.",
        requires_human_review=True,
        params=(
            AgentToolParam(
                name="segment_codes",
                kind="segment_list",
                description="Reviewed segment codes the handoff route should open.",
            ),
            AgentToolParam(name="states", kind="state_list", description="Optional USPS state scope."),
        ),
    ),
    "fn_offer_compare": AgentTool(
        name="fn_offer_compare",
        label="Compare offer fit",
        source_asset=_BORROWER_360,
        specialists=("offer_agent",),
        description="Compares eligible borrower signals to deterministic offer rules.",
        params=(
            AgentToolParam(name="states", kind="state_list", description="Optional USPS state scope."),
        ),
    ),
    "fn_borrower_dossier_evidence": AgentTool(
        name="fn_borrower_dossier_evidence",
        label="Summarize borrower evidence",
        source_asset=_BORROWER_DOSSIER,
        specialists=("borrower_dossier_agent",),
        description="Reads the pre-joined borrower dossier evidence surface for aggregate review.",
        params=(
            AgentToolParam(
                name="min_opportunity_score",
                kind="int_range",
                min_value=50,
                max_value=100,
                description=f"Minimum opportunity score for the dossier screen (default {HIGH_OPPORTUNITY_THRESHOLD}).",
            ),
            AgentToolParam(name="states", kind="state_list", description="Optional USPS state scope."),
        ),
    ),
    "fn_property_loan_lookup": AgentTool(
        name="fn_property_loan_lookup",
        label="Look up property loan by address",
        source_asset=_ADDRESS_LOOKUP,
        specialists=("borrower_dossier_agent",),
        planner_exposed=False,
        description=(
            "Resolves an address + ZIP to a masked CLIP and loan facts via the governed "
            "address_lookup spine. Share-scoped exact match; no fuzzy resolution, no raw "
            "address exposure. Use ONLY when the objective names a specific property address."
        ),
        params=(
            AgentToolParam(
                name="address_line",
                kind="address_line",
                required=True,
                description="Street address line for the property to resolve.",
            ),
            AgentToolParam(
                name="zip5",
                kind="zip5",
                required=True,
                description="5-digit ZIP code for the property.",
            ),
        ),
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
        description="Creates a reviewed Admin/Data Operations route; does not refresh data. Requires human approval.",
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


def tool_catalog_for_planner() -> str:
    """Render the reviewed tool registry for the plan-composer prompt.

    Presents each tool's name, one-line description, whether it gates the run for
    human approval, and its param spec. This is the ONLY tool surface the planner
    model is allowed to compose from; any tool it names outside this list is
    rejected by the composer, never executed.
    """

    lines: list[str] = []
    for tool in _TOOLS.values():
        if not tool.planner_exposed:
            continue
        gate = " [REQUIRES HUMAN APPROVAL — stops the run]" if tool.gates_run else ""
        params = "; ".join(spec.spec_summary() for spec in tool.params) or "no params"
        lines.append(f"- {tool.name}{gate}: {tool.description} Params: {params}")
    return "\n".join(lines)
