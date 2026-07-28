"""Deterministic signed Gateway resource fixtures for runtime unit tests."""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.agents.gateway_contract import (
    GATEWAY_MODEL_ATTESTATION_ALGORITHM_TAG,
    GATEWAY_MODEL_ATTESTATION_SIGNATURE_TAG,
    GATEWAY_MODEL_ATTESTATION_VERIFY_KEY_TAG,
    GATEWAY_MODEL_CONTRACT_FIELD_TAGS,
    GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG,
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    canonical_gateway_runtime_resource_contract,
    gateway_runtime_resource_environment,
)
from backend.services.ai_gateway_proof_attestation import AI_GATEWAY_PROOF_ATTESTATION_ALG

_PRIVATE = Ed25519PrivateKey.from_private_bytes(b"t" * 32)
TEST_GATEWAY_VERIFY_KEY = (
    base64.urlsafe_b64encode(
        _PRIVATE.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    .decode("ascii")
    .rstrip("=")
)


def gateway_runtime_contract_for_scope(
    *,
    catalog: str,
    genie_space_id: str,
    runtime_application_id: str,
    supervisor_id: str,
    supervisor_endpoint: str,
    gateway_endpoint: str,
    gateway_model_name: str,
    gateway_model_version: str,
    gateway_model_source: str,
    gateway_experiment_name: str,
    gateway_experiment_id: str,
    gateway_inference_table: str,
    workspace_host: str = "https://workspace.cloud.databricks.com",
    proxy_caller_application_id: str = "proxy-client",
    proxy_caller_credential_id: str = "proxy-credential",
    proxy_caller_secret_reference: str = (
        "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
    ),
) -> dict[str, str]:
    """Build an exact-field release envelope around an App-visible scope."""

    return {
        "catalog": catalog,
        "gateway_endpoint": gateway_endpoint,
        "gateway_endpoint_budget_policy": "none",
        "gateway_endpoint_creator": runtime_application_id,
        "gateway_endpoint_deprecated_rate_limits": "[]",
        "gateway_endpoint_description": "test-reviewed-description",
        "gateway_endpoint_email_notifications": "none",
        "gateway_endpoint_id": "test-gateway-endpoint-id",
        "gateway_endpoint_route_optimized": "false",
        "gateway_endpoint_task": "agent/v1/responses",
        "gateway_experiment_acl_json": '{"test":"acl"}',
        "gateway_experiment_acl_sha256": "1" * 64,
        "gateway_experiment_base": "test-experiment-family",
        "gateway_experiment_id": gateway_experiment_id,
        "gateway_experiment_name": gateway_experiment_name,
        "gateway_experiment_owner": runtime_application_id,
        "gateway_inference_table": gateway_inference_table,
        "gateway_inference_table_family": gateway_inference_table,
        "gateway_model_family": gateway_model_name,
        "gateway_model_name": gateway_model_name,
        "gateway_model_owner": runtime_application_id,
        "gateway_model_source": gateway_model_source,
        "gateway_model_version": gateway_model_version,
        "gateway_resource_hash": "2" * 64,
        "gateway_source_hash": "3" * 64,
        "genie_space_id": genie_space_id,
        "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
        "runtime_application_id": runtime_application_id,
        "workspace_host": workspace_host,
        "proxy_caller_application_id": proxy_caller_application_id,
        "proxy_caller_credential_id": proxy_caller_credential_id,
        "proxy_caller_secret_reference": proxy_caller_secret_reference,
        "supervisor_canonical_name": "test-supervisor",
        "supervisor_contract_json": '{"test":"supervisor"}',
        "supervisor_contract_sha256": "4" * 64,
        "supervisor_creator": runtime_application_id,
        "supervisor_display_name": "Test Supervisor",
        "supervisor_endpoint": supervisor_endpoint,
        "supervisor_endpoint_creator": runtime_application_id,
        "supervisor_endpoint_id": "test-supervisor-endpoint-id",
        "supervisor_id": supervisor_id,
    }


def signed_gateway_runtime_environment(contract: dict[str, str]) -> dict[str, str]:
    """Sign canonical resource facts without enabling production signing code."""

    contract_json = canonical_gateway_runtime_resource_contract(contract)
    payload = (
        GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG.encode("ascii")
        + b"\0"
        + contract_json.encode("utf-8")
    )
    signature = base64.urlsafe_b64encode(_PRIVATE.sign(payload)).decode("ascii").rstrip("=")
    return gateway_runtime_resource_environment(
        contract,
        signature=signature,
        current_verify_key=TEST_GATEWAY_VERIFY_KEY,
    )


def signed_gateway_model_tags(contract: dict[str, str]) -> dict[str, str]:
    """Return a valid production-shaped UC-safe model-version tag set."""

    payload_contract = {**contract, "version": 3}
    payload = b"mip-gateway-model-contract-v3\0" + json.dumps(
        payload_contract,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = base64.urlsafe_b64encode(_PRIVATE.sign(payload)).decode("ascii").rstrip("=")
    return {
        **{GATEWAY_MODEL_CONTRACT_FIELD_TAGS[field]: value for field, value in contract.items()},
        GATEWAY_MODEL_ATTESTATION_ALGORITHM_TAG: AI_GATEWAY_PROOF_ATTESTATION_ALG,
        GATEWAY_MODEL_ATTESTATION_SIGNATURE_TAG: signature,
        GATEWAY_MODEL_ATTESTATION_VERIFY_KEY_TAG: TEST_GATEWAY_VERIFY_KEY,
    }
