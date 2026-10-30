"""Re-pin the signed last-good record onto the deployment ``apps start`` created (M0-PR8).

A start does not resume blue's deployment: it snapshots the App's default
source into a new deployment (``app_start_deployment``). Every "active ==
record" check after a start would then fail, or worse, pass against a record
that no longer names what runs. ``repin_start_deployment`` closes that gap:

1. the record is the signed one, and names the expected blue deployment (or
   already names the start deployment as a re-pin of it: an idempotent rerun);
2. the start deployment is SUCCEEDED, active, the newest SUCCEEDED and
   nothing is pending;
3. ``prove_start_deployment``: blue's restore source, a snapshot whose full
   tree digest is the expected one (the window journal's, else blue's own
   snapshot's), and the record payload's exact env-name set;
4. authenticated health pinned to the start deployment, before any write;
5. under the held lease, a new record version with the same provenance (every
   field of the old record) naming the start deployment and its snapshot,
   plus ``predecessor_deployment_id``; read back exactly;
6. health again, against the re-pinned id.

The caller supplies health and the stored Gateway resource proof, so this
module never imports the rollback module that calls it.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from databricks.sdk.service.apps import AppDeployment
from tools.databricks.app_deployment_state import (
    active_deployment_id,
    deployment_state,
    latest_succeeded,
)
from tools.databricks.app_rollback_record_builder import build_app_rollback_record
from tools.databricks.app_rollback_record_contract import (
    _load_record,
    _text,
    save_versioned_record,
)
from tools.databricks.app_start_deployment import (
    env_names,
    prove_start_deployment,
    tree_digest,
)

PREDECESSOR_FIELD = "predecessor_deployment_id"
DEPLOY_TIMEOUT = timedelta(minutes=20)

HealthCheck = Callable[[dict[str, Any], str | None], None]
ResourceProof = Callable[[dict[str, Any]], object]


def repinned_record(
    record: dict[str, Any],
    *,
    deployment: object,
    expected_lakebase_instance: str,
) -> dict[str, Any]:
    """The same signed contract, naming ``deployment`` and its snapshot."""

    rebuilt = build_app_rollback_record(
        app_name=record["app_name"],
        deployment=deployment,
        payload=copy.deepcopy(record["payload"]),
        git_sha=record["git_sha"],
        gateway_binding=record["gateway_binding_sha256"],
        gateway_resources=record["gateway_resources"],
        app_resources=record["app_resources"],
        app_service_principal_client_id=record["app_service_principal_client_id"],
        app_service_principal_scim_id=record["app_service_principal_scim_id"],
        expected_lakebase_instance=expected_lakebase_instance,
        pending_proxy_credential_retirement_ids=tuple(
            record["pending_proxy_credential_retirement_ids"]
        ),
        record_version=int(record["version"]),
    )
    rebuilt[PREDECESSOR_FIELD] = record["deployment_id"]
    return rebuilt


def _assert_app_identity(workspace: Any, *, app_name: str, record: dict[str, Any]) -> None:
    app = workspace.apps.get(app_name)
    actual = (
        _text(getattr(app, "service_principal_client_id", None)),
        _text(getattr(app, "service_principal_id", None)),
    )
    if actual != (
        record["app_service_principal_client_id"],
        record["app_service_principal_scim_id"],
    ):
        raise RuntimeError("App service-principal identity drifted from the signed record")


def _assert_start_is_current(workspace: Any, *, app_name: str, deployment_id: str) -> None:
    if active_deployment_id(workspace, app_name=app_name) != deployment_id:
        raise RuntimeError("the start deployment is not the App's exact active deployment")
    latest = latest_succeeded(workspace, app_name=app_name)
    if _text(getattr(latest, "deployment_id", None)) != deployment_id:
        raise RuntimeError("the start deployment is not the newest succeeded deployment")


def repin_start_deployment(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    deployment_id: str,
    expected_rollback_deployment_id: str,
    expected_lakebase_instance: str,
    assert_lease_held: Callable[[], None],
    verify_health: HealthCheck,
    resource_proof: ResourceProof,
    expected_tree_sha256: str | None = None,
    digest: Callable[[Any, str], str] = tree_digest,
) -> dict[str, Any]:
    """Prove the start deployment is blue, then sign a record naming it; return that record."""

    start_id = deployment_id.strip()
    expected_blue = expected_rollback_deployment_id.strip()
    if not start_id or not expected_blue:
        raise RuntimeError("re-pin requires the start deployment id and the expected blue id")
    record = _load_record(
        workspace,
        app_name=app_name,
        scope=scope,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    if record["deployment_id"] == start_id and record.get(PREDECESSOR_FIELD) == expected_blue:
        # A rerun after a crash between the save and the window close.
        _assert_app_identity(workspace, app_name=app_name, record=record)
        _assert_start_is_current(workspace, app_name=app_name, deployment_id=start_id)
        verify_health(record, start_id)
        return record
    if record["deployment_id"] != expected_blue:
        raise RuntimeError("the signed record no longer names the expected blue deployment")
    if start_id == expected_blue:
        raise RuntimeError("the start deployment is blue's own deployment; nothing to re-pin")
    _assert_app_identity(workspace, app_name=app_name, record=record)
    blue = workspace.apps.get_deployment(app_name, expected_blue)
    restore_source = _text(getattr(blue, "source_code_path", None))
    expected_tree = expected_tree_sha256 or digest(workspace, record["payload"]["source_code_path"])
    started = workspace.apps.get_deployment(app_name, start_id)
    proof = prove_start_deployment(
        workspace,
        app_name=app_name,
        deployment=started,
        restore_source=restore_source,
        expected_tree_sha256=expected_tree,
        expected_env_names=env_names(record["payload"]),
        digest=digest,
    )
    _assert_start_is_current(workspace, app_name=app_name, deployment_id=proof.deployment_id)
    resource_proof(record)
    verify_health(record, proof.deployment_id)
    rebuilt = repinned_record(
        record,
        deployment=started,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    _assert_start_is_current(workspace, app_name=app_name, deployment_id=proof.deployment_id)
    assert_lease_held()
    save_versioned_record(workspace, scope=scope, record=rebuilt)
    persisted = _load_record(
        workspace,
        app_name=app_name,
        scope=scope,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    if (
        persisted["deployment_id"] != proof.deployment_id
        or persisted.get(PREDECESSOR_FIELD) != expected_blue
        or persisted["payload_sha256"] != rebuilt["payload_sha256"]
    ):
        raise RuntimeError("the re-pinned record did not persist exactly")
    verify_health(persisted, proof.deployment_id)
    return persisted


def redeploy_signed_blue(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    record: dict[str, Any],
    expected_lakebase_instance: str,
    verify_health: HealthCheck,
    resource_proof: ResourceProof,
    timeout: timedelta = DEPLOY_TIMEOUT,
) -> None:
    """Tier b: redeploy the signed record's own snapshot and sign the new deployment."""

    active_deployment_id(workspace, app_name=app_name)
    restored = workspace.apps.deploy_and_wait(
        app_name,
        AppDeployment.from_dict(record["payload"]),
        timeout=timeout,
    )
    if deployment_state(restored) != "SUCCEEDED":
        raise RuntimeError("last-good App rollback deployment did not succeed")
    restored_id = _text(getattr(restored, "deployment_id", None))
    if active_deployment_id(workspace, app_name=app_name) != restored_id:
        raise RuntimeError("restored App deployment is not the exact active deployment")
    verify_health(record, None)
    resource_proof(record)
    latest = latest_succeeded(workspace, app_name=app_name)
    if _text(getattr(latest, "deployment_id", None)) != restored_id:
        raise RuntimeError("restored App is not the active succeeded deployment")
    app = workspace.apps.get(app_name)
    app_client_id = _text(getattr(app, "service_principal_client_id", None))
    app_scim_id = _text(getattr(app, "service_principal_id", None))
    refreshed = build_app_rollback_record(
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
    save_versioned_record(workspace, scope=scope, record=refreshed)
