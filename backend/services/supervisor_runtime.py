"""Runtime identity proof for the governed Databricks Supervisor proxy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from backend.agents.gateway_contract import (
    GATEWAY_BURST_SCALING_ENABLED,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_ROUTE_OPTIMIZED,
    GATEWAY_RUNTIME_RESOURCE_ENV,
    GATEWAY_SCALE_TO_ZERO_ENABLED,
    GATEWAY_STATIC_ENV,
    GATEWAY_TRAFFIC_PERCENTAGE,
    GATEWAY_UPSTREAM_TAG,
    GATEWAY_WORKLOAD_SIZE,
    GATEWAY_WORKLOAD_TYPE,
    gateway_proxy_source_hash,
    gateway_runtime_binding_hash,
    verified_gateway_runtime_resource_environment,
)
from backend.agents.supervisor_contract import supervisor_contract_hash
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
    return {str(_field(tag, "key") or ""): str(_field(tag, "value") or "") for tag in tags}


def _gateway_inference_binding(details: object) -> tuple[bool, str, str, str]:
    gateway = _field(details, "ai_gateway")
    inference = _field(gateway, "inference_table_config")
    return (
        _field(inference, "enabled") is True,
        str(_field(inference, "catalog_name") or ""),
        str(_field(inference, "schema_name") or ""),
        str(_field(inference, "table_name_prefix") or ""),
    )


def _runtime_creator_matches(value: object, application_id: str) -> bool:
    return str(value or "").strip() == application_id


def _resource_environment(settings: Settings) -> dict[str, str]:
    return {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": (
            settings.mip_expected_agent_gateway_resource_contract_json or ""
        ).strip(),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": (
            settings.mip_expected_agent_gateway_resource_sha256 or ""
        ).strip(),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": (
            settings.mip_expected_agent_gateway_resource_signature or ""
        ).strip(),
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": (
            settings.mip_gateway_model_attestation_verify_key or ""
        ).strip(),
    }


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
    experiment_id = (settings.mip_ai_gateway_experiment_id or "").strip()
    experiment_name = (settings.mip_ai_gateway_experiment_name or "").strip()
    model_source = (settings.mip_ai_gateway_agent_model_source or "").strip()
    resource_digest = (settings.mip_expected_agent_gateway_resource_sha256 or "").strip()
    expected_binding = (settings.mip_expected_agent_gateway_binding_sha256 or "").strip()
    runtime_application_id = (settings.mip_agent_runtime_client_id or "").strip()
    if (
        not endpoint
        or not gateway_endpoint
        or not supervisor_endpoint
        or not supervisor_id
        or not model_name
        or model_version is None
        or not runtime_application_id
        or not experiment_id
        or not experiment_name
        or not model_source
        or re.fullmatch(r"[0-9a-f]{64}", resource_digest) is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_binding) is None
    ):
        return None, "orchestrator_not_configured"
    resource_environment = _resource_environment(settings)
    try:
        expected_resources = verified_gateway_runtime_resource_environment(resource_environment)
    except (RuntimeError, ValueError):
        return None, "gateway_resource_contract_invalid"
    expected_scope = {
        "catalog": settings.mip_default_catalog,
        "genie_space_id": settings.genie_space_id or "",
        "runtime_application_id": runtime_application_id,
        "supervisor_id": supervisor_id,
        "supervisor_endpoint": supervisor_endpoint,
        "gateway_endpoint": endpoint,
        "gateway_model_name": model_name,
        "gateway_model_version": str(model_version),
        "gateway_model_source": model_source,
        "gateway_experiment_name": experiment_name,
        "gateway_experiment_id": experiment_id,
        "gateway_inference_table": inference_table,
    }
    if any(expected_resources.get(key) != value for key, value in expected_scope.items()):
        return None, "gateway_resource_contract_scope_mismatch"
    if gateway_endpoint != endpoint:
        return None, "gateway_product_endpoint_mismatch"
    if endpoint == supervisor_endpoint:
        return None, "gateway_endpoint_recurses_to_itself"
    table_parts = inference_table.split(".")
    if len(table_parts) != 3 or not all(table_parts):
        return None, "gateway_inference_table_not_configured"
    if (
        gateway_runtime_binding_hash(
            endpoint=endpoint,
            supervisor_id=supervisor_id,
            upstream_endpoint=supervisor_endpoint,
            runtime_application_id=runtime_application_id,
            model_name=model_name,
            model_version=model_version,
            inference_table=inference_table,
        )
        != expected_binding
    ):
        return None, "gateway_runtime_binding_digest_mismatch"

    try:
        serving_endpoints = getattr(client, "serving_endpoints", None)
        get_endpoint = getattr(serving_endpoints, "get", None)
        if not callable(get_endpoint):
            return None, "supervisor_serving_client_unavailable"
        details = get_endpoint(endpoint)
        actual_endpoint_id = str(_field(details, "id") or "").strip()
        if actual_endpoint_id != expected_resources["gateway_endpoint_id"]:
            return None, "gateway_endpoint_id_mismatch"
        if not _runtime_creator_matches(_field(details, "creator"), runtime_application_id):
            return None, "gateway_endpoint_creator_mismatch"
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
        expected_environment = {
            **GATEWAY_STATIC_ENV,
            "MIP_UPSTREAM_SUPERVISOR_ID": supervisor_id,
            "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": supervisor_endpoint,
            "MIP_UPSTREAM_SUPERVISOR_CREATOR": runtime_application_id,
            "MIP_SUPERVISOR_CATALOG": settings.mip_default_catalog,
            "MIP_SUPERVISOR_GENIE_SPACE_ID": settings.genie_space_id or "",
            "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
                genie_space_id=settings.genie_space_id or "",
                catalog=settings.mip_default_catalog,
            ),
            "MLFLOW_EXPERIMENT_ID": experiment_id,
        }
        unbound_environment = {
            str(key): str(value)
            for key, value in environment.items()
            if str(key) not in GATEWAY_RUNTIME_RESOURCE_ENV
        }
        bound_resource_environment = {
            str(key): str(value)
            for key, value in environment.items()
            if str(key) in GATEWAY_RUNTIME_RESOURCE_ENV
        }
        expected_resource_environment = {
            key: value for key, value in resource_environment.items() if value
        }
        if bound_resource_environment != expected_resource_environment:
            return None, "gateway_proxy_resource_environment_mismatch"
        if unbound_environment != expected_environment:
            if (
                str(environment.get("MIP_UPSTREAM_SUPERVISOR_ENDPOINT") or "")
                != supervisor_endpoint
            ):
                return None, "gateway_proxy_upstream_mismatch"
            return None, "gateway_proxy_environment_mismatch"
        expected_entity_name = f"mip-growth-supervisor-proxy-{model_version}"
        if str(_field(entity, "name") or "") != expected_entity_name:
            return None, "gateway_proxy_served_entity_name_mismatch"
        if str(_field(entity, "workload_size") or "") != GATEWAY_WORKLOAD_SIZE:
            return None, "gateway_proxy_workload_size_mismatch"
        if _enum_text(_field(entity, "workload_type")).upper() != GATEWAY_WORKLOAD_TYPE:
            return None, "gateway_proxy_workload_type_mismatch"
        if _field(entity, "scale_to_zero_enabled") is not GATEWAY_SCALE_TO_ZERO_ENABLED:
            return None, "gateway_proxy_scale_to_zero_mismatch"
        if _field(entity, "burst_scaling_enabled") is not GATEWAY_BURST_SCALING_ENABLED:
            return None, "gateway_proxy_burst_scaling_mismatch"
        traffic = _field(config, "traffic_config")
        routes = _field(traffic, "routes") or []
        if len(routes) != 1:
            return None, "gateway_proxy_traffic_mismatch"
        route = routes[0]
        if (
            str(_field(route, "served_entity_name") or "") != expected_entity_name
            or _field(route, "traffic_percentage") != GATEWAY_TRAFFIC_PERCENTAGE
        ):
            return None, "gateway_proxy_traffic_mismatch"
        if str(_field(details, "description") or "") != GATEWAY_ENDPOINT_DESCRIPTION:
            return None, "gateway_endpoint_description_mismatch"
        if bool(_field(details, "route_optimized")) is not GATEWAY_ROUTE_OPTIMIZED:
            return None, "gateway_endpoint_route_optimization_mismatch"
        if (
            _field(details, "budget_policy_id") is not None
            or _field(details, "email_notifications") is not None
            or (_field(details, "rate_limits") or [])
        ):
            return None, "gateway_endpoint_policy_mismatch"

        source_hash = gateway_proxy_source_hash(
            upstream_endpoint=supervisor_endpoint,
            catalog=settings.mip_default_catalog,
            genie_space_id=settings.genie_space_id or "",
        )
        tags = _endpoint_tags(details)
        if tags.get(GATEWAY_PROXY_SOURCE_HASH_TAG) != source_hash:
            return None, "gateway_proxy_source_mismatch"
        if tags.get(GATEWAY_UPSTREAM_TAG) != supervisor_endpoint:
            return None, "gateway_proxy_tag_upstream_mismatch"

        actual_gateway = _gateway_inference_binding(details)
        expected_gateway = (True, table_parts[0], table_parts[1], table_parts[2])
        if actual_gateway != expected_gateway:
            return None, "gateway_inference_table_mismatch"
        gateway = _field(details, "ai_gateway")
        if (
            _field(gateway, "fallback_config") is not None
            or _field(gateway, "guardrails") is not None
            or (_field(gateway, "rate_limits") or [])
            or _field(gateway, "usage_tracking_config") is not None
        ):
            return None, "gateway_policy_mismatch"

        supervisor_details = get_endpoint(supervisor_endpoint)
        actual_supervisor_endpoint_id = str(_field(supervisor_details, "id") or "").strip()
        if actual_supervisor_endpoint_id != expected_resources["supervisor_endpoint_id"]:
            return None, "supervisor_endpoint_id_mismatch"
        if not _runtime_creator_matches(
            _field(supervisor_details, "creator"), runtime_application_id
        ):
            return None, "supervisor_endpoint_creator_mismatch"

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
