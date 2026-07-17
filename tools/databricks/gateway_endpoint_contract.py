"""Canonical configuration and matching for the Gateway Responses endpoint."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from backend.agents.gateway_contract import (
    GATEWAY_BURST_SCALING_ENABLED,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_ROUTE_OPTIMIZED,
    GATEWAY_RUNTIME_RESOURCE_ENV,
    GATEWAY_SCALE_TO_ZERO_ENABLED,
    GATEWAY_STATIC_ENV,
    GATEWAY_TRAFFIC_PERCENTAGE,
    GATEWAY_WORKLOAD_SIZE,
    GATEWAY_WORKLOAD_TYPE,
)
from backend.agents.supervisor_contract import supervisor_contract_hash
from databricks.sdk.service.serving import (
    AiGatewayConfig,
    AiGatewayInferenceTableConfig,
    Route,
    ServedEntityInput,
    ServingModelWorkloadType,
    TrafficConfig,
)

_STATIC_ENV = GATEWAY_STATIC_ENV
_WORKLOAD_SIZE = GATEWAY_WORKLOAD_SIZE
_WORKLOAD_TYPE = ServingModelWorkloadType.CPU
assert _WORKLOAD_TYPE.value == GATEWAY_WORKLOAD_TYPE
_SCALE_TO_ZERO_ENABLED = GATEWAY_SCALE_TO_ZERO_ENABLED
_BURST_SCALING_ENABLED = GATEWAY_BURST_SCALING_ENABLED
_ROUTE_OPTIMIZED = GATEWAY_ROUTE_OPTIMIZED
_TRAFFIC_PERCENTAGE = GATEWAY_TRAFFIC_PERCENTAGE
_ENDPOINT_DESCRIPTION = GATEWAY_ENDPOINT_DESCRIPTION


def model_version_from_config(config: Any, *, model_name: str) -> int | None:
    """Return the version only for one unambiguous matching served entity."""

    entities = getattr(config, "served_entities", None) or []
    if len(entities) != 1:
        return None
    for entity in entities:
        if str(getattr(entity, "entity_name", "") or "") != model_name:
            continue
        raw = getattr(entity, "entity_version", None)
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return None
    return None


def current_model_version(details: Any, *, model_name: str) -> int | None:
    """Prefer a pending version when an endpoint update was interrupted."""

    pending = model_version_from_config(
        getattr(details, "pending_config", None),
        model_name=model_name,
    )
    if pending is not None:
        return pending
    return model_version_from_config(getattr(details, "config", None), model_name=model_name)


def proxy_config_matches(details: Any, *, entity: ServedEntityInput) -> bool:
    """Return whether the proxy core and traffic configuration is exact."""

    config = getattr(details, "config", None)
    if (
        config is None
        or getattr(config, "auto_capture_config", None) is not None
        or (getattr(config, "served_models", None) or [])
    ):
        return False
    entities = getattr(config, "served_entities", None) or []
    if len(entities) != 1:
        return False
    current = entities[0]
    scalar_fields = (
        "burst_scaling_enabled",
        "entity_name",
        "entity_version",
        "instance_profile_arn",
        "max_provisioned_concurrency",
        "max_provisioned_throughput",
        "min_provisioned_concurrency",
        "min_provisioned_throughput",
        "name",
        "provisioned_model_units",
        "scale_to_zero_enabled",
        "workload_size",
        "workload_type",
    )
    for field in scalar_fields:
        actual = getattr(current, field, None)
        expected = getattr(entity, field, None)
        actual = getattr(actual, "value", actual)
        expected = getattr(expected, "value", expected)
        if actual != expected:
            return False
    actual_environment = dict(getattr(current, "environment_vars", None) or {})
    expected_environment = dict(entity.environment_vars or {})
    unbound_actual = {
        key: value
        for key, value in actual_environment.items()
        if key not in GATEWAY_RUNTIME_RESOURCE_ENV
    }
    unbound_expected = {
        key: value
        for key, value in expected_environment.items()
        if key not in GATEWAY_RUNTIME_RESOURCE_ENV
    }
    if (
        unbound_actual != unbound_expected
        or set(actual_environment) - set(expected_environment) - GATEWAY_RUNTIME_RESOURCE_ENV
    ):
        return False
    if (
        getattr(current, "external_model", None) is not None
        or getattr(current, "foundation_model", None) is not None
    ):
        return False
    traffic = getattr(config, "traffic_config", None)
    routes = getattr(traffic, "routes", None) or []
    return (
        len(routes) == 1
        and str(getattr(routes[0], "served_entity_name", "") or "") == str(entity.name or "")
        and not str(getattr(routes[0], "served_model_name", "") or "")
        and int(getattr(routes[0], "traffic_percentage", 0) or 0) == _TRAFFIC_PERCENTAGE
    )


def served_entity(
    *,
    supervisor_id: str,
    upstream_endpoint: str,
    runtime_application_id: str,
    catalog: str,
    genie_space_id: str,
    model_name: str,
    model_version: int,
    experiment_id: str,
    resource_binding: Mapping[str, str] | None = None,
) -> tuple[ServedEntityInput, TrafficConfig]:
    """Build the only reviewed served-entity and traffic configuration."""

    served_name = f"mip-growth-supervisor-proxy-{model_version}"
    binding = dict(resource_binding or {})
    current_model_verify_key = str(
        binding.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY")
        if resource_binding is not None
        else os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "")
    ).strip()
    previous_model_verify_key = str(
        binding.get("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", "")
        if resource_binding is not None
        else ""
    ).strip()
    public_model_keys = {
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": current_model_verify_key,
        **(
            {
                "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY": previous_model_verify_key,
            }
            if previous_model_verify_key
            else {}
        ),
    }
    if resource_binding is not None and not current_model_verify_key:
        raise RuntimeError("Gateway model attestation public key is required")
    public_model_keys = {key: value for key, value in public_model_keys.items() if value}
    if set(binding) - GATEWAY_RUNTIME_RESOURCE_ENV:
        raise RuntimeError("Gateway runtime resource environment is invalid")
    environment = {
        **_STATIC_ENV,
        **public_model_keys,
        **binding,
        "MIP_UPSTREAM_SUPERVISOR_ID": supervisor_id,
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream_endpoint,
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": runtime_application_id,
        "MIP_SUPERVISOR_CATALOG": catalog,
        "MIP_SUPERVISOR_GENIE_SPACE_ID": genie_space_id,
        "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
            genie_space_id=genie_space_id,
            catalog=catalog,
        ),
        "MLFLOW_EXPERIMENT_ID": experiment_id,
    }
    entity = ServedEntityInput(
        burst_scaling_enabled=_BURST_SCALING_ENABLED,
        entity_name=model_name,
        entity_version=str(model_version),
        environment_vars=environment,
        name=served_name,
        scale_to_zero_enabled=_SCALE_TO_ZERO_ENABLED,
        workload_size=_WORKLOAD_SIZE,
        workload_type=_WORKLOAD_TYPE,
    )
    traffic = TrafficConfig(
        routes=[
            Route(
                served_entity_name=served_name,
                traffic_percentage=_TRAFFIC_PERCENTAGE,
            )
        ]
    )
    return entity, traffic


def gateway_config(*, catalog: str, schema: str, table_prefix: str) -> AiGatewayConfig:
    """Build the governed inference-table configuration."""

    return AiGatewayConfig(
        inference_table_config=AiGatewayInferenceTableConfig(
            enabled=True,
            catalog_name=catalog,
            schema_name=schema,
            table_name_prefix=table_prefix,
        )
    )


def gateway_matches(
    details: Any,
    *,
    catalog: str,
    schema: str,
    table_prefix: str,
) -> bool:
    """Return whether every readable AI Gateway field is exact."""

    gateway = getattr(details, "ai_gateway", None)
    if (
        gateway is None
        or getattr(gateway, "fallback_config", None) is not None
        or getattr(gateway, "guardrails", None) is not None
        or (getattr(gateway, "rate_limits", None) or [])
        or getattr(gateway, "usage_tracking_config", None) is not None
    ):
        return False
    inference = getattr(gateway, "inference_table_config", None)
    return (
        getattr(inference, "enabled", None) is True
        and str(getattr(inference, "catalog_name", "") or "") == catalog
        and str(getattr(inference, "schema_name", "") or "") == schema
        and str(getattr(inference, "table_name_prefix", "") or "") == table_prefix
    )


def endpoint_tags(details: Any) -> dict[str, str]:
    """Normalize the endpoint tag list for exact comparison."""

    tags = getattr(details, "tags", None) or []
    return {
        str(getattr(tag, "key", "") or ""): str(getattr(tag, "value", "") or "") for tag in tags
    }


def endpoint_tags_match(details: Any, *, expected: dict[str, str]) -> bool:
    """Reject extra, missing, duplicated, or drifted endpoint tags."""

    tags = getattr(details, "tags", None) or []
    return len(tags) == len(expected) and endpoint_tags(details) == expected


def endpoint_policy_matches(details: Any) -> bool:
    """Return whether optional endpoint policies remain disabled."""

    return (
        getattr(details, "route_optimized", None) is _ROUTE_OPTIMIZED
        and getattr(details, "budget_policy_id", None) is None
        and getattr(details, "email_notifications", None) is None
    )


def clear_deprecated_endpoint_rate_limits(workspace: Any, *, endpoint: str) -> None:
    """Authoritatively clear a legacy field omitted from endpoint GET responses."""

    try:
        response = workspace.serving_endpoints.put(endpoint, rate_limits=[])
    except Exception as exc:  # noqa: BLE001 - unsupported/inconclusive is a hard gate
        raise RuntimeError("Gateway deprecated endpoint rate-limit reconciliation failed") from exc
    if getattr(response, "rate_limits", None) != []:
        raise RuntimeError("Gateway deprecated endpoint rate-limit reconciliation was inconclusive")
