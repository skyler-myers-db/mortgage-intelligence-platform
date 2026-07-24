"""Bind the exact live Gateway proof into the served proxy environment."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from backend.agents.gateway_contract import (
    GATEWAY_RUNTIME_RESOURCE_ENV,
    canonical_gateway_runtime_resource_contract,
    gateway_runtime_resource_environment,
    sign_gateway_runtime_resource_contract,
    verified_gateway_runtime_resource_environment,
)
from tools.databricks.gateway_endpoint_contract import served_entity
from tools.databricks.gateway_resource_identity import GatewayAgentDeployment


def gateway_runtime_resource_binding_environment(details: Any) -> dict[str, str]:
    """Return only the non-secret exact-resource binding variables."""

    entities = getattr(getattr(details, "config", None), "served_entities", None) or []
    if len(entities) != 1:
        raise RuntimeError("Gateway runtime resource binding requires one served entity")
    raw = getattr(entities[0], "environment_vars", None) or {}
    if not isinstance(raw, dict):
        raw = dict(raw)
    return {
        str(key): str(value)
        for key, value in raw.items()
        if str(key) in GATEWAY_RUNTIME_RESOURCE_ENV
    }


def assert_gateway_runtime_resource_binding(
    details: Any,
    *,
    contract: dict[str, str],
) -> None:
    """Require the final proxy environment to contain the authenticated proof."""

    environment = gateway_runtime_resource_binding_environment(details)
    verified = verified_gateway_runtime_resource_environment(environment)
    if canonical_gateway_runtime_resource_contract(verified) != (
        canonical_gateway_runtime_resource_contract(contract)
    ):
        raise RuntimeError("Gateway served proxy resource contract drifted")


def bind_gateway_runtime_resource_contract(
    workspace: Any,
    deployment: GatewayAgentDeployment,
    *,
    supervisor_name: str,
    reviewed_function_owner: str,
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
    assert_single_writer: Callable[[], None],
) -> None:
    """Sign, inject, re-read, and prove the final served-proxy environment."""

    # Local import avoids a module cycle: the exporter reuses the provisioner's
    # read-only endpoint verifier, while this mutation is called only by the
    # signing-authorized provisioning subprocess.
    from tools.databricks.export_gateway_runtime_contract import (
        resolve_exact_resource_proof,
    )

    proof = resolve_exact_resource_proof(
        workspace,
        supervisor_name=supervisor_name,
        supervisor_id=deployment.supervisor_id,
        catalog=deployment.catalog,
        genie_space_id=deployment.genie_space_id,
        runtime_application_id=deployment.runtime_application_id,
        reviewed_function_owner=reviewed_function_owner,
        proxy_caller_application_id=deployment.proxy_caller_application_id,
        proxy_caller_credential_id=deployment.proxy_caller_credential_id,
        proxy_caller_secret_reference=deployment.proxy_caller_secret_reference,
        gateway_endpoint=deployment.endpoint,
        gateway_model_family_name=deployment.model_family,
        gateway_experiment_base_name=deployment.experiment_base,
        gateway_table_prefix=deployment.inference_table_prefix,
        model_registry=model_registry,
        tracking_client=tracking_client,
        require_resource_binding=False,
    )
    signature = sign_gateway_runtime_resource_contract(proof.contract)
    binding = gateway_runtime_resource_environment(
        proof.contract,
        signature=signature,
        current_verify_key=os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", ""),
        previous_verify_key=os.environ.get(
            "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
            "",
        ),
    )
    entity, traffic = served_entity(
        supervisor_id=deployment.supervisor_id,
        upstream_endpoint=deployment.upstream_endpoint,
        runtime_application_id=deployment.runtime_application_id,
        proxy_caller_application_id=deployment.proxy_caller_application_id,
        proxy_caller_credential_id=deployment.proxy_caller_credential_id,
        proxy_caller_secret_reference=deployment.proxy_caller_secret_reference,
        catalog=deployment.catalog,
        genie_space_id=deployment.genie_space_id,
        model_name=deployment.model_name,
        model_version=deployment.model_version,
        experiment_id=deployment.experiment_id,
        resource_binding=binding,
    )
    assert_single_writer()
    workspace.serving_endpoints.update_config_and_wait(
        name=deployment.endpoint,
        served_entities=[entity],
        traffic_config=traffic,
    )
    final_details = workspace.serving_endpoints.get(deployment.endpoint)
    assert_gateway_runtime_resource_binding(
        final_details,
        contract=dict(proof.contract),
    )
    final = resolve_exact_resource_proof(
        workspace,
        supervisor_name=supervisor_name,
        supervisor_id=deployment.supervisor_id,
        catalog=deployment.catalog,
        genie_space_id=deployment.genie_space_id,
        runtime_application_id=deployment.runtime_application_id,
        reviewed_function_owner=reviewed_function_owner,
        proxy_caller_application_id=deployment.proxy_caller_application_id,
        proxy_caller_credential_id=deployment.proxy_caller_credential_id,
        proxy_caller_secret_reference=deployment.proxy_caller_secret_reference,
        gateway_endpoint=deployment.endpoint,
        expected={**proof.contract, "resource_digest": proof.digest},
        model_registry=model_registry,
        tracking_client=tracking_client,
        require_resource_binding=True,
    )
    if final.digest != proof.digest:
        raise RuntimeError("Gateway runtime resource proof drifted during binding")
