"""Log, register, and deploy the Gateway-eligible Supervisor proxy agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import mlflow
from mlflow import MlflowClient
from mlflow.models.resources import DatabricksServingEndpoint

from backend.agents.gateway_contract import (
    GATEWAY_MODEL_REQUIREMENTS,
    GATEWAY_PROXY_SOURCE,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_UPSTREAM_TAG,
    gateway_proxy_source_hash,
)
from databricks.sdk.errors import NotFound
from databricks.sdk.service.serving import (
    AiGatewayConfig,
    AiGatewayInferenceTableConfig,
    EndpointCoreConfigInput,
    EndpointTag,
    Route,
    ServedEntityInput,
    TrafficConfig,
)

AGENT_SOURCE = GATEWAY_PROXY_SOURCE
SOURCE_HASH_TAG = GATEWAY_PROXY_SOURCE_HASH_TAG
UPSTREAM_TAG = GATEWAY_UPSTREAM_TAG
_STATIC_ENV = {
    "ENABLE_LANGCHAIN_STREAMING": "true",
    "ENABLE_MLFLOW_TRACING": "true",
    "RETURN_REQUEST_ID_IN_RESPONSE": "true",
}
_MODEL_REQUIREMENTS = GATEWAY_MODEL_REQUIREMENTS


@dataclass(frozen=True)
class GatewayAgentDeployment:
    endpoint: str
    upstream_endpoint: str
    model_name: str
    model_version: int
    source_hash: str
    inference_table: str


def gateway_agent_source_hash(*, upstream_endpoint: str) -> str:
    return gateway_proxy_source_hash(upstream_endpoint=upstream_endpoint)


def _model_version_from_config(config: Any, *, model_name: str) -> int | None:
    entities = getattr(config, "served_entities", None) or []
    if len(entities) != 1:
        return None
    for entity in entities:
        if str(getattr(entity, "entity_name", "") or "") != model_name:
            continue
        raw = getattr(entity, "entity_version", None)
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return None
    return None


def _current_model_version(details: Any, *, model_name: str) -> int | None:
    pending = _model_version_from_config(
        getattr(details, "pending_config", None),
        model_name=model_name,
    )
    if pending is not None:
        return pending
    return _model_version_from_config(getattr(details, "config", None), model_name=model_name)


def _proxy_config_matches(details: Any, *, entity: ServedEntityInput) -> bool:
    config = getattr(details, "config", None)
    entities = getattr(config, "served_entities", None) or []
    if len(entities) != 1:
        return False
    current = entities[0]
    expected_environment = dict(entity.environment_vars or {})
    current_environment = dict(getattr(current, "environment_vars", None) or {})
    if (
        str(getattr(current, "entity_name", "") or "") != str(entity.entity_name or "")
        or str(getattr(current, "entity_version", "") or "") != str(entity.entity_version or "")
        or str(getattr(current, "name", "") or "") != str(entity.name or "")
        or current_environment != expected_environment
    ):
        return False
    traffic = getattr(config, "traffic_config", None)
    routes = getattr(traffic, "routes", None) or []
    return (
        len(routes) == 1
        and str(getattr(routes[0], "served_model_name", "") or "") == str(entity.name or "")
        and int(getattr(routes[0], "traffic_percentage", 0) or 0) == 100
    )


def _existing_source_version(client: Any, *, model_name: str, source_hash: str) -> int | None:
    versions = client.search_model_versions(f"name='{model_name}'")
    matches = [
        int(version.version)
        for version in versions
        if (getattr(version, "tags", None) or {}).get(SOURCE_HASH_TAG) == source_hash
    ]
    return max(matches) if matches else None


def _start_mlflow_run() -> Any:
    return mlflow.start_run()


def _log_responses_model(*, upstream_endpoint: str) -> Any:
    return mlflow.pyfunc.log_model(
        name="mortgage_growth_supervisor_proxy",
        python_model=str(AGENT_SOURCE),
        resources=[DatabricksServingEndpoint(endpoint_name=upstream_endpoint)],
        pip_requirements=list(_MODEL_REQUIREMENTS),
    )


def _log_gateway_model(*, upstream_endpoint: str) -> Any:
    """Log code-from-model while MLflow live-validates the upstream delegation."""

    env_key = "MIP_UPSTREAM_SUPERVISOR_ENDPOINT"
    previous = os.environ.get(env_key)
    os.environ[env_key] = upstream_endpoint
    try:
        with _start_mlflow_run():
            return _log_responses_model(upstream_endpoint=upstream_endpoint)
    finally:
        if previous is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = previous


def _served_entity(
    *,
    upstream_endpoint: str,
    model_name: str,
    model_version: int,
    experiment_id: str,
) -> tuple[ServedEntityInput, TrafficConfig]:
    served_name = f"mip-growth-supervisor-proxy-{model_version}"
    environment = {
        **_STATIC_ENV,
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream_endpoint,
        "MLFLOW_EXPERIMENT_ID": experiment_id,
    }
    entity = ServedEntityInput(
        entity_name=model_name,
        entity_version=str(model_version),
        environment_vars=environment,
        name=served_name,
        scale_to_zero_enabled=True,
        workload_size="Small",
    )
    traffic = TrafficConfig(routes=[Route(served_model_name=served_name, traffic_percentage=100)])
    return entity, traffic


def _gateway_config(*, catalog: str, schema: str, table_prefix: str) -> AiGatewayConfig:
    return AiGatewayConfig(
        inference_table_config=AiGatewayInferenceTableConfig(
            enabled=True,
            catalog_name=catalog,
            schema_name=schema,
            table_name_prefix=table_prefix,
        )
    )


def _gateway_matches(
    details: Any,
    *,
    catalog: str,
    schema: str,
    table_prefix: str,
) -> bool:
    gateway = getattr(details, "ai_gateway", None)
    inference = getattr(gateway, "inference_table_config", None)
    return (
        getattr(inference, "enabled", None) is True
        and str(getattr(inference, "catalog_name", "") or "") == catalog
        and str(getattr(inference, "schema_name", "") or "") == schema
        and str(getattr(inference, "table_name_prefix", "") or "") == table_prefix
    )


def _endpoint_tags(details: Any) -> dict[str, str]:
    tags = getattr(details, "tags", None) or []
    return {
        str(getattr(tag, "key", "") or ""): str(getattr(tag, "value", "") or "") for tag in tags
    }


def _verified_model_version_tags(
    deployment: GatewayAgentDeployment,
    *,
    model_registry: Any | None = None,
) -> dict[str, str]:
    """Read authoritative tags from the exact served UC model version.

    Endpoint tags are mutable independently of the registered model.  They
    therefore cannot, by themselves, prove that the version receiving 100%
    of traffic contains the reviewed proxy source.  The live postflight reads
    that exact version through the UC-backed MLflow registry and requires the
    same source/upstream binding that provisioning wrote at registration.
    """

    registry = model_registry or MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    try:
        version = registry.get_model_version(
            deployment.model_name,
            str(deployment.model_version),
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed live postflight
        raise RuntimeError(
            "could not read the exact served Gateway Agent Model version from Unity Catalog"
        ) from exc
    if (
        str(getattr(version, "name", "") or "") != deployment.model_name
        or str(getattr(version, "version", "") or "") != str(deployment.model_version)
    ):
        raise RuntimeError("Unity Catalog returned an unexpected Gateway Agent Model version")
    tags = {
        str(key): str(value)
        for key, value in dict(getattr(version, "tags", None) or {}).items()
    }
    if (
        tags.get(SOURCE_HASH_TAG) != deployment.source_hash
        or tags.get(UPSTREAM_TAG) != deployment.upstream_endpoint
    ):
        raise RuntimeError(
            "served Gateway Agent Model version tags do not bind its reviewed source"
        )
    return tags


def verify_gateway_responses_agent(
    workspace: Any,
    deployment: GatewayAgentDeployment,
    *,
    model_registry: Any | None = None,
) -> None:
    """Fail closed unless the ready endpoint proves the exact governed boundary."""

    details = workspace.serving_endpoints.get(deployment.endpoint)
    if getattr(details, "pending_config", None) is not None:
        raise RuntimeError(
            f"Gateway ResponsesAgent endpoint {deployment.endpoint} has a pending config update"
        )
    ready_raw = getattr(getattr(details, "state", None), "ready", None)
    ready = str(getattr(ready_raw, "value", ready_raw) or "").upper()
    if ready != "READY":
        raise RuntimeError(
            f"Gateway ResponsesAgent endpoint {deployment.endpoint} is not READY ({ready or 'UNKNOWN'})"
        )
    task_raw = getattr(details, "task", None)
    task = str(getattr(task_raw, "value", task_raw) or "").strip()
    canonical_task = task.lower().replace("-", "_").replace("/", "_")
    if canonical_task != "agent_v1_responses":
        raise RuntimeError(
            f"Gateway endpoint {deployment.endpoint} has task {task or 'UNKNOWN'}, "
            "not agent/v1/responses"
        )
    version = _model_version_from_config(
        getattr(details, "config", None),
        model_name=deployment.model_name,
    )
    if version != deployment.model_version:
        raise RuntimeError(
            f"Gateway endpoint {deployment.endpoint} serves {deployment.model_name} "
            f"v{version or 'UNKNOWN'}, expected v{deployment.model_version}"
        )
    config = getattr(details, "config", None)
    entities = getattr(config, "served_entities", None) or []
    if len(entities) != 1:
        raise RuntimeError("Gateway ResponsesAgent must serve exactly one reviewed proxy model")
    entity = entities[0]
    environment = getattr(entity, "environment_vars", None) or {}
    if environment.get("MIP_UPSTREAM_SUPERVISOR_ENDPOINT") != deployment.upstream_endpoint:
        raise RuntimeError("Gateway ResponsesAgent upstream Supervisor binding is missing or stale")
    tags = _endpoint_tags(details)
    if (
        tags.get(SOURCE_HASH_TAG) != deployment.source_hash
        or tags.get(UPSTREAM_TAG) != deployment.upstream_endpoint
    ):
        raise RuntimeError("Gateway ResponsesAgent endpoint tags do not bind its reviewed source")
    catalog, schema, table_prefix = deployment.inference_table.split(".", 2)
    if not _gateway_matches(
        details,
        catalog=catalog,
        schema=schema,
        table_prefix=table_prefix,
    ):
        raise RuntimeError(
            "Gateway ResponsesAgent inference-table configuration is missing or stale"
        )
    _verified_model_version_tags(deployment, model_registry=model_registry)


def ensure_gateway_responses_agent(
    workspace: Any,
    *,
    endpoint: str,
    upstream_endpoint: str,
    model_name: str,
    experiment_name: str,
    inference_catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
) -> GatewayAgentDeployment:
    """Converge one source-bound Agent Model endpoint and its proof table."""

    source_hash = gateway_agent_source_hash(upstream_endpoint=upstream_endpoint)
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    experiment = mlflow.set_experiment(experiment_name)
    client = MlflowClient()
    model_version = _existing_source_version(
        client,
        model_name=model_name,
        source_hash=source_hash,
    )
    if model_version is None:
        print(f"[agentic] logging Gateway Supervisor proxy: {model_name}")
        logged = _log_gateway_model(upstream_endpoint=upstream_endpoint)
        registered = mlflow.register_model(logged.model_uri, model_name)
        model_version = int(registered.version)
    # Converge the authoritative registered-version proof for both newly
    # created and source-hash-matched legacy versions. Older provisioning
    # wrote only the source tag; leaving the upstream tag absent would make
    # the independent export postflight fail forever without repairing it.
    client.set_model_version_tag(model_name, str(model_version), SOURCE_HASH_TAG, source_hash)
    client.set_model_version_tag(
        model_name,
        str(model_version),
        UPSTREAM_TAG,
        upstream_endpoint,
    )

    entity, traffic = _served_entity(
        upstream_endpoint=upstream_endpoint,
        model_name=model_name,
        model_version=model_version,
        experiment_id=str(experiment.experiment_id),
    )
    gateway = _gateway_config(
        catalog=inference_catalog,
        schema=inference_schema,
        table_prefix=inference_table_prefix,
    )
    tags = [
        EndpointTag(SOURCE_HASH_TAG, source_hash),
        EndpointTag(UPSTREAM_TAG, upstream_endpoint),
    ]
    try:
        details = workspace.serving_endpoints.get(endpoint)
    except NotFound:
        details = None

    if details is None:
        print(
            f"[agentic] creating Gateway Supervisor proxy: {endpoint} "
            f"({model_name} v{model_version})"
        )
        workspace.serving_endpoints.create(
            name=endpoint,
            config=EndpointCoreConfigInput(
                name=endpoint,
                served_entities=[entity],
                traffic_config=traffic,
            ),
            ai_gateway=gateway,
            tags=tags,
            description=(
                "MIP governed ResponsesAgent boundary delegating product planning "
                "to the managed Mortgage Growth Agent Supervisor."
            ),
        )
    else:
        if getattr(details, "pending_config", None) is not None:
            print(f"[agentic] waiting for interrupted endpoint update: {endpoint}")
            details = workspace.serving_endpoints.wait_get_serving_endpoint_not_updating(endpoint)
        if not _proxy_config_matches(details, entity=entity):
            print(
                f"[agentic] reconciling Gateway Supervisor proxy: {endpoint} "
                f"({model_name} v{model_version})"
            )
            details = workspace.serving_endpoints.update_config_and_wait(
                name=endpoint,
                served_entities=[entity],
                traffic_config=traffic,
            )
        else:
            print(f"[agentic] Gateway Supervisor proxy already current: {endpoint}")
        if not _gateway_matches(
            details,
            catalog=inference_catalog,
            schema=inference_schema,
            table_prefix=inference_table_prefix,
        ):
            print(f"[agentic] reconciling AI Gateway inference table: {endpoint}")
            workspace.serving_endpoints.put_ai_gateway(
                name=endpoint,
                inference_table_config=gateway.inference_table_config,
            )
        workspace.serving_endpoints.patch(name=endpoint, add_tags=tags)

    inference_table = ".".join([inference_catalog, inference_schema, inference_table_prefix])
    return GatewayAgentDeployment(
        endpoint,
        upstream_endpoint,
        model_name,
        model_version,
        source_hash,
        inference_table,
    )
