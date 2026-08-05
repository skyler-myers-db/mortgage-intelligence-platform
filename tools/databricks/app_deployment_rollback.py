#!/usr/bin/env python3
"""Persist, verify, and restore the exact last-good Databricks App deployment."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from backend.agents.reviewed_uc_function_contract import (
    assert_reviewed_function_set,
    authenticated_reviewed_function_owner,
)
from databricks.sdk.service.apps import AppDeployment
from tools.databricks.app_deployment_health import health as _health
from tools.databricks.app_deployment_lease import assert_held as assert_deployment_lease_held
from tools.databricks.app_deployment_state import (
    active_deployment_id as _active_deployment_id,
)
from tools.databricks.app_deployment_state import (
    deployment_state as _deployment_state,
)
from tools.databricks.app_deployment_state import (
    latest_succeeded as _latest_succeeded,
)
from tools.databricks.app_gateway_access_mode import (
    preserve_blue_and_revoke_managed_candidates,
)
from tools.databricks.app_health_contract import active_app_deployment_pin
from tools.databricks.app_proxy_retirement_journal import (
    capture_proxy_retirement_ids as _capture_proxy_retirement_ids,
)
from tools.databricks.app_rollback_gateway_binding import (
    payload_gateway_binding as _payload_gateway_binding,
)
from tools.databricks.app_rollback_gateway_proof import (
    resolve_stored_gateway_resource_proof,
)
from tools.databricks.app_rollback_record_builder import (
    build_app_rollback_record as _record_from,
)
from tools.databricks.app_rollback_record_contract import (
    LEGACY_RECORD_VERSION,
    RECORD_VERSION,
    _delete_legacy_record,
    _load_record,
    _save_legacy_record,
    _save_record,
    _text,
    _validated_payload,
)
from tools.databricks.app_rollback_record_contract import (
    _payload_digest as _payload_digest,  # noqa: F401 - compatibility for callers/tests
)
from tools.databricks.app_rollback_record_contract import (
    _record_key as _contract_record_key,
)
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract,
    restore_signed_app_resource_contract,
    validated_app_resource_contract,
)
from tools.databricks.app_rollback_signed_contract import SignedLastGoodAppContract
from tools.databricks.converge_campaign_treatment_access import (
    Mode,
    converge_campaign_treatment_access,
)
from tools.databricks.deployment_lease_authority import held_assertion
from tools.databricks.export_gateway_runtime_contract import (
    ExactGatewayRuntimeProof,
    resolve_exact_resource_proof,
)
from tools.databricks.gateway_legacy_rollback import (
    assert_live_legacy_gateway_resources,
)
from tools.databricks.lakebase_instance_contract import resolve_lakebase_instance_aliases

DEFAULT_SCOPE = "mip-app-rollback"
DEPLOY_TIMEOUT = timedelta(minutes=20)


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
    pin = active_app_deployment_pin(
        workspace, app_name=app_name, expected_lease_id=deployment_lease_id
    )
    actual = _health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
        expected_pin=pin,
    )
    if actual != (git_sha, gateway_binding, deployment_lease_id):
        raise RuntimeError("App health does not match the exact last-good rollback contract")


def _expected_lakebase_instance() -> str:
    try:
        return resolve_lakebase_instance_aliases(os.environ, require_both=True)
    except ValueError as exc:
        raise RuntimeError(f"rollback Lakebase deployment control is invalid: {exc}") from exc


def _record_key(app_name: str) -> str:
    """Retain the rollback module's key helper for compatibility."""
    return _contract_record_key(app_name)


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
    require_resources: bool = True,
) -> None:
    client_id, scim_id = _app_identity(workspace, app_name=app_name)
    if (
        client_id != record["app_service_principal_client_id"]
        or scim_id != record["app_service_principal_scim_id"]
    ):
        raise RuntimeError(
            "App service-principal identity drifted from the signed rollback contract"
        )
    if require_resources and (
        app_resource_contract(workspace, app_name=app_name) != record["app_resources"]
    ):
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
        "MIP_REVIEWED_FUNCTION_OWNER",
        "MIP_AGENT_PROXY_CLIENT_ID",
        "MIP_AGENT_PROXY_CREDENTIAL_ID",
        "MIP_AGENT_PROXY_SECRET_REFERENCE",
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
        reviewed_function_owner=env["MIP_REVIEWED_FUNCTION_OWNER"],
        proxy_caller_application_id=env["MIP_AGENT_PROXY_CLIENT_ID"],
        proxy_caller_credential_id=env["MIP_AGENT_PROXY_CREDENTIAL_ID"],
        proxy_caller_secret_reference=env["MIP_AGENT_PROXY_SECRET_REFERENCE"],
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
    candidate_reviewed_function_owner: str | None = None,
) -> ExactGatewayRuntimeProof:
    return resolve_stored_gateway_resource_proof(
        workspace,
        record=record,
        candidate_reviewed_function_owner=candidate_reviewed_function_owner,
        authenticate_owner=authenticated_reviewed_function_owner,
        assert_function_set=assert_reviewed_function_set,
        assert_legacy_resources=assert_live_legacy_gateway_resources,
        resolve_exact_resource_proof=resolve_exact_resource_proof,
    )


def _rollback_gateway_endpoint(record: dict[str, Any]) -> str:
    env = _env_map(record["payload"])
    endpoint = (env.get("MIP_AGENT_SERVING_ENDPOINT") or "").strip()
    metadata_endpoint = (env.get("MIP_AI_GATEWAY_ENDPOINT") or "").strip()
    if not endpoint or (metadata_endpoint and metadata_endpoint != endpoint):
        raise RuntimeError("signed App rollback payload has no Gateway endpoint")
    return endpoint


def verified_signed_last_good_contract(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
) -> SignedLastGoodAppContract:
    """Verify the signed binding while allowing restore-owned App resource repair."""

    record = _load_record(
        workspace,
        app_name=app_name,
        scope=scope,
        expected_lakebase_instance=_expected_lakebase_instance(),
    )
    _assert_record_app_identity(
        workspace, app_name=app_name, record=record, require_resources=False
    )
    _stored_resource_proof(workspace, record=record)
    is_exact_proxy = record["version"] == RECORD_VERSION
    active_proxy_credential_id = (
        str(record["gateway_resources"]["proxy_caller_credential_id"]) if is_exact_proxy else None
    )
    resources = record["gateway_resources"]
    return SignedLastGoodAppContract(
        record_version=int(record["version"]),
        proxy_rollback_mode=("exact-proxy" if is_exact_proxy else "legacy-proxyless"),
        deployment_id=record["deployment_id"],
        deployment_lease_id=_payload_deployment_lease_id(record["payload"]),
        git_sha=record["git_sha"],
        gateway_binding_sha256=record["gateway_binding_sha256"],
        gateway_endpoint=_rollback_gateway_endpoint(record),
        gateway_endpoint_id=str(resources["gateway_endpoint_id"]).strip(),
        gateway_endpoint_creator=str(resources["gateway_endpoint_creator"]).strip(),
        gateway_inference_table_family=str(resources["gateway_inference_table_family"]).strip(),
        supervisor_id=str(resources["supervisor_id"]).strip(),
        supervisor_creator=str(resources["supervisor_creator"]).strip(),
        supervisor_endpoint=str(resources["supervisor_endpoint"]).strip(),
        supervisor_endpoint_id=str(resources["supervisor_endpoint_id"]).strip(),
        runtime_application_id=str(resources["runtime_application_id"]).strip(),
        genie_space_id=str(resources["genie_space_id"]).strip(),
        proxy_application_id=(
            str(resources.get("proxy_caller_application_id") or "").strip() or None
        ),
        active_proxy_credential_id=active_proxy_credential_id,
        pending_proxy_credential_retirement_ids=tuple(
            record["pending_proxy_credential_retirement_ids"]
        ),
    )


def assert_proxy_credential_retirement(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    proxy_application_id: str,
    retained_credential_id: str,
    retired_credential_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Bind a requested cleanup to the exact active signed retirement journal."""
    expected_lakebase_instance = _expected_lakebase_instance()
    record = _load_record(
        workspace,
        app_name=app_name,
        scope=scope,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    if record["version"] != RECORD_VERSION:
        raise RuntimeError("proxy-credential retirement requires a current rollback record")
    resources = record["gateway_resources"]
    if proxy_application_id.strip() != resources["proxy_caller_application_id"]:
        raise RuntimeError("proxy application does not match the signed App record")
    active_credential_id = resources["proxy_caller_credential_id"]
    if retained_credential_id.strip() != active_credential_id:
        raise RuntimeError("retained proxy credential does not match the signed App record")
    expected_retired = tuple(
        sorted(
            {
                value.strip()
                for value in retired_credential_ids
                if value.strip() and value.strip() != active_credential_id
            }
        )
    )
    pending = tuple(record["pending_proxy_credential_retirement_ids"])
    if expected_retired != pending:
        raise RuntimeError("proxy-credential cleanup does not match the signed retirement journal")
    _assert_record_app_identity(workspace, app_name=app_name, record=record)
    _stored_resource_proof(workspace, record=record)
    if _active_deployment_id(workspace, app_name=app_name) != record["deployment_id"]:
        raise RuntimeError("active App changed before proxy-retirement journal completion")
    return record


def complete_proxy_credential_retirement(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    proxy_application_id: str,
    retained_credential_id: str,
    retired_credential_ids: tuple[str, ...],
    assert_provider_cleanup: Callable[[], None],
) -> None:
    """Clear the signed retirement journal after exact provider cleanup."""

    record = assert_proxy_credential_retirement(
        workspace,
        app_name=app_name,
        scope=scope,
        proxy_application_id=proxy_application_id,
        retained_credential_id=retained_credential_id,
        retired_credential_ids=retired_credential_ids,
    )
    pending = tuple(record["pending_proxy_credential_retirement_ids"])
    assert_provider_cleanup()
    if not pending:
        return
    record["pending_proxy_credential_retirement_ids"] = ()
    _save_record(workspace, scope=scope, record=record)
    persisted = _load_record(
        workspace,
        app_name=app_name,
        scope=scope,
        expected_lakebase_instance=_expected_lakebase_instance(),
    )
    if (
        persisted["deployment_id"] != record["deployment_id"]
        or persisted["gateway_resources"] != record["gateway_resources"]
        or persisted["pending_proxy_credential_retirement_ids"]
    ):
        raise RuntimeError("proxy-retirement journal completion did not converge exactly")


def _converge_rollback_endpoint_acl(
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    revoke_endpoints: tuple[str, ...] = (),
) -> None:
    principal, principal_id = _app_identity(workspace, app_name=app_name)
    assert_single_writer = held_assertion(
        workspace,
        app_name=app_name,
        lease_id=deployment_lease_id,
        source_git_sha=deployment_source_git_sha,
        operation="signed App rollback Gateway ACL mutation",
    )

    preserve_blue_and_revoke_managed_candidates(
        workspace,
        app_name=app_name,
        blue_endpoint=_rollback_gateway_endpoint(record),
        app_client_id=principal,
        app_scim_id=principal_id,
        candidate_endpoints=revoke_endpoints,
        assert_before_mutation=assert_single_writer,
    )


def _save_versioned_record(
    workspace: Any,
    *,
    scope: str,
    record: dict[str, Any],
) -> None:
    if record.get("version") == LEGACY_RECORD_VERSION:
        _save_legacy_record(workspace, scope=scope, record=record)
    else:
        _save_record(workspace, scope=scope, record=record)


def ensure_current(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    base_url: str,
    bearer_token: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
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
        # A killed deploy can leave reviewed candidate resources applied while
        # the still-active source and signed rollback record remain blue. Trust
        # only the authenticated record and immutable App identity, restore its
        # exact resource contract, then require the complete identity/resource
        # binding before any source, endpoint, or health verification.
        _assert_record_app_identity(
            workspace,
            app_name=app_name,
            record=record,
            require_resources=False,
        )
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
                deployment_lease_id=deployment_lease_id,
                deployment_source_git_sha=deployment_source_git_sha,
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
        restore_signed_app_resource_contract(
            workspace,
            app_name=app_name,
            resources=record["app_resources"],
        )
        _assert_record_app_identity(workspace, app_name=app_name, record=record)
        _stored_resource_proof(workspace, record=record)
        if _active_deployment_id(workspace, app_name=app_name) != active_id:
            raise RuntimeError("App active deployment changed during signed resource repair")
        _ensure_started(workspace, app_name=app_name)
        _converge_rollback_endpoint_acl(
            workspace,
            app_name=app_name,
            record=record,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
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
            _save_versioned_record(workspace, scope=scope, record=record)
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
    candidate_pin = active_app_deployment_pin(
        workspace,
        app_name=app_name,
        expected_lease_id=expected_deployment_lease_id,
    )
    actual_sha, actual_binding, actual_lease_id = _health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
        expected_pin=candidate_pin,
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
    if candidate_pin.deployment_id != _text(getattr(latest, "deployment_id", None)):
        raise RuntimeError("health proof did not pin the latest succeeded App deployment")
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
        candidate_reviewed_function_owner=_env_map(candidate)["MIP_REVIEWED_FUNCTION_OWNER"],
    )
    if _active_deployment_id(workspace, app_name=app_name) != _text(
        getattr(latest, "deployment_id", None)
    ):
        raise RuntimeError("App active deployment changed during last-good capture")
    try:
        previous_record = _load_record(
            workspace,
            app_name=app_name,
            scope=scope,
            expected_lakebase_instance=expected_lakebase_instance,
        )
    except RuntimeError as exc:
        if not str(exc).startswith("no server-owned last-good App rollback contract exists"):
            raise
        previous_record = None
    pending_proxy_credential_retirement_ids = _capture_proxy_retirement_ids(
        previous_record,
        candidate_gateway_resources=gateway_resources,
    )
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
        pending_proxy_credential_retirement_ids=(pending_proxy_credential_retirement_ids),
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
        _delete_legacy_record(
            workspace,
            scope=scope,
            app_name=app_name,
        )
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
            expected_pin=candidate_pin,
        )
        if (post_sha, post_binding, post_lease_id) != (
            expected_git_sha,
            expected_gateway_binding,
            expected_deployment_lease_id,
        ):
            raise RuntimeError("App health contract drifted after treatment activation")
        if _active_deployment_id(workspace, app_name=app_name) != expected_deployment_id:
            raise RuntimeError("App active deployment changed during post-treatment proof")
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
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    treatment_warehouse_id: str,
    treatment_catalog: str,
    revoke_endpoints: tuple[str, ...] = (),
    restore_treatment: bool = True,
    expected_rollback_deployment_id: str | None = None,
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
        if (
            expected_rollback_deployment_id is not None
            and record["deployment_id"] != expected_rollback_deployment_id
        ):
            raise RuntimeError("signed App rollback contract changed after identity binding")
        _assert_record_app_identity(
            workspace,
            app_name=app_name,
            record=record,
            require_resources=False,
        )
        restore_signed_app_resource_contract(
            workspace,
            app_name=app_name,
            resources=record["app_resources"],
        )
        _assert_record_app_identity(workspace, app_name=app_name, record=record)
        _stored_resource_proof(workspace, record=record)
        _converge_rollback_endpoint_acl(
            workspace,
            app_name=app_name,
            record=record,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
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
            pending_proxy_credential_retirement_ids=tuple(
                record["pending_proxy_credential_retirement_ids"]
            ),
            record_version=int(record["version"]),
        )
        _save_versioned_record(workspace, scope=scope, record=refreshed)
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


def main(argv: list[str] | None = None) -> int:
    from tools.databricks.app_deployment_rollback_cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
