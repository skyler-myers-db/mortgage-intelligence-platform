"""Extract the exact Gateway binding from a signed App rollback payload."""

from __future__ import annotations

import hashlib
import json
from typing import cast

from backend.agents.gateway_contract import (
    gateway_runtime_binding_hash,
    parse_gateway_runtime_resource_contract,
)
from tools.databricks.gateway_legacy_rollback import (
    PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    PRIOR_V2_GATEWAY_RESOURCE_FIELDS,
    prior_v2_gateway_resource_digest,
)


def _prior_v2_binding_hash(env: dict[str, str], contract_json: str) -> str:
    try:
        decoded = json.loads(contract_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("App rollback payload has an invalid Gateway binding") from exc
    if (
        not isinstance(decoded, dict)
        or set(decoded) != PRIOR_V2_GATEWAY_RESOURCE_FIELDS
        or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in decoded.items()
        )
        or decoded.get("proof_version") != PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
        or json.dumps(decoded, sort_keys=True, separators=(",", ":")) != contract_json
    ):
        raise RuntimeError("App rollback payload has an invalid Gateway binding")
    contract = dict(decoded)
    prior_v2_gateway_resource_digest(contract)
    expected = {
        "gateway_endpoint": env["MIP_AGENT_SERVING_ENDPOINT"],
        "supervisor_id": env["MIP_AGENT_SUPERVISOR_ID"],
        "supervisor_endpoint": env["MIP_AGENT_SUPERVISOR_ENDPOINT"],
        "runtime_application_id": env["MIP_AGENT_RUNTIME_CLIENT_ID"],
        "gateway_model_name": env["MIP_AI_GATEWAY_AGENT_MODEL"],
        "gateway_model_version": env["MIP_AI_GATEWAY_AGENT_MODEL_VERSION"],
        "gateway_inference_table": env["MIP_AI_GATEWAY_INFERENCE_TABLE"],
        "proxy_caller_application_id": env["MIP_AGENT_PROXY_CLIENT_ID"],
        "proxy_caller_credential_id": env["MIP_AGENT_PROXY_CREDENTIAL_ID"],
        "proxy_caller_secret_reference": env["MIP_AGENT_PROXY_SECRET_REFERENCE"],
    }
    if any(contract.get(key) != value for key, value in expected.items()):
        raise RuntimeError("App rollback payload Gateway binding contradicts its signed proof")
    canonical = "\0".join(
        [
            expected["gateway_endpoint"],
            expected["supervisor_id"],
            expected["supervisor_endpoint"],
            expected["runtime_application_id"],
            expected["gateway_model_name"],
            expected["gateway_model_version"],
            expected["gateway_inference_table"],
            expected["proxy_caller_application_id"],
            expected["proxy_caller_credential_id"],
            expected["proxy_caller_secret_reference"],
        ]
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def payload_gateway_binding(payload: dict[str, object]) -> str | None:
    env_vars = cast(list[object], payload["env_vars"])
    env = {
        str(item["name"]): str(item.get("value") or "")
        for item in env_vars
        if isinstance(item, dict) and "value" in item
    }
    names = (
        "MIP_AGENT_SERVING_ENDPOINT",
        "MIP_AGENT_SUPERVISOR_ID",
        "MIP_AGENT_SUPERVISOR_ENDPOINT",
        "MIP_AGENT_RUNTIME_CLIENT_ID",
        "MIP_AI_GATEWAY_AGENT_MODEL",
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
        "MIP_AI_GATEWAY_INFERENCE_TABLE",
        "MIP_AGENT_PROXY_CLIENT_ID",
        "MIP_AGENT_PROXY_CREDENTIAL_ID",
        "MIP_AGENT_PROXY_SECRET_REFERENCE",
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON",
    )
    if not all(env.get(name) for name in names):
        return None
    contract_json = env["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"]
    try:
        version = int(env["MIP_AI_GATEWAY_AGENT_MODEL_VERSION"])
        decoded = json.loads(contract_json)
    except ValueError as exc:
        raise RuntimeError("App rollback payload has an invalid Gateway binding") from exc
    if (
        isinstance(decoded, dict)
        and decoded.get("proof_version") == PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    ):
        return _prior_v2_binding_hash(env, contract_json)
    try:
        workspace_host = parse_gateway_runtime_resource_contract(contract_json)["workspace_host"]
    except ValueError as exc:
        raise RuntimeError("App rollback payload has an invalid Gateway binding") from exc
    return gateway_runtime_binding_hash(
        endpoint=env["MIP_AGENT_SERVING_ENDPOINT"],
        supervisor_id=env["MIP_AGENT_SUPERVISOR_ID"],
        upstream_endpoint=env["MIP_AGENT_SUPERVISOR_ENDPOINT"],
        runtime_application_id=env["MIP_AGENT_RUNTIME_CLIENT_ID"],
        workspace_host=workspace_host,
        model_name=env["MIP_AI_GATEWAY_AGENT_MODEL"],
        model_version=version,
        inference_table=env["MIP_AI_GATEWAY_INFERENCE_TABLE"],
        proxy_caller_application_id=env["MIP_AGENT_PROXY_CLIENT_ID"],
        proxy_caller_credential_id=env["MIP_AGENT_PROXY_CREDENTIAL_ID"],
        proxy_caller_secret_reference=env["MIP_AGENT_PROXY_SECRET_REFERENCE"],
    )
