"""Authenticated transition verifier for signed proxyless Gateway v5 rollback records."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mlflow import MlflowClient

from backend.agents.gateway_contract import (
    GATEWAY_BURST_SCALING_ENABLED,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_ROUTE_OPTIMIZED,
    GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG,
    GATEWAY_RUNTIME_RESOURCE_ENV,
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    GATEWAY_SCALE_TO_ZERO_ENABLED,
    GATEWAY_STATIC_ENV,
    GATEWAY_TRAFFIC_PERCENTAGE,
    GATEWAY_UPSTREAM_TAG,
    GATEWAY_WORKLOAD_SIZE,
    GATEWAY_WORKLOAD_TYPE,
    decode_gateway_attestation_base64,
    reviewed_workspace_https_origin,
)
from backend.agents.gateway_live_resource_contract import (
    _experiment_acl_contract,
    _verify_model_attestation,
)
from backend.agents.gateway_provider_shape import (
    field,
    legacy_served_model_matches,
    provider_bool_matches,
    route_targets_served_entity,
    usage_tracking_is_disabled,
)
from backend.agents.supervisor_contract import (
    canonical_supervisor_contract_json,
    supervisor_contract_hash,
)

LEGACY_GATEWAY_RESOURCE_FIELDS = frozenset(
    {
        "proof_version",
        "catalog",
        "genie_space_id",
        "runtime_application_id",
        "workspace_host",
        "supervisor_canonical_name",
        "supervisor_display_name",
        "supervisor_contract_json",
        "supervisor_contract_sha256",
        "supervisor_id",
        "supervisor_creator",
        "supervisor_endpoint",
        "supervisor_endpoint_id",
        "supervisor_endpoint_creator",
        "gateway_endpoint",
        "gateway_endpoint_id",
        "gateway_endpoint_creator",
        "gateway_endpoint_description",
        "gateway_endpoint_task",
        "gateway_endpoint_route_optimized",
        "gateway_endpoint_budget_policy",
        "gateway_endpoint_email_notifications",
        "gateway_endpoint_deprecated_rate_limits",
        "gateway_source_hash",
        "gateway_resource_hash",
        "gateway_model_family",
        "gateway_model_name",
        "gateway_model_version",
        "gateway_model_source",
        "gateway_model_owner",
        "gateway_experiment_base",
        "gateway_experiment_acl_json",
        "gateway_experiment_acl_sha256",
        "gateway_experiment_name",
        "gateway_experiment_id",
        "gateway_experiment_owner",
        "gateway_inference_table_family",
        "gateway_inference_table",
    }
)
PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION = "gateway-runtime-resource-proof-v2"
_PROXY_RESOURCE_FIELDS = frozenset(
    {
        "proxy_caller_application_id",
        "proxy_caller_credential_id",
        "proxy_caller_secret_reference",
    }
)
PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS = LEGACY_GATEWAY_RESOURCE_FIELDS - {"workspace_host"}
PRIOR_V2_GATEWAY_RESOURCE_FIELDS = PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS | _PROXY_RESOURCE_FIELDS
_IMMUTABLE_MODEL_SOURCE = re.compile(r"models:/m-[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _enum_text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def legacy_gateway_resource_digest(contract: Mapping[str, str]) -> str:
    """Digest only the exact pre-proxy-credential resource schema."""

    if set(contract) != LEGACY_GATEWAY_RESOURCE_FIELDS or any(
        not isinstance(value, str) or not value for value in contract.values()
    ):
        raise ValueError("legacy Gateway resource contract fields are invalid")
    if contract.get("proof_version") != GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION:
        raise ValueError("legacy Gateway resource contract version is invalid")
    if contract.get("workspace_host") != reviewed_workspace_https_origin(
        contract.get("workspace_host", "")
    ):
        raise ValueError("legacy Gateway resource workspace host is invalid")
    canonical = json.dumps(dict(contract), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def validated_legacy_gateway_resources(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise RuntimeError("legacy App rollback Gateway resource contract is invalid")
    resources = dict(value)
    if resources.get("proof_version") == PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION:
        return validated_prior_v2_gateway_resources(resources, proxy_aware=False)
    digest = resources.pop("resource_digest", "")
    try:
        actual = legacy_gateway_resource_digest(resources)
    except ValueError as exc:
        raise RuntimeError("legacy App rollback Gateway resource contract is invalid") from exc
    if not digest or digest != actual:
        raise RuntimeError("legacy App rollback Gateway resource digest is invalid")
    return {**resources, "resource_digest": digest}


def prior_v2_gateway_resource_digest(contract: Mapping[str, str]) -> str:
    """Digest only one exact historical v2 resource schema."""

    fields = set(contract)
    if fields not in {
        PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS,
        PRIOR_V2_GATEWAY_RESOURCE_FIELDS,
    } or any(not isinstance(value, str) or not value for value in contract.values()):
        raise ValueError("prior v2 Gateway resource contract fields are invalid")
    if contract.get("proof_version") != PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION:
        raise ValueError("prior v2 Gateway resource contract version is invalid")
    canonical = json.dumps(dict(contract), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def validated_prior_v2_gateway_resources(
    value: object,
    *,
    proxy_aware: bool | None = None,
) -> dict[str, str]:
    """Validate prior v2 bytes without promoting them to the current proof schema."""

    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise RuntimeError("prior v2 Gateway resource contract is invalid")
    resources = dict(value)
    digest = resources.pop("resource_digest", "")
    expected_fields = (
        PRIOR_V2_GATEWAY_RESOURCE_FIELDS
        if proxy_aware is True
        else PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS
        if proxy_aware is False
        else None
    )
    if expected_fields is not None and set(resources) != expected_fields:
        raise RuntimeError("prior v2 Gateway resource contract is invalid")
    try:
        actual = prior_v2_gateway_resource_digest(resources)
    except ValueError as exc:
        raise RuntimeError("prior v2 Gateway resource contract is invalid") from exc
    if not digest or digest != actual:
        raise RuntimeError("prior v2 Gateway resource contract digest is invalid")
    return {**resources, "resource_digest": digest}


def _resource_hash(
    contract: Mapping[str, str],
    *,
    attestation_verify_key: str,
    include_workspace_host: bool,
) -> str:
    inference = contract["gateway_inference_table_family"].split(".", 2)
    if len(inference) != 3 or not attestation_verify_key:
        raise RuntimeError("legacy Gateway allocation scope is invalid")
    allocation: dict[str, object] = {
        "source_hash": contract["gateway_source_hash"],
        "supervisor_id": contract["supervisor_id"],
        "supervisor_endpoint_id": contract["supervisor_endpoint_id"],
        "runtime_application_id": contract["runtime_application_id"],
        "model_name": contract["gateway_model_family"],
        "experiment_name": contract["gateway_experiment_base"],
        "inference_schema": inference[1],
        "inference_table_prefix": inference[2],
        "attestation_verify_key": attestation_verify_key,
        "environment": dict(GATEWAY_STATIC_ENV),
        "workload_size": GATEWAY_WORKLOAD_SIZE,
        "workload_type": GATEWAY_WORKLOAD_TYPE,
        "scale_to_zero_enabled": GATEWAY_SCALE_TO_ZERO_ENABLED,
        "burst_scaling_enabled": GATEWAY_BURST_SCALING_ENABLED,
        "endpoint_policy": {
            "budget_policy_id": None,
            "email_notifications": None,
            "rate_limits": None,
            "route_optimized": GATEWAY_ROUTE_OPTIMIZED,
        },
        "description": GATEWAY_ENDPOINT_DESCRIPTION,
        "traffic": {
            "route_field": "served_entity_name",
            "traffic_percentage": GATEWAY_TRAFFIC_PERCENTAGE,
        },
    }
    if include_workspace_host:
        allocation["workspace_host"] = contract["workspace_host"]
    if _PROXY_RESOURCE_FIELDS.issubset(contract):
        allocation.update(
            proxy_caller_application_id=contract["proxy_caller_application_id"],
            proxy_caller_credential_id=contract["proxy_caller_credential_id"],
            proxy_caller_secret_reference=contract["proxy_caller_secret_reference"],
        )
    canonical = json.dumps(allocation, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _legacy_resource_hash(
    contract: Mapping[str, str],
    *,
    attestation_verify_key: str,
) -> str:
    return _resource_hash(
        contract,
        attestation_verify_key=attestation_verify_key,
        include_workspace_host=True,
    )


def _verified_resource_environment(
    entity: object,
    *,
    contract: Mapping[str, str],
    prior_v2: bool = False,
) -> dict[str, str]:
    raw = field(entity, "environment_vars") or {}
    if not isinstance(raw, Mapping):
        raise RuntimeError("legacy Gateway served environment is invalid")
    environment = {
        str(key): str(value)
        for key, value in raw.items()
        if str(key) in GATEWAY_RUNTIME_RESOURCE_ENV
    }
    contract_json = json.dumps(dict(contract), sort_keys=True, separators=(",", ":"))
    if environment.get("MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON") != contract_json:
        raise RuntimeError("legacy Gateway served resource binding drifted")
    digest = (
        prior_v2_gateway_resource_digest(contract)
        if prior_v2
        else legacy_gateway_resource_digest(contract)
    )
    if environment.get("MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256") != digest:
        raise RuntimeError("legacy Gateway served resource binding drifted")
    trusted = {
        os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip(),
        os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", "").strip(),
    } - {""}
    record_key = environment.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    if record_key not in trusted:
        raise RuntimeError("legacy Gateway served resource signer is not trusted")
    try:
        public = Ed25519PublicKey.from_public_bytes(
            decode_gateway_attestation_base64(record_key, length=32)
        )
        signature = decode_gateway_attestation_base64(
            environment.get("MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE", ""),
            length=64,
        )
        public.verify(
            signature,
            GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG.encode() + b"\0" + contract_json.encode(),
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("legacy Gateway served resource signature is invalid") from exc
    return environment


def _assert_endpoint(
    workspace: Any,
    *,
    contract: Mapping[str, str],
    prior_v2: bool = False,
) -> dict[str, str]:
    supervisor = workspace.serving_endpoints.get(contract["supervisor_endpoint"])
    if (
        str(field(supervisor, "id") or "").strip() != contract["supervisor_endpoint_id"]
        or str(field(supervisor, "name") or "").strip() != contract["supervisor_endpoint"]
        or str(field(supervisor, "creator") or "").strip()
        != contract["supervisor_endpoint_creator"]
        or contract["supervisor_endpoint_creator"] != contract["runtime_application_id"]
        or _enum_text(field(supervisor, "task")).lower() != "agent/v1/responses"
        or field(supervisor, "pending_config") is not None
        or _enum_text(field(field(supervisor, "state"), "ready")).upper() != "READY"
    ):
        raise RuntimeError("legacy managed Supervisor endpoint drifted")
    details = workspace.serving_endpoints.get(contract["gateway_endpoint"])
    if (
        str(field(details, "id") or "").strip() != contract["gateway_endpoint_id"]
        or str(field(details, "creator") or "").strip() != contract["gateway_endpoint_creator"]
        or str(field(details, "description") or "") != contract["gateway_endpoint_description"]
        or _enum_text(field(details, "task")) != contract["gateway_endpoint_task"]
        or str(bool(field(details, "route_optimized"))).lower()
        != contract["gateway_endpoint_route_optimized"]
        or field(details, "pending_config") is not None
        or _enum_text(field(field(details, "state"), "ready")).upper() != "READY"
    ):
        raise RuntimeError("legacy Gateway endpoint identity drifted")
    if (
        field(details, "budget_policy_id") is not None
        or field(details, "email_notifications") is not None
        or (field(details, "rate_limits") or [])
    ):
        raise RuntimeError("legacy Gateway endpoint policy drifted")
    config = field(details, "config")
    entities = field(config, "served_entities") or []
    routes = field(field(config, "traffic_config"), "routes") or []
    if field(config, "auto_capture_config") is not None or len(entities) != 1 or len(routes) != 1:
        raise RuntimeError("legacy Gateway served entity drifted")
    entity = entities[0]
    legacy_models = field(config, "served_models") or []
    if len(legacy_models) > 1 or (
        legacy_models and not legacy_served_model_matches(legacy_models[0], entity)
    ):
        raise RuntimeError("legacy Gateway served-model alias drifted")
    environment = _verified_resource_environment(
        entity,
        contract=contract,
        prior_v2=prior_v2,
    )
    served_name = f"mip-growth-supervisor-proxy-{contract['gateway_model_version']}"
    expected_environment = {
        **GATEWAY_STATIC_ENV,
        **environment,
        "MIP_UPSTREAM_SUPERVISOR_ID": contract["supervisor_id"],
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": contract["supervisor_endpoint"],
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": contract["runtime_application_id"],
        "MIP_SUPERVISOR_CATALOG": contract["catalog"],
        "MIP_SUPERVISOR_GENIE_SPACE_ID": contract["genie_space_id"],
        "MIP_SUPERVISOR_CONTRACT_SHA256": contract["supervisor_contract_sha256"],
        "MLFLOW_EXPERIMENT_ID": contract["gateway_experiment_id"],
    }
    if not prior_v2:
        expected_environment["DATABRICKS_HOST"] = contract["workspace_host"]
    if _PROXY_RESOURCE_FIELDS.issubset(contract):
        expected_environment.update(
            MIP_UPSTREAM_PROXY_CLIENT_ID=contract["proxy_caller_application_id"],
            MIP_UPSTREAM_PROXY_CREDENTIAL_ID=contract["proxy_caller_credential_id"],
            MIP_UPSTREAM_PROXY_CLIENT_SECRET=contract["proxy_caller_secret_reference"],
        )
    if dict(field(entity, "environment_vars") or {}) != expected_environment:
        raise RuntimeError("legacy Gateway served environment drifted")
    if (
        str(field(entity, "entity_name") or "") != contract["gateway_model_name"]
        or str(field(entity, "entity_version") or "") != contract["gateway_model_version"]
        or str(field(entity, "name") or "") != served_name
        or str(field(entity, "workload_size") or "") != GATEWAY_WORKLOAD_SIZE
        or _enum_text(field(entity, "workload_type")) != GATEWAY_WORKLOAD_TYPE
        or field(entity, "scale_to_zero_enabled") is not GATEWAY_SCALE_TO_ZERO_ENABLED
        or not provider_bool_matches(
            field(entity, "burst_scaling_enabled"), GATEWAY_BURST_SCALING_ENABLED
        )
        or not route_targets_served_entity(routes[0], served_name)
        or field(routes[0], "traffic_percentage") != GATEWAY_TRAFFIC_PERCENTAGE
    ):
        raise RuntimeError("legacy Gateway served configuration drifted")
    tags = field(details, "tags") or []
    actual_tags = (
        {str(key): str(value) for key, value in tags.items()}
        if isinstance(tags, Mapping)
        else {str(field(item, "key")): str(field(item, "value")) for item in tags}
    )
    if actual_tags != {
        GATEWAY_PROXY_SOURCE_HASH_TAG: contract["gateway_source_hash"],
        GATEWAY_UPSTREAM_TAG: contract["supervisor_endpoint"],
    }:
        raise RuntimeError("legacy Gateway endpoint tags drifted")
    gateway = field(details, "ai_gateway")
    inference = field(gateway, "inference_table_config")
    expected_table = contract["gateway_inference_table"].split(".", 2)
    if (
        len(expected_table) != 3
        or field(inference, "enabled") is not True
        or [
            str(field(inference, "catalog_name") or ""),
            str(field(inference, "schema_name") or ""),
            str(field(inference, "table_name_prefix") or ""),
        ]
        != expected_table
        or field(gateway, "fallback_config") is not None
        or field(gateway, "guardrails") is not None
        or (field(gateway, "rate_limits") or [])
        or not usage_tracking_is_disabled(field(gateway, "usage_tracking_config"))
    ):
        raise RuntimeError("legacy Gateway inference-table policy drifted")
    return environment


def _assert_live_resources(
    workspace: Any,
    *,
    resources: dict[str, str],
    prior_v2: bool,
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
) -> dict[str, str]:
    contract = {key: value for key, value in resources.items() if key != "resource_digest"}
    try:
        authenticated_workspace_host = reviewed_workspace_https_origin(
            str(field(field(workspace, "config"), "host") or "")
        )
    except ValueError as exc:
        raise RuntimeError("authenticated legacy Gateway workspace host is invalid") from exc
    if not prior_v2 and contract["workspace_host"] != authenticated_workspace_host:
        raise RuntimeError("legacy Gateway workspace host binding drifted")
    runtime_id = contract["runtime_application_id"]
    metadata = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(contract['supervisor_id'], safe='')}",
    )
    if not isinstance(metadata, Mapping) or (
        str(metadata.get("supervisor_agent_id") or "") != contract["supervisor_id"]
        or str(metadata.get("endpoint_name") or "") != contract["supervisor_endpoint"]
        or str(metadata.get("creator") or "") != contract["supervisor_creator"]
        or contract["supervisor_creator"] != runtime_id
    ):
        raise RuntimeError("legacy managed Supervisor identity drifted")
    expected_supervisor = canonical_supervisor_contract_json(
        genie_space_id=contract["genie_space_id"],
        catalog=contract["catalog"],
    )
    if contract["supervisor_contract_json"] != expected_supervisor or contract[
        "supervisor_contract_sha256"
    ] != supervisor_contract_hash(
        genie_space_id=contract["genie_space_id"],
        catalog=contract["catalog"],
    ):
        raise RuntimeError("legacy managed Supervisor contract drifted")
    environment = _assert_endpoint(
        workspace,
        contract=contract,
        prior_v2=prior_v2,
    )
    resource_hash = _resource_hash(
        contract,
        attestation_verify_key=environment["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"],
        include_workspace_host=not prior_v2,
    )
    inference = contract["gateway_inference_table_family"].split(".", 2)
    if (
        resource_hash != contract["gateway_resource_hash"]
        or contract["gateway_model_name"]
        != f"{contract['gateway_model_family']}_{resource_hash[:12]}"
        or contract["gateway_experiment_name"]
        != f"/Users/{runtime_id}/{contract['gateway_experiment_base']}-{resource_hash[:12]}"
        or contract["gateway_inference_table"]
        != f"{contract['catalog']}.{inference[1]}.{inference[2]}_{resource_hash[:12]}"
    ):
        raise RuntimeError("legacy Gateway immutable naming drifted")
    registry = model_registry or MlflowClient(
        tracking_uri="databricks", registry_uri="databricks-uc"
    )
    model = workspace.registered_models.get(contract["gateway_model_name"])
    version = registry.get_model_version(
        contract["gateway_model_name"], contract["gateway_model_version"]
    )
    model_source = str(field(version, "source") or "").strip()
    if (
        str(field(model, "owner") or "") != runtime_id
        or contract["gateway_model_owner"] != runtime_id
        or model_source != contract["gateway_model_source"]
        or _IMMUTABLE_MODEL_SOURCE.fullmatch(model_source) is None
    ):
        raise RuntimeError("legacy Gateway model identity drifted")
    model_contract = {
        "full_name": contract["gateway_model_name"],
        "model_source": model_source,
        "source_hash": contract["gateway_source_hash"],
        "supervisor_id": contract["supervisor_id"],
        "supervisor_endpoint_id": contract["supervisor_endpoint_id"],
        "upstream_endpoint": contract["supervisor_endpoint"],
        "runtime_application_id": runtime_id,
        "model_family": contract["gateway_model_family"],
        "experiment_base": contract["gateway_experiment_base"],
        "catalog": contract["catalog"],
        "genie_space_id": contract["genie_space_id"],
        "inference_schema": inference[1],
        "inference_table_prefix": inference[2],
    }
    tags = {str(key): str(value) for key, value in dict(field(version, "tags") or {}).items()}
    _verify_model_attestation(
        tags,
        expected=model_contract,
        current_verify_key=environment["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"],
        previous_verify_key=environment.get(
            "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", ""
        ),
    )
    experiments = tracking_client or MlflowClient(tracking_uri="databricks")
    experiment = experiments.get_experiment(contract["gateway_experiment_id"])
    experiment_owner = str((field(experiment, "tags") or {}).get("mlflow.ownerEmail") or "").strip()
    acl_json = _experiment_acl_contract(workspace, experiment_id=contract["gateway_experiment_id"])
    if (
        str(field(experiment, "experiment_id") or "") != contract["gateway_experiment_id"]
        or str(field(experiment, "name") or "") != contract["gateway_experiment_name"]
        or experiment_owner != runtime_id
        or contract["gateway_experiment_owner"] != runtime_id
        or acl_json != contract["gateway_experiment_acl_json"]
        or hashlib.sha256(acl_json.encode()).hexdigest()
        != contract["gateway_experiment_acl_sha256"]
    ):
        raise RuntimeError("legacy Gateway experiment identity drifted")
    return resources


def assert_live_prior_v2_gateway_resources(
    workspace: Any,
    *,
    expected: Mapping[str, str],
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
) -> dict[str, str]:
    """Authenticate prior v2 bytes and live resources only for transition/retirement."""

    resources = validated_prior_v2_gateway_resources(dict(expected))
    return _assert_live_resources(
        workspace,
        resources=resources,
        prior_v2=True,
        model_registry=model_registry,
        tracking_client=tracking_client,
    )


def assert_live_legacy_gateway_resources(
    workspace: Any,
    *,
    expected: Mapping[str, str],
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
) -> dict[str, str]:
    """Re-prove a signed rollback Gateway without widening its proof schema."""

    if expected.get("proof_version") == PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION:
        return assert_live_prior_v2_gateway_resources(
            workspace,
            expected=expected,
            model_registry=model_registry,
            tracking_client=tracking_client,
        )
    resources = validated_legacy_gateway_resources(dict(expected))
    return _assert_live_resources(
        workspace,
        resources=resources,
        prior_v2=False,
        model_registry=model_registry,
        tracking_client=tracking_client,
    )
