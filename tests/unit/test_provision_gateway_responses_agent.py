from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound

from tools.databricks import provision_gateway_responses_agent as gateway
from tools.databricks.provision_gateway_responses_agent import (
    GatewayAgentDeployment,
    _current_model_version,
    ensure_gateway_responses_agent,
    gateway_agent_source_hash,
    verify_gateway_responses_agent,
)


def test_gateway_agent_source_hash_binds_code_and_upstream_endpoint() -> None:
    first = gateway_agent_source_hash(upstream_endpoint="supervisor-a")
    assert len(first) == 64
    assert first != gateway_agent_source_hash(upstream_endpoint="supervisor-b")


def test_current_model_version_requires_exactly_one_expected_registered_model() -> None:
    drifted = SimpleNamespace(
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(entity_name="mip.app.other", entity_version="9"),
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="3",
                ),
            ]
        )
    )

    assert (
        _current_model_version(
            drifted,
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
        )
        is None
    )

    details = SimpleNamespace(
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="3",
                )
            ]
        )
    )
    assert (
        _current_model_version(
            details,
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
        )
        == 3
    )


def test_current_model_version_prefers_interrupted_pending_update() -> None:
    details = SimpleNamespace(
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="3",
                )
            ]
        ),
        pending_config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="4",
                )
            ]
        ),
    )

    assert (
        _current_model_version(
            details,
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
        )
        == 4
    )


class _Client:
    def __init__(self, versions: list[object] | None = None) -> None:
        self.versions = versions or []
        self.tags: list[tuple[str, str, str, str]] = []

    def search_model_versions(self, query: str) -> list[object]:
        assert query == "name='mip.audit.mortgage_growth_supervisor_proxy'"
        return self.versions

    def set_model_version_tag(self, *args: str) -> None:
        self.tags.append(args)


class _ServingEndpoints:
    def __init__(self, details: object | None = None) -> None:
        self.details = details
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.gateway_updates: list[dict[str, Any]] = []
        self.patches: list[dict[str, Any]] = []
        self.events: list[str] = []

    def get(self, endpoint: str) -> object:
        assert endpoint == "mip-growth-agent-gateway"
        if self.details is None:
            raise NotFound("missing")
        return self.details

    def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def update_config(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)

    def update_config_and_wait(self, **kwargs: Any) -> object:
        self.events.append("update_config_and_wait")
        self.updated.append(kwargs)
        assert self.details is not None
        self.details.config = SimpleNamespace(
            served_entities=kwargs["served_entities"],
            traffic_config=kwargs["traffic_config"],
        )
        self.details.pending_config = None
        return self.details

    def wait_get_serving_endpoint_not_updating(self, endpoint: str) -> object:
        assert endpoint == "mip-growth-agent-gateway"
        self.events.append("wait_not_updating")
        assert self.details is not None
        self.details.pending_config = None
        return self.details

    def put_ai_gateway(self, **kwargs: Any) -> None:
        self.events.append("put_ai_gateway")
        self.gateway_updates.append(kwargs)

    def patch(self, **kwargs: Any) -> None:
        self.events.append("patch")
        self.patches.append(kwargs)


def _patch_mlflow(monkeypatch, *, client: _Client) -> None:
    monkeypatch.setattr(gateway, "MlflowClient", lambda: client)
    monkeypatch.setattr(gateway.mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(gateway.mlflow, "set_registry_uri", lambda _uri: None)
    monkeypatch.setattr(
        gateway.mlflow,
        "set_experiment",
        lambda _name: SimpleNamespace(experiment_id="experiment-7"),
    )


def test_log_gateway_model_temporarily_binds_upstream_for_mlflow_validation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_ENDPOINT", "prior-supervisor")
    monkeypatch.setattr(gateway, "_start_mlflow_run", nullcontext)

    def fake_log_model(*, upstream_endpoint: str) -> object:
        assert gateway.os.environ["MIP_UPSTREAM_SUPERVISOR_ENDPOINT"] == "managed-supervisor"
        assert upstream_endpoint == "managed-supervisor"
        return SimpleNamespace(model_uri="runs:/run/model")

    monkeypatch.setattr(gateway, "_log_responses_model", fake_log_model)

    logged = gateway._log_gateway_model(upstream_endpoint="managed-supervisor")

    assert logged.model_uri == "runs:/run/model"
    assert gateway.os.environ["MIP_UPSTREAM_SUPERVISOR_ENDPOINT"] == "prior-supervisor"


def test_ensure_gateway_agent_creates_one_responses_endpoint_with_exact_gateway_table(
    monkeypatch,
) -> None:
    client = _Client()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="runs:/run/model"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda *_args: SimpleNamespace(version="4"),
    )
    serving = _ServingEndpoints()
    workspace = SimpleNamespace(serving_endpoints=serving)

    deployment = ensure_gateway_responses_agent(
        workspace,
        endpoint="mip-growth-agent-gateway",
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="/Shared/mip/agent-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )

    assert deployment.model_version == 4
    assert deployment.inference_table == "mip.audit.mip_agent_gateway_growth_agent"
    assert len(serving.created) == 1
    created = serving.created[0]
    entity = created["config"].served_entities[0]
    assert entity.entity_name == "mip.audit.mortgage_growth_supervisor_proxy"
    assert entity.entity_version == "4"
    assert entity.environment_vars["MIP_UPSTREAM_SUPERVISOR_ENDPOINT"] == "managed-supervisor"
    assert entity.environment_vars["MLFLOW_EXPERIMENT_ID"] == "experiment-7"
    inference = created["ai_gateway"].inference_table_config
    assert (inference.catalog_name, inference.schema_name, inference.table_name_prefix) == (
        "mip",
        "audit",
        "mip_agent_gateway_growth_agent",
    )
    assert created["ai_gateway"].rate_limits is None
    assert serving.gateway_updates == []


def test_ensure_gateway_agent_reconciles_model_and_gateway_drift(monkeypatch) -> None:
    source_hash = gateway_agent_source_hash(upstream_endpoint="managed-supervisor")
    client = _Client(
        [SimpleNamespace(version="5", tags={gateway.SOURCE_HASH_TAG: source_hash})]
    )
    _patch_mlflow(monkeypatch, client=client)
    details = SimpleNamespace(
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="4",
                )
            ]
        ),
        ai_gateway=SimpleNamespace(
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix="stale_prefix",
            )
        ),
    )
    serving = _ServingEndpoints(details)

    deployment = ensure_gateway_responses_agent(
        SimpleNamespace(serving_endpoints=serving),
        endpoint="mip-growth-agent-gateway",
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="/Shared/mip/agent-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )

    assert deployment.model_version == 5
    assert client.tags == [
        (
            "mip.audit.mortgage_growth_supervisor_proxy",
            "5",
            gateway.SOURCE_HASH_TAG,
            source_hash,
        ),
        (
            "mip.audit.mortgage_growth_supervisor_proxy",
            "5",
            gateway.UPSTREAM_TAG,
            "managed-supervisor",
        ),
    ]
    assert serving.created == []
    assert serving.updated[0]["served_entities"][0].entity_version == "5"
    inference = serving.gateway_updates[0]["inference_table_config"]
    assert inference.table_name_prefix == "mip_agent_gateway_growth_agent"
    assert serving.patches[0]["name"] == "mip-growth-agent-gateway"
    assert serving.events == ["update_config_and_wait", "put_ai_gateway", "patch"]


def test_gateway_agent_postflight_binds_ready_task_model_upstream_tags_and_table() -> None:
    source_hash = "a" * 64
    details = SimpleNamespace(
        state=SimpleNamespace(ready="READY"),
        task="agent/v1/responses",
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="7",
                    environment_vars={
                        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": "managed-supervisor"
                    },
                )
            ]
        ),
        tags=[
            SimpleNamespace(key=gateway.SOURCE_HASH_TAG, value=source_hash),
            SimpleNamespace(key=gateway.UPSTREAM_TAG, value="managed-supervisor"),
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
    deployment = GatewayAgentDeployment(
        endpoint="mip-growth-agent-gateway",
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        model_version=7,
        source_hash=source_hash,
        inference_table="mip.audit.mip_agent_gateway_growth_agent",
    )
    model_registry = SimpleNamespace(
        get_model_version=lambda name, version: SimpleNamespace(
            name=name,
            version=version,
            tags={
                gateway.SOURCE_HASH_TAG: source_hash,
                gateway.UPSTREAM_TAG: "managed-supervisor",
            },
        )
    )

    verify_gateway_responses_agent(
        SimpleNamespace(serving_endpoints=_ServingEndpoints(details)),
        deployment,
        model_registry=model_registry,
    )


def test_gateway_agent_postflight_rejects_rogue_served_model_version_tags() -> None:
    source_hash = "a" * 64
    details = SimpleNamespace(
        state=SimpleNamespace(ready="READY"),
        task="agent/v1/responses",
        pending_config=None,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="7",
                    environment_vars={
                        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": "managed-supervisor"
                    },
                )
            ]
        ),
        tags=[
            SimpleNamespace(key=gateway.SOURCE_HASH_TAG, value=source_hash),
            SimpleNamespace(key=gateway.UPSTREAM_TAG, value="managed-supervisor"),
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
    deployment = GatewayAgentDeployment(
        endpoint="mip-growth-agent-gateway",
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        model_version=7,
        source_hash=source_hash,
        inference_table="mip.audit.mip_agent_gateway_growth_agent",
    )
    rogue_registry = SimpleNamespace(
        get_model_version=lambda name, version: SimpleNamespace(
            name=name,
            version=version,
            tags={
                gateway.SOURCE_HASH_TAG: "b" * 64,
                gateway.UPSTREAM_TAG: "rogue-supervisor",
            },
        )
    )

    try:
        verify_gateway_responses_agent(
            SimpleNamespace(serving_endpoints=_ServingEndpoints(details)),
            deployment,
            model_registry=rogue_registry,
        )
    except RuntimeError as exc:
        assert "Model version tags do not bind" in str(exc)
    else:  # pragma: no cover - model-version proof is load-bearing
        raise AssertionError("rogue served model version tags must fail the postflight")


def test_gateway_agent_postflight_rejects_non_responses_task() -> None:
    deployment = GatewayAgentDeployment(
        endpoint="mip-growth-agent-gateway",
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        model_version=7,
        source_hash="a" * 64,
        inference_table="mip.audit.mip_agent_gateway_growth_agent",
    )
    details = SimpleNamespace(state=SimpleNamespace(ready="READY"), task="llm/v1/chat")

    try:
        verify_gateway_responses_agent(
            SimpleNamespace(serving_endpoints=_ServingEndpoints(details)),
            deployment,
        )
    except RuntimeError as exc:
        assert "not agent/v1/responses" in str(exc)
    else:  # pragma: no cover - fail-closed task proof is load-bearing
        raise AssertionError("non-Responses task must fail provisioning postflight")
