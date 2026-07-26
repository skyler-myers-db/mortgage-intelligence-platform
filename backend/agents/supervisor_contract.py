"""Immutable reviewed contract for the Module 0 managed Supervisor."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

RUNTIME_REPLACEMENT_SUFFIX = " [mip-agent-runtime]"
RUNTIME_REPLACEMENT_PREFIX = " [mip-agent-runtime-"
SUPERVISOR_DESCRIPTION = "Governed mortgage-growth supervisor for Module 0 lead generation."
SUPERVISOR_INSTRUCTIONS = (
    "Route borrower, segment, and source-readiness questions to the Mortgage Lead "
    "Intelligence Genie Space and reviewed Unity Catalog functions. Never expose raw "
    "PII, never send outreach, and always return a human-review handoff for action."
)


class SupervisorContractDrift(RuntimeError):
    """The live Supervisor exists but does not match the reviewed contract."""


def supervisor_tool_resource_is_exact(
    tool_type: str,
    actual: object,
    expected: object,
) -> bool:
    """Allow only the provider's redundant, equal Genie-space identifier alias."""

    if tool_type != "genie_space":
        return actual == expected
    if not isinstance(expected, Mapping) or set(expected) != {"id"}:
        return False
    expected_id = expected.get("id")
    if (
        not isinstance(expected_id, str)
        or not expected_id
        or expected_id.strip() != expected_id
        or not isinstance(actual, Mapping)
    ):
        return False
    if set(actual) == {"id"}:
        return actual.get("id") == expected_id
    return set(actual) == {"id", "space_id"} and (
        actual.get("id") == expected_id == actual.get("space_id")
    )


def supervisor_tool_specs(
    *, genie_space_id: str, catalog: str
) -> list[tuple[str, str, str, dict[str, Any]]]:
    return [
        (
            "mortgage_data_analyst",
            "genie_space",
            "Answers governed data questions over the Mortgage Lead Intelligence Genie Space.",
            {"genie_space": {"id": genie_space_id}},
        ),
        (
            "build_cohort",
            "uc_function",
            "Counts broad borrower cohorts from reviewed Module 0 UC function logic.",
            {"uc_function": {"name": f"{catalog}.gold.fn_build_cohort"}},
        ),
        (
            "segment_counts",
            "uc_function",
            "Reconciles broad cohorts to eligible Lead Queue counts.",
            {"uc_function": {"name": f"{catalog}.gold.fn_segment_counts"}},
        ),
        (
            "lead_queue_url",
            "uc_function",
            "Creates governed Lead Queue handoff URLs for human review.",
            {"uc_function": {"name": f"{catalog}.gold.fn_lead_queue_url"}},
        ),
    ]


def supervisor_contract_document(*, genie_space_id: str, catalog: str) -> dict[str, Any]:
    """Return the canonical, JSON-native Supervisor definition and tools."""

    tools = []
    for tool_id, tool_type, description, body in supervisor_tool_specs(
        genie_space_id=genie_space_id,
        catalog=catalog,
    ):
        tools.append(
            {
                "tool_id": tool_id,
                "tool_type": tool_type,
                "description": description,
                **body,
            }
        )
    return {
        "description": SUPERVISOR_DESCRIPTION,
        "instructions": SUPERVISOR_INSTRUCTIONS,
        "tools": tools,
        "examples": [],
    }


def canonical_supervisor_contract_json(*, genie_space_id: str, catalog: str) -> str:
    return json.dumps(
        supervisor_contract_document(genie_space_id=genie_space_id, catalog=catalog),
        sort_keys=True,
        separators=(",", ":"),
    )


def supervisor_contract_hash(*, genie_space_id: str, catalog: str) -> str:
    """Return the immutable digest used to name a green Supervisor candidate."""

    canonical = canonical_supervisor_contract_json(
        genie_space_id=genie_space_id,
        catalog=catalog,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def supervisor_replacement_name(
    display_name: str,
    *,
    genie_space_id: str,
    catalog: str,
) -> str:
    digest = supervisor_contract_hash(genie_space_id=genie_space_id, catalog=catalog)
    return f"{display_name}{RUNTIME_REPLACEMENT_PREFIX}{digest[:12]}]"
