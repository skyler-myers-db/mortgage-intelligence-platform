"""Cut over and finalize a blue/green agent-runtime Supervisor replacement."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mlflow import MlflowClient

from databricks.sdk import WorkspaceClient
from tools.databricks import app_deployment_lease
from tools.databricks.agent_runtime_access import (
    assert_current_runtime_identity,
    assert_runtime_creator,
)
from tools.databricks.agent_runtime_cutover_cli import build_parser
from tools.databricks.agentic_supervisor_endpoint import (
    managed_query_supervisor_replacement_name,
)
from tools.databricks.app_gateway_access_mode import (
    app_service_principal_identity,
    assert_pinned_access_retirement_authority,
    revoke_managed_app_access,
)
from tools.databricks.cutover_journal_clearance import clear_journal
from tools.databricks.cutover_journal_store import (
    assert_retirement_journal,
    persist_cutover_journal,
    refresh_cutover_journal_attestation,
)
from tools.databricks.cutover_journal_store import read_cutover_journal as _read_journal
from tools.databricks.cutover_runtime_identity import (
    agent_by_id as _agent_by_id,
)
from tools.databricks.cutover_runtime_identity import (
    endpoint_identity as _endpoint_identity,
)
from tools.databricks.cutover_runtime_identity import (
    retirement_supervisor_by_id as _retirement_supervisor_by_id,
)
from tools.databricks.gateway_resource_identity import authenticated_workspace_host
from tools.databricks.provision_agentic_resources import (
    _converge_app_gateway_permissions,
    _run_no_json,
    _supervisor_agents,
    assert_exact_supervisor_contract,
)
from tools.databricks.provision_gateway_responses_agent import (
    GatewayAgentDeployment,
    gateway_agent_model_name,
    gateway_agent_source_hash,
    gateway_experiment_name,
    gateway_inference_table_prefix,
    gateway_resource_hash,
    verify_gateway_responses_agent,
)
from tools.databricks.retired_serving_query_groups import (
    delete_pinned_gateway,
    exact_service_principal_scim_id,
    retire_endpoint_query_groups,
    retire_pinned_supervisor,
)
from tools.databricks.supervisor_agent_contract import (
    RUNTIME_REPLACEMENT_SUFFIX,
    supervisor_replacement_name,
)
from tools.databricks.supervisor_creation_runtime import (
    assert_unique_live_supervisor_binding,
)


def pin_journal(
    workspace: Any,
    *,
    assert_single_writer: Callable[[], None],
    runtime_application_id: str,
    canonical_name: str,
    old_id: str | None = None,
    old_endpoint: str | None = None,
    old_creator: str | None = None,
    old_create_time: str | None = None,
    old_gateway_endpoint: str | None = None,
) -> None:
    """Persist the destructive tuple in the runtime SP's server-owned home."""

    supervisor_values = (old_id, old_endpoint, old_creator, old_create_time)
    if any(supervisor_values) and not all(supervisor_values):
        raise RuntimeError("old Supervisor cutover tuple is incomplete")
    payload = {
        "version": 3,
        "canonical_name": canonical_name,
    }
    if old_id and old_endpoint and old_creator and old_create_time:
        old = _agent_by_id(old_id)
        if old is None:
            raise RuntimeError("cannot pin a missing old Supervisor")
        pinned = (
            str(old.get("display_name") or ""),
            str(old.get("endpoint_name") or ""),
            str(old.get("creator") or ""),
            str(old.get("create_time") or ""),
        )
        if pinned != (canonical_name, old_endpoint, old_creator, old_create_time):
            raise RuntimeError("old Supervisor changed before cutover journal pinning")
        endpoint_id, endpoint_creator = _endpoint_identity(workspace, old_endpoint)
        if endpoint_creator != old_creator:
            raise RuntimeError("old Supervisor endpoint creator does not match the pinned agent")
        payload.update(
            old_id=old_id,
            old_endpoint=old_endpoint,
            old_endpoint_id=endpoint_id,
            old_creator=old_creator,
            old_create_time=old_create_time,
        )
    if old_gateway_endpoint:
        gateway_id, gateway_creator = _endpoint_identity(workspace, old_gateway_endpoint)
        payload.update(
            old_gateway_endpoint=old_gateway_endpoint,
            old_gateway_endpoint_id=gateway_id,
            old_gateway_creator=gateway_creator,
            old_gateway_delete_allowed=("1" if gateway_creator == runtime_application_id else "0"),
        )
    if len(payload) == 2:
        raise RuntimeError("cutover journal has no old runtime resource to pin")
    existing = _read_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    expected_existing = {key: str(value) for key, value in payload.items() if key != "version"}
    if existing is not None:
        if existing != expected_existing:
            raise RuntimeError("a different immutable cutover tuple is already pinned")
        refresh_cutover_journal_attestation(
            workspace,
            runtime_application_id=runtime_application_id,
            assert_single_writer=assert_single_writer,
        )
        return
    persist_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
        payload=payload,
        assert_single_writer=assert_single_writer,
    )


def export_journal(
    workspace: Any,
    *,
    runtime_application_id: str,
    out_env: Path,
) -> None:
    assert_current_runtime_identity(
        workspace,
        application_id=runtime_application_id,
    )
    journal = _read_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    rows: list[tuple[str, str]] = []
    if journal is not None:
        if journal.get("old_id"):
            supervisor_pin = json.dumps(
                {
                    "supervisor_id": journal["old_id"],
                    "endpoint": journal["old_endpoint"],
                    "endpoint_id": journal["old_endpoint_id"],
                    "creator": journal["old_creator"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            rows.extend(
                [
                    ("MIP_REPLACED_AGENT_SUPERVISOR_ID", journal["old_id"]),
                    ("MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT", journal["old_endpoint"]),
                    (
                        "MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID",
                        journal["old_endpoint_id"],
                    ),
                    ("MIP_REPLACED_AGENT_SUPERVISOR_CREATOR", journal["old_creator"]),
                    (
                        "MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME",
                        journal["old_create_time"],
                    ),
                    ("MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON", supervisor_pin),
                ]
            )
        if journal.get("old_gateway_endpoint"):
            gateway_pin = json.dumps(
                {
                    "name": journal["old_gateway_endpoint"],
                    "endpoint_id": journal["old_gateway_endpoint_id"],
                    "creator": journal["old_gateway_creator"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            rows.extend(
                [
                    ("MIP_REPLACED_AGENT_GATEWAY_ENDPOINT", journal["old_gateway_endpoint"]),
                    (
                        "MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID",
                        journal["old_gateway_endpoint_id"],
                    ),
                    ("MIP_REPLACED_AGENT_GATEWAY_CREATOR", journal["old_gateway_creator"]),
                    (
                        "MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED",
                        journal["old_gateway_delete_allowed"],
                    ),
                    ("MIP_REPLACED_AGENT_GATEWAY_PIN_JSON", gateway_pin),
                ]
            )
    out_env.write_text(
        "".join(f"{key}={shlex.quote(value)}\n" for key, value in rows),
        encoding="utf-8",
    )


def _assert_ready_endpoint(
    workspace: Any,
    *,
    endpoint: str,
    application_id: str,
    resource: str,
) -> object:
    details = workspace.serving_endpoints.get(endpoint)
    assert_runtime_creator(
        getattr(details, "creator", None),
        application_id=application_id,
        resource=resource,
    )
    state = getattr(details, "state", None)
    ready = str(
        getattr(getattr(state, "ready", None), "value", None) or getattr(state, "ready", "")
    )
    updating = str(
        getattr(getattr(state, "config_update", None), "value", None)
        or getattr(state, "config_update", "")
    )
    if ready.upper() != "READY" or updating.upper() != "NOT_UPDATING":
        raise RuntimeError(f"{resource} is not ready and stable")
    return details


def _assert_green_path(
    workspace: Any,
    *,
    assert_single_writer: Callable[[], None],
    canonical_name: str,
    replacement_id: str,
    replacement_endpoint: str,
    gateway_endpoint: str,
    gateway_model: str,
    gateway_model_version: int,
    gateway_inference_table: str,
    gateway_model_family: str,
    gateway_experiment_base: str,
    gateway_table_prefix: str,
    catalog: str,
    genie_space_id: str,
    runtime_application_id: str,
) -> None:
    workspace_host = authenticated_workspace_host(workspace, context="cutover")
    replacement = _agent_by_id(replacement_id)
    if replacement is None:
        raise RuntimeError("replacement Supervisor disappeared before cutover")
    if str(replacement.get("endpoint_name") or "") != replacement_endpoint:
        raise RuntimeError("replacement Supervisor endpoint changed before cutover")
    reviewed_names = {
        canonical_name,
        f"{canonical_name}{RUNTIME_REPLACEMENT_SUFFIX}",
        supervisor_replacement_name(
            canonical_name,
            genie_space_id=genie_space_id,
            catalog=catalog,
        ),
        managed_query_supervisor_replacement_name(
            canonical_name,
            genie_space_id=genie_space_id,
            catalog=catalog,
        ),
    }
    if str(replacement.get("display_name") or "") not in reviewed_names:
        raise RuntimeError("replacement Supervisor display name is outside the reviewed contract")
    assert_runtime_creator(
        replacement.get("creator"),
        application_id=runtime_application_id,
        resource="replacement Supervisor agent",
    )
    assert_exact_supervisor_contract(
        replacement_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    replacement_endpoint_details = _assert_ready_endpoint(
        workspace,
        endpoint=replacement_endpoint,
        application_id=runtime_application_id,
        resource="replacement managed Supervisor endpoint",
    )
    replacement_endpoint_id = str(getattr(replacement_endpoint_details, "id", "") or "").strip()
    if not replacement_endpoint_id:
        raise RuntimeError("replacement managed Supervisor endpoint has no immutable ID")
    gateway = _assert_ready_endpoint(
        workspace,
        endpoint=gateway_endpoint,
        application_id=runtime_application_id,
        resource="outer Gateway endpoint",
    )
    entities = getattr(getattr(gateway, "config", None), "served_entities", None) or []
    if len(entities) != 1:
        raise RuntimeError("outer Gateway must serve exactly one reviewed proxy")
    environment = getattr(entities[0], "environment_vars", None) or {}
    if environment.get("MIP_UPSTREAM_SUPERVISOR_ENDPOINT") != replacement_endpoint or (
        environment.get("DATABRICKS_HOST") != workspace_host
    ):
        raise RuntimeError("outer Gateway is not bound to the replacement Supervisor")
    model_family_parts = gateway_model_family.split(".")
    inference_parts = gateway_inference_table.split(".")
    if (
        len(model_family_parts) != 3
        or model_family_parts[0] != catalog
        or len(inference_parts) != 3
        or inference_parts[0] != catalog
        or not gateway_experiment_base.strip()
        or "/" in gateway_experiment_base
        or not gateway_table_prefix.strip()
    ):
        raise RuntimeError("outer Gateway family inputs are outside the reviewed target")
    _catalog, inference_schema, _concrete_inference_table = inference_parts
    model_attestation_verify_key = str(
        environment.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY") or ""
    ).strip()
    proxy_caller_application_id = str(environment.get("MIP_UPSTREAM_PROXY_CLIENT_ID") or "").strip()
    proxy_caller_credential_id = str(
        environment.get("MIP_UPSTREAM_PROXY_CREDENTIAL_ID") or ""
    ).strip()
    proxy_caller_secret_reference = str(
        environment.get("MIP_UPSTREAM_PROXY_CLIENT_SECRET") or ""
    ).strip()
    source_hash = gateway_agent_source_hash(
        upstream_endpoint=replacement_endpoint,
        catalog=catalog,
        genie_space_id=genie_space_id,
    )
    resource_hash = gateway_resource_hash(
        source_hash=source_hash,
        supervisor_id=replacement_id,
        supervisor_endpoint_id=replacement_endpoint_id,
        runtime_application_id=runtime_application_id,
        workspace_host=workspace_host,
        model_name=gateway_model_family,
        experiment_name=gateway_experiment_base,
        inference_schema=inference_schema,
        inference_table_prefix=gateway_table_prefix,
        attestation_verify_key=model_attestation_verify_key,
        proxy_caller_application_id=proxy_caller_application_id,
        proxy_caller_credential_id=proxy_caller_credential_id,
        proxy_caller_secret_reference=proxy_caller_secret_reference,
    )
    expected_model = gateway_agent_model_name(
        base_model_name=gateway_model_family,
        contract_hash=resource_hash,
    )
    expected_inference_table = ".".join(
        [
            catalog,
            inference_schema,
            gateway_inference_table_prefix(
                base_prefix=gateway_table_prefix,
                contract_hash=resource_hash,
            ),
        ]
    )
    if gateway_model != expected_model or gateway_inference_table != expected_inference_table:
        raise RuntimeError("outer Gateway model or inference-table family is not target-bound")
    model = workspace.registered_models.get(gateway_model)
    assert_runtime_creator(
        getattr(model, "owner", None),
        application_id=runtime_application_id,
        resource="registered Gateway proxy model",
    )
    experiment_id = str(environment.get("MLFLOW_EXPERIMENT_ID") or "").strip()
    if not experiment_id:
        raise RuntimeError("outer Gateway is missing its MLflow experiment binding")
    expected_experiment_name = gateway_experiment_name(
        base_experiment_name=gateway_experiment_base,
        contract_hash=resource_hash,
        runtime_application_id=runtime_application_id,
    )
    tracking_client = MlflowClient(tracking_uri="databricks")
    experiment = tracking_client.get_experiment(experiment_id)
    experiment_by_name = tracking_client.get_experiment_by_name(expected_experiment_name)
    if (
        experiment is None
        or experiment_by_name is None
        or str(getattr(experiment, "name", "") or "") != expected_experiment_name
        or str(getattr(experiment_by_name, "experiment_id", "") or "") != experiment_id
    ):
        raise RuntimeError("outer Gateway MLflow experiment name/ID binding drifted")
    assert_runtime_creator(
        (getattr(experiment, "tags", None) or {}).get("mlflow.ownerEmail"),
        application_id=runtime_application_id,
        resource="Gateway proxy MLflow experiment",
    )
    model_registry = MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    version = model_registry.get_model_version(gateway_model, str(gateway_model_version))
    model_source = str(getattr(version, "source", "") or "").strip()
    if not model_source:
        raise RuntimeError("outer Gateway model version has no immutable source")
    verify_gateway_responses_agent(
        workspace,
        GatewayAgentDeployment(
            endpoint=gateway_endpoint,
            supervisor_id=replacement_id,
            supervisor_endpoint_id=replacement_endpoint_id,
            upstream_endpoint=replacement_endpoint,
            runtime_application_id=runtime_application_id,
            workspace_host=workspace_host,
            proxy_caller_application_id=proxy_caller_application_id,
            proxy_caller_credential_id=proxy_caller_credential_id,
            proxy_caller_secret_reference=proxy_caller_secret_reference,
            model_name=gateway_model,
            model_version=gateway_model_version,
            model_source=model_source,
            model_attestation_verify_key=model_attestation_verify_key,
            model_family=gateway_model_family,
            source_hash=source_hash,
            resource_hash=resource_hash,
            inference_table=gateway_inference_table,
            inference_table_prefix=gateway_table_prefix,
            experiment_base=gateway_experiment_base,
            experiment_name=expected_experiment_name,
            experiment_id=experiment_id,
            catalog=catalog,
            genie_space_id=genie_space_id,
        ),
        model_registry=model_registry,
        tracking_client=tracking_client,
        assert_single_writer=assert_single_writer,
    )


def prepare(
    workspace: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    verifier_application_id: str,
    verifier_scim_id: str,
    assert_single_writer: Callable[[], None],
    preserve_endpoint: tuple[str, ...] = (),
    **green: Any,
) -> None:
    """Prove green and grant only its outer endpoint while old stays live."""

    _assert_green_path(workspace, assert_single_writer=assert_single_writer, **green)
    app_client_id, app_scim_id = app_service_principal_identity(workspace, app_name=app_name)
    assert_pinned_access_retirement_authority(
        workspace,
        app_name=app_name,
        journal=_read_journal(workspace, runtime_application_id=green["runtime_application_id"]),
        canonical_name=green["canonical_name"],
        green_gateway_endpoint=green["gateway_endpoint"],
        runtime_application_id=green["runtime_application_id"],
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
        verifier_application_id=verifier_application_id,
        verifier_scim_id=verifier_scim_id,
        agent_by_id=lambda supervisor_id: _retirement_supervisor_by_id(
            workspace,
            supervisor_id,
        ),
        preserve_endpoints=preserve_endpoint,
    )
    _converge_app_gateway_permissions(
        workspace,
        gateway_endpoint=green["gateway_endpoint"],
        supervisor_endpoint=green["replacement_endpoint"],
        app_name=app_name,
        deployment_lease_id=deployment_lease_id,
        deployment_source_git_sha=deployment_source_git_sha,
        preserve_endpoints=preserve_endpoint,
        assert_single_writer=assert_single_writer,
    )


def _delete_pinned_gateway(
    workspace: Any,
    *,
    app_name: str,
    endpoint: str | None,
    endpoint_id: str | None,
    creator: str | None,
    delete_allowed: bool,
    green_endpoint: str,
    runtime_application_id: str,
    app_principal: str,
    app_principal_id: str,
    verifier_application_id: str | None = None,
    verifier_scim_id: str | None = None,
    timeout_s: int,
    assert_single_writer: Callable[[], None],
) -> None:
    delete_pinned_gateway(
        workspace,
        app_name=app_name,
        endpoint=endpoint,
        endpoint_id=endpoint_id,
        creator=creator,
        delete_allowed=delete_allowed,
        green_endpoint=green_endpoint,
        runtime_application_id=runtime_application_id,
        app_principal=app_principal,
        app_principal_id=app_principal_id,
        verifier_application_id=verifier_application_id,
        verifier_scim_id=verifier_scim_id,
        timeout_s=timeout_s,
        assert_single_writer=assert_single_writer,
        endpoint_identity=_endpoint_identity,
        revoke_app_access=revoke_managed_app_access,
        retire_query_groups=retire_endpoint_query_groups,
    )


def retire(
    workspace: Any,
    *,
    canonical_name: str,
    replacement_id: str,
    replacement_endpoint: str,
    gateway_endpoint: str,
    app_name: str,
    runtime_application_id: str,
    old_id: str | None,
    old_endpoint: str | None,
    old_endpoint_id: str | None,
    old_creator: str | None,
    old_create_time: str | None,
    old_gateway_endpoint: str | None = None,
    old_gateway_endpoint_id: str | None = None,
    old_gateway_creator: str | None = None,
    old_gateway_delete_allowed: bool = False,
    verifier_application_id: str | None = None,
    verifier_scim_id: str | None = None,
    proxy_application_id: str | None = None,
    timeout_s: int,
    gateway_model: str,
    gateway_model_version: int,
    gateway_inference_table: str,
    gateway_model_family: str,
    gateway_experiment_base: str,
    gateway_table_prefix: str,
    catalog: str,
    genie_space_id: str,
    preserve_endpoint: tuple[str, ...] = (),
    assert_single_writer: Callable[[], None],
) -> None:
    """Re-prove green, then delete only the pinned old agent and endpoint."""

    assert_retirement_journal(
        workspace,
        runtime_application_id=runtime_application_id,
        canonical_name=canonical_name,
        old_id=old_id,
        old_endpoint=old_endpoint,
        old_endpoint_id=old_endpoint_id,
        old_creator=old_creator,
        old_create_time=old_create_time,
        old_gateway_endpoint=old_gateway_endpoint,
        old_gateway_endpoint_id=old_gateway_endpoint_id,
        old_gateway_creator=old_gateway_creator,
        old_gateway_delete_allowed=old_gateway_delete_allowed,
    )
    unpinned_preserve_endpoints = {
        endpoint
        for endpoint in preserve_endpoint
        if endpoint
        and endpoint
        not in {
            gateway_endpoint,
            replacement_endpoint,
            old_endpoint,
            old_gateway_endpoint,
        }
    }
    if unpinned_preserve_endpoints:
        raise RuntimeError("App ACL retirement includes an endpoint absent from the signed journal")
    _assert_green_path(
        workspace,
        assert_single_writer=assert_single_writer,
        canonical_name=canonical_name,
        replacement_id=replacement_id,
        replacement_endpoint=replacement_endpoint,
        gateway_endpoint=gateway_endpoint,
        gateway_model=gateway_model,
        gateway_model_version=gateway_model_version,
        gateway_inference_table=gateway_inference_table,
        gateway_model_family=gateway_model_family,
        gateway_experiment_base=gateway_experiment_base,
        gateway_table_prefix=gateway_table_prefix,
        catalog=catalog,
        genie_space_id=genie_space_id,
        runtime_application_id=runtime_application_id,
    )
    app_principal, app_principal_id = app_service_principal_identity(workspace, app_name=app_name)
    cleanup_enabled = bool(verifier_application_id or verifier_scim_id or proxy_application_id)
    if cleanup_enabled and not all(
        [verifier_application_id, verifier_scim_id, proxy_application_id]
    ):
        raise ValueError(
            "verifier application/SCIM and proxy application IDs are all required "
            "for endpoint-bound group retirement"
        )
    proxy_scim_id = (
        exact_service_principal_scim_id(
            workspace,
            application_id=str(proxy_application_id),
        )
        if cleanup_enabled
        else None
    )
    _delete_pinned_gateway(
        workspace,
        app_name=app_name,
        endpoint=old_gateway_endpoint,
        endpoint_id=old_gateway_endpoint_id,
        creator=old_gateway_creator,
        delete_allowed=old_gateway_delete_allowed,
        green_endpoint=gateway_endpoint,
        runtime_application_id=runtime_application_id,
        app_principal=app_principal,
        app_principal_id=app_principal_id,
        verifier_application_id=verifier_application_id,
        verifier_scim_id=verifier_scim_id,
        timeout_s=timeout_s,
        assert_single_writer=assert_single_writer,
    )
    retire_pinned_supervisor(
        workspace,
        app_name=app_name,
        canonical_name=canonical_name,
        old_id=old_id,
        old_endpoint=old_endpoint,
        old_endpoint_id=old_endpoint_id,
        old_creator=old_creator,
        old_create_time=old_create_time,
        app_principal=app_principal,
        app_principal_id=app_principal_id,
        proxy_application_id=proxy_application_id,
        proxy_scim_id=proxy_scim_id,
        cleanup_enabled=cleanup_enabled,
        timeout_s=timeout_s,
        assert_single_writer=assert_single_writer,
        agent_by_id=lambda supervisor_id: _retirement_supervisor_by_id(
            workspace,
            supervisor_id,
        ),
        endpoint_identity=_endpoint_identity,
        revoke_app_access=revoke_managed_app_access,
        delete_agent=_run_no_json,
        retire_query_groups=retire_endpoint_query_groups,
    )


def finalize(
    workspace: Any,
    *,
    canonical_name: str,
    replacement_id: str,
    replacement_endpoint: str,
    runtime_application_id: str,
    catalog: str,
    genie_space_id: str,
    assert_single_writer: Callable[[], None],
) -> None:
    """Rename the runtime-owned replacement only after the old ID is absent."""

    assert_current_runtime_identity(workspace, application_id=runtime_application_id)
    rows = _supervisor_agents()
    replacement = next(
        (row for row in rows if str(row.get("supervisor_agent_id") or "") == replacement_id),
        None,
    )
    if replacement is None:
        raise RuntimeError("replacement Supervisor is missing during finalization")
    conflicts = [
        row
        for row in rows
        if row.get("display_name") == canonical_name
        and str(row.get("supervisor_agent_id") or "") != replacement_id
    ]
    if conflicts:
        raise RuntimeError("old canonical Supervisor still exists; refusing rename")
    if str(replacement.get("endpoint_name") or "") != replacement_endpoint:
        raise RuntimeError("replacement endpoint changed before finalization")
    assert_runtime_creator(
        replacement.get("creator"),
        application_id=runtime_application_id,
        resource="replacement Supervisor agent",
    )
    if replacement.get("display_name") != canonical_name:
        assert_single_writer()
        try:
            _run_no_json(
                [
                    "supervisor-agents",
                    "update-supervisor-agent",
                    f"supervisor-agents/{replacement_id}",
                    "display_name",
                    canonical_name,
                ]
            )
        except Exception:  # noqa: BLE001 - resolve ambiguous provider commit
            try:
                assert_unique_live_supervisor_binding(
                    workspace,
                    supervisor_id=replacement_id,
                    display_name=canonical_name,
                    endpoint=replacement_endpoint,
                    runtime_application_id=runtime_application_id,
                )
            except Exception as read_error:  # noqa: BLE001
                raise RuntimeError(
                    "Supervisor canonical-name rename state is ambiguous"
                ) from read_error
    assert_unique_live_supervisor_binding(
        workspace,
        supervisor_id=replacement_id,
        display_name=canonical_name,
        endpoint=replacement_endpoint,
        runtime_application_id=runtime_application_id,
    )
    assert_exact_supervisor_contract(
        replacement_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
        expected_display_name=canonical_name,
    )
    assert_unique_live_supervisor_binding(
        workspace,
        supervisor_id=replacement_id,
        display_name=canonical_name,
        endpoint=replacement_endpoint,
        runtime_application_id=runtime_application_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = WorkspaceClient()
    lease_check: Callable[[], None] | None = None
    if args.command != "export-journal":
        lease_check = app_deployment_lease.held_assertion(
            workspace,
            app_name=args.app_name,
            lease_id=args.deployment_lease_id,
            source_git_sha=args.deployment_source_git_sha,
        )
        lease_check()
    if args.command == "pin-journal":
        assert lease_check is not None
        pin_journal(
            workspace,
            assert_single_writer=lease_check,
            runtime_application_id=args.runtime_application_id,
            canonical_name=args.canonical_name,
            old_id=args.old_id,
            old_endpoint=args.old_endpoint,
            old_creator=args.old_creator,
            old_create_time=args.old_create_time,
            old_gateway_endpoint=args.old_gateway_endpoint,
        )
        return 0
    if args.command == "export-journal":
        export_journal(
            workspace,
            runtime_application_id=args.runtime_application_id,
            out_env=args.out_env,
        )
        return 0
    if args.command == "refresh-journal-attestation":
        assert lease_check is not None
        refresh_cutover_journal_attestation(
            workspace,
            runtime_application_id=args.runtime_application_id,
            assert_single_writer=lease_check,
        )
        return 0
    if args.command == "clear-journal":
        assert lease_check is not None
        clear_journal(
            workspace,
            app_name=args.app_name,
            runtime_application_id=args.runtime_application_id,
            app_application_id=args.app_application_id,
            app_scim_id=args.app_scim_id,
            verifier_application_id=args.verifier_application_id,
            verifier_scim_id=args.verifier_scim_id,
            proxy_application_id=args.proxy_application_id,
            assert_single_writer=lease_check,
        )
        return 0
    if args.command == "resume-stale-journal":
        from tools.databricks.cutover_stale_journal_recovery import resume_stale_journal_from_args

        resume_stale_journal_from_args(workspace, args, lease_check)
        return 0
    if args.command == "converge-app-acl":
        assert lease_check is not None
        _converge_app_gateway_permissions(
            workspace,
            gateway_endpoint=args.gateway_endpoint,
            supervisor_endpoint=args.supervisor_endpoint,
            app_name=args.app_name,
            deployment_lease_id=args.deployment_lease_id,
            deployment_source_git_sha=args.deployment_source_git_sha,
            assert_single_writer=lease_check,
        )
        return 0
    assert lease_check is not None
    common = {
        "workspace": workspace,
        "canonical_name": args.canonical_name,
        "replacement_id": args.replacement_id,
        "replacement_endpoint": args.replacement_endpoint,
        "runtime_application_id": args.runtime_application_id,
        "assert_single_writer": lease_check,
    }
    if args.command in {"prepare", "retire"}:
        green = {
            **common,
            "gateway_endpoint": args.gateway_endpoint,
            "gateway_model": args.gateway_model,
            "gateway_model_version": args.gateway_model_version,
            "gateway_inference_table": args.gateway_inference_table,
            "gateway_model_family": args.gateway_model_family,
            "gateway_experiment_base": args.gateway_experiment_base,
            "gateway_table_prefix": args.gateway_table_prefix,
            "catalog": args.catalog,
            "genie_space_id": args.genie_space_id,
            "app_name": args.app_name,
            "preserve_endpoint": tuple(args.preserve_endpoint),
        }
        if args.command == "prepare":
            prepare(
                **green,
                deployment_lease_id=args.deployment_lease_id,
                deployment_source_git_sha=args.deployment_source_git_sha,
                verifier_application_id=args.verifier_application_id,
                verifier_scim_id=args.verifier_scim_id,
            )
        else:
            retire(
                **green,
                old_id=args.old_id,
                old_endpoint=args.old_endpoint,
                old_endpoint_id=args.old_endpoint_id,
                old_creator=args.old_creator,
                old_create_time=args.old_create_time,
                old_gateway_endpoint=args.old_gateway_endpoint,
                old_gateway_endpoint_id=args.old_gateway_endpoint_id,
                old_gateway_creator=args.old_gateway_creator,
                old_gateway_delete_allowed=args.old_gateway_delete_allowed,
                verifier_application_id=args.verifier_application_id,
                verifier_scim_id=args.verifier_scim_id,
                proxy_application_id=args.proxy_application_id,
                timeout_s=args.timeout_s,
            )
    else:
        finalize(
            **common,
            catalog=args.catalog,
            genie_space_id=args.genie_space_id,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
