"""Authoritative admin lifecycle audit for active and archived Gateway models."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.app_gateway_access_mode import (
    classify_cutover_journal_against_signed_blue,
    json_pin_from_env,
)
from tools.databricks.app_rollback_record_contract import _load_record
from tools.databricks.cutover_journal_store import read_signed_cutover_journal
from tools.databricks.gateway_model_archival import (
    GatewayModelArchiveScope,
    _assert_experiment_identity,
    _assert_frozen_versions,
    _field,
    _scope_record,
    _table_evidence,
    _table_family,
)
from tools.databricks.gateway_model_archival_inventory import (
    exact_experiment_acl,
    inventory_gateway_model_versions,
    inventory_gateway_serving,
    inventory_gateway_tables,
)
from tools.databricks.gateway_model_archival_protection import (
    _allocation,
    _endpoint_contracts,
    _registration_recovery_contracts,
    discover_protected_allocation_contracts,
    zero_effective_access_evidence,
)
from tools.databricks.gateway_model_lifecycle_proof import (
    GatewayModelLifecycleProof,
    GatewayModelLifecycleState,
    _issue_gateway_model_lifecycle_proof,
)
from tools.databricks.gateway_model_retirement_record import (
    archived_head_path,
    canonical_json,
    load_retirement_record,
    record_sha256,
    stage_path,
    verify_retirement_record,
)


def _assert_absent_tables(workspace: Any, names: list[str]) -> None:
    for full_name in names:
        try:
            workspace.tables.get(full_name, include_browse=True)
        except (NotFound, ResourceDoesNotExist):
            continue
        raise RuntimeError("archived Gateway expected-absent table appeared")


def authenticate_gateway_inventory_principal(
    workspace: Any,
    *,
    expected_inventory_principal: str,
    expected_archive_owner: str,
) -> tuple[str, str]:
    """Authenticate the exact admin caller and archive ownership boundary."""

    inventory_principal = expected_inventory_principal.strip()
    caller = workspace.current_user.me()
    caller_principals = {
        _field(caller, "user_name"),
        _field(caller, "application_id"),
    } - {""}
    metastore_id = _field(workspace.metastores.current(), "metastore_id")
    if (
        not inventory_principal
        or inventory_principal not in caller_principals
        or expected_archive_owner.strip() != inventory_principal
        or not metastore_id
    ):
        raise RuntimeError("Gateway lifecycle admin inventory identity is not exact")
    return inventory_principal, metastore_id


def assert_completed_gateway_archive(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    scope: GatewayModelArchiveScope,
    completion: Mapping[str, Any],
    resolve_delta_version: Callable[[str], str],
    allow_authenticated_cutover: bool = False,
) -> dict[str, Any]:
    """Re-prove one signed completed archive against current authoritative state."""

    exact = verify_retirement_record(completion)
    if exact.get("phase") != "completed":
        raise RuntimeError("Gateway archive head is not a completion record")
    live_scope = _scope_record(workspace, scope)
    for field in set(live_scope) - {"lease_id", "source_git_sha"}:
        if exact.get(field) != live_scope[field]:
            raise RuntimeError("Gateway archive head escaped the current audit scope")
    if exact["archive_owner"] != scope.archive_owner:
        raise RuntimeError("Gateway archive owner differs from signed retirement authority")
    stage = load_retirement_record(
        workspace,
        stage_path(
            str(exact["app_name"]),
            str(exact["model_name"]),
            str(exact["lease_id"]),
        ),
    )
    if (
        stage is None
        or stage.get("phase") != "staged"
        or record_sha256(stage) != exact["stage_record_sha256"]
        or stage["archive_owner"] != exact["archive_owner"]
    ):
        raise RuntimeError("Gateway archive completion lacks its exact signed stage")
    versions_sha256 = _assert_frozen_versions(
        model_registry,
        tracking_client,
        stage=stage,
    )
    if versions_sha256 != exact["versions_sha256"]:
        raise RuntimeError("archived Gateway version evidence drifted")
    model = workspace.registered_models.get(str(exact["model_name"]))
    if _field(model, "owner") != exact["archive_owner"]:
        raise RuntimeError("archived Gateway model owner drifted")
    tables = [
        _table_evidence(workspace, resolve_delta_version, expected)
        for expected in exact["inference_tables"]
    ]
    if tables != exact["inference_tables"] or any(
        table["owner"] != exact["archive_owner"] for table in tables
    ):
        raise RuntimeError("archived Gateway table identity, contents, or owner drifted")
    _assert_absent_tables(
        workspace,
        list(exact["expected_absent_inference_tables"]),
    )
    experiment = _assert_experiment_identity(tracking_client, stage=stage)
    if (
        experiment["name"] != exact["experiment_archive_name"]
        or experiment["artifact_location"] != exact["experiment_artifact_location"]
        or experiment["lifecycle_state"] != exact["experiment_lifecycle_state"]
        or experiment["owner"] != exact["experiment_owner"]
        or experiment["tags"] != exact["experiment_tags"]
    ):
        raise RuntimeError("archived Gateway experiment drifted")
    acl = exact_experiment_acl(
        workspace,
        experiment_id=str(exact["experiment_id"]),
    )
    if list(acl) != exact["experiment_acl"]:
        raise RuntimeError("archived Gateway experiment ACL drifted")
    _inventory, references = inventory_gateway_serving(
        workspace,
        model_name=str(exact["model_name"]),
        inference_table_family=_table_family(scope, str(exact["model_name"])),
    )
    if references:
        raise RuntimeError("archived Gateway allocation regained a serving reference")
    if allow_authenticated_cutover:
        protected_models = _active_contracts(
            workspace,
            model_registry,
            tracking_client,
            scope=scope,
            model_family=scope.model_family,
        )
        target_protected = exact["model_name"] in protected_models
    else:
        protected = discover_protected_allocation_contracts(
            workspace,
            model_registry,
            tracking_client,
            app_name=scope.app_name,
            runtime_application_id=scope.runtime_application_id,
            rollback_scope=scope.rollback_scope,
            expected_lakebase_instance=scope.expected_lakebase_instance,
            model_family=scope.model_family,
            experiment_base=scope.experiment_base,
            catalog=scope.catalog,
            inference_schema=scope.inference_schema,
            inference_table_prefix=scope.inference_table_prefix,
        )
        target_protected = any(
            item.get("gateway_model_name") == exact["model_name"]
            for item in protected
        )
    if target_protected:
        raise RuntimeError("archived Gateway allocation became release-protected")
    zero_effective_access_evidence(
        workspace,
        experiment_acl=acl,
        model_name=str(exact["model_name"]),
        table_names=[str(item["full_name"]) for item in tables],
        runtime_application_id=scope.runtime_application_id,
        app_application_id=scope.app_application_id,
        proxy_application_id=scope.proxy_application_id,
        verifier_application_id=scope.verifier_application_id,
    )
    return exact


def _active_contracts(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    scope: GatewayModelArchiveScope,
    model_family: str,
) -> dict[str, list[dict[str, Any]]]:
    protected = _endpoint_contracts(workspace)
    protected.extend(
        _registration_recovery_contracts(
            workspace,
            model_registry,
            tracking_client,
            runtime_application_id=scope.runtime_application_id,
            model_family=model_family,
            experiment_base=scope.experiment_base,
            catalog=scope.catalog,
            inference_schema=scope.inference_schema,
            inference_table_prefix=scope.inference_table_prefix,
        )
    )
    try:
        rollback = _load_record(
            workspace,
            app_name=scope.app_name,
            scope=scope.rollback_scope,
            expected_lakebase_instance=scope.expected_lakebase_instance,
        )
    except RuntimeError as exc:
        if "no server-owned last-good App rollback contract exists" not in str(exc):
            raise
    else:
        resources = rollback.get("gateway_resources")
        if not isinstance(resources, Mapping):
            raise RuntimeError("Gateway lifecycle rollback resources are invalid")
        protected.append(
            _allocation(
                "rollback",
                rollback,
                gateway_model_name=str(resources.get("gateway_model_name") or ""),
            )
        )
    cutover = read_signed_cutover_journal(
        workspace,
        runtime_application_id=scope.runtime_application_id,
    )
    if cutover is not None:
        signed_blue_gateway = json_pin_from_env(
            "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON"
        )
        signed_blue_supervisor = json_pin_from_env(
            "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON"
        )
        if signed_blue_gateway is None or signed_blue_supervisor is None:
            raise RuntimeError(
                "Gateway lifecycle cutover journal lacks signed-blue authority"
            )
        journal_gateway = (
            {
                "name": cutover.get("old_gateway_endpoint"),
                "endpoint_id": cutover.get("old_gateway_endpoint_id"),
                "creator": cutover.get("old_gateway_creator"),
            }
            if cutover.get("old_gateway_endpoint")
            else None
        )
        journal_supervisor = (
            {
                "supervisor_id": cutover.get("old_id"),
                "endpoint": cutover.get("old_endpoint"),
                "endpoint_id": cutover.get("old_endpoint_id"),
                "creator": cutover.get("old_creator"),
            }
            if cutover.get("old_id")
            else None
        )
        relation = classify_cutover_journal_against_signed_blue(
            journal_gateway_pin=journal_gateway,
            journal_supervisor_pin=journal_supervisor,
            signed_blue_gateway_pin=signed_blue_gateway,
            signed_blue_supervisor_pin=signed_blue_supervisor,
        )
        if relation != "current":
            raise RuntimeError(
                "Gateway lifecycle cutover journal is not the current signed-blue tuple"
            )
        if (
            journal_gateway is not None
            and {
                key: str(value or "").strip()
                for key, value in journal_gateway.items()
            }
            != {
                key: str(signed_blue_gateway.get(key) or "").strip()
                for key in ("name", "endpoint_id", "creator")
            }
        ) or (
            journal_supervisor is not None
            and {
                key: str(value or "").strip()
                for key, value in journal_supervisor.items()
            }
            != {
                key: str(signed_blue_supervisor.get(key) or "").strip()
                for key in ("supervisor_id", "endpoint", "endpoint_id", "creator")
            }
        ):
            raise RuntimeError(
                "Gateway lifecycle cutover journal differs from signed-blue authority"
            )
        gateway_name = str(cutover.get("old_gateway_endpoint") or "").strip()
        gateway_id = str(cutover.get("old_gateway_endpoint_id") or "").strip()
        gateway_creator = str(cutover.get("old_gateway_creator") or "").strip()
        matches = [
            allocation
            for allocation in protected
            if allocation["kind"].startswith("endpoint-current-")
            and (
                str(allocation["contract"].get("gateway_endpoint") or ""),
                str(allocation["contract"].get("gateway_endpoint_id") or ""),
                str(allocation["contract"].get("gateway_endpoint_creator") or ""),
            )
            == (gateway_name, gateway_id, gateway_creator)
        ]
        if gateway_name and len(matches) != 1:
            raise RuntimeError(
                "Gateway lifecycle cutover journal lacks one exact signed model contract"
            )
        if matches:
            protected.append(
                _allocation(
                    "cutover",
                    cutover,
                    gateway_model_name=str(matches[0]["gateway_model_name"]),
                )
            )
    pattern = re.compile(rf"{re.escape(model_family)}_[0-9a-f]{{12}}\Z")
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for allocation in protected:
        model_name = str(allocation.get("gateway_model_name") or "")
        if pattern.fullmatch(model_name) is None:
            continue
        by_model[model_name].append(allocation)
    for contracts in by_model.values():
        contracts.sort(
            key=lambda item: (str(item["kind"]), str(item["contract_sha256"]))
        )
    return dict(by_model)


def audit_gateway_model_lifecycle(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    scope: GatewayModelArchiveScope,
    resolve_delta_version: Callable[[str], str],
    expected_inventory_principal: str,
    expected_candidate_model: str,
) -> GatewayModelLifecycleProof:
    """Issue one opaque proof only after classifying every exact family model."""

    inventory_principal, metastore_id = authenticate_gateway_inventory_principal(
        workspace,
        expected_inventory_principal=expected_inventory_principal,
        expected_archive_owner=scope.archive_owner,
    )
    pattern = re.compile(rf"{re.escape(scope.model_family)}_[0-9a-f]{{12}}\Z")
    candidate_model = expected_candidate_model.strip()
    if pattern.fullmatch(candidate_model) is None:
        raise RuntimeError("Gateway lifecycle expected candidate is invalid")
    models: dict[str, Any] = {}
    for model in workspace.registered_models.list(include_browse=True):
        full_name = _field(model, "full_name")
        if pattern.fullmatch(full_name) is None:
            continue
        if full_name in models:
            raise RuntimeError("Gateway lifecycle model inventory contains duplicates")
        models[full_name] = model
    if not models:
        raise RuntimeError("Gateway lifecycle model inventory is empty")
    active_contracts = _active_contracts(
        workspace,
        model_registry,
        tracking_client,
        scope=scope,
        model_family=scope.model_family,
    )
    candidate_contracts = [
        contract
        for contract in active_contracts.get(candidate_model, [])
        if str(contract.get("kind") or "").startswith("endpoint-current-")
    ]
    if candidate_model not in models or len(candidate_contracts) != 1:
        raise RuntimeError(
            "Gateway lifecycle candidate lacks one exact current endpoint contract"
        )
    states: list[GatewayModelLifecycleState] = []
    for model_name in sorted(models):
        owner = _field(models[model_name], "owner")
        contracts = active_contracts.get(model_name)
        if contracts is not None:
            if owner != scope.runtime_application_id:
                raise RuntimeError("active Gateway allocation is not runtime-owned")
            versions, versions_sha256, _experiment_id = (
                inventory_gateway_model_versions(
                    model_registry,
                    tracking_client,
                    model_name=model_name,
                    runtime_application_id=scope.runtime_application_id,
                    model_family=scope.model_family,
                    experiment_base=scope.experiment_base,
                    catalog=scope.catalog,
                    inference_schema=scope.inference_schema,
                    inference_table_prefix=scope.inference_table_prefix,
                )
            )
            if model_name == candidate_model and (
                len(versions) != 1 or versions[0]["attestation_epoch"] != "current"
            ):
                raise RuntimeError("Gateway candidate does not use the current model epoch")
            tables, _absent = inventory_gateway_tables(
                workspace,
                catalog=scope.catalog,
                inference_schema=scope.inference_schema,
                inference_table_prefix=scope.inference_table_prefix,
                model_name=model_name,
                expected_owner=scope.runtime_application_id,
                delta_version_resolver=resolve_delta_version,
            )
            states.append(
                GatewayModelLifecycleState(
                    model_name=model_name,
                    owner=owner,
                    disposition="active",
                    versions_sha256=versions_sha256,
                    inference_tables=tuple(
                        sorted(str(table["full_name"]) for table in tables)
                    ),
                    active_contract_json=canonical_json(contracts),
                    retirement_record_sha256="",
                )
            )
            continue
        head = load_retirement_record(
            workspace,
            archived_head_path(scope.app_name, model_name),
        )
        if head is None:
            raise RuntimeError("historical Gateway model has no signed archive head")
        exact = assert_completed_gateway_archive(
            workspace,
            model_registry,
            tracking_client,
            scope=scope,
            completion=head,
            resolve_delta_version=resolve_delta_version,
            allow_authenticated_cutover=True,
        )
        if owner != exact["archive_owner"]:
            raise RuntimeError("archived Gateway owner differs from retirement record")
        states.append(
            GatewayModelLifecycleState(
                model_name=model_name,
                owner=owner,
                disposition="archived",
                versions_sha256=str(exact["versions_sha256"]),
                inference_tables=tuple(
                    sorted(
                        str(table["full_name"])
                        for table in exact["inference_tables"]
                    )
                ),
                active_contract_json="",
                retirement_record_sha256=record_sha256(exact),
            )
        )
    return _issue_gateway_model_lifecycle_proof(
        application_id=scope.runtime_application_id,
        inventory_principal=inventory_principal,
        catalog=scope.catalog,
        metastore_id=metastore_id,
        workspace_id=str(workspace.get_workspace_id()),
        model_family=scope.model_family,
        candidate_model=candidate_model,
        states=tuple(states),
    )
