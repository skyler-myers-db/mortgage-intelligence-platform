"""Strict live attestation for pre-resource-envelope Gateway endpoints."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote

from mlflow import MlflowClient

from backend.agents.gateway_contract import (
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    gateway_exact_resource_digest,
    gateway_model_version_tags,
    reviewed_workspace_https_origin,
)
from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.experiment_acl_contract import resolve_exact_experiment_acl
from tools.databricks.gateway_model_attestation import (
    gateway_model_attestation_record_key,
)
from tools.databricks.gateway_resource_identity import GatewayAgentDeployment
from tools.databricks.provision_gateway_responses_agent import (
    gateway_agent_model_name,
    gateway_experiment_name,
    gateway_inference_table_prefix,
    gateway_resource_hash,
    verify_gateway_responses_agent,
)
from tools.databricks.supervisor_agent_contract import (
    canonical_supervisor_contract_json,
    supervisor_contract_hash,
)

_RESPONSES_TASK = "agent/v1/responses"


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _environment(details: Any) -> dict[str, str]:
    entities = getattr(getattr(details, "config", None), "served_entities", None) or []
    if len(entities) != 1:
        raise RuntimeError("legacy Gateway must have exactly one served entity")
    raw = getattr(entities[0], "environment_vars", None) or {}
    try:
        environment = {str(key): str(value) for key, value in dict(raw).items()}
    except (TypeError, ValueError) as exc:
        raise RuntimeError("legacy Gateway environment is malformed") from exc
    required = {
        "MIP_UPSTREAM_SUPERVISOR_ID",
        "DATABRICKS_HOST",
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT",
        "MIP_UPSTREAM_SUPERVISOR_CREATOR",
        "MIP_UPSTREAM_PROXY_CLIENT_ID",
        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID",
        "MIP_UPSTREAM_PROXY_CLIENT_SECRET",
        "MIP_SUPERVISOR_CATALOG",
        "MIP_SUPERVISOR_GENIE_SPACE_ID",
        "MIP_SUPERVISOR_CONTRACT_SHA256",
        "MLFLOW_EXPERIMENT_ID",
    }
    if not required.issubset(environment) or any(not environment[key].strip() for key in required):
        raise RuntimeError("legacy Gateway environment binding is incomplete")
    return environment


def _entity_identity(details: Any) -> tuple[str, int]:
    entities = getattr(getattr(details, "config", None), "served_entities", None) or []
    if len(entities) != 1:
        raise RuntimeError("legacy Gateway must have exactly one served entity")
    model_name = _text(getattr(entities[0], "entity_name", None))
    try:
        model_version = int(_text(getattr(entities[0], "entity_version", None)))
    except ValueError as exc:
        raise RuntimeError("legacy Gateway model version is invalid") from exc
    if not model_name or model_version <= 0:
        raise RuntimeError("legacy Gateway model identity is invalid")
    return model_name, model_version


def _family_name_exact(
    name: str,
    *,
    prefixes: Sequence[str],
    resource_hash: str,
) -> bool:
    expected = (
        {prefix for prefix in prefixes if prefix}
        | {f"{prefix}-{resource_hash[:12]}" for prefix in prefixes if prefix}
        | {f"{prefix}-{resource_hash[:12]}-mq1" for prefix in prefixes if prefix}
    )
    return name in expected


def attest_legacy_gateway(
    workspace: Any,
    details: Any,
    *,
    endpoint_name: str,
    endpoint_prefixes: Sequence[str],
    runtime_application_id: str,
    supervisor_name: str,
    catalog: str,
    genie_space_id: str,
    assert_single_writer: Callable[[], None],
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
) -> dict[str, str]:
    """Build an exact reviewed record from signed model and live server facts."""

    try:
        workspace_host = reviewed_workspace_https_origin(
            str(getattr(getattr(workspace, "config", None), "host", "") or "")
        )
    except ValueError as exc:
        raise RuntimeError("authenticated legacy Gateway workspace host is invalid") from exc
    endpoint_id = _text(getattr(details, "id", None))
    endpoint_creator = assert_runtime_creator(
        getattr(details, "creator", None),
        application_id=runtime_application_id,
        resource=f"legacy Gateway endpoint {endpoint_name}",
    )
    if (
        not endpoint_id
        or getattr(details, "pending_config", None) is not None
        or _text(getattr(details, "description", None)) != GATEWAY_ENDPOINT_DESCRIPTION
        or _text(getattr(details, "task", None)) != _RESPONSES_TASK
    ):
        raise RuntimeError("legacy Gateway immutable endpoint contract drifted")
    environment = _environment(details)
    expected_environment = {
        "DATABRICKS_HOST": workspace_host,
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": runtime_application_id,
        "MIP_SUPERVISOR_CATALOG": catalog,
        "MIP_SUPERVISOR_GENIE_SPACE_ID": genie_space_id,
        "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
            genie_space_id=genie_space_id,
            catalog=catalog,
        ),
    }
    if any(environment.get(key) != expected for key, expected in expected_environment.items()):
        raise RuntimeError("legacy Gateway Supervisor scope binding drifted")
    supervisor_id = environment["MIP_UPSTREAM_SUPERVISOR_ID"]
    supervisor_endpoint = environment["MIP_UPSTREAM_SUPERVISOR_ENDPOINT"]
    supervisor_details = workspace.serving_endpoints.get(supervisor_endpoint)
    supervisor_endpoint_id = _text(getattr(supervisor_details, "id", None))
    supervisor_endpoint_creator = assert_runtime_creator(
        getattr(supervisor_details, "creator", None),
        application_id=runtime_application_id,
        resource=f"legacy Gateway upstream endpoint {supervisor_endpoint}",
    )
    if not supervisor_endpoint_id:
        raise RuntimeError("legacy Gateway upstream endpoint has no immutable ID")
    raw_supervisor = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}",
    )
    if not isinstance(raw_supervisor, dict):
        raise RuntimeError("legacy Gateway upstream Supervisor metadata is malformed")
    supervisor_display_name = _text(raw_supervisor.get("display_name"))
    if (
        _text(raw_supervisor.get("supervisor_agent_id")) != supervisor_id
        or _text(raw_supervisor.get("endpoint_name")) != supervisor_endpoint
        or _text(raw_supervisor.get("creator")) != runtime_application_id
        or not supervisor_display_name
    ):
        raise RuntimeError("legacy Gateway upstream Supervisor identity drifted")

    registry = model_registry or MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    tracking = tracking_client or MlflowClient(tracking_uri="databricks")
    model_name, model_version = _entity_identity(details)
    try:
        version = registry.get_model_version(model_name, str(model_version))
    except Exception as exc:  # noqa: BLE001 - live proof is fail-closed
        raise RuntimeError("legacy Gateway model version could not be resolved") from exc
    if _text(getattr(version, "name", None)) != model_name or _text(
        getattr(version, "version", None)
    ) != str(model_version):
        raise RuntimeError("legacy Gateway model name/version binding drifted")
    model_source = _text(getattr(version, "source", None))
    tags = {
        str(key): str(value) for key, value in dict(getattr(version, "tags", None) or {}).items()
    }
    model_tags = gateway_model_version_tags(tags)
    signed = model_tags.contract
    inference_family = (
        f"{signed['catalog']}.{signed['inference_schema']}." f"{signed['inference_table_prefix']}"
    )
    expected_signed = {
        "full_name": model_name,
        "model_source": model_source,
        "supervisor_id": supervisor_id,
        "supervisor_endpoint_id": supervisor_endpoint_id,
        "upstream_endpoint": supervisor_endpoint,
        "runtime_application_id": runtime_application_id,
        "catalog": catalog,
        "genie_space_id": genie_space_id,
    }
    if any(signed.get(key) != expected for key, expected in expected_signed.items()):
        raise RuntimeError("legacy Gateway signed model contract drifted")
    proxy_application_id = environment["MIP_UPSTREAM_PROXY_CLIENT_ID"]
    proxy_credential_id = environment["MIP_UPSTREAM_PROXY_CREDENTIAL_ID"]
    proxy_secret_reference = environment["MIP_UPSTREAM_PROXY_CLIENT_SECRET"]
    resource_hash = gateway_resource_hash(
        source_hash=signed["source_hash"],
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        runtime_application_id=runtime_application_id,
        workspace_host=workspace_host,
        model_name=signed["model_family"],
        experiment_name=signed["experiment_base"],
        inference_schema=signed["inference_schema"],
        inference_table_prefix=signed["inference_table_prefix"],
        attestation_verify_key=gateway_model_attestation_record_key(tags),
        proxy_caller_application_id=proxy_application_id,
        proxy_caller_credential_id=proxy_credential_id,
        proxy_caller_secret_reference=proxy_secret_reference,
    )
    if not _family_name_exact(
        endpoint_name,
        prefixes=endpoint_prefixes,
        resource_hash=resource_hash,
    ) or model_name != gateway_agent_model_name(
        base_model_name=signed["model_family"],
        contract_hash=resource_hash,
    ):
        raise RuntimeError("legacy Gateway deterministic resource name drifted")
    inference_table = ".".join(
        (
            catalog,
            signed["inference_schema"],
            gateway_inference_table_prefix(
                base_prefix=signed["inference_table_prefix"],
                contract_hash=resource_hash,
            ),
        )
    )
    experiment_name = gateway_experiment_name(
        base_experiment_name=signed["experiment_base"],
        contract_hash=resource_hash,
        runtime_application_id=runtime_application_id,
    )
    experiment_id = environment["MLFLOW_EXPERIMENT_ID"]
    deployment = GatewayAgentDeployment(
        endpoint=endpoint_name,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        upstream_endpoint=supervisor_endpoint,
        runtime_application_id=runtime_application_id,
        workspace_host=workspace_host,
        proxy_caller_application_id=proxy_application_id,
        proxy_caller_credential_id=proxy_credential_id,
        proxy_caller_secret_reference=proxy_secret_reference,
        model_name=model_name,
        model_version=model_version,
        model_source=model_source,
        model_attestation_verify_key=model_tags.verify_key,
        model_family=signed["model_family"],
        source_hash=signed["source_hash"],
        resource_hash=resource_hash,
        inference_table=inference_table,
        inference_table_prefix=signed["inference_table_prefix"],
        experiment_base=signed["experiment_base"],
        experiment_name=experiment_name,
        experiment_id=experiment_id,
        catalog=catalog,
        genie_space_id=genie_space_id,
    )
    verify_gateway_responses_agent(
        workspace,
        deployment,
        model_registry=registry,
        tracking_client=tracking,
        assert_single_writer=assert_single_writer,
    )
    registered_model = workspace.registered_models.get(model_name)
    model_owner = assert_runtime_creator(
        getattr(registered_model, "owner", None),
        application_id=runtime_application_id,
        resource=f"legacy Gateway model {model_name}",
    )
    experiment = tracking.get_experiment(experiment_id)
    experiment_owner = assert_runtime_creator(
        (getattr(experiment, "tags", None) or {}).get("mlflow.ownerEmail"),
        application_id=runtime_application_id,
        resource=f"legacy Gateway experiment {experiment_name}",
    )
    experiment_acl = resolve_exact_experiment_acl(
        workspace,
        experiment_id=experiment_id,
        runtime_application_id=runtime_application_id,
    )
    supervisor_json = canonical_supervisor_contract_json(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    contract = {
        "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
        "catalog": catalog,
        "genie_space_id": genie_space_id,
        "runtime_application_id": runtime_application_id,
        "workspace_host": workspace_host,
        "supervisor_canonical_name": supervisor_name,
        "supervisor_display_name": supervisor_display_name,
        "supervisor_contract_json": supervisor_json,
        "supervisor_contract_sha256": hashlib.sha256(supervisor_json.encode("utf-8")).hexdigest(),
        "supervisor_id": supervisor_id,
        "supervisor_creator": runtime_application_id,
        "supervisor_endpoint": supervisor_endpoint,
        "supervisor_endpoint_id": supervisor_endpoint_id,
        "supervisor_endpoint_creator": supervisor_endpoint_creator,
        "gateway_endpoint": endpoint_name,
        "gateway_endpoint_id": endpoint_id,
        "gateway_endpoint_creator": endpoint_creator,
        "gateway_endpoint_description": GATEWAY_ENDPOINT_DESCRIPTION,
        "gateway_endpoint_task": _RESPONSES_TASK,
        "gateway_endpoint_route_optimized": "false",
        "gateway_endpoint_budget_policy": "none",
        "gateway_endpoint_email_notifications": "none",
        "gateway_endpoint_deprecated_rate_limits": "[]",
        "gateway_source_hash": deployment.source_hash,
        "gateway_resource_hash": resource_hash,
        "gateway_model_family": deployment.model_family,
        "gateway_model_name": model_name,
        "gateway_model_version": str(model_version),
        "gateway_model_source": model_source,
        "gateway_model_owner": model_owner,
        "gateway_experiment_base": deployment.experiment_base,
        "gateway_experiment_acl_json": experiment_acl.canonical_json,
        "gateway_experiment_acl_sha256": experiment_acl.sha256,
        "gateway_experiment_name": experiment_name,
        "gateway_experiment_id": experiment_id,
        "gateway_experiment_owner": experiment_owner,
        "gateway_inference_table_family": inference_family,
        "gateway_inference_table": inference_table,
        "proxy_caller_application_id": proxy_application_id,
        "proxy_caller_credential_id": proxy_credential_id,
        "proxy_caller_secret_reference": proxy_secret_reference,
    }
    gateway_exact_resource_digest(contract)
    return contract
