"""Exact source-bound Supervisor proxy fixtures for service unit tests."""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_MODEL,
    GATEWAY_BURST_SCALING_ENABLED,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_ROUTE_OPTIMIZED,
    GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG,
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    GATEWAY_SCALE_TO_ZERO_ENABLED,
    GATEWAY_STATIC_ENV,
    GATEWAY_TRAFFIC_PERCENTAGE,
    GATEWAY_UPSTREAM_TAG,
    GATEWAY_WORKLOAD_SIZE,
    GATEWAY_WORKLOAD_TYPE,
    canonical_gateway_runtime_resource_contract,
    gateway_proxy_source_hash,
    gateway_resource_allocation_hash,
    gateway_runtime_binding_hash,
    gateway_runtime_resource_environment,
)
from backend.agents.supervisor_contract import (
    canonical_supervisor_contract_json,
    supervisor_contract_hash,
)
from backend.config.settings import Settings
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key

GATEWAY_ENDPOINT = "mip-growth-agent-gateway"
SUPERVISOR_ENDPOINT = "mas-supervisor-endpoint"
SUPERVISOR_ID = "supervisor-123"
SUPERVISOR_ENDPOINT_ID = "supervisor-endpoint-id"
GATEWAY_ENDPOINT_ID = "gateway-endpoint-id"
INFERENCE_TABLE = "mip.audit.mip_agent_gateway_growth_agent"
MODEL_VERSION = 7
GENIE_SPACE_ID = "space-123"
EXPERIMENT_ID = "experiment-7"
EXPERIMENT_NAME = "/Users/runtime-client/proxy"
MODEL_SOURCE = "models:/m-reviewed-proxy"
PROXY_CLIENT_ID = "proxy-client"
PROXY_CREDENTIAL_ID = "proxy-credential"
PROXY_SECRET_REFERENCE = (
    "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
)


def _signed_resource_environment(values: dict[str, object]) -> dict[str, str]:
    catalog = str(values["mip_default_catalog"])
    runtime_id = str(values["mip_agent_runtime_client_id"])
    supervisor_id = str(values["mip_agent_supervisor_id"])
    supervisor_endpoint = str(values["mip_agent_supervisor_endpoint"])
    gateway_endpoint = str(values["mip_agent_serving_endpoint"])
    model_name = str(values["mip_agent_gateway_model"])
    model_version = str(values["mip_agent_gateway_model_version"])
    inference_table = str(values["mip_ai_gateway_inference_table"])
    genie_space_id = str(values["genie_space_id"])
    experiment_name = str(values["mip_ai_gateway_experiment_name"])
    experiment_id = str(values["mip_ai_gateway_experiment_id"])
    model_source = str(values["mip_ai_gateway_agent_model_source"])
    proxy_client_id = str(values["mip_agent_proxy_client_id"])
    proxy_credential_id = str(values["mip_agent_proxy_credential_id"])
    proxy_secret_reference = str(values["mip_agent_proxy_secret_reference"])
    signing_key = base64.urlsafe_b64encode(b"u" * 32).decode("ascii").rstrip("=")
    verify_key = derive_gateway_proof_verify_key(signing_key)
    source_hash = gateway_proxy_source_hash(
        upstream_endpoint=supervisor_endpoint,
        catalog=catalog,
        genie_space_id=genie_space_id,
    )
    supervisor_json = canonical_supervisor_contract_json(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    experiment_acl_json = '{"contract_version":"unit-test"}'
    contract = {
        "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
        "catalog": catalog,
        "genie_space_id": genie_space_id,
        "runtime_application_id": runtime_id,
        "supervisor_canonical_name": "Mortgage Growth Agent",
        "supervisor_display_name": "Mortgage Growth Agent",
        "supervisor_contract_json": supervisor_json,
        "supervisor_contract_sha256": hashlib.sha256(supervisor_json.encode("utf-8")).hexdigest(),
        "supervisor_id": supervisor_id,
        "supervisor_creator": runtime_id,
        "supervisor_endpoint": supervisor_endpoint,
        "supervisor_endpoint_id": SUPERVISOR_ENDPOINT_ID,
        "supervisor_endpoint_creator": runtime_id,
        "gateway_endpoint": gateway_endpoint,
        "gateway_endpoint_id": GATEWAY_ENDPOINT_ID,
        "gateway_endpoint_creator": runtime_id,
        "gateway_endpoint_description": GATEWAY_ENDPOINT_DESCRIPTION,
        "gateway_endpoint_task": "agent/v1/responses",
        "gateway_endpoint_route_optimized": str(GATEWAY_ROUTE_OPTIMIZED).lower(),
        "gateway_endpoint_budget_policy": "none",
        "gateway_endpoint_email_notifications": "none",
        "gateway_endpoint_deprecated_rate_limits": "[]",
        "gateway_source_hash": source_hash,
        "gateway_resource_hash": gateway_resource_allocation_hash(
            source_hash=source_hash,
            supervisor_id=supervisor_id,
            supervisor_endpoint_id=SUPERVISOR_ENDPOINT_ID,
            runtime_application_id=runtime_id,
            model_name=model_name,
            experiment_name="proxy",
            inference_schema=inference_table.split(".", 2)[1],
            inference_table_prefix=inference_table.split(".", 2)[2],
            attestation_verify_key=verify_key,
            proxy_caller_application_id=proxy_client_id,
            proxy_caller_credential_id=proxy_credential_id,
            proxy_caller_secret_reference=proxy_secret_reference,
        ),
        "gateway_model_family": model_name,
        "gateway_model_name": model_name,
        "gateway_model_version": model_version,
        "gateway_model_source": model_source,
        "gateway_model_owner": runtime_id,
        "gateway_experiment_base": "proxy",
        "gateway_experiment_acl_json": experiment_acl_json,
        "gateway_experiment_acl_sha256": hashlib.sha256(
            experiment_acl_json.encode("utf-8")
        ).hexdigest(),
        "gateway_experiment_name": experiment_name,
        "gateway_experiment_id": experiment_id,
        "gateway_experiment_owner": runtime_id,
        "gateway_inference_table_family": inference_table,
        "gateway_inference_table": inference_table,
        "proxy_caller_application_id": proxy_client_id,
        "proxy_caller_credential_id": proxy_credential_id,
        "proxy_caller_secret_reference": proxy_secret_reference,
    }
    contract_json = canonical_gateway_runtime_resource_contract(contract)
    signature = Ed25519PrivateKey.from_private_bytes(b"u" * 32).sign(
        GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG.encode("ascii")
        + b"\0"
        + contract_json.encode("utf-8")
    )
    return gateway_runtime_resource_environment(
        contract,
        signature=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        current_verify_key=verify_key,
    )


def runtime_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mip_agent_orchestrator": True,
        "mip_agent_serving_endpoint": GATEWAY_ENDPOINT,
        "mip_agent_supervisor_endpoint": SUPERVISOR_ENDPOINT,
        "mip_agent_supervisor_id": SUPERVISOR_ID,
        "mip_agent_gateway_model": DEFAULT_GATEWAY_AGENT_MODEL,
        "mip_agent_gateway_model_version": MODEL_VERSION,
        "mip_ai_gateway": True,
        "mip_ai_gateway_endpoint": GATEWAY_ENDPOINT,
        "mip_ai_gateway_inference_table": INFERENCE_TABLE,
        "mip_ai_gateway_experiment_id": EXPERIMENT_ID,
        "mip_ai_gateway_experiment_name": EXPERIMENT_NAME,
        "mip_ai_gateway_agent_model_source": MODEL_SOURCE,
        "mip_expected_agent_gateway_binding_sha256": gateway_runtime_binding_hash(
            endpoint=GATEWAY_ENDPOINT,
            supervisor_id=SUPERVISOR_ID,
            upstream_endpoint=SUPERVISOR_ENDPOINT,
            runtime_application_id="runtime-client",
            model_name=DEFAULT_GATEWAY_AGENT_MODEL,
            model_version=MODEL_VERSION,
            inference_table=INFERENCE_TABLE,
            proxy_caller_application_id=PROXY_CLIENT_ID,
            proxy_caller_credential_id=PROXY_CREDENTIAL_ID,
            proxy_caller_secret_reference=PROXY_SECRET_REFERENCE,
        ),
        "genie_space_id": GENIE_SPACE_ID,
        "mip_agent_runtime_client_id": "runtime-client",
        "mip_agent_proxy_client_id": PROXY_CLIENT_ID,
        "mip_agent_proxy_credential_id": PROXY_CREDENTIAL_ID,
        "mip_agent_proxy_secret_reference": PROXY_SECRET_REFERENCE,
        "mip_default_catalog": "mip",
    }
    values.update(overrides)
    environment = _signed_resource_environment(values)
    values.update(
        {
            "mip_expected_agent_gateway_resource_contract_json": environment[
                "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"
            ],
            "mip_expected_agent_gateway_resource_sha256": environment[
                "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"
            ],
            "mip_expected_agent_gateway_resource_signature": environment[
                "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE"
            ],
            "mip_gateway_model_attestation_verify_key": environment[
                "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"
            ],
        }
    )
    return Settings(**values)


def supervisor_metadata() -> dict[str, str]:
    return {
        "supervisor_agent_id": SUPERVISOR_ID,
        "endpoint_name": SUPERVISOR_ENDPOINT,
        "creator": "runtime-client",
    }


def supervisor_endpoint_details(
    *,
    endpoint_id: str = SUPERVISOR_ENDPOINT_ID,
    creator: str = "runtime-client",
) -> object:
    """Return the live serving endpoint named by the signed Supervisor proof."""

    return SimpleNamespace(id=endpoint_id, creator=creator)


def gateway_endpoint_details(
    *,
    ready: str = "READY",
    task: str = "agent/v1/responses",
    upstream_endpoint: str = SUPERVISOR_ENDPOINT,
) -> object:
    return SimpleNamespace(
        id=GATEWAY_ENDPOINT_ID,
        creator="runtime-client",
        state=SimpleNamespace(ready=ready),
        task=task,
        pending_config=None,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name=DEFAULT_GATEWAY_AGENT_MODEL,
                    entity_version=str(MODEL_VERSION),
                    name=f"mip-growth-supervisor-proxy-{MODEL_VERSION}",
                    environment_vars={
                        **GATEWAY_STATIC_ENV,
                        **_signed_resource_environment(
                            {
                                "mip_default_catalog": "mip",
                                "mip_agent_runtime_client_id": "runtime-client",
                                "mip_agent_supervisor_id": SUPERVISOR_ID,
                                "mip_agent_supervisor_endpoint": SUPERVISOR_ENDPOINT,
                                "mip_agent_serving_endpoint": GATEWAY_ENDPOINT,
                                "mip_agent_gateway_model": DEFAULT_GATEWAY_AGENT_MODEL,
                                "mip_agent_gateway_model_version": MODEL_VERSION,
                                "mip_ai_gateway_inference_table": INFERENCE_TABLE,
                                "genie_space_id": GENIE_SPACE_ID,
                                "mip_ai_gateway_experiment_name": EXPERIMENT_NAME,
                                "mip_ai_gateway_experiment_id": EXPERIMENT_ID,
                                "mip_ai_gateway_agent_model_source": MODEL_SOURCE,
                                "mip_agent_proxy_client_id": PROXY_CLIENT_ID,
                                "mip_agent_proxy_credential_id": PROXY_CREDENTIAL_ID,
                                "mip_agent_proxy_secret_reference": PROXY_SECRET_REFERENCE,
                            }
                        ),
                        "MIP_UPSTREAM_SUPERVISOR_ID": SUPERVISOR_ID,
                        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream_endpoint,
                        "MIP_UPSTREAM_SUPERVISOR_CREATOR": "runtime-client",
                        "MIP_UPSTREAM_PROXY_CLIENT_ID": PROXY_CLIENT_ID,
                        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": PROXY_CREDENTIAL_ID,
                        "MIP_UPSTREAM_PROXY_CLIENT_SECRET": PROXY_SECRET_REFERENCE,
                        "MIP_SUPERVISOR_CATALOG": "mip",
                        "MIP_SUPERVISOR_GENIE_SPACE_ID": GENIE_SPACE_ID,
                        "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
                            genie_space_id=GENIE_SPACE_ID,
                            catalog="mip",
                        ),
                        "MLFLOW_EXPERIMENT_ID": "experiment-7",
                    },
                    workload_size=GATEWAY_WORKLOAD_SIZE,
                    workload_type=GATEWAY_WORKLOAD_TYPE,
                    scale_to_zero_enabled=GATEWAY_SCALE_TO_ZERO_ENABLED,
                    burst_scaling_enabled=GATEWAY_BURST_SCALING_ENABLED,
                )
            ],
            traffic_config=SimpleNamespace(
                routes=[
                    SimpleNamespace(
                        served_entity_name=f"mip-growth-supervisor-proxy-{MODEL_VERSION}",
                        traffic_percentage=GATEWAY_TRAFFIC_PERCENTAGE,
                    )
                ]
            ),
        ),
        description=GATEWAY_ENDPOINT_DESCRIPTION,
        route_optimized=GATEWAY_ROUTE_OPTIMIZED,
        budget_policy_id=None,
        email_notifications=None,
        rate_limits=[],
        tags=[
            SimpleNamespace(
                key=GATEWAY_PROXY_SOURCE_HASH_TAG,
                value=gateway_proxy_source_hash(
                    upstream_endpoint=SUPERVISOR_ENDPOINT,
                    catalog="mip",
                    genie_space_id=GENIE_SPACE_ID,
                ),
            ),
            SimpleNamespace(key=GATEWAY_UPSTREAM_TAG, value=SUPERVISOR_ENDPOINT),
        ],
        ai_gateway=SimpleNamespace(
            fallback_config=None,
            guardrails=None,
            rate_limits=[],
            usage_tracking_config=None,
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix="mip_agent_gateway_growth_agent",
            ),
        ),
    )
