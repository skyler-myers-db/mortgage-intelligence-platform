"""The deploy payload's literal env and its live Gateway resource proof.

Moved out of ``app_deployment_rollback`` (M0-PR8) to keep that module under the
file-size gate. The resolver is injected so the rollback module's own name for
``resolve_exact_resource_proof`` stays the one its callers and tests replace.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from tools.databricks.export_gateway_runtime_contract import ExactGatewayRuntimeProof


def env_map(payload: dict[str, object]) -> dict[str, str]:
    """The literal env values of a deploy payload, by name."""

    env_vars = cast(list[object], payload["env_vars"])
    return {
        str(item["name"]): str(item.get("value") or "")
        for item in env_vars
        if isinstance(item, dict) and "value" in item
    }


def payload_resource_proof(
    workspace: Any,
    *,
    payload: dict[str, object],
    genie_space_id: str,
    resolve_exact_resource_proof: Callable[..., ExactGatewayRuntimeProof],
) -> ExactGatewayRuntimeProof:
    """The live exact Gateway resource proof a deploy payload's env names."""

    env = env_map(payload)
    required = (
        "MIP_DEFAULT_CATALOG",
        "MIP_AGENT_SUPERVISOR_NAME",
        "MIP_AGENT_RUNTIME_CLIENT_ID",
        "MIP_REVIEWED_FUNCTION_OWNER",
        "MIP_AGENT_PROXY_CLIENT_ID",
        "MIP_AGENT_PROXY_CREDENTIAL_ID",
        "MIP_AGENT_PROXY_SECRET_REFERENCE",
        "MIP_AI_GATEWAY_ENDPOINT",
        "MIP_AI_GATEWAY_AGENT_MODEL",
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
        "MIP_AI_GATEWAY_AGENT_MODEL_SOURCE",
        "MIP_AI_GATEWAY_EXPERIMENT_NAME",
        "MIP_AI_GATEWAY_EXPERIMENT_ID",
        "MIP_AI_GATEWAY_INFERENCE_TABLE",
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256",
    )
    if not genie_space_id.strip() or not all(env.get(name) for name in required):
        raise RuntimeError("App rollback payload lacks its exact Gateway resource contract")
    proof = resolve_exact_resource_proof(
        workspace,
        supervisor_name=env["MIP_AGENT_SUPERVISOR_NAME"],
        catalog=env["MIP_DEFAULT_CATALOG"],
        genie_space_id=genie_space_id,
        runtime_application_id=env["MIP_AGENT_RUNTIME_CLIENT_ID"],
        reviewed_function_owner=env["MIP_REVIEWED_FUNCTION_OWNER"],
        proxy_caller_application_id=env["MIP_AGENT_PROXY_CLIENT_ID"],
        proxy_caller_credential_id=env["MIP_AGENT_PROXY_CREDENTIAL_ID"],
        proxy_caller_secret_reference=env["MIP_AGENT_PROXY_SECRET_REFERENCE"],
        supervisor_id=env["MIP_AGENT_SUPERVISOR_ID"],
        gateway_endpoint=env["MIP_AI_GATEWAY_ENDPOINT"],
        gateway_model_family_name=env.get("MIP_AI_GATEWAY_AGENT_MODEL_FAMILY"),
        gateway_experiment_base_name=env.get("MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE"),
        gateway_table_prefix=env.get("MIP_AI_GATEWAY_TABLE_PREFIX"),
        require_resource_binding=True,
    )
    expected = {
        "gateway_model_name": env["MIP_AI_GATEWAY_AGENT_MODEL"],
        "gateway_model_version": env["MIP_AI_GATEWAY_AGENT_MODEL_VERSION"],
        "gateway_model_source": env["MIP_AI_GATEWAY_AGENT_MODEL_SOURCE"],
        "gateway_experiment_name": env["MIP_AI_GATEWAY_EXPERIMENT_NAME"],
        "gateway_experiment_id": env["MIP_AI_GATEWAY_EXPERIMENT_ID"],
        "gateway_inference_table": env["MIP_AI_GATEWAY_INFERENCE_TABLE"],
    }
    drifted = sorted(key for key, value in expected.items() if proof.contract.get(key) != value)
    if drifted or proof.digest != env["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"]:
        raise RuntimeError(
            "App rollback payload does not match its live exact Gateway resource proof"
        )
    return proof
