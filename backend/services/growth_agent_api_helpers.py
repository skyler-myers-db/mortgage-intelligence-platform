"""Growth Agent API helpers kept out of the router module."""

from __future__ import annotations

import re

from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.capabilities import (
    Capability,
    CapabilityStatus,
    LiveCapabilityMap,
    probe_capabilities,
)
from backend.services.state_footprint import US_STATE_NAME_BY_CODE

_STATE_CODE_PATTERN = re.compile(
    r"\b(?:in|for|state|states|scope)\s+("
    + "|".join(re.escape(code) for code in sorted(US_STATE_NAME_BY_CODE))
    + r")\b",
    flags=re.IGNORECASE,
)
_STATE_NAME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (code, re.compile(rf"\b{re.escape(name.lower())}\b"))
    for code, name in sorted(US_STATE_NAME_BY_CODE.items())
)
_PUBLIC_CAPABILITY_AVAILABLE_DETAILS = {
    "genie_conversation_api": "Live Genie Conversation API probe passed for this workspace.",
    "certified_metric_views": "Live certified metric-view probes passed for this workspace.",
    "uc_function_tools": "Live reviewed SQL tool probes passed for this workspace.",
    "agent_eval": "Live MLflow Agent Evaluation passed for this deployment.",
    "agent_orchestrator": "Live Supervisor Agent endpoint probe passed for this workspace.",
    "ai_gateway": (
        "Live AI Gateway endpoint probe passed; inference logging proof is present as "
        "either an exact current inference row or an asynchronous recent deployment-scoped row."
    ),
    "lakebase_sync": "Live Lakebase synced-table probes passed for MIP-owned serving tables.",
}
_PUBLIC_CAPABILITY_CONFIGURED_DETAILS = {
    "genie_conversation_api": "Genie Conversation API dependencies are configured; live proof is required before claiming this row.",
    "certified_metric_views": "Certified metric-view SQL contracts are bundled; live proof is required before claiming this row.",
    "uc_function_tools": "Reviewed SQL tool contracts are bundled; live proof is required before claiming this row.",
    "agent_eval": "Agent Evaluation assets are configured; a live passing evaluation is required before claiming this row.",
    "agent_orchestrator": "Supervisor Agent configuration is present; live endpoint proof is required before claiming this row.",
    "ai_gateway": "AI Gateway configuration is present; live endpoint and inference-log proof are required before claiming this row.",
    "lakebase_sync": "Lakebase synced-table configuration is present; live row-count proof is required before claiming this row.",
}


def public_capability_rows(
    *,
    live_statuses: LiveCapabilityMap | None = None,
) -> list[dict[str, object]]:
    """Return Growth Agent-safe capability rows.

    The admin capability endpoint may expose raw proof details such as endpoint
    names, table paths, run IDs, and deployed SHAs. The Growth Agent route is a
    normal user surface, so it may show whether a capability is claimable but
    must keep proof identifiers behind the admin boundary.
    """

    rows: list[dict[str, object]] = []
    for capability in probe_capabilities(live_statuses=live_statuses):
        row = capability.to_dict()
        row["detail"] = _public_capability_detail(capability)
        rows.append(row)
    return rows


def payload_with_prompt_state_scope(payload: GrowthAgentPromptRunRequest) -> GrowthAgentPromptRunRequest:
    if payload.states:
        return payload
    states = states_from_prompt(payload.prompt)
    if not states:
        return payload
    return payload.model_copy(update={"states": states})


def states_from_prompt(prompt: str) -> list[str]:
    found: list[str] = []
    lowered = prompt.lower()
    for code, pattern in _STATE_NAME_PATTERNS:
        if pattern.search(lowered):
            found.append(code)
    for match in _STATE_CODE_PATTERN.finditer(prompt):
        code = match.group(1).upper()
        if code in US_STATE_NAME_BY_CODE:
            found.append(code)
    out: list[str] = []
    for code in found:
        if code not in out:
            out.append(code)
    return out[:20]


def _public_capability_detail(capability: Capability) -> str:
    if capability.status == CapabilityStatus.AVAILABLE and capability.claimable:
        return _PUBLIC_CAPABILITY_AVAILABLE_DETAILS.get(
            capability.key,
            "Live capability proof passed for this workspace.",
        )
    if capability.status == CapabilityStatus.CONFIGURED:
        return _PUBLIC_CAPABILITY_CONFIGURED_DETAILS.get(
            capability.key,
            "Configured for this workspace; live proof is required before claiming this row.",
        )
    if capability.status == CapabilityStatus.NOT_PROVISIONED:
        return "Not provisioned for this workspace."
    if capability.status == CapabilityStatus.PREVIEW_MIRROR:
        return "Roadmap pattern only; not claimed as an active integration."
    return "Hidden until enabled."
