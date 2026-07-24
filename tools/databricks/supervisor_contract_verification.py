"""Reusable exact managed-Supervisor contract verification."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks.supervisor_agent_contract import (
    SupervisorContractDrift,
    supervisor_contract_document,
)


def assert_exact_supervisor_contract(
    supervisor_id: str,
    *,
    genie_space_id: str,
    catalog: str,
    run: Callable[[list[str]], Any],
    exact_tools: Callable[..., Any],
    expected_contract: dict[str, Any] | None = None,
) -> None:
    parent = f"supervisor-agents/{supervisor_id}"
    details = run(["supervisor-agents", "get-supervisor-agent", parent])
    if not isinstance(details, dict):
        raise SupervisorContractDrift(
            "Supervisor definition postflight returned an invalid payload"
        )
    contract = expected_contract or supervisor_contract_document(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    tools = contract.get("tools")
    if (
        set(contract) != {"description", "instructions", "tools", "examples"}
        or not isinstance(tools, list)
        or contract.get("examples") != []
    ):
        raise SupervisorContractDrift("stored Supervisor contract is invalid")
    specs: list[tuple[str, str, str, dict[str, Any]]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise SupervisorContractDrift("stored Supervisor tool contract is invalid")
        tool_id = str(tool.get("tool_id") or "")
        tool_type = str(tool.get("tool_type") or "")
        description = str(tool.get("description") or "")
        resource = tool.get(tool_type)
        if not tool_id or not tool_type or not description or not isinstance(resource, dict):
            raise SupervisorContractDrift("stored Supervisor tool contract is invalid")
        specs.append((tool_id, tool_type, description, {tool_type: resource}))
    if details.get("description") != contract["description"]:
        raise SupervisorContractDrift("Supervisor description drifted from the reviewed contract")
    if details.get("instructions") != contract["instructions"]:
        raise SupervisorContractDrift("Supervisor instructions drifted from the reviewed contract")
    exact_tools(
        supervisor_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
        specs=specs,
    )
