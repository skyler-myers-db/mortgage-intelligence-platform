"""Immutable resource identity reads for agent-runtime cutover."""

from __future__ import annotations

from typing import Any

from tools.databricks.cutover_supervisor_inventory import (
    supervisor_by_id_direct,
)
from tools.databricks.provision_agentic_resources import _supervisor_agents


def agent_by_id(supervisor_id: str) -> dict[str, Any] | None:
    matches = [
        row
        for row in _supervisor_agents()
        if str(row.get("supervisor_agent_id") or "") == supervisor_id
    ]
    if len(matches) > 1:
        raise RuntimeError(f"duplicate Supervisor immutable ID {supervisor_id!r}")
    return matches[0] if matches else None


def retirement_supervisor_by_id(
    workspace: Any,
    supervisor_id: str,
) -> dict[str, Any] | None:
    """Resolve destructive retirement state through the immutable GET API."""

    return supervisor_by_id_direct(workspace, supervisor_id)


def endpoint_identity(workspace: Any, endpoint: str) -> tuple[str, str]:
    details = workspace.serving_endpoints.get(endpoint)
    endpoint_id = str(getattr(details, "id", None) or "").strip()
    creator = str(getattr(details, "creator", None) or "").strip()
    if not endpoint_id or not creator:
        raise RuntimeError("serving endpoint has no immutable id or creator")
    return endpoint_id, creator
