"""Runtime identity proof for the governed Databricks Supervisor proxy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from backend.agents.gateway_contract import (
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_UPSTREAM_TAG,
    gateway_proxy_source_hash,
)
from backend.config.settings import Settings


@dataclass(frozen=True)
class VerifiedSupervisorRuntime:
    endpoint: str
    supervisor_id: str
    supervisor_endpoint: str
    model_name: str
    source_hash: str
    task: str


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _enum_text(value: object) -> str:
    return str(_field(value, "value") or value or "").strip()


def _endpoint_tags(details: object) -> dict[str, str]:
    tags = _field(details, "tags") or []
    if isinstance(tags, Mapping):
        return {str(key): str(value) for key, value in tags.items()}
    return {
        str(_field(tag, "key") or ""): str(_field(tag, "value") or "")
        for tag in tags
    }


def _gateway_inference_binding(details: object) -> tuple[bool, str, str, str]:
    gateway = _field(details, "ai_gateway")
    inference = _field(gateway, "inference_table_config")
    return (
        _field(inference, "enabled") is True,
        str(_field(inference, "catalog_name") or ""),
        str(_field(inference, "schema_name") or ""),
        str(_field(inference, "table_name_prefix") or ""),
    )


def verify_supervisor_runtime(
    client: Any,
    settings: Settings,
) -> tuple[VerifiedSupervisorRuntime | None, str | None]:
    """Bind managed identity, reviewed proxy bytes, upstream, and Gateway config.

    Endpoint names and feature flags are only configuration. Every generation
    path calls this helper immediately before inference and labels output as
    Supervisor-generated only if the live workspace proves the complete chain:
    managed Supervisor identity -> reviewed single-model proxy -> exact
    AI Gateway inference-table binding.
    """

    if not settings.mip_agent_orchestrator:
        return None, "orchestrator_disabled"
    endpoint = (settings.mip_agent_serving_endpoint or "").strip()
    gateway_endpoint = (settings.mip_ai_gateway_endpoint or "").strip()
    supervisor_endpoint = (settings.mip_agent_supervisor_endpoint or "").strip()
    supervisor_id = (settings.mip_agent_supervisor_id or "").strip()
    model_name = settings.mip_agent_gateway_model.strip()
    model_version = settings.mip_agent_gateway_model_version
    inference_table = (settings.mip_ai_gateway_inference_table or "").strip()
    if (
        not endpoint
        or not gateway_endpoint
        or not supervisor_endpoint
        or not supervisor_id
        or not model_name
        or model_version is None
    ):
        return None, "orchestrator_not_configured"
    if gateway_endpoint != endpoint:
        return None, "gateway_product_endpoint_mismatch"
    if endpoint == supervisor_endpoint:
        return None, "gateway_endpoint_recurses_to_itself"
    table_parts = inference_table.split(".")
    if len(table_parts) != 3 or not all(table_parts):
        return None, "gateway_inference_table_not_configured"

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
        if metadata_endpoint != supervisor_endpoint:
            return None, "supervisor_endpoint_mismatch"

        serving_endpoints = getattr(client, "serving_endpoints", None)
        get_endpoint = getattr(serving_endpoints, "get", None)
        if not callable(get_endpoint):
            return None, "supervisor_serving_client_unavailable"
        details = get_endpoint(endpoint)
        ready = _enum_text(_field(_field(details, "state"), "ready")).upper()
        if ready != "READY":
            return None, f"gateway_endpoint_not_ready:{ready or 'UNKNOWN'}"
        if _field(details, "pending_config") is not None:
            return None, "gateway_endpoint_update_pending"
        task_value = _enum_text(_field(details, "task"))
        canonical_task = task_value.lower().replace("-", "_").replace("/", "_")
        if canonical_task != "agent_v1_responses":
            return None, f"gateway_task_not_agent:{task_value or 'UNKNOWN'}"

        config = _field(details, "config")
        entities = _field(config, "served_entities") or []
        if len(entities) != 1:
            return None, "gateway_proxy_entity_count_mismatch"
        entity = entities[0]
        if str(_field(entity, "entity_name") or "") != model_name:
            return None, "gateway_proxy_model_mismatch"
        try:
            actual_model_version = int(str(_field(entity, "entity_version") or ""))
        except ValueError:
            return None, "gateway_proxy_model_version_invalid"
        if actual_model_version != model_version:
            return None, "gateway_proxy_model_version_mismatch"
        environment = _field(entity, "environment_vars") or {}
        if not isinstance(environment, Mapping):
            return None, "gateway_proxy_environment_invalid"
        if str(environment.get("MIP_UPSTREAM_SUPERVISOR_ENDPOINT") or "") != supervisor_endpoint:
            return None, "gateway_proxy_upstream_mismatch"

        source_hash = gateway_proxy_source_hash(upstream_endpoint=supervisor_endpoint)
        tags = _endpoint_tags(details)
        if tags.get(GATEWAY_PROXY_SOURCE_HASH_TAG) != source_hash:
            return None, "gateway_proxy_source_mismatch"
        if tags.get(GATEWAY_UPSTREAM_TAG) != supervisor_endpoint:
            return None, "gateway_proxy_tag_upstream_mismatch"

        actual_gateway = _gateway_inference_binding(details)
        expected_gateway = (True, table_parts[0], table_parts[1], table_parts[2])
        if actual_gateway != expected_gateway:
            return None, "gateway_inference_table_mismatch"

        return (
            VerifiedSupervisorRuntime(
                endpoint=endpoint,
                supervisor_id=supervisor_id,
                supervisor_endpoint=supervisor_endpoint,
                model_name=model_name,
                source_hash=source_hash,
                task="agent/v1/responses",
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - callers degrade honestly on workspace errors
        return None, f"supervisor_identity_probe_failed:{type(exc).__name__}"
