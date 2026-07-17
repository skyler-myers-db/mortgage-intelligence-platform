from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agents import gateway_live_resource_contract as live_resource_module
from backend.agents.gateway_contract import (
    GATEWAY_BURST_SCALING_ENABLED,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_ROUTE_OPTIMIZED,
    GATEWAY_SCALE_TO_ZERO_ENABLED,
    GATEWAY_STATIC_ENV,
    GATEWAY_TRAFFIC_PERCENTAGE,
    GATEWAY_UPSTREAM_TAG,
    GATEWAY_WORKLOAD_SIZE,
    GATEWAY_WORKLOAD_TYPE,
    gateway_proxy_source_hash,
    gateway_runtime_binding_hash,
)
from backend.agents.supervisor_contract import supervisor_contract_hash
from backend.config.settings import Settings
from backend.services.supervisor_runtime import verify_supervisor_runtime
from tests.fixtures.gateway_runtime_resources import (
    gateway_runtime_contract_for_scope,
    signed_gateway_runtime_environment,
)

_UPSTREAM = "managed-supervisor-endpoint"
_MODEL = "mip.audit.mortgage_growth_supervisor_proxy"
_TABLE = "mip.audit.mip_agent_gateway_growth_agent"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mip_agent_orchestrator": True,
        "mip_agent_supervisor_id": "supervisor-1",
        "mip_agent_serving_endpoint": "mip-growth-agent-gateway",
        "mip_ai_gateway_endpoint": "mip-growth-agent-gateway",
        "mip_agent_supervisor_endpoint": _UPSTREAM,
        "mip_agent_gateway_model": _MODEL,
        "mip_agent_gateway_model_version": 7,
        "mip_ai_gateway_inference_table": _TABLE,
        "mip_agent_runtime_client_id": "runtime-client",
        "mip_default_catalog": "mip",
        "genie_space_id": "space-123",
        "mip_ai_gateway_experiment_id": "experiment-7",
        "mip_ai_gateway_experiment_name": "/Users/runtime-client/proxy",
        "mip_ai_gateway_agent_model_source": "models:/m-reviewed-proxy",
    }
    values.update(overrides)
    binding_values = (
        values.get("mip_agent_serving_endpoint"),
        values.get("mip_agent_supervisor_id"),
        values.get("mip_agent_supervisor_endpoint"),
        values.get("mip_agent_runtime_client_id"),
        values.get("mip_agent_gateway_model"),
        values.get("mip_agent_gateway_model_version"),
        values.get("mip_ai_gateway_inference_table"),
    )
    if "mip_expected_agent_gateway_binding_sha256" not in overrides:
        if all(binding_values):
            values["mip_expected_agent_gateway_binding_sha256"] = gateway_runtime_binding_hash(
                endpoint=str(binding_values[0]),
                supervisor_id=str(binding_values[1]),
                upstream_endpoint=str(binding_values[2]),
                runtime_application_id=str(binding_values[3]),
                model_name=str(binding_values[4]),
                model_version=int(str(binding_values[5])),
                inference_table=str(binding_values[6]),
            )
        else:
            values["mip_expected_agent_gateway_binding_sha256"] = "b" * 64
    contract = gateway_runtime_contract_for_scope(
        catalog=str(values.get("mip_default_catalog") or ""),
        genie_space_id=str(values.get("genie_space_id") or ""),
        runtime_application_id=str(values.get("mip_agent_runtime_client_id") or ""),
        supervisor_id=str(values.get("mip_agent_supervisor_id") or ""),
        supervisor_endpoint=str(values.get("mip_agent_supervisor_endpoint") or ""),
        gateway_endpoint=str(values.get("mip_agent_serving_endpoint") or ""),
        gateway_model_name=str(values.get("mip_agent_gateway_model") or ""),
        gateway_model_version=str(values.get("mip_agent_gateway_model_version") or ""),
        gateway_model_source=str(values.get("mip_ai_gateway_agent_model_source") or ""),
        gateway_experiment_name=str(values.get("mip_ai_gateway_experiment_name") or ""),
        gateway_experiment_id=str(values.get("mip_ai_gateway_experiment_id") or ""),
        gateway_inference_table=str(values.get("mip_ai_gateway_inference_table") or ""),
    )
    resource_environment = signed_gateway_runtime_environment(contract)
    values.update(
        {
            "mip_expected_agent_gateway_resource_contract_json": resource_environment[
                "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON"
            ],
            "mip_expected_agent_gateway_resource_sha256": resource_environment[
                "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"
            ],
            "mip_expected_agent_gateway_resource_signature": resource_environment[
                "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE"
            ],
            "mip_gateway_model_attestation_verify_key": resource_environment[
                "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"
            ],
        }
    )
    return Settings(**values)


def _resource_environment() -> dict[str, str]:
    configured = _settings()
    return {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": (
            configured.mip_expected_agent_gateway_resource_contract_json or ""
        ),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": (
            configured.mip_expected_agent_gateway_resource_sha256 or ""
        ),
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": (
            configured.mip_expected_agent_gateway_resource_signature or ""
        ),
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": (
            configured.mip_gateway_model_attestation_verify_key or ""
        ),
    }


class _ApiClient:
    def do(self, method: str, path: str) -> dict[str, str]:
        assert method == "GET"
        assert path == "/api/2.1/supervisor-agents/supervisor-1"
        return {
            "supervisor_agent_id": "supervisor-1",
            "endpoint_name": "managed-supervisor-endpoint",
            "creator": "runtime-client",
        }


class _ServingEndpoints:
    def __init__(
        self,
        *,
        upstream: str = _UPSTREAM,
        source_hash: str | None = None,
        model_version: str = "7",
        endpoint_id: str | None = "test-gateway-endpoint-id",
        supervisor_endpoint_id: str | None = "test-supervisor-endpoint-id",
    ) -> None:
        self.upstream = upstream
        self.model_version = model_version
        self.endpoint_id = endpoint_id
        self.supervisor_endpoint_id = supervisor_endpoint_id
        self.source_hash = source_hash or gateway_proxy_source_hash(
            upstream_endpoint=_UPSTREAM,
            catalog="mip",
            genie_space_id="space-123",
        )

    def get(self, endpoint: str) -> object:
        if endpoint == _UPSTREAM:
            return SimpleNamespace(
                id=self.supervisor_endpoint_id,
                creator="runtime-client",
            )
        assert endpoint == "mip-growth-agent-gateway"
        return SimpleNamespace(
            id=self.endpoint_id,
            creator="runtime-client",
            state=SimpleNamespace(ready="READY"),
            task="agent/v1/responses",
            pending_config=None,
            config=SimpleNamespace(
                served_entities=[
                    SimpleNamespace(
                        entity_name=_MODEL,
                        entity_version=self.model_version,
                        name=f"mip-growth-supervisor-proxy-{self.model_version}",
                        environment_vars={
                            **GATEWAY_STATIC_ENV,
                            **_resource_environment(),
                            "MIP_UPSTREAM_SUPERVISOR_ID": "supervisor-1",
                            "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": self.upstream,
                            "MIP_UPSTREAM_SUPERVISOR_CREATOR": "runtime-client",
                            "MIP_SUPERVISOR_CATALOG": "mip",
                            "MIP_SUPERVISOR_GENIE_SPACE_ID": "space-123",
                            "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
                                genie_space_id="space-123",
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
                            served_entity_name=(
                                f"mip-growth-supervisor-proxy-{self.model_version}"
                            ),
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
                SimpleNamespace(key=GATEWAY_PROXY_SOURCE_HASH_TAG, value=self.source_hash),
                SimpleNamespace(key=GATEWAY_UPSTREAM_TAG, value=self.upstream),
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


def test_runtime_verifies_managed_identity_and_gateway_product_endpoint_separately() -> None:
    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint="managed-supervisor-endpoint",
        genie_space_id="space-123",
        mip_default_catalog="mip",
        mip_agent_runtime_client_id="runtime-client",
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints())

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert reason is None
    assert runtime is not None
    assert runtime.endpoint == "mip-growth-agent-gateway"
    assert runtime.supervisor_id == "supervisor-1"
    assert runtime.supervisor_endpoint == _UPSTREAM
    assert runtime.model_name == _MODEL
    assert runtime.task == "agent/v1/responses"


def test_app_runtime_does_not_query_private_gateway_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AppWorkspace:
        serving_endpoints = _ServingEndpoints()

        @property
        def api_client(self) -> object:
            raise AssertionError("App must not query private Supervisor or experiment ACL APIs")

        @property
        def registered_models(self) -> object:
            raise AssertionError("App must not query the runtime-owned model registry")

    def _private_mlflow_client(**_kwargs: object) -> object:
        raise AssertionError("App must not query runtime-owned MLflow resources")

    monkeypatch.setattr(live_resource_module, "MlflowClient", _private_mlflow_client)
    runtime, reason = verify_supervisor_runtime(_AppWorkspace(), _settings())

    assert reason is None
    assert runtime is not None


def test_runtime_rejects_human_owned_outer_gateway() -> None:
    class _CreatorEndpoints(_ServingEndpoints):
        def get(self, endpoint: str) -> object:
            details = super().get(endpoint)
            details.creator = "skyler@entrada.ai"
            return details

    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        genie_space_id="space-123",
        mip_default_catalog="mip",
        mip_agent_runtime_client_id="runtime-client",
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
    )

    runtime, reason = verify_supervisor_runtime(
        SimpleNamespace(serving_endpoints=_CreatorEndpoints()),
        settings,
    )

    assert runtime is None
    assert reason == "gateway_endpoint_creator_mismatch"


@pytest.mark.parametrize(
    "endpoint_id",
    ["attacker-gateway-endpoint-id", None],
    ids=["drifted", "missing"],
)
def test_runtime_rejects_outer_gateway_immutable_id_drift(
    endpoint_id: str | None,
) -> None:
    runtime, reason = verify_supervisor_runtime(
        SimpleNamespace(serving_endpoints=_ServingEndpoints(endpoint_id=endpoint_id)),
        _settings(),
    )

    assert runtime is None
    assert reason == "gateway_endpoint_id_mismatch"


def test_runtime_fails_when_managed_identity_does_not_match_configured_upstream() -> None:
    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint="different-supervisor-endpoint",
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
        mip_agent_runtime_client_id="runtime-client",
        mip_default_catalog="mip",
        genie_space_id="space-123",
    )
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints())

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "gateway_proxy_resource_environment_mismatch"


def test_runtime_rejects_proxy_upstream_drift() -> None:
    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
        mip_agent_runtime_client_id="runtime-client",
        mip_default_catalog="mip",
        genie_space_id="space-123",
    )
    client = SimpleNamespace(
        api_client=_ApiClient(),
        serving_endpoints=_ServingEndpoints(upstream="wrong-supervisor"),
    )

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "gateway_proxy_upstream_mismatch"


def test_runtime_rejects_outer_gateway_resource_envelope_drift() -> None:
    class _DriftedEndpoints(_ServingEndpoints):
        def get(self, endpoint: str) -> object:
            details = super().get(endpoint)
            details.config.served_entities[0].environment_vars[
                "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"
            ] = "0" * 64
            return details

    runtime, reason = verify_supervisor_runtime(
        SimpleNamespace(serving_endpoints=_DriftedEndpoints()),
        _settings(),
    )

    assert runtime is None
    assert reason == "gateway_proxy_resource_environment_mismatch"


def test_runtime_rejects_reviewed_source_drift() -> None:
    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
        mip_agent_runtime_client_id="runtime-client",
        mip_default_catalog="mip",
        genie_space_id="space-123",
    )
    client = SimpleNamespace(
        api_client=_ApiClient(),
        serving_endpoints=_ServingEndpoints(source_hash="0" * 64),
    )

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "gateway_proxy_source_mismatch"


def test_runtime_rejects_payload_binding_digest_drift_before_workspace_calls() -> None:
    settings = _settings(mip_expected_agent_gateway_binding_sha256="0" * 64)

    runtime, reason = verify_supervisor_runtime(SimpleNamespace(), settings)

    assert runtime is None
    assert reason == "gateway_runtime_binding_digest_mismatch"


def test_runtime_rejects_same_model_name_at_unreviewed_version() -> None:
    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="mip-growth-agent-gateway",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
        mip_agent_runtime_client_id="runtime-client",
    )
    client = SimpleNamespace(
        api_client=_ApiClient(),
        serving_endpoints=_ServingEndpoints(model_version="8"),
    )

    runtime, reason = verify_supervisor_runtime(client, settings)

    assert runtime is None
    assert reason == "gateway_proxy_model_version_mismatch"


def test_runtime_rejects_self_recursive_gateway_before_workspace_calls() -> None:
    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint=_UPSTREAM,
        mip_ai_gateway_endpoint=_UPSTREAM,
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
        mip_agent_runtime_client_id="runtime-client",
    )

    runtime, reason = verify_supervisor_runtime(SimpleNamespace(), settings)

    assert runtime is None
    assert reason == "gateway_endpoint_recurses_to_itself"


def test_runtime_rejects_ai_gateway_proof_for_a_different_outer_endpoint() -> None:
    settings = _settings(
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-1",
        mip_agent_serving_endpoint="mip-growth-agent-gateway",
        mip_ai_gateway_endpoint="unrelated-proof-endpoint",
        mip_agent_supervisor_endpoint=_UPSTREAM,
        mip_agent_gateway_model_version=7,
        mip_ai_gateway_inference_table=_TABLE,
        mip_agent_runtime_client_id="runtime-client",
    )

    runtime, reason = verify_supervisor_runtime(SimpleNamespace(), settings)

    assert runtime is None
    assert reason == "gateway_product_endpoint_mismatch"
