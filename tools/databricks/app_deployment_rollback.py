#!/usr/bin/env python3
"""Persist, verify, and restore the exact last-good Databricks App deployment."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_ENDPOINT,
    LEGACY_GATEWAY_ENDPOINT,
    gateway_runtime_binding_hash,
)
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import AppDeployment
from tools.databricks.app_deployment_lease import assert_held as assert_deployment_lease_held
from tools.databricks.app_health_contract import authenticated_app_health
from tools.databricks.app_rollback_record_contract import (
    RECORD_VERSION,
    _load_record,
    _payload_digest,
    _save_record,
    _text,
    _validated_gateway_resources,
    _validated_payload,
)
from tools.databricks.app_rollback_record_contract import (
    _record_key as _contract_record_key,
)
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract,
    app_resource_contract_digest,
    reviewed_app_resource_contract,
    validated_app_resource_contract,
)
from tools.databricks.converge_campaign_treatment_access import (
    Mode,
    converge_campaign_treatment_access,
)
from tools.databricks.export_gateway_runtime_contract import (
    ExactGatewayRuntimeProof,
    resolve_exact_resource_proof,
)
from tools.databricks.lakebase_instance_contract import resolve_lakebase_instance_aliases
from tools.databricks.serving_endpoint_acl import (
    grant_direct_can_query,
    revoke_direct_permissions,
)

DEFAULT_SCOPE = "mip-app-rollback"
DEPLOY_TIMEOUT = timedelta(minutes=20)


def _expected_lakebase_instance() -> str:
    try:
        return resolve_lakebase_instance_aliases(os.environ, require_both=True)
    except ValueError as exc:
        raise RuntimeError(f"rollback Lakebase deployment control is invalid: {exc}") from exc


def _record_key(app_name: str) -> str:
    """Retain the rollback module's key helper for compatibility."""
    return _contract_record_key(app_name)


def _deployment_state(deployment: object) -> str:
    return _text(getattr(getattr(deployment, "status", None), "state", None)).split(".")[-1].upper()


def _latest_succeeded(workspace: Any, *, app_name: str) -> object:
    deployments = [
        deployment
        for deployment in workspace.apps.list_deployments(app_name)
        if _deployment_state(deployment) == "SUCCEEDED"
        and _text(getattr(deployment, "deployment_id", None))
    ]
    if not deployments:
        raise RuntimeError("existing App has no succeeded deployment to preserve")
    deployments.sort(
        key=lambda item: (
            _text(getattr(item, "update_time", None)),
            _text(getattr(item, "create_time", None)),
            _text(getattr(item, "deployment_id", None)),
        )
    )
    return deployments[-1]


def _app_identity(workspace: Any, *, app_name: str) -> tuple[str, str]:
    app = workspace.apps.get(app_name)
    client_id = _text(getattr(app, "service_principal_client_id", None))
    scim_id = _text(getattr(app, "service_principal_id", None))
    if not client_id or not scim_id:
        raise RuntimeError("App rollback could not resolve both service-principal identifiers")
    return client_id, scim_id


def _assert_record_app_identity(
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
) -> None:
    client_id, scim_id = _app_identity(workspace, app_name=app_name)
    if (
        client_id != record["app_service_principal_client_id"]
        or scim_id != record["app_service_principal_scim_id"]
    ):
        raise RuntimeError(
            "App service-principal identity drifted from the signed rollback contract"
        )
    if app_resource_contract(workspace, app_name=app_name) != record["app_resources"]:
        raise RuntimeError("Databricks App resource bindings drifted from the signed contract")


def _converge_treatment_guard(
    workspace: Any,
    *,
    app_name: str,
    warehouse_id: str,
    catalog: str,
    mode: Mode,
) -> None:
    principal, _scim_id = _app_identity(workspace, app_name=app_name)
    existed = converge_campaign_treatment_access(
        warehouse_id=warehouse_id,
        catalog=catalog,
        principal=principal,
        mode=mode,
        workspace=workspace,
    )
    if not existed:
        raise RuntimeError("signed App rollback requires the governed treatment table")


def _active_deployment_id(workspace: Any, *, app_name: str) -> str:
    app = workspace.apps.get(app_name)
    if getattr(app, "pending_deployment", None) is not None:
        raise RuntimeError("App has a pending deployment; rollback identity is not stable")
    active = getattr(app, "active_deployment", None)
    if active is None:
        raise RuntimeError("App has no active deployment to bind")
    if _deployment_state(active) == "IN_PROGRESS":
        raise RuntimeError("App active deployment is still in progress")
    deployment_id = _text(getattr(active, "deployment_id", None))
    if not deployment_id:
        raise RuntimeError("App active deployment has no immutable deployment ID")
    return deployment_id


def _immutable_source(deployment: object) -> str:
    source = _text(
        getattr(getattr(deployment, "deployment_artifacts", None), "source_code_path", None)
    )
    if not source.startswith("/Workspace/Users/") or "/src/" not in source:
        raise RuntimeError("succeeded App deployment has no immutable source artifact")
    return source


def _health(
    workspace: Any,
    *,
    app_name: str,
    base_url: str,
    bearer_token: str,
) -> tuple[str, str | None, str]:
    body = authenticated_app_health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
    )
    git_sha = str(body.get("git_sha") or "").strip()
    binding = body.get("agent_gateway_binding_sha256")
    lease_id = str(body.get("deployment_lease_id") or "").strip()
    if len(git_sha) != 40:
        raise RuntimeError("App health did not expose an exact deployment SHA")
    if binding is not None and (not isinstance(binding, str) or len(binding) != 64):
        raise RuntimeError("App health exposed an invalid Gateway binding")
    try:
        UUID(lease_id)
    except ValueError as exc:
        raise RuntimeError("App health did not expose a valid deployment lease") from exc
    return git_sha, binding, lease_id


def _verify_health(
    workspace: Any,
    *,
    app_name: str,
    base_url: str,
    bearer_token: str,
    git_sha: str,
    gateway_binding: str | None,
    deployment_lease_id: str,
) -> None:
    actual_sha, actual_binding, actual_lease_id = _health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
    )
    if (
        actual_sha != git_sha
        or actual_binding != gateway_binding
        or actual_lease_id != deployment_lease_id
    ):
        raise RuntimeError("App health does not match the exact last-good rollback contract")


def _ensure_started(workspace: Any, *, app_name: str) -> None:
    state = _text(
        getattr(getattr(workspace.apps.get(app_name), "compute_status", None), "state", None)
    )
    normalized = state.split(".")[-1].upper()
    if normalized == "STOPPING":
        workspace.apps.wait_get_app_stopped(app_name, timeout=DEPLOY_TIMEOUT)
        normalized = "STOPPED"
    if normalized == "STOPPED":
        workspace.apps.start_and_wait(app_name, timeout=DEPLOY_TIMEOUT)


def _env_map(payload: dict[str, object]) -> dict[str, str]:
    env_vars = cast(list[object], payload["env_vars"])
    return {
        str(item["name"]): str(item.get("value") or "")
        for item in env_vars
        if isinstance(item, dict) and "value" in item
    }


def _payload_gateway_binding(payload: dict[str, object]) -> str | None:
    env = _env_map(payload)
    names = (
        "MIP_AGENT_SERVING_ENDPOINT",
        "MIP_AGENT_SUPERVISOR_ID",
        "MIP_AGENT_SUPERVISOR_ENDPOINT",
        "MIP_AGENT_RUNTIME_CLIENT_ID",
        "MIP_AI_GATEWAY_AGENT_MODEL",
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
        "MIP_AI_GATEWAY_INFERENCE_TABLE",
    )
    if not all(env.get(name) for name in names):
        return None
    try:
        version = int(env["MIP_AI_GATEWAY_AGENT_MODEL_VERSION"])
    except ValueError as exc:
        raise RuntimeError("App rollback payload has an invalid Gateway model version") from exc
    return gateway_runtime_binding_hash(
        endpoint=env["MIP_AGENT_SERVING_ENDPOINT"],
        supervisor_id=env["MIP_AGENT_SUPERVISOR_ID"],
        upstream_endpoint=env["MIP_AGENT_SUPERVISOR_ENDPOINT"],
        runtime_application_id=env["MIP_AGENT_RUNTIME_CLIENT_ID"],
        model_name=env["MIP_AI_GATEWAY_AGENT_MODEL"],
        model_version=version,
        inference_table=env["MIP_AI_GATEWAY_INFERENCE_TABLE"],
    )


def _payload_deployment_lease_id(payload: dict[str, object]) -> str:
    lease_id = _env_map(payload).get("MIP_APP_DEPLOYMENT_LEASE_ID", "").strip()
    try:
        UUID(lease_id)
    except ValueError as exc:
        raise RuntimeError("App rollback payload has an invalid deployment lease") from exc
    return lease_id


def _payload_resource_proof(
    workspace: Any,
    *,
    payload: dict[str, object],
    genie_space_id: str,
) -> ExactGatewayRuntimeProof:
    env = _env_map(payload)
    required = (
        "MIP_DEFAULT_CATALOG",
        "MIP_AGENT_SUPERVISOR_NAME",
        "MIP_AGENT_RUNTIME_CLIENT_ID",
        "MIP_AI_GATEWAY_ENDPOINT",
        "MIP_AI_GATEWAY_AGENT_MODEL",
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
        "MIP_AI_GATEWAY_AGENT_MODEL_SOURCE",
        "MIP_AI_GATEWAY_EXPERIMENT_NAME",
        "MIP_AI_GATEWAY_EXPERIMENT_ID",
        "MIP_AI_GATEWAY_INFERENCE_TABLE",
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256",
    )
    if not genie_space_id.strip() or not all(env.get(name) for name in required):
        raise RuntimeError("App rollback payload lacks its exact Gateway resource contract")
    proof = resolve_exact_resource_proof(
        workspace,
        supervisor_name=env["MIP_AGENT_SUPERVISOR_NAME"],
        catalog=env["MIP_DEFAULT_CATALOG"],
        genie_space_id=genie_space_id,
        runtime_application_id=env["MIP_AGENT_RUNTIME_CLIENT_ID"],
        supervisor_id=env["MIP_AGENT_SUPERVISOR_ID"],
        gateway_endpoint=env["MIP_AI_GATEWAY_ENDPOINT"],
        gateway_model_family_name=env.get("MIP_AI_GATEWAY_AGENT_MODEL_FAMILY"),
        gateway_experiment_base_name=env.get("MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE"),
        gateway_table_prefix=env.get("MIP_AI_GATEWAY_TABLE_PREFIX"),
        require_resource_binding=True,
    )
    expected = {
        "gateway_model_name": env["MIP_AI_GATEWAY_AGENT_MODEL"],
        "gateway_model_version": env["MIP_AI_GATEWAY_AGENT_MODEL_VERSION"],
        "gateway_model_source": env["MIP_AI_GATEWAY_AGENT_MODEL_SOURCE"],
        "gateway_experiment_name": env["MIP_AI_GATEWAY_EXPERIMENT_NAME"],
        "gateway_experiment_id": env["MIP_AI_GATEWAY_EXPERIMENT_ID"],
        "gateway_inference_table": env["MIP_AI_GATEWAY_INFERENCE_TABLE"],
    }
    drifted = sorted(key for key, value in expected.items() if proof.contract.get(key) != value)
    if drifted or proof.digest != env["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"]:
        raise RuntimeError(
            "App rollback payload does not match its live exact Gateway resource proof"
        )
    return proof


def _stored_resource_proof(
    workspace: Any,
    *,
    record: dict[str, Any],
) -> ExactGatewayRuntimeProof:
    resources = _validated_gateway_resources(record.get("gateway_resources"))
    return resolve_exact_resource_proof(
        workspace,
        supervisor_name=resources["supervisor_canonical_name"],
        catalog=resources["catalog"],
        genie_space_id=resources["genie_space_id"],
        runtime_application_id=resources["runtime_application_id"],
        supervisor_id=resources["supervisor_id"],
        gateway_endpoint=resources["gateway_endpoint"],
        expected=resources,
        require_resource_binding=True,
    )


def _rollback_gateway_endpoint(record: dict[str, Any]) -> str:
    env = _env_map(record["payload"])
    endpoint = (env.get("MIP_AGENT_SERVING_ENDPOINT") or "").strip()
    metadata_endpoint = (env.get("MIP_AI_GATEWAY_ENDPOINT") or "").strip()
    if not endpoint or (metadata_endpoint and metadata_endpoint != endpoint):
        raise RuntimeError("signed App rollback payload has no Gateway endpoint")
    return endpoint


def _app_principal(workspace: Any, *, app_name: str) -> str:
    principal = _text(getattr(workspace.apps.get(app_name), "service_principal_client_id", None))
    if not principal:
        raise RuntimeError("App rollback could not resolve the App service principal")
    return principal


def _converge_rollback_endpoint_acl(
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
    revoke_endpoints: tuple[str, ...] = (),
) -> None:
    principal = _app_principal(workspace, app_name=app_name)
    blue_endpoint = _rollback_gateway_endpoint(record)
    grant_direct_can_query(
        workspace,
        endpoint_name=blue_endpoint,
        service_principal=principal,
    )
    candidates = {
        DEFAULT_GATEWAY_ENDPOINT,
        LEGACY_GATEWAY_ENDPOINT,
        *revoke_endpoints,
    }
    list_endpoints = getattr(getattr(workspace, "serving_endpoints", None), "list", None)
    if callable(list_endpoints):
        for item in list_endpoints():
            name = _text(
                item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
            )
            if name in (LEGACY_GATEWAY_ENDPOINT, DEFAULT_GATEWAY_ENDPOINT) or name.startswith(
                f"{DEFAULT_GATEWAY_ENDPOINT}-"
            ):
                candidates.add(name)
    for endpoint in sorted(candidates - {blue_endpoint, ""}):
        revoke_direct_permissions(
            workspace,
            endpoint_name=endpoint,
            service_principal=principal,
            missing_ok=True,
        )


def _record_from(
    *,
    app_name: str,
    deployment: object,
    payload: dict[str, object],
    git_sha: str,
    gateway_binding: str | None,
    gateway_resources: dict[str, str],
    app_resources: list[dict[str, object]],
    app_service_principal_client_id: str,
    app_service_principal_scim_id: str,
    expected_lakebase_instance: str,
) -> dict[str, Any]:
    immutable_payload = copy.deepcopy(payload)
    immutable_payload["source_code_path"] = _immutable_source(deployment)
    immutable_payload = _validated_payload(
        immutable_payload,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    return {
        "version": RECORD_VERSION,
        "app_name": app_name,
        "deployment_id": _text(getattr(deployment, "deployment_id", None)),
        "app_service_principal_client_id": app_service_principal_client_id,
        "app_service_principal_scim_id": app_service_principal_scim_id,
        "git_sha": git_sha,
        "gateway_binding_sha256": gateway_binding,
        "gateway_resources": _validated_gateway_resources(gateway_resources),
        "app_resources": validated_app_resource_contract(app_resources),
        "app_resources_sha256": app_resource_contract_digest(app_resources),
        "payload": immutable_payload,
        "payload_sha256": _payload_digest(immutable_payload),
    }


def ensure_current(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    base_url: str,
    bearer_token: str,
    treatment_warehouse_id: str,
    treatment_catalog: str,
) -> str:
    expected_lakebase_instance = _expected_lakebase_instance()
    _converge_treatment_guard(
        workspace,
        app_name=app_name,
        warehouse_id=treatment_warehouse_id,
        catalog=treatment_catalog,
        mode="quiesce",
    )
    try:
        record = _load_record(
            workspace,
            app_name=app_name,
            scope=scope,
            expected_lakebase_instance=expected_lakebase_instance,
        )
        _assert_record_app_identity(workspace, app_name=app_name, record=record)
        _stored_resource_proof(workspace, record=record)
        _ensure_started(workspace, app_name=app_name)
        active_id = _active_deployment_id(workspace, app_name=app_name)
        latest = _latest_succeeded(workspace, app_name=app_name)
        if (
            active_id != record["deployment_id"]
            or _text(getattr(latest, "deployment_id", None)) != record["deployment_id"]
        ):
            restore_last_good(
                workspace,
                app_name=app_name,
                scope=scope,
                base_url=base_url,
                bearer_token=bearer_token,
                treatment_warehouse_id=treatment_warehouse_id,
                treatment_catalog=treatment_catalog,
                restore_treatment=False,
            )
            refreshed = _load_record(
                workspace,
                app_name=app_name,
                scope=scope,
                expected_lakebase_instance=expected_lakebase_instance,
            )
            return _rollback_gateway_endpoint(refreshed)
        _converge_rollback_endpoint_acl(
            workspace,
            app_name=app_name,
            record=record,
        )
        _verify_health(
            workspace,
            app_name=app_name,
            base_url=base_url,
            bearer_token=bearer_token,
            git_sha=record["git_sha"],
            gateway_binding=record["gateway_binding_sha256"],
            deployment_lease_id=_payload_deployment_lease_id(record["payload"]),
        )
        _stored_resource_proof(workspace, record=record)
        if _active_deployment_id(workspace, app_name=app_name) != record["deployment_id"]:
            raise RuntimeError("App active deployment changed during signed-blue verification")
        if (
            record.get("attestation_verify_key")
            != os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
        ):
            _save_record(workspace, scope=scope, record=record)
        return _rollback_gateway_endpoint(record)
    except BaseException:
        _converge_treatment_guard(
            workspace,
            app_name=app_name,
            warehouse_id=treatment_warehouse_id,
            catalog=treatment_catalog,
            mode="quiesce",
        )
        raise


def capture_current(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    payload: dict[str, object],
    base_url: str,
    bearer_token: str,
    expected_git_sha: str,
    expected_gateway_binding: str | None,
    expected_deployment_lease_id: str,
    genie_space_id: str,
    expected_app_resources: list[dict[str, object]],
    treatment_warehouse_id: str,
    treatment_catalog: str,
) -> None:
    expected_lakebase_instance = _expected_lakebase_instance()
    candidate = _validated_payload(
        payload,
        require_immutable_source=False,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    candidate_lease_id = _payload_deployment_lease_id(candidate)
    try:
        UUID(expected_deployment_lease_id)
    except ValueError as exc:
        raise RuntimeError("capture requires a valid deployment lease") from exc
    actual_sha, actual_binding, actual_lease_id = _health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
    )
    if (
        actual_sha != expected_git_sha
        or actual_binding != expected_gateway_binding
        or actual_lease_id != expected_deployment_lease_id
        or candidate_lease_id != expected_deployment_lease_id
    ):
        raise RuntimeError("cannot record an App deployment that failed its exact health contract")
    if _payload_gateway_binding(candidate) != expected_gateway_binding:
        raise RuntimeError("App deploy payload does not match its observed Gateway binding")
    assert_deployment_lease_held(
        workspace,
        app_name=app_name,
        lease_id=expected_deployment_lease_id,
        source_git_sha=expected_git_sha,
    )
    latest = _latest_succeeded(workspace, app_name=app_name)
    if _text(getattr(latest, "source_code_path", None)) != candidate["source_code_path"]:
        raise RuntimeError("latest succeeded App deployment is not the submitted source snapshot")
    if _active_deployment_id(workspace, app_name=app_name) != _text(
        getattr(latest, "deployment_id", None)
    ):
        raise RuntimeError("latest succeeded App deployment is not the exact active deployment")
    resource_proof = _payload_resource_proof(
        workspace,
        payload=candidate,
        genie_space_id=genie_space_id,
    )
    gateway_resources = {
        **dict(resource_proof.contract),
        "resource_digest": resource_proof.digest,
    }
    _stored_resource_proof(
        workspace,
        record={"gateway_resources": gateway_resources},
    )
    if _active_deployment_id(workspace, app_name=app_name) != _text(
        getattr(latest, "deployment_id", None)
    ):
        raise RuntimeError("App active deployment changed during last-good capture")
    app_client_id, app_scim_id = _app_identity(workspace, app_name=app_name)
    app_resources = app_resource_contract(workspace, app_name=app_name)
    reviewed_resources = validated_app_resource_contract(expected_app_resources)
    if app_resources != reviewed_resources:
        raise RuntimeError(
            "Databricks App resource bindings do not match the reviewed bundle manifest"
        )
    record = _record_from(
        app_name=app_name,
        deployment=latest,
        payload=candidate,
        git_sha=actual_sha,
        gateway_binding=actual_binding,
        gateway_resources=gateway_resources,
        app_resources=app_resources,
        app_service_principal_client_id=app_client_id,
        app_service_principal_scim_id=app_scim_id,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    expected_deployment_id = _text(getattr(latest, "deployment_id", None))
    treatment_activated = False
    try:
        # The candidate must be durable and independently readable while it is
        # still unable to write treatment state. A non-unwinding host failure
        # after the later grant can then leave only a proven candidate active.
        _converge_treatment_guard(
            workspace,
            app_name=app_name,
            warehouse_id=treatment_warehouse_id,
            catalog=treatment_catalog,
            mode="quiesce",
        )
        if _active_deployment_id(workspace, app_name=app_name) != expected_deployment_id:
            raise RuntimeError("App active deployment changed before last-good persistence")
        assert_deployment_lease_held(
            workspace,
            app_name=app_name,
            lease_id=expected_deployment_lease_id,
            source_git_sha=expected_git_sha,
        )
        _save_record(workspace, scope=scope, record=record)
        persisted_record = _load_record(
            workspace,
            app_name=app_name,
            scope=scope,
            expected_lakebase_instance=expected_lakebase_instance,
        )
        if persisted_record["deployment_id"] != expected_deployment_id:
            raise RuntimeError("App rollback contract readback names another deployment")
        if _active_deployment_id(workspace, app_name=app_name) != expected_deployment_id:
            raise RuntimeError("App active deployment changed during last-good persistence")
        _converge_treatment_guard(
            workspace,
            app_name=app_name,
            warehouse_id=treatment_warehouse_id,
            catalog=treatment_catalog,
            mode="runtime",
        )
        treatment_activated = True
        if _active_deployment_id(workspace, app_name=app_name) != expected_deployment_id:
            raise RuntimeError("App active deployment changed after last-good persistence")
        _assert_record_app_identity(workspace, app_name=app_name, record=persisted_record)
        _stored_resource_proof(workspace, record=persisted_record)
        post_sha, post_binding, post_lease_id = _health(
            workspace,
            app_name=app_name,
            base_url=base_url,
            bearer_token=bearer_token,
        )
        if (post_sha, post_binding, post_lease_id) != (
            expected_git_sha,
            expected_gateway_binding,
            expected_deployment_lease_id,
        ):
            raise RuntimeError("App health contract drifted after treatment activation")
        assert_deployment_lease_held(
            workspace,
            app_name=app_name,
            lease_id=expected_deployment_lease_id,
            source_git_sha=expected_git_sha,
        )
    except BaseException:
        if treatment_activated:
            _converge_treatment_guard(
                workspace,
                app_name=app_name,
                warehouse_id=treatment_warehouse_id,
                catalog=treatment_catalog,
                mode="quiesce",
            )
        raise


def restore_last_good(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    base_url: str,
    bearer_token: str,
    treatment_warehouse_id: str,
    treatment_catalog: str,
    revoke_endpoints: tuple[str, ...] = (),
    restore_treatment: bool = True,
) -> None:
    expected_lakebase_instance = _expected_lakebase_instance()
    _converge_treatment_guard(
        workspace,
        app_name=app_name,
        warehouse_id=treatment_warehouse_id,
        catalog=treatment_catalog,
        mode="quiesce",
    )
    try:
        record = _load_record(
            workspace,
            app_name=app_name,
            scope=scope,
            expected_lakebase_instance=expected_lakebase_instance,
        )
        _assert_record_app_identity(workspace, app_name=app_name, record=record)
        _stored_resource_proof(workspace, record=record)
        _converge_rollback_endpoint_acl(
            workspace,
            app_name=app_name,
            record=record,
            revoke_endpoints=revoke_endpoints,
        )
        _ensure_started(workspace, app_name=app_name)
        _active_deployment_id(workspace, app_name=app_name)
        restored = workspace.apps.deploy_and_wait(
            app_name,
            AppDeployment.from_dict(record["payload"]),
            timeout=DEPLOY_TIMEOUT,
        )
        if _deployment_state(restored) != "SUCCEEDED":
            raise RuntimeError("last-good App rollback deployment did not succeed")
        restored_id = _text(getattr(restored, "deployment_id", None))
        if _active_deployment_id(workspace, app_name=app_name) != restored_id:
            raise RuntimeError("restored App deployment is not the exact active deployment")
        _verify_health(
            workspace,
            app_name=app_name,
            base_url=base_url,
            bearer_token=bearer_token,
            git_sha=record["git_sha"],
            gateway_binding=record["gateway_binding_sha256"],
            deployment_lease_id=_payload_deployment_lease_id(record["payload"]),
        )
        _stored_resource_proof(workspace, record=record)
        latest = _latest_succeeded(workspace, app_name=app_name)
        if _text(getattr(latest, "deployment_id", None)) != restored_id:
            raise RuntimeError("restored App is not the active succeeded deployment")
        app_client_id, app_scim_id = _app_identity(workspace, app_name=app_name)
        refreshed = _record_from(
            app_name=app_name,
            deployment=latest,
            payload=record["payload"],
            git_sha=record["git_sha"],
            gateway_binding=record["gateway_binding_sha256"],
            gateway_resources=record["gateway_resources"],
            app_resources=record["app_resources"],
            app_service_principal_client_id=app_client_id,
            app_service_principal_scim_id=app_scim_id,
            expected_lakebase_instance=expected_lakebase_instance,
        )
        _save_record(workspace, scope=scope, record=refreshed)
        if restore_treatment:
            _converge_treatment_guard(
                workspace,
                app_name=app_name,
                warehouse_id=treatment_warehouse_id,
                catalog=treatment_catalog,
                mode="runtime",
            )
    except BaseException:
        _converge_treatment_guard(
            workspace,
            app_name=app_name,
            warehouse_id=treatment_warehouse_id,
            catalog=treatment_catalog,
            mode="quiesce",
        )
        raise


def _payload_file(path: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("App deployment payload file is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("App deployment payload file is not an object")
    return value


def _reviewed_resources_file(path: str) -> list[dict[str, object]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundle summary file is invalid") from exc
    return reviewed_app_resource_contract(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "capture", "restore"))
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--payload")
    parser.add_argument("--expected-git-sha")
    parser.add_argument("--expected-gateway-binding")
    parser.add_argument("--deployment-lease-id")
    parser.add_argument("--genie-space-id")
    parser.add_argument("--bundle-summary")
    parser.add_argument("--revoke-endpoint", action="append", default=[])
    parser.add_argument("--treatment-warehouse-id")
    parser.add_argument("--treatment-catalog", default="mip")
    parser.add_argument("--out-env", type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        parser.error(f"{args.token_env} is empty")
    workspace = WorkspaceClient()
    common = {
        "workspace": workspace,
        "app_name": args.app_name,
        "scope": args.scope,
        "base_url": args.base_url,
        "bearer_token": token,
    }
    if args.action == "ensure":
        if not args.treatment_warehouse_id:
            parser.error("--treatment-warehouse-id is required for ensure")
        endpoint = ensure_current(
            **common,
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
        )
        if args.out_env is not None:
            args.out_env.write_text(
                f"MIP_APP_ROLLBACK_GATEWAY_ENDPOINT={shlex.quote(endpoint)}\n",
                encoding="utf-8",
            )
    elif args.action == "restore":
        if not args.treatment_warehouse_id:
            parser.error("--treatment-warehouse-id is required for restore")
        restore_last_good(
            **common,
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
            revoke_endpoints=tuple(args.revoke_endpoint),
        )
    else:
        if not args.payload:
            parser.error("--payload is required for capture")
        payload = _payload_file(args.payload)
        if not args.expected_git_sha:
            parser.error("--expected-git-sha is required for capture")
        if not args.genie_space_id:
            parser.error("--genie-space-id is required for capture")
        if not args.deployment_lease_id:
            parser.error("--deployment-lease-id is required for capture")
        if not args.bundle_summary:
            parser.error("--bundle-summary is required for capture")
        if not args.treatment_warehouse_id:
            parser.error("--treatment-warehouse-id is required for capture")
        capture_current(
            **common,
            payload=payload,
            expected_git_sha=args.expected_git_sha,
            expected_gateway_binding=args.expected_gateway_binding,
            expected_deployment_lease_id=args.deployment_lease_id,
            genie_space_id=args.genie_space_id,
            expected_app_resources=_reviewed_resources_file(args.bundle_summary),
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
        )
    print(f"App last-good rollback contract {args.action}: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
