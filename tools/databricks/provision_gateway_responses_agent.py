"""Log, register, and deploy the Gateway-eligible Supervisor proxy agent."""

from __future__ import annotations

import os
from typing import Any

import mlflow
from mlflow import MlflowClient
from mlflow.models.resources import (
    DatabricksFunction,
    DatabricksGenieSpace,
    DatabricksServingEndpoint,
)

from backend.agents.gateway_contract import (
    GATEWAY_BURST_SCALING_ENABLED,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_MODEL_REQUIREMENTS,
    GATEWAY_MODEL_SOURCE_HASH_TAG,
    GATEWAY_MODEL_UPSTREAM_TAG,
    GATEWAY_PROXY_SOURCE,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_ROUTE_OPTIMIZED,
    GATEWAY_SCALE_TO_ZERO_ENABLED,
    GATEWAY_STATIC_ENV,
    GATEWAY_TRAFFIC_PERCENTAGE,
    GATEWAY_UPSTREAM_TAG,
    GATEWAY_WORKLOAD_SIZE,
    GATEWAY_WORKLOAD_TYPE,
    gateway_experiment_base,
    gateway_model_version_tags,
    gateway_resource_allocation_hash,
)
from backend.agents.supervisor_contract import supervisor_contract_hash as supervisor_contract_hash
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.serving import (
    EndpointCoreConfigInput,
    EndpointTag,
    ServingModelWorkloadType,
)
from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.experiment_acl_contract import resolve_exact_experiment_acl
from tools.databricks.gateway_endpoint_contract import (
    clear_deprecated_endpoint_rate_limits as _clear_deprecated_endpoint_rate_limits,
)
from tools.databricks.gateway_endpoint_contract import (
    current_model_version,
)
from tools.databricks.gateway_endpoint_contract import (
    endpoint_policy_matches as _endpoint_policy_matches,
)
from tools.databricks.gateway_endpoint_contract import endpoint_tags_match as _endpoint_tags_match
from tools.databricks.gateway_endpoint_contract import gateway_config as _gateway_config
from tools.databricks.gateway_endpoint_contract import gateway_matches as _gateway_matches
from tools.databricks.gateway_endpoint_contract import (
    model_version_from_config as _model_version_from_config,
)
from tools.databricks.gateway_endpoint_contract import proxy_config_matches as _proxy_config_matches
from tools.databricks.gateway_endpoint_contract import served_entity as _served_entity
from tools.databricks.gateway_model_attestation import (
    gateway_model_attestation_record_key,
    require_gateway_model_attestation_signing_authority,
    sign_gateway_model_contract,
    verify_gateway_model_contract,
)
from tools.databricks.gateway_registration_recovery import (
    DurableRegistrationJournal,
    RegistrationJournalVisibilityError,
    RegistrationReconciliationPendingError,
    attested_source_versions,
    clear_registration_journal,
    compensate_unregistered_logged_model,
    persist_registration_journal,
    reconcile_incomplete_source_versions,
    require_no_unjournaled_gateway_sources,
    validated_model_version_tags,
)
from tools.databricks.gateway_registration_recovery import (
    compensate_failed_model_registration as _compensate_failed_model_registration,
)
from tools.databricks.gateway_registration_recovery import (
    registration_cleanup_journal as _registration_cleanup_journal,
)
from tools.databricks.gateway_registration_recovery import (
    require_ready_model_version as _require_ready_model_version,
)
from tools.databricks.gateway_resource_identity import (
    GatewayAgentDeployment,
    _resolve_exact_experiment,
    _target_model_family,
    gateway_agent_model_name,
    gateway_agent_source_hash,
    gateway_experiment_name,
    gateway_inference_table_prefix,
)
from tools.databricks.mlflow_responses_packaging import responses_agent_packaging_validation

AGENT_SOURCE = GATEWAY_PROXY_SOURCE
SOURCE_HASH_TAG = GATEWAY_PROXY_SOURCE_HASH_TAG
UPSTREAM_TAG = GATEWAY_UPSTREAM_TAG
MODEL_SOURCE_HASH_TAG = GATEWAY_MODEL_SOURCE_HASH_TAG
MODEL_UPSTREAM_TAG = GATEWAY_MODEL_UPSTREAM_TAG
_STATIC_ENV = GATEWAY_STATIC_ENV
_MODEL_REQUIREMENTS = GATEWAY_MODEL_REQUIREMENTS
_MLFLOW_LOG_MODEL = mlflow.pyfunc.log_model
_WORKLOAD_SIZE = GATEWAY_WORKLOAD_SIZE
_WORKLOAD_TYPE = ServingModelWorkloadType.CPU
assert _WORKLOAD_TYPE.value == GATEWAY_WORKLOAD_TYPE
_SCALE_TO_ZERO_ENABLED = GATEWAY_SCALE_TO_ZERO_ENABLED
_BURST_SCALING_ENABLED = GATEWAY_BURST_SCALING_ENABLED
_ROUTE_OPTIMIZED = GATEWAY_ROUTE_OPTIMIZED
_TRAFFIC_PERCENTAGE = GATEWAY_TRAFFIC_PERCENTAGE
_ENDPOINT_DESCRIPTION = GATEWAY_ENDPOINT_DESCRIPTION


def _current_model_version(details: Any, *, model_name: str) -> int | None:
    """Compatibility wrapper for callers that inspect interrupted updates."""

    return current_model_version(details, model_name=model_name)


def gateway_resource_hash(
    *,
    source_hash: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    runtime_application_id: str,
    model_name: str,
    experiment_name: str,
    inference_schema: str,
    inference_table_prefix: str,
    attestation_verify_key: str,
) -> str:
    """Bind every mutable deployment input used to allocate green resources."""

    return gateway_resource_allocation_hash(
        source_hash=source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        runtime_application_id=runtime_application_id,
        model_name=model_name,
        experiment_name=experiment_name,
        inference_schema=inference_schema,
        inference_table_prefix=inference_table_prefix,
        attestation_verify_key=attestation_verify_key,
        environment=_STATIC_ENV,
        workload_size=_WORKLOAD_SIZE,
        workload_type=_WORKLOAD_TYPE.value,
        scale_to_zero_enabled=_SCALE_TO_ZERO_ENABLED,
        burst_scaling_enabled=_BURST_SCALING_ENABLED,
        route_optimized=_ROUTE_OPTIMIZED,
        traffic_percentage=_TRAFFIC_PERCENTAGE,
        description=_ENDPOINT_DESCRIPTION,
    )


def _existing_source_version(
    client: Any,
    *,
    model_name: str,
    source_hash: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    upstream_endpoint: str,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    genie_space_id: str,
    inference_schema: str,
    inference_table_prefix: str,
) -> int | None:
    ready, incomplete = attested_source_versions(
        client,
        model_name=model_name,
        source_hash=source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        upstream_endpoint=upstream_endpoint,
        runtime_application_id=runtime_application_id,
        model_family=model_family,
        experiment_base=experiment_base,
        catalog=catalog,
        genie_space_id=genie_space_id,
        inference_schema=inference_schema,
        inference_table_prefix=inference_table_prefix,
        verify_attestation=verify_gateway_model_contract,
    )
    if incomplete:
        candidate = incomplete[0]
        raise RuntimeError(
            f"Gateway candidate model {model_name} v{candidate.version} "
            f"is not ready ({candidate.status})"
        )
    return max(ready) if ready else None


def _start_mlflow_run() -> Any:
    return mlflow.start_run()


def _log_responses_model(*, upstream_endpoint: str, catalog: str, genie_space_id: str) -> Any:
    return _MLFLOW_LOG_MODEL(
        name="mortgage_growth_supervisor_proxy",
        python_model=str(AGENT_SOURCE),
        resources=[
            DatabricksServingEndpoint(
                endpoint_name=upstream_endpoint,
                on_behalf_of_user=False,
            ),
            DatabricksFunction(
                function_name=f"{catalog}.gold.fn_build_cohort",
                on_behalf_of_user=False,
            ),
            DatabricksFunction(
                function_name=f"{catalog}.gold.fn_segment_counts",
                on_behalf_of_user=False,
            ),
            DatabricksFunction(
                function_name=f"{catalog}.gold.fn_lead_queue_url",
                on_behalf_of_user=False,
            ),
            DatabricksGenieSpace(
                genie_space_id=genie_space_id,
                on_behalf_of_user=False,
            ),
        ],
        input_example={
            "input": [
                {
                    "role": "user",
                    "content": (
                        "Use build_cohort to count a broad California refinance cohort; "
                        "return only a governed aggregate and no borrower-level data."
                    ),
                }
            ],
            "max_output_tokens": 256,
        },
        pip_requirements=list(_MODEL_REQUIREMENTS),
        code_paths=[str(GATEWAY_PROXY_SOURCE.parents[1])],
    )


def _log_gateway_model(*, upstream_endpoint: str, catalog: str, genie_space_id: str) -> Any:
    """Log code-from-model without invoking resources that do not exist yet."""

    with responses_agent_packaging_validation(), _start_mlflow_run():
        return _log_responses_model(
            upstream_endpoint=upstream_endpoint,
            catalog=catalog,
            genie_space_id=genie_space_id,
        )


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
    if str(getattr(version, "name", "") or "") != deployment.model_name or str(
        getattr(version, "version", "") or ""
    ) != str(deployment.model_version):
        raise RuntimeError("Unity Catalog returned an unexpected Gateway Agent Model version")
    if str(getattr(version, "source", "") or "").strip() != deployment.model_source:
        raise RuntimeError("served Gateway Agent Model version source drifted")
    _require_ready_model_version(
        version,
        resource=(
            f"served Gateway Agent Model {deployment.model_name} v{deployment.model_version}"
        ),
    )
    tags = {
        str(key): str(value) for key, value in dict(getattr(version, "tags", None) or {}).items()
    }
    model_tags = gateway_model_version_tags(tags)
    if (
        model_tags.contract["source_hash"] != deployment.source_hash
        or model_tags.contract["upstream_endpoint"] != deployment.upstream_endpoint
    ):
        raise RuntimeError(
            "served Gateway Agent Model version tags do not bind its reviewed source"
        )
    verify_gateway_model_contract(
        tags=tags,
        full_name=deployment.model_name,
        model_source=deployment.model_source,
        source_hash=deployment.source_hash,
        supervisor_id=deployment.supervisor_id,
        supervisor_endpoint_id=deployment.supervisor_endpoint_id,
        upstream_endpoint=deployment.upstream_endpoint,
        runtime_application_id=deployment.runtime_application_id,
        model_family=deployment.model_family,
        experiment_base=deployment.experiment_base,
        catalog=deployment.catalog,
        genie_space_id=deployment.genie_space_id,
        inference_schema=deployment.inference_table.split(".", 2)[1],
        inference_table_prefix=deployment.inference_table_prefix,
    )
    return tags


def gateway_endpoint_configuration_matches(
    details: Any,
    deployment: GatewayAgentDeployment,
) -> bool:
    """Return whether every readable endpoint configuration field is exact."""

    expected_entity, _traffic = _served_entity(
        supervisor_id=deployment.supervisor_id,
        upstream_endpoint=deployment.upstream_endpoint,
        runtime_application_id=deployment.runtime_application_id,
        catalog=deployment.catalog,
        genie_space_id=deployment.genie_space_id,
        model_name=deployment.model_name,
        model_version=deployment.model_version,
        experiment_id=deployment.experiment_id,
    )
    catalog, schema, table_prefix = deployment.inference_table.split(".", 2)
    return (
        _proxy_config_matches(details, entity=expected_entity)
        and _endpoint_policy_matches(details)
        and str(getattr(details, "description", "") or "") == _ENDPOINT_DESCRIPTION
        and _endpoint_tags_match(
            details,
            expected={
                SOURCE_HASH_TAG: deployment.source_hash,
                UPSTREAM_TAG: deployment.upstream_endpoint,
            },
        )
        and _gateway_matches(
            details,
            catalog=catalog,
            schema=schema,
            table_prefix=table_prefix,
        )
    )


def verify_gateway_responses_agent(
    workspace: Any,
    deployment: GatewayAgentDeployment,
    *,
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
) -> None:
    """Fail closed unless the ready endpoint proves the exact governed boundary."""

    expected_resource_hash = gateway_resource_hash(
        source_hash=deployment.source_hash,
        supervisor_id=deployment.supervisor_id,
        supervisor_endpoint_id=deployment.supervisor_endpoint_id,
        runtime_application_id=deployment.runtime_application_id,
        model_name=deployment.model_family,
        experiment_name=deployment.experiment_base,
        inference_schema=deployment.inference_table.split(".", 2)[1],
        inference_table_prefix=deployment.inference_table_prefix,
        attestation_verify_key=deployment.model_attestation_verify_key,
    )
    expected_model_name = gateway_agent_model_name(
        base_model_name=deployment.model_family,
        contract_hash=expected_resource_hash,
    )
    expected_experiment_name = gateway_experiment_name(
        base_experiment_name=deployment.experiment_base,
        contract_hash=expected_resource_hash,
        runtime_application_id=deployment.runtime_application_id,
    )
    expected_table = ".".join(
        [
            deployment.catalog,
            deployment.inference_table.split(".", 2)[1],
            gateway_inference_table_prefix(
                base_prefix=deployment.inference_table_prefix,
                contract_hash=expected_resource_hash,
            ),
        ]
    )
    if (
        deployment.resource_hash != expected_resource_hash
        or deployment.model_name != expected_model_name
        or deployment.experiment_name != expected_experiment_name
        or deployment.inference_table != expected_table
    ):
        raise RuntimeError("Gateway ResponsesAgent resource allocation contract drifted")
    upstream_details = workspace.serving_endpoints.get(deployment.upstream_endpoint)
    assert_runtime_creator(
        getattr(upstream_details, "creator", None),
        application_id=deployment.runtime_application_id,
        resource=f"managed Supervisor endpoint {deployment.upstream_endpoint}",
    )
    if str(getattr(upstream_details, "id", "") or "").strip() != (
        deployment.supervisor_endpoint_id
    ):
        raise RuntimeError("managed Supervisor endpoint immutable identity drifted")
    details = workspace.serving_endpoints.get(deployment.endpoint)
    assert_runtime_creator(
        getattr(details, "creator", None),
        application_id=deployment.runtime_application_id,
        resource=f"Gateway endpoint {deployment.endpoint}",
    )
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
    expected_entity, _traffic = _served_entity(
        supervisor_id=deployment.supervisor_id,
        upstream_endpoint=deployment.upstream_endpoint,
        runtime_application_id=deployment.runtime_application_id,
        catalog=deployment.catalog,
        genie_space_id=deployment.genie_space_id,
        model_name=deployment.model_name,
        model_version=deployment.model_version,
        experiment_id=deployment.experiment_id,
    )
    if not _proxy_config_matches(details, entity=expected_entity):
        raise RuntimeError("Gateway ResponsesAgent serving configuration is missing or stale")
    if not _endpoint_policy_matches(details):
        raise RuntimeError("Gateway ResponsesAgent endpoint policy is missing or stale")
    if str(getattr(details, "description", "") or "") != _ENDPOINT_DESCRIPTION:
        raise RuntimeError("Gateway ResponsesAgent endpoint description is missing or stale")
    if not _endpoint_tags_match(
        details,
        expected={
            SOURCE_HASH_TAG: deployment.source_hash,
            UPSTREAM_TAG: deployment.upstream_endpoint,
        },
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
    registered_model = workspace.registered_models.get(deployment.model_name)
    assert_runtime_creator(
        getattr(registered_model, "owner", None),
        application_id=deployment.runtime_application_id,
        resource=f"registered model {deployment.model_name}",
    )
    _verified_model_version_tags(deployment, model_registry=model_registry)
    experiment_client = tracking_client or MlflowClient(tracking_uri="databricks")
    _resolve_exact_experiment(
        experiment_client,
        experiment_name=deployment.experiment_name,
        experiment_id=deployment.experiment_id,
        runtime_application_id=deployment.runtime_application_id,
    )
    resolve_exact_experiment_acl(
        workspace,
        experiment_id=deployment.experiment_id,
        runtime_application_id=deployment.runtime_application_id,
    )
    _clear_deprecated_endpoint_rate_limits(workspace, endpoint=deployment.endpoint)


def ensure_gateway_responses_agent(
    workspace: Any,
    *,
    endpoint: str,
    endpoint_prefix: str,
    supervisor_id: str,
    upstream_endpoint: str,
    model_name: str,
    experiment_name: str,
    inference_catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
    genie_space_id: str,
    expected_creator_application_id: str,
) -> GatewayAgentDeployment:
    """Reuse an exact endpoint or create an immutable, versioned green endpoint."""

    require_gateway_model_attestation_signing_authority()
    attestation_verify_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    upstream_details = workspace.serving_endpoints.get(upstream_endpoint)
    assert_runtime_creator(
        getattr(upstream_details, "creator", None),
        application_id=expected_creator_application_id,
        resource=f"managed Supervisor endpoint {upstream_endpoint}",
    )
    supervisor_endpoint_id = str(getattr(upstream_details, "id", "") or "").strip()
    if not supervisor_endpoint_id:
        raise RuntimeError("managed Supervisor endpoint has no immutable ID")

    model_family = _target_model_family(
        configured=model_name,
        catalog=inference_catalog,
    )
    experiment_family = experiment_name.strip()
    gateway_experiment_base(
        runtime_application_id=expected_creator_application_id,
        experiment_family=experiment_family,
    )
    source_hash = gateway_agent_source_hash(
        upstream_endpoint=upstream_endpoint,
        catalog=inference_catalog,
        genie_space_id=genie_space_id,
    )
    contract_hash = gateway_resource_hash(
        source_hash=source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        runtime_application_id=expected_creator_application_id,
        model_name=model_family,
        experiment_name=experiment_family,
        inference_schema=inference_schema,
        inference_table_prefix=inference_table_prefix,
        attestation_verify_key=attestation_verify_key,
    )
    versioned_table_prefix = gateway_inference_table_prefix(
        base_prefix=inference_table_prefix,
        contract_hash=contract_hash,
    )
    versioned_model_name = gateway_agent_model_name(
        base_model_name=model_family,
        contract_hash=contract_hash,
    )
    versioned_experiment_name = gateway_experiment_name(
        base_experiment_name=experiment_family,
        contract_hash=contract_hash,
        runtime_application_id=expected_creator_application_id,
    )
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    selected_experiment = mlflow.set_experiment(versioned_experiment_name)
    client = MlflowClient()
    experiment_id = str(getattr(selected_experiment, "experiment_id", "") or "").strip()
    if not experiment_id:
        raise RuntimeError("Gateway MLflow experiment has no immutable ID")
    experiment = _resolve_exact_experiment(
        client,
        experiment_name=versioned_experiment_name,
        experiment_id=experiment_id,
        runtime_application_id=expected_creator_application_id,
    )
    recovery = reconcile_incomplete_source_versions(
        client,
        workspace,
        model_name=versioned_model_name,
        experiment_id=experiment_id,
        expected_creator_application_id=expected_creator_application_id,
        source_hash=source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        upstream_endpoint=upstream_endpoint,
        runtime_application_id=expected_creator_application_id,
        model_family=model_family,
        experiment_base=experiment_family,
        catalog=inference_catalog,
        genie_space_id=genie_space_id,
        inference_schema=inference_schema,
        inference_table_prefix=inference_table_prefix,
        verify_attestation=verify_gateway_model_contract,
    )
    active_durable = recovery.durable if recovery and recovery.journal_requires_clear else None
    model_version = recovery.ready_version if recovery else None
    if recovery is None:
        model_version = _existing_source_version(
            client,
            model_name=versioned_model_name,
            source_hash=source_hash,
            supervisor_id=supervisor_id,
            supervisor_endpoint_id=supervisor_endpoint_id,
            upstream_endpoint=upstream_endpoint,
            runtime_application_id=expected_creator_application_id,
            model_family=model_family,
            experiment_base=experiment_family,
            catalog=inference_catalog,
            genie_space_id=genie_space_id,
            inference_schema=inference_schema,
            inference_table_prefix=inference_table_prefix,
        )
    if model_version is None:
        if recovery is not None:
            cleanup_journal = recovery.durable.journal
            model_source = cleanup_journal.model_source
            registration_tags = recovery.durable.registration_tags
        else:
            require_no_unjournaled_gateway_sources(
                client,
                experiment_id=experiment_id,
                expected_logged_model_name="mortgage_growth_supervisor_proxy",
            )
            print(f"[agentic] logging Gateway Supervisor proxy: {versioned_model_name}")
            logged = _log_gateway_model(
                upstream_endpoint=upstream_endpoint,
                catalog=inference_catalog,
                genie_space_id=genie_space_id,
            )
            model_source = str(getattr(logged, "model_uri", "") or "").strip()
            if not model_source:
                raise RuntimeError("logged Gateway model has no immutable model URI")
            registration_tags = validated_model_version_tags(
                sign_gateway_model_contract(
                    full_name=versioned_model_name,
                    model_source=model_source,
                    source_hash=source_hash,
                    supervisor_id=supervisor_id,
                    supervisor_endpoint_id=supervisor_endpoint_id,
                    upstream_endpoint=upstream_endpoint,
                    runtime_application_id=expected_creator_application_id,
                    model_family=model_family,
                    experiment_base=experiment_family,
                    catalog=inference_catalog,
                    genie_space_id=genie_space_id,
                    inference_schema=inference_schema,
                    inference_table_prefix=inference_table_prefix,
                )
            )
            try:
                cleanup_journal = _registration_cleanup_journal(
                    client,
                    model_source=model_source,
                    expected_experiment_id=experiment_id,
                    logged=logged,
                )
            except RegistrationJournalVisibilityError as journal_error:
                try:
                    compensate_unregistered_logged_model(client, journal_error.journal)
                except Exception as cleanup_error:  # noqa: BLE001 - preserve both causes
                    raise RuntimeError(
                        "Gateway model journaling failed and pre-registration cleanup "
                        f"did not converge: {cleanup_error}"
                    ) from journal_error
                raise
            active_durable = DurableRegistrationJournal(
                model_name=versioned_model_name,
                journal=cleanup_journal,
                registration_tags=registration_tags,
            )
            persist_registration_journal(client, active_durable)
        try:
            registered = mlflow.register_model(
                model_source,
                versioned_model_name,
                tags=registration_tags,
            )
        except Exception as registration_error:  # noqa: BLE001 - compensate exact UC write
            try:
                recovered_version = _compensate_failed_model_registration(
                    client,
                    workspace,
                    model_name=versioned_model_name,
                    journal=cleanup_journal,
                    registration_tags=registration_tags,
                    expected_creator_application_id=expected_creator_application_id,
                )
            except RegistrationReconciliationPendingError as cleanup_error:
                raise cleanup_error from registration_error
            except Exception as cleanup_error:  # noqa: BLE001 - preserve both failure causes
                raise RuntimeError(
                    "Gateway model registration failed and cleanup did not converge: "
                    f"{cleanup_error}"
                ) from registration_error
            if recovered_version is None:
                raise RegistrationReconciliationPendingError(
                    "incomplete Gateway registration was removed; preserving the durable "
                    "journal and source for exact retry"
                ) from registration_error
            model_version = recovered_version
        else:
            model_version = int(registered.version)
    model_details = workspace.registered_models.get(versioned_model_name)
    assert_runtime_creator(
        getattr(model_details, "owner", None),
        application_id=expected_creator_application_id,
        resource=f"registered model {versioned_model_name}",
    )
    model_version_details = client.get_model_version(
        versioned_model_name,
        str(model_version),
    )
    if str(getattr(model_version_details, "name", "") or "").strip() != versioned_model_name or str(
        getattr(model_version_details, "version", "") or ""
    ).strip() != str(model_version):
        raise RuntimeError("Unity Catalog returned an unexpected Gateway model version")
    _require_ready_model_version(
        model_version_details,
        resource=f"Gateway candidate model {versioned_model_name} v{model_version}",
    )
    model_version_tags = {
        str(key): str(value)
        for key, value in dict(getattr(model_version_details, "tags", None) or {}).items()
    }
    model_source = str(getattr(model_version_details, "source", "") or "").strip()
    if not model_source:
        raise RuntimeError("registered Gateway model version has no immutable source")
    attestation_contract = {
        "full_name": versioned_model_name,
        "model_source": model_source,
        "source_hash": source_hash,
        "supervisor_id": supervisor_id,
        "supervisor_endpoint_id": supervisor_endpoint_id,
        "upstream_endpoint": upstream_endpoint,
        "runtime_application_id": expected_creator_application_id,
        "model_family": model_family,
        "experiment_base": experiment_family,
        "catalog": inference_catalog,
        "genie_space_id": genie_space_id,
        "inference_schema": inference_schema,
        "inference_table_prefix": inference_table_prefix,
    }
    if not verify_gateway_model_contract(tags=model_version_tags, **attestation_contract):
        raise RuntimeError("Gateway candidate model uses a previous attestation epoch")
    if gateway_model_attestation_record_key(model_version_tags) != attestation_verify_key:
        raise RuntimeError("Gateway candidate model attestation epoch drifted")
    persisted_model_tags = gateway_model_version_tags(model_version_tags)
    if (
        persisted_model_tags.contract["source_hash"] != source_hash
        or persisted_model_tags.contract["upstream_endpoint"] != upstream_endpoint
    ):
        raise RuntimeError("Gateway model version source-binding tags are not immutable")
    if active_durable is not None:
        if (
            model_source != active_durable.journal.model_source
            or model_version_tags != active_durable.registration_tags
        ):
            raise RuntimeError("READY Gateway version does not match its durable journal")
        clear_registration_journal(client, active_durable)

    entity, traffic = _served_entity(
        supervisor_id=supervisor_id,
        upstream_endpoint=upstream_endpoint,
        runtime_application_id=expected_creator_application_id,
        catalog=inference_catalog,
        genie_space_id=genie_space_id,
        model_name=versioned_model_name,
        model_version=model_version,
        experiment_id=str(experiment.experiment_id),
    )
    gateway = _gateway_config(
        catalog=inference_catalog,
        schema=inference_schema,
        table_prefix=versioned_table_prefix,
    )
    tags = [
        EndpointTag(SOURCE_HASH_TAG, source_hash),
        EndpointTag(UPSTREAM_TAG, upstream_endpoint),
    ]

    def read(candidate: str, *, require_runtime_creator: bool) -> Any | None:
        try:
            current = workspace.serving_endpoints.get(candidate)
        except (NotFound, ResourceDoesNotExist):
            return None
        creator = str(getattr(current, "creator", None) or "").strip()
        if require_runtime_creator:
            assert_runtime_creator(
                creator,
                application_id=expected_creator_application_id,
                resource=f"Gateway endpoint {candidate}",
            )
        if (
            creator == expected_creator_application_id
            and getattr(current, "pending_config", None) is not None
        ):
            print(f"[agentic] waiting for interrupted endpoint update: {candidate}")
            current = workspace.serving_endpoints.wait_get_serving_endpoint_not_updating(candidate)
        return current

    def exact(current: Any) -> bool:
        return (
            _proxy_config_matches(current, entity=entity)
            and _gateway_matches(
                current,
                catalog=inference_catalog,
                schema=inference_schema,
                table_prefix=versioned_table_prefix,
            )
            and _endpoint_tags_match(
                current,
                expected={SOURCE_HASH_TAG: source_hash, UPSTREAM_TAG: upstream_endpoint},
            )
            and str(getattr(current, "description", "") or "") == _ENDPOINT_DESCRIPTION
            and _endpoint_policy_matches(current)
        )

    details = read(endpoint, require_runtime_creator=False)
    actual_endpoint = endpoint
    if details is not None and (
        str(getattr(details, "creator", None) or "").strip() != expected_creator_application_id
        or not exact(details)
    ):
        actual_endpoint = f"{endpoint_prefix}-{contract_hash[:12]}"
        if actual_endpoint == endpoint:
            raise RuntimeError("Gateway green endpoint name collides with the live endpoint")
        details = read(actual_endpoint, require_runtime_creator=True)
        if details is not None and not exact(details):
            raise RuntimeError(
                "immutable green Gateway candidate drifted; refusing in-place repair"
            )

    if details is None:
        print(
            f"[agentic] creating Gateway Supervisor proxy: {actual_endpoint} "
            f"({versioned_model_name} v{model_version})"
        )
        workspace.serving_endpoints.create(
            name=actual_endpoint,
            config=EndpointCoreConfigInput(
                name=actual_endpoint,
                served_entities=[entity],
                traffic_config=traffic,
            ),
            ai_gateway=gateway,
            tags=tags,
            description=_ENDPOINT_DESCRIPTION,
            route_optimized=_ROUTE_OPTIMIZED,
        )
    else:
        print(f"[agentic] exact Gateway Supervisor proxy exists: {actual_endpoint}")

    inference_table = ".".join([inference_catalog, inference_schema, versioned_table_prefix])
    return GatewayAgentDeployment(
        endpoint=actual_endpoint,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        upstream_endpoint=upstream_endpoint,
        runtime_application_id=expected_creator_application_id,
        model_name=versioned_model_name,
        model_version=model_version,
        model_source=model_source,
        model_attestation_verify_key=attestation_verify_key,
        model_family=model_family,
        source_hash=source_hash,
        resource_hash=contract_hash,
        inference_table=inference_table,
        inference_table_prefix=inference_table_prefix,
        experiment_base=experiment_family,
        experiment_name=versioned_experiment_name,
        experiment_id=str(experiment.experiment_id),
        catalog=inference_catalog,
        genie_space_id=genie_space_id,
    )
