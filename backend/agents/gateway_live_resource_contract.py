"""Live fail-closed verifier for the signed Gateway runtime resource contract."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mlflow import MlflowClient

try:
    from backend.agents.gateway_contract import (
        GATEWAY_BURST_SCALING_ENABLED,
        GATEWAY_ENDPOINT_DESCRIPTION,
        GATEWAY_MODEL_CONTRACT_FIELDS,
        GATEWAY_PROXY_SOURCE_HASH_TAG,
        GATEWAY_ROUTE_OPTIMIZED,
        GATEWAY_RUNTIME_RESOURCE_ENV,
        GATEWAY_SCALE_TO_ZERO_ENABLED,
        GATEWAY_STATIC_ENV,
        GATEWAY_TRAFFIC_PERCENTAGE,
        GATEWAY_UPSTREAM_TAG,
        GATEWAY_WORKLOAD_SIZE,
        GATEWAY_WORKLOAD_TYPE,
        decode_gateway_attestation_base64,
        gateway_model_version_tags,
        gateway_proxy_source_hash,
        gateway_resource_allocation_hash,
        verified_gateway_runtime_resource_environment,
    )
    from backend.agents.supervisor_contract import (
        canonical_supervisor_contract_json,
        supervisor_contract_hash,
    )
    from backend.services.ai_gateway_proof_attestation import (
        AI_GATEWAY_PROOF_ATTESTATION_ALG,
    )
except ModuleNotFoundError:  # MLflow may place backend/ directly on sys.path.
    from agents.gateway_contract import (  # type: ignore[no-redef]
        GATEWAY_BURST_SCALING_ENABLED,
        GATEWAY_ENDPOINT_DESCRIPTION,
        GATEWAY_MODEL_CONTRACT_FIELDS,
        GATEWAY_PROXY_SOURCE_HASH_TAG,
        GATEWAY_ROUTE_OPTIMIZED,
        GATEWAY_RUNTIME_RESOURCE_ENV,
        GATEWAY_SCALE_TO_ZERO_ENABLED,
        GATEWAY_STATIC_ENV,
        GATEWAY_TRAFFIC_PERCENTAGE,
        GATEWAY_UPSTREAM_TAG,
        GATEWAY_WORKLOAD_SIZE,
        GATEWAY_WORKLOAD_TYPE,
        decode_gateway_attestation_base64,
        gateway_model_version_tags,
        gateway_proxy_source_hash,
        gateway_resource_allocation_hash,
        verified_gateway_runtime_resource_environment,
    )
    from agents.supervisor_contract import (  # type: ignore[no-redef]
        canonical_supervisor_contract_json,
        supervisor_contract_hash,
    )
    from services.ai_gateway_proof_attestation import (  # type: ignore[no-redef]
        AI_GATEWAY_PROOF_ATTESTATION_ALG,
    )

_IMMUTABLE_MODEL_SOURCE = re.compile(r"models:/m-[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_MODEL_CONTRACT_FIELDS = GATEWAY_MODEL_CONTRACT_FIELDS


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: object) -> str:
    return str(_field(value, "value") or value or "").strip()


def _mapping(value: object, *, resource: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        converted = as_dict()
        if isinstance(converted, Mapping):
            return converted
    raise RuntimeError(f"{resource} contract is invalid")


def _decode(value: str, *, length: int) -> bytes:
    try:
        return decode_gateway_attestation_base64(value, length=length)
    except RuntimeError as exc:
        raise RuntimeError("Gateway model attestation is invalid") from exc


def _model_payload(contract: Mapping[str, str]) -> bytes:
    payload = {**contract, "version": 3}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return b"mip-gateway-model-contract-v3\0" + canonical


def _verify_model_attestation(
    tags: Mapping[str, str],
    *,
    expected: dict[str, str],
    current_verify_key: str,
    previous_verify_key: str,
) -> None:
    record = gateway_model_version_tags(tags)
    contract = dict(record.contract)
    if set(contract) != _MODEL_CONTRACT_FIELDS or contract != expected:
        raise RuntimeError("Gateway model contract attestation identity is invalid")
    record_key = record.verify_key.strip()
    if record.algorithm != AI_GATEWAY_PROOF_ATTESTATION_ALG or record_key not in {
        current_verify_key,
        previous_verify_key,
    } - {""}:
        raise RuntimeError("Gateway model contract attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(record_key, length=32))
        public.verify(
            _decode(record.signature, length=64),
            _model_payload(expected),
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("Gateway model contract attestation signature is invalid") from exc


def _endpoint_tags(details: object) -> dict[str, str]:
    raw = _field(details, "tags") or []
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    tags = {str(_field(item, "key") or ""): str(_field(item, "value") or "") for item in raw}
    if len(tags) != len(raw):
        raise RuntimeError("Gateway endpoint tags are duplicated")
    return tags


def _experiment_acl_contract(workspace: Any, *, experiment_id: str) -> str:
    response = workspace.api_client.do(
        "GET",
        f"/api/2.0/permissions/experiments/{quote(experiment_id, safe='')}",
    )
    document = _mapping(response, resource="Gateway experiment ACL")
    entries = document.get("access_control_list")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("Gateway experiment ACL contract is invalid")
    normalized: list[dict[str, Any]] = []
    for raw in entries:
        entry = _mapping(raw, resource="Gateway experiment ACL")
        principals = [
            ("service_principal", str(entry.get("service_principal_name") or "").strip()),
            ("user", str(entry.get("user_name") or "").strip()),
            ("group", str(entry.get("group_name") or "").strip()),
        ]
        principals = [(kind, name) for kind, name in principals if name]
        permissions = entry.get("all_permissions")
        if len(principals) != 1 or not isinstance(permissions, list) or len(permissions) != 1:
            raise RuntimeError("Gateway experiment ACL contract is invalid")
        permission = _mapping(permissions[0], resource="Gateway experiment ACL")
        inherited_from = permission.get("inherited_from_object") or []
        if not isinstance(inherited_from, list):
            raise RuntimeError("Gateway experiment ACL contract is invalid")
        normalized.append(
            {
                "principal_type": principals[0][0],
                "principal_name": principals[0][1],
                "permission_level": str(permission.get("permission_level") or "").upper(),
                "inherited": permission.get("inherited"),
                "inherited_from_object": sorted(str(item).strip() for item in inherited_from),
            }
        )
    contract = {
        "contract_version": "mip-gateway-experiment-acl-v1",
        "experiment_id": experiment_id,
        "access_control_list": sorted(
            normalized,
            key=lambda item: (item["principal_type"], item["principal_name"]),
        ),
    }
    return json.dumps(contract, sort_keys=True, separators=(",", ":"))


def _assert_endpoint_contract(
    workspace: Any,
    *,
    contract: Mapping[str, str],
    environment: Mapping[str, str],
) -> None:
    if (
        contract["supervisor_endpoint_creator"] != contract["runtime_application_id"]
        or contract["gateway_endpoint_creator"] != contract["runtime_application_id"]
        or contract["gateway_endpoint_description"] != GATEWAY_ENDPOINT_DESCRIPTION
        or contract["gateway_endpoint_route_optimized"] != str(GATEWAY_ROUTE_OPTIMIZED).lower()
        or contract["gateway_endpoint_budget_policy"] != "none"
        or contract["gateway_endpoint_email_notifications"] != "none"
        or contract["gateway_endpoint_deprecated_rate_limits"] != "[]"
    ):
        raise RuntimeError("Gateway signed endpoint policy contract is invalid")
    supervisor = workspace.serving_endpoints.get(contract["supervisor_endpoint"])
    if (
        str(_field(supervisor, "id") or "").strip() != contract["supervisor_endpoint_id"]
        or str(_field(supervisor, "creator") or "").strip()
        != contract["supervisor_endpoint_creator"]
    ):
        raise RuntimeError("managed Supervisor immutable endpoint contract drifted")
    details = workspace.serving_endpoints.get(contract["gateway_endpoint"])
    if (
        str(_field(details, "id") or "").strip() != contract["gateway_endpoint_id"]
        or str(_field(details, "creator") or "").strip() != contract["gateway_endpoint_creator"]
        or str(_field(details, "description") or "") != contract["gateway_endpoint_description"]
        or _text(_field(details, "task")) != contract["gateway_endpoint_task"]
        or str(bool(_field(details, "route_optimized"))).lower()
        != contract["gateway_endpoint_route_optimized"]
        or _field(details, "pending_config") is not None
    ):
        raise RuntimeError("Gateway immutable endpoint contract drifted")
    ready = _text(_field(_field(details, "state"), "ready")).upper()
    if ready != "READY":
        raise RuntimeError("Gateway endpoint is not ready")
    if (
        _field(details, "budget_policy_id") is not None
        or _field(details, "email_notifications") is not None
        or (_field(details, "rate_limits") or [])
    ):
        raise RuntimeError("Gateway endpoint policy contract drifted")
    config = _field(details, "config")
    if _field(config, "auto_capture_config") is not None or (_field(config, "served_models") or []):
        raise RuntimeError("Gateway served entity contract drifted")
    entities = _field(config, "served_entities") or []
    routes = _field(_field(config, "traffic_config"), "routes") or []
    if len(entities) != 1 or len(routes) != 1:
        raise RuntimeError("Gateway served entity contract drifted")
    entity = entities[0]
    version = contract["gateway_model_version"]
    served_name = f"mip-growth-supervisor-proxy-{version}"
    bound_environment = {
        str(key): str(value)
        for key, value in dict(_field(entity, "environment_vars") or {}).items()
    }
    expected_environment = {
        **GATEWAY_STATIC_ENV,
        **{key: value for key, value in environment.items() if key in GATEWAY_RUNTIME_RESOURCE_ENV},
        "MIP_UPSTREAM_SUPERVISOR_ID": contract["supervisor_id"],
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": contract["supervisor_endpoint"],
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": contract["runtime_application_id"],
        "MIP_SUPERVISOR_CATALOG": contract["catalog"],
        "MIP_SUPERVISOR_GENIE_SPACE_ID": contract["genie_space_id"],
        "MIP_SUPERVISOR_CONTRACT_SHA256": contract["supervisor_contract_sha256"],
        "MLFLOW_EXPERIMENT_ID": contract["gateway_experiment_id"],
    }
    if bound_environment != expected_environment:
        raise RuntimeError("Gateway served proxy environment contract drifted")
    if (
        str(_field(entity, "entity_name") or "") != contract["gateway_model_name"]
        or str(_field(entity, "entity_version") or "") != version
        or str(_field(entity, "name") or "") != served_name
        or str(_field(entity, "workload_size") or "") != GATEWAY_WORKLOAD_SIZE
        or _text(_field(entity, "workload_type")).upper() != GATEWAY_WORKLOAD_TYPE
        or _field(entity, "scale_to_zero_enabled") is not GATEWAY_SCALE_TO_ZERO_ENABLED
        or _field(entity, "burst_scaling_enabled") is not GATEWAY_BURST_SCALING_ENABLED
        or str(_field(routes[0], "served_entity_name") or "") != served_name
        or _field(routes[0], "traffic_percentage") != GATEWAY_TRAFFIC_PERCENTAGE
    ):
        raise RuntimeError("Gateway served proxy configuration contract drifted")
    tags = _endpoint_tags(details)
    if tags != {
        GATEWAY_PROXY_SOURCE_HASH_TAG: contract["gateway_source_hash"],
        GATEWAY_UPSTREAM_TAG: contract["supervisor_endpoint"],
    }:
        raise RuntimeError("Gateway endpoint source-binding tags drifted")
    inference = _field(_field(details, "ai_gateway"), "inference_table_config")
    gateway = _field(details, "ai_gateway")
    expected_table = contract["gateway_inference_table"].split(".", 2)
    if (
        len(expected_table) != 3
        or _field(inference, "enabled") is not True
        or [
            str(_field(inference, "catalog_name") or ""),
            str(_field(inference, "schema_name") or ""),
            str(_field(inference, "table_name_prefix") or ""),
        ]
        != expected_table
        or _field(gateway, "fallback_config") is not None
        or _field(gateway, "guardrails") is not None
        or (_field(gateway, "rate_limits") or [])
        or _field(gateway, "usage_tracking_config") is not None
    ):
        raise RuntimeError("Gateway inference-table contract drifted")


def assert_live_gateway_runtime_resources(
    workspace: Any,
    *,
    environment: Mapping[str, str],
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
) -> dict[str, str]:
    """Authenticate expected facts and re-prove every mutable live resource."""

    contract = verified_gateway_runtime_resource_environment(environment)
    runtime_id = contract["runtime_application_id"]
    metadata = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(contract['supervisor_id'], safe='')}",
    )
    if not isinstance(metadata, Mapping) or (
        str(metadata.get("supervisor_agent_id") or "").strip() != contract["supervisor_id"]
        or str(metadata.get("endpoint_name") or "").strip() != contract["supervisor_endpoint"]
        or str(metadata.get("creator") or "").strip() != contract["supervisor_creator"]
        or contract["supervisor_creator"] != runtime_id
    ):
        raise RuntimeError("managed Supervisor immutable identity contract drifted")
    expected_supervisor_json = canonical_supervisor_contract_json(
        genie_space_id=contract["genie_space_id"],
        catalog=contract["catalog"],
    )
    if contract["supervisor_contract_json"] != expected_supervisor_json or contract[
        "supervisor_contract_sha256"
    ] != supervisor_contract_hash(
        genie_space_id=contract["genie_space_id"],
        catalog=contract["catalog"],
    ):
        raise RuntimeError("managed Supervisor canonical contract drifted")
    _assert_endpoint_contract(workspace, contract=contract, environment=environment)
    source_hash = gateway_proxy_source_hash(
        upstream_endpoint=contract["supervisor_endpoint"],
        catalog=contract["catalog"],
        genie_space_id=contract["genie_space_id"],
    )
    inference_family = contract["gateway_inference_table_family"].split(".", 2)
    if len(inference_family) != 3 or source_hash != contract["gateway_source_hash"]:
        raise RuntimeError("Gateway reviewed proxy source contract drifted")
    resource_hash = gateway_resource_allocation_hash(
        source_hash=source_hash,
        supervisor_id=contract["supervisor_id"],
        supervisor_endpoint_id=contract["supervisor_endpoint_id"],
        runtime_application_id=runtime_id,
        model_name=contract["gateway_model_family"],
        experiment_name=contract["gateway_experiment_base"],
        inference_schema=inference_family[1],
        inference_table_prefix=inference_family[2],
        attestation_verify_key=str(
            environment.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY") or ""
        ),
    )
    if resource_hash != contract["gateway_resource_hash"]:
        raise RuntimeError("Gateway resource allocation contract drifted")
    expected_model_name = f"{contract['gateway_model_family']}_{resource_hash[:12]}"
    expected_experiment_name = (
        f"/Users/{runtime_id}/{contract['gateway_experiment_base']}-{resource_hash[:12]}"
    )
    expected_table = (
        f"{contract['catalog']}.{inference_family[1]}."
        f"{inference_family[2]}_{resource_hash[:12]}"
    )
    if (
        contract["gateway_model_name"] != expected_model_name
        or contract["gateway_experiment_name"] != expected_experiment_name
        or contract["gateway_inference_table"] != expected_table
    ):
        raise RuntimeError("Gateway immutable resource naming contract drifted")

    registry = model_registry or MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    model = workspace.registered_models.get(contract["gateway_model_name"])
    if (
        str(_field(model, "owner") or "").strip() != contract["gateway_model_owner"]
        or contract["gateway_model_owner"] != runtime_id
    ):
        raise RuntimeError("Gateway registered-model owner contract drifted")
    version = registry.get_model_version(
        contract["gateway_model_name"],
        contract["gateway_model_version"],
    )
    model_source = str(_field(version, "source") or "").strip()
    if (
        model_source != contract["gateway_model_source"]
        or _IMMUTABLE_MODEL_SOURCE.fullmatch(model_source) is None
    ):
        raise RuntimeError("Gateway immutable model-version source contract drifted")
    model_contract = {
        "full_name": contract["gateway_model_name"],
        "model_source": model_source,
        "source_hash": source_hash,
        "supervisor_id": contract["supervisor_id"],
        "supervisor_endpoint_id": contract["supervisor_endpoint_id"],
        "upstream_endpoint": contract["supervisor_endpoint"],
        "runtime_application_id": runtime_id,
        "model_family": contract["gateway_model_family"],
        "experiment_base": contract["gateway_experiment_base"],
        "catalog": contract["catalog"],
        "genie_space_id": contract["genie_space_id"],
        "inference_schema": inference_family[1],
        "inference_table_prefix": inference_family[2],
    }
    tags = {str(key): str(value) for key, value in dict(_field(version, "tags") or {}).items()}
    _verify_model_attestation(
        tags,
        expected=model_contract,
        current_verify_key=str(environment.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY") or ""),
        previous_verify_key=str(
            environment.get("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY") or ""
        ),
    )

    experiments = tracking_client or MlflowClient(tracking_uri="databricks")
    experiment = experiments.get_experiment(contract["gateway_experiment_id"])
    experiment_owner = str(
        (_field(experiment, "tags") or {}).get("mlflow.ownerEmail") or ""
    ).strip()
    if (
        str(_field(experiment, "experiment_id") or "") != contract["gateway_experiment_id"]
        or str(_field(experiment, "name") or "") != contract["gateway_experiment_name"]
        or experiment_owner != contract["gateway_experiment_owner"]
        or experiment_owner != runtime_id
    ):
        raise RuntimeError("Gateway MLflow experiment identity contract drifted")
    acl_json = _experiment_acl_contract(
        workspace,
        experiment_id=contract["gateway_experiment_id"],
    )
    if (
        acl_json != contract["gateway_experiment_acl_json"]
        or hashlib.sha256(acl_json.encode("utf-8")).hexdigest()
        != contract["gateway_experiment_acl_sha256"]
    ):
        raise RuntimeError("Gateway MLflow experiment ACL contract drifted")
    return contract
