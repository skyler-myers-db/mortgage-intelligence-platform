"""Cut over and finalize a blue/green agent-runtime Supervisor replacement."""

from __future__ import annotations

import argparse
import shlex
import time
from pathlib import Path
from typing import Any

from mlflow import MlflowClient

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.agent_runtime_access import (
    assert_current_runtime_identity,
    assert_runtime_creator,
)
from tools.databricks.cutover_journal_store import (
    assert_retirement_journal,
    clear_cutover_journal_exact,
    persist_cutover_journal,
    refresh_cutover_journal_attestation,
)
from tools.databricks.cutover_journal_store import (
    read_cutover_journal as _read_journal,
)
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
from tools.databricks.serving_endpoint_acl import revoke_direct_permissions
from tools.databricks.supervisor_agent_contract import (
    RUNTIME_REPLACEMENT_SUFFIX,
    supervisor_replacement_name,
)


def _agent_by_id(supervisor_id: str) -> dict[str, Any] | None:
    matches = [
        row
        for row in _supervisor_agents()
        if str(row.get("supervisor_agent_id") or "") == supervisor_id
    ]
    if len(matches) > 1:
        raise RuntimeError(f"duplicate Supervisor immutable ID {supervisor_id!r}")
    return matches[0] if matches else None


def _app_principal(workspace: Any, app_name: str) -> str:
    app = workspace.apps.get(app_name)
    principal = str(
        getattr(app, "service_principal_client_id", None)
        or (app.get("service_principal_client_id") if isinstance(app, dict) else "")
        or ""
    ).strip()
    if not principal:
        raise RuntimeError(f"app service principal not found for {app_name!r}")
    return principal


def _endpoint_identity(workspace: Any, endpoint: str) -> tuple[str, str]:
    details = workspace.serving_endpoints.get(endpoint)
    endpoint_id = str(getattr(details, "id", None) or "").strip()
    creator = str(getattr(details, "creator", None) or "").strip()
    if not endpoint_id or not creator:
        raise RuntimeError("serving endpoint has no immutable id or creator")
    return endpoint_id, creator


def pin_journal(
    workspace: Any,
    *,
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
        )
        return
    persist_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
        payload=payload,
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
                ]
            )
        if journal.get("old_gateway_endpoint"):
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
                ]
            )
    out_env.write_text(
        "".join(f"{key}={shlex.quote(value)}\n" for key, value in rows),
        encoding="utf-8",
    )


def clear_journal(workspace: Any, *, runtime_application_id: str) -> None:
    assert_current_runtime_identity(
        workspace,
        application_id=runtime_application_id,
    )
    clear_cutover_journal_exact(
        workspace,
        runtime_application_id=runtime_application_id,
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
    if environment.get("MIP_UPSTREAM_SUPERVISOR_ENDPOINT") != replacement_endpoint:
        raise RuntimeError("outer Gateway is not bound to the replacement Supervisor")
    model_family = gateway_model_family
    model_family_parts = model_family.split(".")
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
    inference_table_prefix = gateway_table_prefix
    model_attestation_verify_key = str(
        environment.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY") or ""
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
        model_name=model_family,
        experiment_name=gateway_experiment_base,
        inference_schema=inference_schema,
        inference_table_prefix=inference_table_prefix,
        attestation_verify_key=model_attestation_verify_key,
    )
    expected_model = gateway_agent_model_name(
        base_model_name=model_family,
        contract_hash=resource_hash,
    )
    expected_inference_table = ".".join(
        [
            catalog,
            inference_schema,
            gateway_inference_table_prefix(
                base_prefix=inference_table_prefix,
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
            model_name=gateway_model,
            model_version=gateway_model_version,
            model_source=model_source,
            model_attestation_verify_key=model_attestation_verify_key,
            model_family=model_family,
            source_hash=source_hash,
            resource_hash=resource_hash,
            inference_table=gateway_inference_table,
            inference_table_prefix=inference_table_prefix,
            experiment_base=gateway_experiment_base,
            experiment_name=expected_experiment_name,
            experiment_id=experiment_id,
            catalog=catalog,
            genie_space_id=genie_space_id,
        ),
        model_registry=model_registry,
        tracking_client=tracking_client,
    )


def prepare(
    workspace: Any,
    *,
    app_name: str,
    preserve_endpoint: tuple[str, ...] = (),
    **green: Any,
) -> None:
    """Prove green and grant only its outer endpoint while old stays live."""

    _assert_green_path(workspace, **green)
    _converge_app_gateway_permissions(
        workspace,
        gateway_endpoint=green["gateway_endpoint"],
        supervisor_endpoint=green["replacement_endpoint"],
        app_name=app_name,
        preserve_endpoints=preserve_endpoint,
    )


def _delete_pinned_gateway(
    workspace: Any,
    *,
    endpoint: str | None,
    endpoint_id: str | None,
    creator: str | None,
    delete_allowed: bool,
    green_endpoint: str,
    runtime_application_id: str,
    app_principal: str,
    timeout_s: int,
) -> None:
    values = (endpoint, endpoint_id, creator)
    if not any(values):
        return
    if not all(values):
        raise RuntimeError("old Gateway cutover requires its complete pinned identity")
    assert endpoint is not None and endpoint_id is not None and creator is not None
    if endpoint == green_endpoint:
        raise RuntimeError("old Gateway endpoint equals green; refusing destructive cutover")
    if delete_allowed:
        assert_runtime_creator(
            creator,
            application_id=runtime_application_id,
            resource=f"pinned old Gateway endpoint {endpoint}",
        )
    try:
        actual = _endpoint_identity(workspace, endpoint)
    except (NotFound, ResourceDoesNotExist):
        return
    if actual != (endpoint_id, creator):
        raise RuntimeError("old Gateway endpoint changed; refusing destructive cutover")
    revoke_direct_permissions(
        workspace,
        endpoint_name=endpoint,
        service_principal=app_principal,
        missing_ok=True,
    )
    if _endpoint_identity(workspace, endpoint) != (endpoint_id, creator):
        raise RuntimeError("old Gateway endpoint changed while revoking its App access")
    if not delete_allowed:
        return
    workspace.serving_endpoints.delete(endpoint)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            workspace.serving_endpoints.get(endpoint)
        except (NotFound, ResourceDoesNotExist):
            return
        time.sleep(5)
    raise TimeoutError("old Gateway endpoint remained after governed cleanup")


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
    app_principal = _app_principal(workspace, app_name)
    _delete_pinned_gateway(
        workspace,
        endpoint=old_gateway_endpoint,
        endpoint_id=old_gateway_endpoint_id,
        creator=old_gateway_creator,
        delete_allowed=old_gateway_delete_allowed,
        green_endpoint=gateway_endpoint,
        runtime_application_id=runtime_application_id,
        app_principal=app_principal,
        timeout_s=timeout_s,
    )
    if not old_id:
        return
    if not all([old_endpoint, old_endpoint_id, old_creator, old_create_time]):
        raise RuntimeError("old Supervisor cutover requires its complete pinned identity")
    assert old_endpoint is not None
    assert old_endpoint_id is not None
    assert old_creator is not None
    assert old_create_time is not None
    try:
        actual_endpoint_id, actual_endpoint_creator = _endpoint_identity(
            workspace,
            old_endpoint,
        )
    except (NotFound, ResourceDoesNotExist) as exc:
        if _agent_by_id(old_id) is not None:
            raise RuntimeError("old Supervisor still exists without its pinned endpoint") from exc
        return
    if (actual_endpoint_id, actual_endpoint_creator) != (old_endpoint_id, old_creator):
        raise RuntimeError("old managed endpoint changed; refusing destructive cutover")
    old = _agent_by_id(old_id)
    if old is not None:
        pinned = (
            str(old.get("display_name") or ""),
            str(old.get("endpoint_name") or ""),
            str(old.get("creator") or ""),
            str(old.get("create_time") or ""),
        )
        if pinned != (canonical_name, old_endpoint, old_creator, old_create_time):
            raise RuntimeError(
                "old Supervisor changed after provisioning; refusing destructive cutover"
            )

        revoke_direct_permissions(
            workspace,
            endpoint_name=old_endpoint,
            service_principal=app_principal,
            missing_ok=False,
        )
        if _endpoint_identity(workspace, old_endpoint) != (old_endpoint_id, old_creator):
            raise RuntimeError("old managed endpoint changed while revoking its App bypass")
        if _agent_by_id(old_id) != old:
            raise RuntimeError("old Supervisor changed while revoking its App bypass")
        _run_no_json(
            [
                "supervisor-agents",
                "delete-supervisor-agent",
                f"supervisor-agents/{old_id}",
            ]
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if _agent_by_id(old_id) is None:
                break
            time.sleep(5)
        else:
            raise TimeoutError("old Supervisor was not deleted after the governed cutover")
    else:
        revoke_direct_permissions(
            workspace,
            endpoint_name=old_endpoint,
            service_principal=app_principal,
            missing_ok=True,
        )

    try:
        orphan_identity = _endpoint_identity(workspace, old_endpoint)
    except (NotFound, ResourceDoesNotExist):
        return
    if orphan_identity != (old_endpoint_id, old_creator):
        raise RuntimeError("old managed endpoint identity changed; refusing orphan cleanup")
    workspace.serving_endpoints.delete(old_endpoint)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            workspace.serving_endpoints.get(old_endpoint)
        except (NotFound, ResourceDoesNotExist):
            return
        time.sleep(5)
    raise TimeoutError("old managed Supervisor endpoint remained after explicit cleanup")


def finalize(
    workspace: Any,
    *,
    canonical_name: str,
    replacement_id: str,
    replacement_endpoint: str,
    runtime_application_id: str,
    catalog: str,
    genie_space_id: str,
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
        _run_no_json(
            [
                "supervisor-agents",
                "update-supervisor-agent",
                f"supervisor-agents/{replacement_id}",
                "display_name",
                canonical_name,
            ]
        )
    final = _agent_by_id(replacement_id)
    if final is None or final.get("display_name") != canonical_name:
        raise RuntimeError("Supervisor canonical-name finalization failed")
    assert_runtime_creator(
        final.get("creator"),
        application_id=runtime_application_id,
        resource="canonical Supervisor agent",
    )
    assert_exact_supervisor_contract(
        replacement_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "retire", "finalize"):
        command = subparsers.add_parser(name)
        command.add_argument("--canonical-name", default="Mortgage Growth Agent")
        command.add_argument("--replacement-id", required=True)
        command.add_argument("--replacement-endpoint", required=True)
        command.add_argument("--runtime-application-id", required=True)
    for name in ("prepare", "retire"):
        command = subparsers.choices[name]
        command.add_argument("--gateway-endpoint", required=True)
        command.add_argument("--gateway-model", required=True)
        command.add_argument("--gateway-model-version", type=int, required=True)
        command.add_argument("--gateway-inference-table", required=True)
        command.add_argument("--gateway-model-family", required=True)
        command.add_argument("--gateway-experiment-base", required=True)
        command.add_argument("--gateway-table-prefix", required=True)
        command.add_argument("--catalog", required=True)
        command.add_argument("--genie-space-id", required=True)
        command.add_argument("--app-name", required=True)
        command.add_argument("--preserve-endpoint", action="append", default=[])
    retire_parser = subparsers.choices["retire"]
    retire_parser.add_argument("--old-id")
    retire_parser.add_argument("--old-endpoint")
    retire_parser.add_argument("--old-endpoint-id")
    retire_parser.add_argument("--old-creator")
    retire_parser.add_argument("--old-create-time")
    retire_parser.add_argument("--old-gateway-endpoint")
    retire_parser.add_argument("--old-gateway-endpoint-id")
    retire_parser.add_argument("--old-gateway-creator")
    retire_parser.add_argument("--old-gateway-delete-allowed", action="store_true")
    retire_parser.add_argument("--timeout-s", type=int, default=900)
    finalize_parser = subparsers.choices["finalize"]
    finalize_parser.add_argument("--catalog", required=True)
    finalize_parser.add_argument("--genie-space-id", required=True)
    pin_parser = subparsers.add_parser("pin-journal")
    pin_parser.add_argument("--runtime-application-id", required=True)
    pin_parser.add_argument("--canonical-name", default="Mortgage Growth Agent")
    pin_parser.add_argument("--old-id")
    pin_parser.add_argument("--old-endpoint")
    pin_parser.add_argument("--old-creator")
    pin_parser.add_argument("--old-create-time")
    pin_parser.add_argument("--old-gateway-endpoint")
    export_parser = subparsers.add_parser("export-journal")
    export_parser.add_argument("--runtime-application-id", required=True)
    export_parser.add_argument("--out-env", type=Path, required=True)
    refresh_parser = subparsers.add_parser("refresh-journal-attestation")
    refresh_parser.add_argument("--runtime-application-id", required=True)
    clear_parser = subparsers.add_parser("clear-journal")
    clear_parser.add_argument("--runtime-application-id", required=True)
    acl_parser = subparsers.add_parser("converge-app-acl")
    acl_parser.add_argument("--gateway-endpoint", required=True)
    acl_parser.add_argument("--supervisor-endpoint", required=True)
    acl_parser.add_argument("--app-name", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = WorkspaceClient()
    if args.command == "pin-journal":
        pin_journal(
            workspace,
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
        refresh_cutover_journal_attestation(
            workspace,
            runtime_application_id=args.runtime_application_id,
        )
        return 0
    if args.command == "clear-journal":
        clear_journal(
            workspace,
            runtime_application_id=args.runtime_application_id,
        )
        return 0
    if args.command == "converge-app-acl":
        _converge_app_gateway_permissions(
            workspace,
            gateway_endpoint=args.gateway_endpoint,
            supervisor_endpoint=args.supervisor_endpoint,
            app_name=args.app_name,
        )
        return 0
    common = {
        "workspace": workspace,
        "canonical_name": args.canonical_name,
        "replacement_id": args.replacement_id,
        "replacement_endpoint": args.replacement_endpoint,
        "runtime_application_id": args.runtime_application_id,
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
            prepare(**green)
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
