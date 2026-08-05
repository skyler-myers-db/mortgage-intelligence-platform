from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agents import gateway_live_resource_contract as live_resource_contract
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
    assert_live_historical_gateway_runtime_resources,
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
from tools.databricks import (
    export_gateway_runtime_contract,
    gateway_runtime_resource_binding,
)
from tools.databricks.export_gateway_runtime_contract import ExactGatewayRuntimeProof
from tools.databricks.gateway_resource_identity import GatewayAgentDeployment


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
        "proxy_caller_application_id",
        "proxy_caller_credential_id",
        "proxy_caller_secret_reference",
        "runtime_application_id",
        "workspace_host",
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
    contract["workspace_host"] = "https://workspace.cloud.databricks.com"
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
_PROXY_CLIENT_ID = "proxy-client"
_PROXY_CREDENTIAL_ID = "proxy-credential"
_PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
_WORKSPACE_HOST = "https://workspace.cloud.databricks.com"


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
        workspace_host=_WORKSPACE_HOST,
        model_name=_MODEL_FAMILY,
        experiment_name=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        attestation_verify_key=model_verify_key,
        proxy_caller_application_id=_PROXY_CLIENT_ID,
        proxy_caller_credential_id=_PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=_PROXY_SECRET_REFERENCE,
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
        "proxy_caller_application_id": _PROXY_CLIENT_ID,
        "proxy_caller_credential_id": _PROXY_CREDENTIAL_ID,
        "proxy_caller_secret_reference": _PROXY_SECRET_REFERENCE,
        "runtime_application_id": _RUNTIME_ID,
        "workspace_host": _WORKSPACE_HOST,
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
        "DATABRICKS_HOST": _WORKSPACE_HOST,
        "MIP_UPSTREAM_SUPERVISOR_ID": _SUPERVISOR_ID,
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": _SUPERVISOR_ENDPOINT,
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": _RUNTIME_ID,
        "MIP_UPSTREAM_PROXY_CLIENT_ID": _PROXY_CLIENT_ID,
        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": _PROXY_CREDENTIAL_ID,
        "MIP_UPSTREAM_PROXY_CLIENT_SECRET": _PROXY_SECRET_REFERENCE,
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
        config=SimpleNamespace(host=_WORKSPACE_HOST),
        api_client=_ApiClient(acl),
        serving_endpoints=_NamedResources(
            {
                _GATEWAY_ENDPOINT: gateway,
                _SUPERVISOR_ENDPOINT: SimpleNamespace(
                    id=contract["supervisor_endpoint_id"],
                    name=_SUPERVISOR_ENDPOINT,
                    creator=_RUNTIME_ID,
                    task="agent/v1/responses",
                    pending_config=None,
                    state=SimpleNamespace(ready="READY"),
                ),
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


def test_historical_gateway_verifier_accepts_signed_prior_source_only_for_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _live_resources()
    monkeypatch.setattr(
        live_resource_contract,
        "gateway_proxy_source_hash",
        lambda **_kwargs: "f" * 64,
    )

    with pytest.raises(RuntimeError, match="reviewed proxy source contract drifted"):
        _verify_live(resources)

    assert (
        assert_live_historical_gateway_runtime_resources(
            resources.workspace,
            environment=resources.environment,
            model_registry=resources.model_registry,
            tracking_client=resources.tracking_client,
        )
        == resources.contract
    )


def test_historical_gateway_verifier_still_rejects_immutable_identity_drift() -> None:
    resources = _live_resources()
    resources.gateway.id = "attacker-replacement"

    with pytest.raises(RuntimeError, match="immutable endpoint contract drifted"):
        assert_live_historical_gateway_runtime_resources(
            resources.workspace,
            environment=resources.environment,
            model_registry=resources.model_registry,
            tracking_client=resources.tracking_client,
        )


def test_served_binding_preserves_previous_model_attestation_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    proof = ExactGatewayRuntimeProof(
        contract=contract,
        digest=gateway_exact_resource_digest(contract),
    )
    monkeypatch.setattr(
        export_gateway_runtime_contract,
        "resolve_exact_resource_proof",
        lambda *_args, **_kwargs: proof,
    )
    monkeypatch.setattr(
        gateway_runtime_resource_binding,
        "sign_gateway_runtime_resource_contract",
        lambda _contract: "signature",
    )
    observed: dict[str, str] = {}

    def environment(
        _contract: object,
        *,
        signature: str,
        current_verify_key: str,
        previous_verify_key: str,
    ) -> dict[str, str]:
        observed.update(
            signature=signature,
            current_verify_key=current_verify_key,
            previous_verify_key=previous_verify_key,
        )
        return {"MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": proof.digest}

    monkeypatch.setattr(
        gateway_runtime_resource_binding,
        "gateway_runtime_resource_environment",
        environment,
    )
    monkeypatch.setattr(
        gateway_runtime_resource_binding,
        "served_entity",
        lambda **_kwargs: (object(), object()),
    )
    monkeypatch.setattr(
        gateway_runtime_resource_binding,
        "assert_gateway_runtime_resource_binding",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "current-key")
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
        "previous-key",
    )
    deployment = GatewayAgentDeployment(
        endpoint="gateway",
        supervisor_id="supervisor",
        supervisor_endpoint_id="supervisor-endpoint-id",
        upstream_endpoint="supervisor-endpoint",
        runtime_application_id="runtime",
        workspace_host=_WORKSPACE_HOST,
        proxy_caller_application_id="proxy",
        proxy_caller_credential_id="credential",
        proxy_caller_secret_reference="{{secrets/scope/oauth-client-secret-credential}}",
        model_name="mip.audit.proxy",
        model_version=7,
        model_source="models:/m-source",
        model_attestation_verify_key="current-key",
        model_family="mip.audit.proxy",
        source_hash="a" * 64,
        resource_hash="b" * 64,
        inference_table="mip.audit.inference",
        inference_table_prefix="inference",
        experiment_base="proxy",
        experiment_name="/Users/runtime/proxy",
        experiment_id="experiment",
        catalog="mip",
        genie_space_id="space",
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            update_config_and_wait=lambda **_kwargs: None,
            get=lambda _endpoint: object(),
        )
    )

    gateway_runtime_resource_binding.bind_gateway_runtime_resource_contract(
        workspace,
        deployment,
        supervisor_name="Mortgage Growth Agent",
        reviewed_function_owner="reviewed-owner",
        assert_single_writer=lambda: None,
    )

    assert observed == {
        "signature": "signature",
        "current_verify_key": "current-key",
        "previous_verify_key": "previous-key",
    }


def test_live_gateway_runtime_resources_reject_private_supervisor_endpoint_id_drift() -> None:
    resources = _live_resources()
    resources.workspace.serving_endpoints.resources[
        _SUPERVISOR_ENDPOINT
    ].id = "se-attacker-replacement"

    with pytest.raises(RuntimeError, match="Supervisor immutable endpoint contract drifted"):
        _verify_live(resources)


def test_live_gateway_runtime_resources_accept_provider_normalized_release_state() -> None:
    resources = _live_resources()
    entity = resources.gateway.config.served_entities[0]
    entity.burst_scaling_enabled = None
    resources.gateway.config.served_models = [
        SimpleNamespace(
            model_name=entity.entity_name,
            model_version=entity.entity_version,
            name=entity.name,
            environment_vars=dict(entity.environment_vars),
            workload_size=entity.workload_size,
            workload_type=entity.workload_type,
            scale_to_zero_enabled=entity.scale_to_zero_enabled,
        )
    ]
    resources.gateway.config.traffic_config.routes[0].served_model_name = entity.name
    resources.gateway.ai_gateway.usage_tracking_config = SimpleNamespace(enabled=False)

    assert _verify_live(resources) == resources.contract


@pytest.mark.parametrize("drift", ["legacy_environment", "usage_tracking"])
def test_live_gateway_runtime_resources_reject_provider_normalization_drift(
    drift: str,
) -> None:
    resources = _live_resources()
    entity = resources.gateway.config.served_entities[0]
    resources.gateway.config.served_models = [
        SimpleNamespace(
            model_name=entity.entity_name,
            model_version=entity.entity_version,
            name=entity.name,
            environment_vars=dict(entity.environment_vars),
            workload_size=entity.workload_size,
            workload_type=entity.workload_type,
            scale_to_zero_enabled=entity.scale_to_zero_enabled,
        )
    ]
    if drift == "legacy_environment":
        resources.gateway.config.served_models[0].environment_vars = {
            **entity.environment_vars,
            "MIP_UPSTREAM_SUPERVISOR_ID": "attacker",
        }
        error = "served entity contract drifted"
    else:
        resources.gateway.ai_gateway.usage_tracking_config = SimpleNamespace(enabled=True)
        error = "inference-table contract drifted"

    with pytest.raises(RuntimeError, match=error):
        _verify_live(resources)


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
