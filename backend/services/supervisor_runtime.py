"""Runtime identity proof for the configured Databricks Supervisor Agent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from backend.config.settings import Settings


@dataclass(frozen=True)
class VerifiedSupervisorRuntime:
    endpoint: str
    supervisor_id: str
    task: str


def verify_supervisor_runtime(
    client: Any,
    settings: Settings,
) -> tuple[VerifiedSupervisorRuntime | None, str | None]:
    """Verify id -> endpoint metadata and an Agent Responses-ready endpoint.

    Feature flags and endpoint names are configuration, not proof. Every model
    generation path calls this helper immediately before inference so output is
    labelled Supervisor-generated only when the workspace confirms the exact
    Supervisor identity and serving endpoint mapping.
    """

    if not settings.mip_agent_orchestrator:
        return None, "orchestrator_disabled"
    endpoint = (settings.mip_agent_serving_endpoint or "").strip()
    supervisor_id = (settings.mip_agent_supervisor_id or "").strip()
    if not endpoint or not supervisor_id:
        return None, "orchestrator_not_configured"
    try:
        api_client = getattr(client, "api_client", None)
        request = getattr(api_client, "do", None)
        if not callable(request):
            return None, "supervisor_metadata_client_unavailable"
        metadata_path = f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}"
        metadata = request("GET", metadata_path)
        if not isinstance(metadata, Mapping):
            return None, "supervisor_metadata_invalid"
        metadata_id = str(metadata.get("supervisor_agent_id") or "").strip()
        metadata_endpoint = str(metadata.get("endpoint_name") or "").strip()
        if metadata_id != supervisor_id:
            return None, "supervisor_identity_mismatch"
        if metadata_endpoint != endpoint:
            return None, "supervisor_endpoint_mismatch"
        serving_endpoints = getattr(client, "serving_endpoints", None)
        get_endpoint = getattr(serving_endpoints, "get", None)
        if not callable(get_endpoint):
            return None, "supervisor_serving_client_unavailable"
        details = get_endpoint(endpoint)
        ready_raw = getattr(getattr(details, "state", None), "ready", None)
        ready = str(getattr(ready_raw, "value", ready_raw) or "").upper()
        if ready != "READY":
            return None, f"supervisor_endpoint_not_ready:{ready or 'UNKNOWN'}"
        task_raw = getattr(details, "task", None)
        task_value = str(getattr(task_raw, "value", task_raw) or "").strip()
        canonical_task = task_value.lower().replace("-", "_").replace("/", "_")
        if canonical_task != "agent_v1_responses":
            return None, f"supervisor_task_not_agent:{task_value or 'UNKNOWN'}"
        return VerifiedSupervisorRuntime(endpoint, supervisor_id, "agent/v1/responses"), None
    except Exception as exc:  # noqa: BLE001 - callers degrade honestly on workspace errors
        return None, f"supervisor_identity_probe_failed:{type(exc).__name__}"
