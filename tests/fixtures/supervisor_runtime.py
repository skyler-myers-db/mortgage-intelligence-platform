"""Exact source-bound Supervisor proxy fixtures for service unit tests."""

from __future__ import annotations

from types import SimpleNamespace

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_MODEL,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_UPSTREAM_TAG,
    gateway_proxy_source_hash,
)
from backend.config.settings import Settings

GATEWAY_ENDPOINT = "mip-growth-agent-gateway"
SUPERVISOR_ENDPOINT = "mas-supervisor-endpoint"
SUPERVISOR_ID = "supervisor-123"
INFERENCE_TABLE = "mip.audit.mip_agent_gateway_growth_agent"
MODEL_VERSION = 7


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
    }
    values.update(overrides)
    return Settings(**values)


def supervisor_metadata() -> dict[str, str]:
    return {
        "supervisor_agent_id": SUPERVISOR_ID,
        "endpoint_name": SUPERVISOR_ENDPOINT,
    }


def gateway_endpoint_details(
    *,
    ready: str = "READY",
    task: str = "agent/v1/responses",
    upstream_endpoint: str = SUPERVISOR_ENDPOINT,
) -> object:
    return SimpleNamespace(
        state=SimpleNamespace(ready=ready),
        task=task,
        pending_config=None,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name=DEFAULT_GATEWAY_AGENT_MODEL,
                    entity_version=str(MODEL_VERSION),
                    environment_vars={
                        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream_endpoint,
                    },
                )
            ]
        ),
        tags=[
            SimpleNamespace(
                key=GATEWAY_PROXY_SOURCE_HASH_TAG,
                value=gateway_proxy_source_hash(upstream_endpoint=SUPERVISOR_ENDPOINT),
            ),
            SimpleNamespace(key=GATEWAY_UPSTREAM_TAG, value=SUPERVISOR_ENDPOINT),
        ],
        ai_gateway=SimpleNamespace(
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix="mip_agent_gateway_growth_agent",
            )
        ),
    )
