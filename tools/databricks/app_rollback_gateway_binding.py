"""Extract the exact Gateway binding from a signed App rollback payload."""

from __future__ import annotations

from typing import cast

from backend.agents.gateway_contract import gateway_runtime_binding_hash


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
    )
    if not all(env.get(name) for name in names):
        return None
    try:
        version = int(env["MIP_AI_GATEWAY_AGENT_MODEL_VERSION"])
    except ValueError as exc:
        raise RuntimeError("App rollback payload has an invalid Gateway model version") from exc
    return gateway_runtime_binding_hash(
        endpoint=env["MIP_AGENT_SERVING_ENDPOINT"],
        supervisor_id=env["MIP_AGENT_SUPERVISOR_ID"],
        upstream_endpoint=env["MIP_AGENT_SUPERVISOR_ENDPOINT"],
        runtime_application_id=env["MIP_AGENT_RUNTIME_CLIENT_ID"],
        model_name=env["MIP_AI_GATEWAY_AGENT_MODEL"],
        model_version=version,
        inference_table=env["MIP_AI_GATEWAY_INFERENCE_TABLE"],
        proxy_caller_application_id=env["MIP_AGENT_PROXY_CLIENT_ID"],
        proxy_caller_credential_id=env["MIP_AGENT_PROXY_CREDENTIAL_ID"],
        proxy_caller_secret_reference=env["MIP_AGENT_PROXY_SECRET_REFERENCE"],
    )
