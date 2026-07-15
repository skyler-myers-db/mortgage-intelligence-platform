from __future__ import annotations

from types import SimpleNamespace

from backend.agents.gateway_contract import (
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_UPSTREAM_TAG,
    gateway_proxy_source_hash,
)
from backend.config.settings import Settings
from backend.services.supervisor_runtime import verify_supervisor_runtime

_UPSTREAM = "managed-supervisor-endpoint"
_MODEL = "mip.audit.mortgage_growth_supervisor_proxy"
_TABLE = "mip.audit.mip_agent_gateway_growth_agent"


class _ApiClient:
    def do(self, method: str, path: str) -> dict[str, str]:
        assert method == "GET"
        assert path == "/api/2.1/supervisor-agents/supervisor-1"
        return {
            "supervisor_agent_id": "supervisor-1",
            "endpoint_name": "managed-supervisor-endpoint",
        }


class _ServingEndpoints:
    def __init__(
        self,
        *,
        upstream: str = _UPSTREAM,
        source_hash: str | None = None,
        model_version: str = "7",
    ) -> None:
        self.upstream = upstream
        self.model_version = model_version
        self.source_hash = source_hash or gateway_proxy_source_hash(
            upstream_endpoint=_UPSTREAM
        )

    def get(self, endpoint: str) -> object:
        assert endpoint == "mip-growth-agent-gateway"
        return SimpleNamespace(
            state=SimpleNamespace(ready="READY"),
            task="agent/v1/responses",
            pending_config=None,
            config=SimpleNamespace(
                served_entities=[
                    SimpleNamespace(
                        entity_name=_MODEL,
                        entity_version=self.model_version,
                        environment_vars={
                            "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": self.upstream,
                        },
                    )
                ]
            ),
            tags=[
                SimpleNamespace(key=GATEWAY_PROXY_SOURCE_HASH_TAG, value=self.source_hash),
                SimpleNamespace(key=GATEWAY_UPSTREAM_TAG, value=self.upstream),
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


def test_runtime_verifies_managed_identity_and_gateway_product_endpoint_separately() -> None:
    settings = Settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint="managed-supervisor-endpoint",
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )
    client = SimpleNamespace(api_client=_ApiClient(), serving_endpoints=_ServingEndpoints())

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert reason is None
    assert runtime is not None
    assert runtime.endpoint == "mip-growth-agent-gateway"
    assert runtime.supervisor_id == "supervisor-1"
    assert runtime.supervisor_endpoint == _UPSTREAM
    assert runtime.model_name == _MODEL
    assert runtime.task == "agent/v1/responses"


def test_runtime_fails_when_managed_identity_does_not_match_configured_upstream() -> None:
    settings = Settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint="different-supervisor-endpoint",
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )
    client = SimpleNamespace(api_client=_ApiClient(), serving_endpoints=_ServingEndpoints())

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "supervisor_endpoint_mismatch"


def test_runtime_rejects_proxy_upstream_drift() -> None:
    settings = Settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )
    client = SimpleNamespace(
        api_client=_ApiClient(),
        serving_endpoints=_ServingEndpoints(upstream="wrong-supervisor"),
    )

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "gateway_proxy_upstream_mismatch"


def test_runtime_rejects_reviewed_source_drift() -> None:
    settings = Settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )
    client = SimpleNamespace(
        api_client=_ApiClient(),
        serving_endpoints=_ServingEndpoints(source_hash="0" * 64),
    )

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "gateway_proxy_source_mismatch"


def test_runtime_rejects_same_model_name_at_unreviewed_version() -> None:
    settings = Settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )
    client = SimpleNamespace(
        api_client=_ApiClient(),
        serving_endpoints=_ServingEndpoints(model_version="8"),
    )

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "gateway_proxy_model_version_mismatch"


def test_runtime_rejects_self_recursive_gateway_before_workspace_calls() -> None:
    settings = Settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint=_UPSTREAM,
        mip_ai_gateway_endpoint=_UPSTREAM,
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )

    runtime, reason = verify_supervisor_runtime(SimpleNamespace(), settings)

    assert runtime is None
    assert reason == "gateway_endpoint_recurses_to_itself"


def test_runtime_rejects_ai_gateway_proof_for_a_different_outer_endpoint() -> None:
    settings = Settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="unrelated-proof-endpoint",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )

    runtime, reason = verify_supervisor_runtime(SimpleNamespace(), settings)

    assert runtime is None
    assert reason == "gateway_product_endpoint_mismatch"
