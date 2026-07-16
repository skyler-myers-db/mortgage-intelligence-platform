#!/usr/bin/env python3
"""Resolve and export the exact source-bound Gateway/Supervisor contract."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mlflow import MlflowClient  # noqa: E402

from backend.agents.gateway_contract import (  # noqa: E402
    DEFAULT_GATEWAY_AGENT_MODEL,
    DEFAULT_GATEWAY_ENDPOINT,
    DEFAULT_GATEWAY_INFERENCE_TABLE,
    gateway_proxy_source_hash,
    gateway_runtime_binding_hash,
)
from databricks.sdk import WorkspaceClient  # noqa: E402
from tools.databricks.provision_gateway_responses_agent import (  # noqa: E402
    GatewayAgentDeployment,
    verify_gateway_responses_agent,
)


def _supervisors(client: Any) -> list[Mapping[str, Any]]:
    response = client.api_client.do("GET", "/api/2.1/supervisor-agents")
    rows = response if isinstance(response, list) else response.get("supervisor_agents", [])
    return [row for row in rows if isinstance(row, Mapping)]


def resolve_contract(
    client: Any,
    *,
    supervisor_name: str,
    model_registry: Any | None = None,
) -> dict[str, str]:
    matches = [
        row
        for row in _supervisors(client)
        if str(row.get("display_name") or "").strip() == supervisor_name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one Supervisor named {supervisor_name!r}, found {len(matches)}"
        )
    supervisor_id = str(matches[0].get("supervisor_agent_id") or "").strip()
    upstream = str(matches[0].get("endpoint_name") or "").strip()
    if not supervisor_id or not upstream:
        raise RuntimeError("managed Supervisor identity or endpoint is missing")

    details = client.serving_endpoints.get(DEFAULT_GATEWAY_ENDPOINT)
    config = getattr(details, "config", None)
    entities = getattr(config, "served_entities", None) or []
    if len(entities) != 1:
        raise RuntimeError("Gateway endpoint must serve exactly one reviewed Agent Model")
    entity = entities[0]
    if str(getattr(entity, "entity_name", "") or "") != DEFAULT_GATEWAY_AGENT_MODEL:
        raise RuntimeError("Gateway endpoint serves an unexpected Agent Model")
    try:
        model_version = int(str(getattr(entity, "entity_version", "") or ""))
    except ValueError as exc:
        raise RuntimeError("Gateway Agent Model version is invalid") from exc
    source_hash = gateway_proxy_source_hash(upstream_endpoint=upstream)
    deployment = GatewayAgentDeployment(
        endpoint=DEFAULT_GATEWAY_ENDPOINT,
        upstream_endpoint=upstream,
        model_name=DEFAULT_GATEWAY_AGENT_MODEL,
        model_version=model_version,
        source_hash=source_hash,
        inference_table=DEFAULT_GATEWAY_INFERENCE_TABLE,
    )
    verify_gateway_responses_agent(client, deployment, model_registry=model_registry)
    binding_hash = gateway_runtime_binding_hash(
        endpoint=deployment.endpoint,
        supervisor_id=supervisor_id,
        upstream_endpoint=deployment.upstream_endpoint,
        model_name=deployment.model_name,
        model_version=deployment.model_version,
        inference_table=deployment.inference_table,
    )
    return {
        "MIP_AGENT_SERVING_ENDPOINT": deployment.endpoint,
        "MIP_AGENT_SUPERVISOR_ENDPOINT": deployment.upstream_endpoint,
        "MIP_AGENT_SUPERVISOR_ID": supervisor_id,
        "MIP_AI_GATEWAY_ENDPOINT": deployment.endpoint,
        "MIP_AI_GATEWAY_INFERENCE_TABLE": deployment.inference_table,
        "MIP_AI_GATEWAY_AGENT_MODEL": deployment.model_name,
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION": str(deployment.model_version),
        "MIP_EXPECTED_AGENT_GATEWAY_BINDING_SHA256": binding_hash,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-env", type=Path, required=True)
    parser.add_argument("--supervisor-name", default="Mortgage Growth Agent")
    args = parser.parse_args(argv)
    contract = resolve_contract(
        WorkspaceClient(),
        supervisor_name=args.supervisor_name,
        model_registry=MlflowClient(
            tracking_uri="databricks",
            registry_uri="databricks-uc",
        ),
    )
    with args.github_env.open("a", encoding="utf-8") as handle:
        for key, value in contract.items():
            handle.write(f"{key}={value}\n")
    print(
        "[gateway-contract] exported source-bound endpoint, model version, "
        "Supervisor, inference table, and binding digest"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
