from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    GATEWAY_BURST_SCALING_ENABLED,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_MODEL_ATTESTATION_SIGNATURE_TAG,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_ROUTE_OPTIMIZED,
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    GATEWAY_SCALE_TO_ZERO_ENABLED,
    GATEWAY_STATIC_ENV,
    GATEWAY_TRAFFIC_PERCENTAGE,
    GATEWAY_UPSTREAM_TAG,
    GATEWAY_WORKLOAD_SIZE,
    GATEWAY_WORKLOAD_TYPE,
    gateway_exact_resource_digest,
    gateway_proxy_source_hash,
    gateway_resource_allocation_hash,
    gateway_runtime_resource_environment,
    sign_gateway_runtime_resource_contract,
    verified_gateway_runtime_resource_environment,
)
from backend.agents.gateway_live_resource_contract import (
    assert_live_gateway_runtime_resources,
)
from backend.agents.supervisor_contract import (
    canonical_supervisor_contract_json,
    supervisor_contract_hash,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.fixtures.gateway_runtime_resources import (
    TEST_GATEWAY_VERIFY_KEY,
    signed_gateway_model_tags,
    signed_gateway_runtime_environment,
)


def _contract() -> dict[str, str]:
    fields = {
        "catalog",
        "gateway_endpoint",
        "gateway_endpoint_budget_policy",
        "gateway_endpoint_creator",
        "gateway_endpoint_deprecated_rate_limits",
        "gateway_endpoint_description",
        "gateway_endpoint_email_notifications",
        "gateway_endpoint_id",
        "gateway_endpoint_route_optimized",
        "gateway_endpoint_task",
        "gateway_experiment_acl_json",
        "gateway_experiment_acl_sha256",
        "gateway_experiment_base",
        "gateway_experiment_id",
        "gateway_experiment_name",
        "gateway_experiment_owner",
        "gateway_inference_table",
        "gateway_inference_table_family",
        "gateway_model_family",
        "gateway_model_name",
        "gateway_model_owner",
        "gateway_model_source",
        "gateway_model_version",
        "gateway_resource_hash",
        "gateway_source_hash",
        "genie_space_id",
        "proof_version",
        "runtime_application_id",
        "supervisor_canonical_name",
        "supervisor_contract_json",
        "supervisor_contract_sha256",
        "supervisor_creator",
        "supervisor_display_name",
        "supervisor_endpoint",
        "supervisor_endpoint_creator",
        "supervisor_endpoint_id",
        "supervisor_id",
    }
    contract = {field: f"value-{field}" for field in fields}
    contract["proof_version"] = GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    return contract


def test_signed_resource_environment_recomputes_canonical_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing = base64.urlsafe_b64encode(b"r" * 32).decode("ascii").rstrip("=")
    verify = derive_gateway_proof_verify_key(signing)
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", signing)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", verify)
    contract = _contract()
    environment = gateway_runtime_resource_environment(
        contract,
        signature=sign_gateway_runtime_resource_contract(contract),
        current_verify_key=verify,
    )

    assert verified_gateway_runtime_resource_environment(environment) == contract
    assert environment["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"] == (
        gateway_exact_resource_digest(contract)
    )


def test_runtime_resource_environment_rejects_noncanonical_base64_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing = base64.urlsafe_b64encode(b"r" * 32).decode("ascii").rstrip("=")
    verify = derive_gateway_proof_verify_key(signing)
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", signing)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", verify)
    contract = _contract()
    environment = gateway_runtime_resource_environment(
        contract,
        signature=f"{sign_gateway_runtime_resource_contract(contract)}!!!",
        current_verify_key=verify,
    )

    with pytest.raises(RuntimeError, match="attestation key is invalid"):
        verified_gateway_runtime_resource_environment(environment)


def test_arbitrary_digest_or_contract_drift_cannot_establish_runtime_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="environment is incomplete"):
        verified_gateway_runtime_resource_environment(
            {"MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": "a" * 64}
        )

    signing = base64.urlsafe_b64encode(b"r" * 32).decode("ascii").rstrip("=")
    verify = derive_gateway_proof_verify_key(signing)
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", signing)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", verify)
    contract = _contract()
    environment = gateway_runtime_resource_environment(
        contract,
        signature=sign_gateway_runtime_resource_contract(contract),
        current_verify_key=verify,
    )
    environment["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"] = environment[
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"
    ].replace("value-gateway_endpoint_id", "attacker-endpoint-id")

    with pytest.raises(RuntimeError, match="signature is invalid"):
        verified_gateway_runtime_resource_environment(environment)


_RUNTIME_ID = "runtime-client"
_SUPERVISOR_ID = "supervisor-1"
_SUPERVISOR_ENDPOINT = "managed-supervisor-endpoint"
_GATEWAY_ENDPOINT = "mip-growth-agent-gateway"
_CATALOG = "mip"
_GENIE_SPACE = "01f-genie-space"
_MODEL_FAMILY = "mip.audit.mortgage_growth_supervisor_proxy"
_INFERENCE_FAMILY = "mip.audit.mip_agent_gateway_growth_agent"
_MODEL_SOURCE = "models:/m-reviewed-proxy"
_EXPERIMENT_ID = "experiment-7"


def _acl_document() -> dict[str, Any]:
    def _entry(kind: str, name: str) -> dict[str, Any]:
        return {
            f"{kind}_name": name,
            "all_permissions": [
                {
                    "permission_level": "CAN_MANAGE",
                    "inherited": False,
                    "inherited_from_object": [],
                }
            ],
        }

    return {
        "access_control_list": [
            _entry("service_principal", _RUNTIME_ID),
            _entry("group", "admins"),
        ]
    }


def _canonical_acl(document: dict[str, Any]) -> str:
    normalized = []
    for entry in document["access_control_list"]:
        principal_type = "service_principal" if "service_principal_name" in entry else "group"
        permission = entry["all_permissions"][0]
        normalized.append(
            {
                "principal_type": principal_type,
                "principal_name": entry[f"{principal_type}_name"],
                "permission_level": permission["permission_level"],
                "inherited": permission["inherited"],
                "inherited_from_object": permission["inherited_from_object"],
            }
        )
    return json.dumps(
        {
            "contract_version": "mip-gateway-experiment-acl-v1",
            "experiment_id": _EXPERIMENT_ID,
            "access_control_list": sorted(
                normalized,
                key=lambda item: (item["principal_type"], item["principal_name"]),
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class _ApiClient:
    def __init__(self, acl: dict[str, Any]) -> None:
        self.acl = acl

    def do(self, method: str, path: str) -> dict[str, Any]:
        assert method == "GET"
        if path == f"/api/2.1/supervisor-agents/{_SUPERVISOR_ID}":
            return {
                "supervisor_agent_id": _SUPERVISOR_ID,
                "endpoint_name": _SUPERVISOR_ENDPOINT,
                "creator": _RUNTIME_ID,
            }
        assert path == f"/api/2.0/permissions/experiments/{_EXPERIMENT_ID}"
        return self.acl


class _NamedResources:
    def __init__(self, resources: dict[str, object]) -> None:
        self.resources = resources

    def get(self, name: str) -> object:
        return self.resources[name]


class _ModelRegistry:
    def __init__(self, version: object) -> None:
        self.version = version

    def get_model_version(self, name: str, version: str) -> object:
        assert name == self.version.name
        assert version == self.version.version
        return self.version


class _TrackingClient:
    def __init__(self, experiment: object) -> None:
        self.experiment = experiment

    def get_experiment(self, experiment_id: str) -> object:
        assert experiment_id == _EXPERIMENT_ID
        return self.experiment


@dataclass
class _LiveResources:
    environment: dict[str, str]
    contract: dict[str, str]
    workspace: object
    gateway: Any
    model_version: Any
    experiment: Any
    acl: dict[str, Any]
    model_registry: _ModelRegistry
    tracking_client: _TrackingClient


def _live_resources() -> _LiveResources:
    model_verify_key = TEST_GATEWAY_VERIFY_KEY
    source_hash = gateway_proxy_source_hash(
        upstream_endpoint=_SUPERVISOR_ENDPOINT,
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE,
    )
    resource_hash = gateway_resource_allocation_hash(
        source_hash=source_hash,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id="se-supervisor-immutable",
        runtime_application_id=_RUNTIME_ID,
        model_name=_MODEL_FAMILY,
        experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        attestation_verify_key=model_verify_key,
    )
    model_name = f"{_MODEL_FAMILY}_{resource_hash[:12]}"
    experiment_name = (
        f"/Users/{_RUNTIME_ID}/{DEFAULT_GATEWAY_AGENT_EXPERIMENT}-{resource_hash[:12]}"
    )
    inference_table = f"mip.audit.mip_agent_gateway_growth_agent_{resource_hash[:12]}"
    acl = _acl_document()
    acl_json = _canonical_acl(acl)
    supervisor_json = canonical_supervisor_contract_json(
        genie_space_id=_GENIE_SPACE,
        catalog=_CATALOG,
    )
    contract = {
        "catalog": _CATALOG,
        "gateway_endpoint": _GATEWAY_ENDPOINT,
        "gateway_endpoint_budget_policy": "none",
        "gateway_endpoint_creator": _RUNTIME_ID,
        "gateway_endpoint_deprecated_rate_limits": "[]",
        "gateway_endpoint_description": GATEWAY_ENDPOINT_DESCRIPTION,
        "gateway_endpoint_email_notifications": "none",
        "gateway_endpoint_id": "se-gateway-immutable",
        "gateway_endpoint_route_optimized": str(GATEWAY_ROUTE_OPTIMIZED).lower(),
        "gateway_endpoint_task": "agent/v1/responses",
        "gateway_experiment_acl_json": acl_json,
        "gateway_experiment_acl_sha256": hashlib.sha256(acl_json.encode()).hexdigest(),
        "gateway_experiment_base": DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        "gateway_experiment_id": _EXPERIMENT_ID,
        "gateway_experiment_name": experiment_name,
        "gateway_experiment_owner": _RUNTIME_ID,
        "gateway_inference_table": inference_table,
        "gateway_inference_table_family": _INFERENCE_FAMILY,
        "gateway_model_family": _MODEL_FAMILY,
        "gateway_model_name": model_name,
        "gateway_model_owner": _RUNTIME_ID,
        "gateway_model_source": _MODEL_SOURCE,
        "gateway_model_version": "7",
        "gateway_resource_hash": resource_hash,
        "gateway_source_hash": source_hash,
        "genie_space_id": _GENIE_SPACE,
        "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
        "runtime_application_id": _RUNTIME_ID,
        "supervisor_canonical_name": "Mortgage Growth Agent Supervisor",
        "supervisor_contract_json": supervisor_json,
        "supervisor_contract_sha256": supervisor_contract_hash(
            genie_space_id=_GENIE_SPACE,
            catalog=_CATALOG,
        ),
        "supervisor_creator": _RUNTIME_ID,
        "supervisor_display_name": "Mortgage Growth Agent Supervisor",
        "supervisor_endpoint": _SUPERVISOR_ENDPOINT,
        "supervisor_endpoint_creator": _RUNTIME_ID,
        "supervisor_endpoint_id": "se-supervisor-immutable",
        "supervisor_id": _SUPERVISOR_ID,
    }
    environment = signed_gateway_runtime_environment(contract)
    model_contract = {
        "catalog": _CATALOG,
        "experiment_base": DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        "full_name": model_name,
        "genie_space_id": _GENIE_SPACE,
        "inference_schema": "audit",
        "inference_table_prefix": "mip_agent_gateway_growth_agent",
        "model_family": _MODEL_FAMILY,
        "model_source": _MODEL_SOURCE,
        "runtime_application_id": _RUNTIME_ID,
        "source_hash": source_hash,
        "supervisor_id": _SUPERVISOR_ID,
        "supervisor_endpoint_id": "se-supervisor-immutable",
        "upstream_endpoint": _SUPERVISOR_ENDPOINT,
    }
    model_version = SimpleNamespace(
        name=model_name,
        version="7",
        source=_MODEL_SOURCE,
        tags=signed_gateway_model_tags(model_contract),
    )
    experiment = SimpleNamespace(
        experiment_id=_EXPERIMENT_ID,
        name=experiment_name,
        tags={"mlflow.ownerEmail": _RUNTIME_ID},
    )
    entity_environment = {
        **GATEWAY_STATIC_ENV,
        **environment,
        "MIP_UPSTREAM_SUPERVISOR_ID": _SUPERVISOR_ID,
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": _SUPERVISOR_ENDPOINT,
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": _RUNTIME_ID,
        "MIP_SUPERVISOR_CATALOG": _CATALOG,
        "MIP_SUPERVISOR_GENIE_SPACE_ID": _GENIE_SPACE,
        "MIP_SUPERVISOR_CONTRACT_SHA256": contract["supervisor_contract_sha256"],
        "MLFLOW_EXPERIMENT_ID": _EXPERIMENT_ID,
    }
    served_name = "mip-growth-supervisor-proxy-7"
    gateway = SimpleNamespace(
        id=contract["gateway_endpoint_id"],
        creator=_RUNTIME_ID,
        description=GATEWAY_ENDPOINT_DESCRIPTION,
        task="agent/v1/responses",
        route_optimized=GATEWAY_ROUTE_OPTIMIZED,
        pending_config=None,
        state=SimpleNamespace(ready="READY"),
        budget_policy_id=None,
        email_notifications=None,
        rate_limits=[],
        config=SimpleNamespace(
            auto_capture_config=None,
            served_models=[],
            served_entities=[
                SimpleNamespace(
                    entity_name=model_name,
                    entity_version="7",
                    name=served_name,
                    environment_vars=entity_environment,
                    workload_size=GATEWAY_WORKLOAD_SIZE,
                    workload_type=GATEWAY_WORKLOAD_TYPE,
                    scale_to_zero_enabled=GATEWAY_SCALE_TO_ZERO_ENABLED,
                    burst_scaling_enabled=GATEWAY_BURST_SCALING_ENABLED,
                )
            ],
            traffic_config=SimpleNamespace(
                routes=[
                    SimpleNamespace(
                        served_entity_name=served_name,
                        traffic_percentage=GATEWAY_TRAFFIC_PERCENTAGE,
                    )
                ]
            ),
        ),
        tags=[
            SimpleNamespace(key=GATEWAY_PROXY_SOURCE_HASH_TAG, value=source_hash),
            SimpleNamespace(key=GATEWAY_UPSTREAM_TAG, value=_SUPERVISOR_ENDPOINT),
        ],
        ai_gateway=SimpleNamespace(
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix=f"mip_agent_gateway_growth_agent_{resource_hash[:12]}",
            ),
            fallback_config=None,
            guardrails=None,
            rate_limits=[],
            usage_tracking_config=None,
        ),
    )
    workspace = SimpleNamespace(
        api_client=_ApiClient(acl),
        serving_endpoints=_NamedResources(
            {
                _SUPERVISOR_ENDPOINT: SimpleNamespace(
                    id=contract["supervisor_endpoint_id"],
                    creator=_RUNTIME_ID,
                ),
                _GATEWAY_ENDPOINT: gateway,
            }
        ),
        registered_models=_NamedResources({model_name: SimpleNamespace(owner=_RUNTIME_ID)}),
    )
    return _LiveResources(
        environment=environment,
        contract=contract,
        workspace=workspace,
        gateway=gateway,
        model_version=model_version,
        experiment=experiment,
        acl=acl,
        model_registry=_ModelRegistry(model_version),
        tracking_client=_TrackingClient(experiment),
    )


def _verify_live(resources: _LiveResources) -> dict[str, str]:
    return assert_live_gateway_runtime_resources(
        resources.workspace,
        environment=resources.environment,
        model_registry=resources.model_registry,
        tracking_client=resources.tracking_client,
    )


def test_live_gateway_runtime_resources_accept_exact_signed_release_state() -> None:
    resources = _live_resources()

    assert _verify_live(resources) == resources.contract


@pytest.mark.parametrize("drift", ["endpoint_id", "environment"])
def test_live_gateway_runtime_resources_reject_endpoint_drift(drift: str) -> None:
    resources = _live_resources()
    if drift == "endpoint_id":
        resources.gateway.id = "se-attacker-replacement"
        error = "immutable endpoint contract drifted"
    else:
        resources.gateway.config.served_entities[0].environment_vars[
            "MIP_UPSTREAM_SUPERVISOR_ENDPOINT"
        ] = "attacker-endpoint"
        error = "environment contract drifted"

    with pytest.raises(RuntimeError, match=error):
        _verify_live(resources)


@pytest.mark.parametrize("drift", ["source", "signature", "signature_encoding"])
def test_live_gateway_runtime_resources_reject_model_drift(drift: str) -> None:
    resources = _live_resources()
    if drift == "source":
        resources.model_version.source = "models:/m-attacker-replacement"
        error = "model-version source contract drifted"
    elif drift == "signature":
        resources.model_version.tags[GATEWAY_MODEL_ATTESTATION_SIGNATURE_TAG] = (
            base64.urlsafe_b64encode(b"x" * 64).decode().rstrip("=")
        )
        error = "attestation signature is invalid"
    else:
        resources.model_version.tags[GATEWAY_MODEL_ATTESTATION_SIGNATURE_TAG] += "!!!"
        error = "attestation signature is invalid"

    with pytest.raises(RuntimeError, match=error):
        _verify_live(resources)


@pytest.mark.parametrize("drift", ["owner", "acl"])
def test_live_gateway_runtime_resources_reject_experiment_drift(drift: str) -> None:
    resources = _live_resources()
    if drift == "owner":
        resources.experiment.tags["mlflow.ownerEmail"] = "attacker@example.com"
        error = "experiment identity contract drifted"
    else:
        resources.acl["access_control_list"][0]["all_permissions"][0]["permission_level"] = (
            "CAN_READ"
        )
        error = "experiment ACL contract drifted"

    with pytest.raises(RuntimeError, match=error):
        _verify_live(resources)
