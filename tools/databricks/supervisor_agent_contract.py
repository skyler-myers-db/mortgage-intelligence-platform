"""Compatibility exports for the shared managed Supervisor contract."""

from backend.agents.supervisor_contract import (
    RUNTIME_REPLACEMENT_PREFIX,
    RUNTIME_REPLACEMENT_SUFFIX,
    SUPERVISOR_DESCRIPTION,
    SUPERVISOR_INSTRUCTIONS,
    SupervisorContractDrift,
    canonical_supervisor_contract_json,
    supervisor_contract_document,
    supervisor_contract_hash,
    supervisor_replacement_name,
    supervisor_tool_specs,
)

__all__ = [
    "RUNTIME_REPLACEMENT_PREFIX",
    "RUNTIME_REPLACEMENT_SUFFIX",
    "SUPERVISOR_DESCRIPTION",
    "SUPERVISOR_INSTRUCTIONS",
    "SupervisorContractDrift",
    "canonical_supervisor_contract_json",
    "supervisor_contract_document",
    "supervisor_contract_hash",
    "supervisor_replacement_name",
    "supervisor_tool_specs",
]
