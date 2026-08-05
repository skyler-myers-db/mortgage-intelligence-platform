"""Lease-fenced, evidence-preserving convergence for Gateway model archival."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.ml import (
    ExperimentAccessControlRequest,
    ExperimentPermissionLevel,
)
from tools.databricks.app_deployment_lease import held_assertion
from tools.databricks.gateway_model_archival_inventory import (
    archive_experiment_name,
    exact_experiment_acl,
    inventory_gateway_model_archive,
    inventory_gateway_serving,
)
from tools.databricks.gateway_model_archival_protection import (
    discover_protected_allocation_contracts,
    zero_effective_access_evidence,
)
from tools.databricks.gateway_model_archival_sdk import (
    delta_version_resolver as delta_version_resolver,
)
from tools.databricks.gateway_model_archival_sdk import (
    experiment_state as _experiment_state,
)
from tools.databricks.gateway_model_archival_sdk import (
    experiments_named as _experiments_named,
)
from tools.databricks.gateway_model_retirement_record import (
    archived_head_path,
    completion_path,
    in_progress_path,
    load_retirement_record,
    persist_retirement_record,
    record_sha256,
    sign_retirement_record,
    stage_path,
)
from tools.databricks.mlflow_uc_model_versions import (
    authoritative_model_version,
    model_version_tags,
)

_SIGNATURE_FIELDS = {
    "attestation_alg",
    "attestation_verify_key",
    "attestation_signature",
}
_LOGGED_MODEL = re.compile(r"models:/(?P<model_id>m-[A-Za-z0-9][A-Za-z0-9_-]*)\Z")


@dataclass(frozen=True)
class GatewayModelArchiveScope:
    """All mutable authority and exact allocation scope for one archival."""

    app_name: str
    lease_id: str
    source_git_sha: str
    runtime_application_id: str
    app_application_id: str
    proxy_application_id: str
    verifier_application_id: str
    archive_owner: str
    governance_group: str
    catalog: str
    model_family: str
    experiment_base: str
    inference_schema: str
    inference_table_prefix: str
    rollback_scope: str
    expected_lakebase_instance: str
    warehouse_id: str


def _protection_inventory(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    scope: GatewayModelArchiveScope,
) -> tuple[dict[str, Any], ...]:
    return discover_protected_allocation_contracts(
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


def _field(value: Any, name: str) -> str:
    raw = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    enum_value = getattr(raw, "value", None)
    return str(enum_value if type(enum_value) is str else raw or "").strip()


def _scope_record(workspace: Any, scope: GatewayModelArchiveScope) -> dict[str, Any]:
    host = str(getattr(getattr(workspace, "config", None), "host", "") or "").rstrip("/")
    workspace_id = str(workspace.get_workspace_id()).strip()
    metastore_id = _field(workspace.metastores.current(), "metastore_id")
    ids = (
        scope.runtime_application_id,
        scope.app_application_id,
        scope.proxy_application_id,
        scope.verifier_application_id,
        scope.archive_owner,
    )
    if (
        not host
        or not workspace_id
        or not metastore_id
        or any(not value.strip() for value in ids)
        or len(set(ids)) != len(ids)
        or scope.governance_group in {"", "admins"}
    ):
        raise ValueError("Gateway archival scope is incomplete or non-separated")
    return {
        "app_name": scope.app_name,
        "lease_id": scope.lease_id,
        "source_git_sha": scope.source_git_sha,
        "workspace_host": host,
        "workspace_id": workspace_id,
        "metastore_id": metastore_id,
        "runtime_application_id": scope.runtime_application_id,
        "app_application_id": scope.app_application_id,
        "proxy_application_id": scope.proxy_application_id,
        "verifier_application_id": scope.verifier_application_id,
        "archive_owner": scope.archive_owner,
        "governance_group": scope.governance_group,
        "catalog": scope.catalog,
        "model_family": scope.model_family,
        "experiment_base": scope.experiment_base,
        "inference_schema": scope.inference_schema,
        "inference_table_prefix": scope.inference_table_prefix,
    }


def _table_family(scope: GatewayModelArchiveScope, model_name: str) -> str:
    suffix = model_name.rsplit("_", 1)[-1]
    return (
        f"{scope.catalog}.{scope.inference_schema}."
        f"{scope.inference_table_prefix}_{suffix}"
    )


def _assert_experiment_identity(
    client: Any,
    *,
    stage: Mapping[str, Any],
) -> dict[str, Any]:
    experiment_id = str(stage["experiment_id"])
    state = _experiment_state(client, experiment_id)
    immutable = {
        "artifact_location": stage["experiment_artifact_location"],
        "lifecycle_state": stage["experiment_lifecycle_state"],
        "owner": stage["experiment_owner"],
        "tags": stage["experiment_tags"],
    }
    if any(state[key] != value for key, value in immutable.items()) or state["name"] not in {
        stage["experiment_original_name"],
        stage["experiment_archive_name"],
    }:
        raise RuntimeError("Gateway archival experiment contents drifted")
    for name in (stage["experiment_original_name"], stage["experiment_archive_name"]):
        matches = _experiments_named(client, str(name))
        if any(_field(item, "experiment_id") != experiment_id for item in matches):
            raise RuntimeError("Gateway archival experiment name collides across active/deleted")
        expected = 1 if state["name"] == name else 0
        if len(matches) != expected:
            raise RuntimeError("Gateway archival experiment name inventory is not exact")
    return state


def _table_evidence(
    workspace: Any,
    resolver: Callable[[str], str],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    table = workspace.tables.get(
        str(expected["full_name"]),
        include_browse=True,
        include_delta_metadata=True,
    )
    evidence = {
        "full_name": _field(table, "full_name"),
        "table_id": _field(table, "table_id"),
        "owner": _field(table, "owner"),
        "storage_location": _field(table, "storage_location"),
        "data_source_format": _field(table, "data_source_format").upper(),
        "delta_latest_version": "",
    }
    if evidence["data_source_format"] == "DELTA":
        evidence["delta_latest_version"] = resolver(evidence["full_name"])
    return evidence


def _assert_tables(
    workspace: Any,
    resolver: Callable[[str], str],
    *,
    stage: Mapping[str, Any],
) -> None:
    allowed_owners = {stage["runtime_application_id"], stage["archive_owner"]}
    present_names: set[str] = set()
    for expected in stage["inference_tables"]:
        current = _table_evidence(workspace, resolver, expected)
        owner = current.pop("owner")
        baseline = dict(expected)
        baseline.pop("owner")
        if current != baseline or owner not in allowed_owners:
            raise RuntimeError("Gateway archival table identity or contents drifted")
        present_names.add(str(expected["full_name"]))
    for full_name in stage["expected_absent_inference_tables"]:
        try:
            workspace.tables.get(full_name, include_browse=True)
        except (NotFound, ResourceDoesNotExist):
            continue
        raise RuntimeError("Gateway archival expected-absent table appeared")
    family = _table_family(
        GatewayModelArchiveScope(
            **{
                field: str(stage[field])
                for field in GatewayModelArchiveScope.__dataclass_fields__
                if field in stage
            },
            rollback_scope="unused",
            expected_lakebase_instance="unused",
            warehouse_id="unused",
        ),
        str(stage["model_name"]),
    )
    for table in workspace.tables.list(
        str(stage["catalog"]),
        str(stage["inference_schema"]),
        include_browse=True,
        omit_columns=True,
        omit_properties=True,
    ):
        full_name = _field(table, "full_name")
        if full_name.rsplit(".", 1)[-1].startswith(family.rsplit('.', 1)[-1]) and (
            full_name not in present_names
            and full_name not in set(stage["expected_absent_inference_tables"])
        ):
            raise RuntimeError("Gateway archival same-allocation table appeared")


def _acl_is_target(
    acl: Sequence[Mapping[str, Any]],
    *,
    stage: Mapping[str, Any],
) -> bool:
    allowed = {"admins", stage["archive_owner"], stage["governance_group"]}
    governance_direct = 0
    for entry in acl:
        principal = (
            str(entry.get("group_name") or "").strip()
            or str(entry.get("service_principal_name") or "").strip()
            or str(entry.get("user_name") or "").strip()
        )
        permissions = entry.get("all_permissions")
        if principal not in allowed or not isinstance(permissions, list) or any(
            permission.get("permission_level") != "CAN_MANAGE"
            for permission in permissions
        ):
            return False
        if principal == stage["governance_group"] and any(
            permission.get("inherited") is False for permission in permissions
        ):
            governance_direct += 1
    return governance_direct == 1


def _acl_is_convergent(
    acl: Sequence[Mapping[str, Any]],
    *,
    stage: Mapping[str, Any],
) -> bool:
    if list(acl) == stage["experiment_acl"] or _acl_is_target(acl, stage=stage):
        return True
    allowed = {"admins", stage["archive_owner"]}
    for entry in acl:
        principal = (
            str(entry.get("group_name") or "").strip()
            or str(entry.get("service_principal_name") or "").strip()
            or str(entry.get("user_name") or "").strip()
        )
        permissions = entry.get("all_permissions")
        if principal not in allowed or not isinstance(permissions, list) or any(
            permission.get("permission_level") != "CAN_MANAGE"
            for permission in permissions
        ):
            return False
    return bool(acl)


def _assert_frozen_versions(
    model_registry: Any,
    tracking_client: Any,
    *,
    stage: Mapping[str, Any],
) -> str:
    expected_versions = stage["versions"]
    if not isinstance(expected_versions, list) or len(expected_versions) != 1:
        raise RuntimeError("Gateway archival stage model evidence is invalid")
    raw_versions = list(
        model_registry.search_model_versions(
            filter_string=f"name='{stage['model_name']}'",
            max_results=2,
        )
    )
    if len(raw_versions) != 1:
        raise RuntimeError("Gateway archival model-version cardinality drifted")
    version = authoritative_model_version(
        model_registry,
        raw_versions[0],
        expected_model_name=str(stage["model_name"]),
    )
    expected = expected_versions[0]
    tags = model_version_tags(
        version,
        resource=f"archived Gateway model {stage['model_name']}",
    )
    source = _field(version, "source")
    if (
        _field(version, "version") != expected["version"]
        or _field(version, "status").upper() != "READY"
        or source != expected["source"]
        or record_sha256(source) != expected["source_sha256"]
        or dict(sorted(tags.items())) != expected["tags"]
        or record_sha256(tags) != expected["tags_sha256"]
    ):
        raise RuntimeError("Gateway archival frozen model evidence drifted")
    match = _LOGGED_MODEL.fullmatch(source)
    if match is None or match.group("model_id") != expected["logged_model_id"]:
        raise RuntimeError("Gateway archival logged-model identity drifted")
    logged = tracking_client.get_logged_model(expected["logged_model_id"])
    if (
        _field(logged, "model_id") != expected["logged_model_id"]
        or _field(logged, "source_run_id") != expected["run_id"]
        or _field(logged, "experiment_id") != stage["experiment_id"]
    ):
        raise RuntimeError("Gateway archival logged-model lineage drifted")
    run = tracking_client.get_run(expected["run_id"])
    run_info = getattr(run, "info", None)
    if (
        (_field(run_info, "run_id") or _field(run, "run_id")) != expected["run_id"]
        or (
            _field(run_info, "experiment_id") or _field(run, "experiment_id")
        )
        != stage["experiment_id"]
    ):
        raise RuntimeError("Gateway archival source-run lineage drifted")
    return str(stage["versions_sha256"])


def _fence(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    resolver: Callable[[str], str],
    *,
    scope: GatewayModelArchiveScope,
    stage: Mapping[str, Any],
    assert_held: Callable[[], None],
) -> None:
    assert_held()
    _assert_frozen_versions(
        model_registry,
        tracking_client,
        stage=stage,
    )
    model = workspace.registered_models.get(str(stage["model_name"]))
    if _field(model, "owner") not in {
        scope.runtime_application_id,
        scope.archive_owner,
    }:
        raise RuntimeError("Gateway archival model owner escaped convergent states")
    _assert_tables(workspace, resolver, stage=stage)
    _assert_experiment_identity(tracking_client, stage=stage)
    acl = exact_experiment_acl(
        workspace,
        experiment_id=str(stage["experiment_id"]),
    )
    if not _acl_is_convergent(acl, stage=stage):
        raise RuntimeError("Gateway archival experiment ACL escaped convergent states")
    inventory, references = inventory_gateway_serving(
        workspace,
        model_name=str(stage["model_name"]),
        inference_table_family=_table_family(scope, str(stage["model_name"])),
    )
    protected = _protection_inventory(
        workspace, model_registry, tracking_client, scope=scope
    )
    if (
        list(inventory) != stage["serving_inventory"]
        or references
        or list(protected) != stage["protected_allocation_contracts"]
    ):
        raise RuntimeError("Gateway archival serving/protection fence drifted")


def _record_root(scope: GatewayModelArchiveScope, model_name: str) -> str:
    return archived_head_path(scope.app_name, model_name).rsplit("/", 1)[0]


def _operation_records(
    workspace: Any,
    *,
    scope: GatewayModelArchiveScope,
    model_name: str,
    leaf: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        objects = workspace.workspace.list(
            _record_root(scope, model_name),
            recursive=True,
        )
        for item in objects:
            path = _field(item, "path")
            if path.endswith(f"/{leaf}"):
                record = load_retirement_record(workspace, path)
                if record is None:
                    raise RuntimeError("Gateway archival listed record disappeared")
                records.append(record)
    except (NotFound, ResourceDoesNotExist):
        return []
    return records


def _unique_record(records: Sequence[dict[str, Any]], *, label: str) -> dict[str, Any] | None:
    if not records:
        return None
    first = records[0]
    if any(record != first for record in records[1:]):
        raise RuntimeError(f"Gateway archival has divergent {label} records")
    return first


def _fresh_stage(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    resolver: Callable[[str], str],
    *,
    scope: GatewayModelArchiveScope,
    model_name: str,
) -> dict[str, Any]:
    inventory = inventory_gateway_model_archive(
        workspace,
        model_registry,
        tracking_client,
        model_name=model_name,
        runtime_application_id=scope.runtime_application_id,
        model_family=scope.model_family,
        experiment_base=scope.experiment_base,
        catalog=scope.catalog,
        inference_schema=scope.inference_schema,
        inference_table_prefix=scope.inference_table_prefix,
        delta_version_resolver=resolver,
    )
    archive_name = archive_experiment_name(
        archive_owner=scope.archive_owner,
        app_name=scope.app_name,
        model_name=model_name,
    )
    if _experiments_named(tracking_client, archive_name):
        raise RuntimeError("Gateway archival destination experiment already exists")
    protected = _protection_inventory(
        workspace, model_registry, tracking_client, scope=scope
    )
    unsigned = {
        "version": 1,
        "kind": "gateway-model-retirement",
        "phase": "staged",
        "disposition": "archive",
        **_scope_record(workspace, scope),
        "model_name": model_name,
        "model_owner": inventory.model_owner,
        "versions": list(inventory.versions),
        "versions_sha256": inventory.versions_sha256,
        "model_sources": sorted({str(item["source"]) for item in inventory.versions}),
        "logged_model_ids": list(inventory.logged_model_ids),
        "source_run_ids": list(inventory.source_run_ids),
        "experiment_id": inventory.experiment_id,
        "experiment_original_name": inventory.experiment_name,
        "experiment_archive_name": archive_name,
        "experiment_artifact_location": inventory.experiment_artifact_location,
        "experiment_lifecycle_state": inventory.experiment_lifecycle_state,
        "experiment_owner": inventory.experiment_owner,
        "experiment_tags": inventory.experiment_tags,
        "experiment_tags_sha256": inventory.experiment_tags_sha256,
        "experiment_acl": list(inventory.experiment_acl),
        "experiment_acl_sha256": inventory.experiment_acl_sha256,
        "inference_tables": list(inventory.inference_tables),
        "expected_absent_inference_tables": list(
            inventory.expected_absent_inference_tables
        ),
        "serving_inventory": list(inventory.serving_inventory),
        "serving_inventory_sha256": inventory.serving_inventory_sha256,
        "serving_references": list(inventory.serving_references),
        "serving_references_sha256": inventory.serving_references_sha256,
        "protected_allocation_contracts": list(protected),
        "protected_allocation_contracts_sha256": record_sha256(protected),
        "created_at": datetime.now(UTC).isoformat(),
    }
    return sign_retirement_record(unsigned)


def _adopt_stage(
    workspace: Any,
    *,
    scope: GatewayModelArchiveScope,
    pointer: Mapping[str, Any],
) -> dict[str, Any]:
    expected_scope = _scope_record(workspace, scope)
    stable_scope = set(expected_scope) - {"lease_id", "source_git_sha"}
    if (
        pointer.get("phase") != "staged"
        or any(pointer.get(key) != expected_scope[key] for key in stable_scope)
    ):
        raise RuntimeError("Gateway archival in-progress pointer escaped caller scope")
    unsigned = {
        key: copy.deepcopy(value)
        for key, value in pointer.items()
        if key not in _SIGNATURE_FIELDS
    }
    unsigned.update(
        {
            **_scope_record(workspace, scope),
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return sign_retirement_record(unsigned)


def _converge_owner(
    read: Callable[[], Any],
    update: Callable[[], Any],
    *,
    archive_owner: str,
    label: str,
    assert_held: Callable[[], None],
) -> None:
    if _field(read(), "owner") == archive_owner:
        return
    assert_held()
    try:
        update()
    except Exception as update_error:
        if _field(read(), "owner") != archive_owner:
            raise RuntimeError(f"Gateway archival {label} owner update is ambiguous") from update_error
    if _field(read(), "owner") != archive_owner:
        raise RuntimeError(f"Gateway archival {label} owner did not converge")


def _converge_experiment(
    workspace: Any,
    tracking_client: Any,
    *,
    stage: Mapping[str, Any],
    assert_held: Callable[[], None],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    experiment_id = str(stage["experiment_id"])
    archive_name = str(stage["experiment_archive_name"])
    parent = archive_name.rsplit("/", 1)[0]
    assert_held()
    workspace.workspace.mkdirs(parent)
    state = _assert_experiment_identity(tracking_client, stage=stage)
    if state["name"] != archive_name:
        assert_held()
        try:
            tracking_client.rename_experiment(experiment_id, archive_name)
        except Exception as rename_error:
            if _experiment_state(tracking_client, experiment_id)["name"] != archive_name:
                raise RuntimeError(
                    "Gateway archival experiment rename is ambiguous"
                ) from rename_error
    _assert_experiment_identity(tracking_client, stage=stage)
    request = ExperimentAccessControlRequest(
        group_name=str(stage["governance_group"]),
        permission_level=ExperimentPermissionLevel.CAN_MANAGE,
    )
    assert_held()
    try:
        workspace.experiments.set_permissions(
            experiment_id,
            access_control_list=[request],
        )
    except Exception as acl_error:
        acl = exact_experiment_acl(workspace, experiment_id=experiment_id)
        if not _acl_is_target(acl, stage=stage):
            raise RuntimeError("Gateway archival experiment ACL update is ambiguous") from acl_error
    state = _assert_experiment_identity(tracking_client, stage=stage)
    acl = exact_experiment_acl(workspace, experiment_id=experiment_id)
    if not _acl_is_target(acl, stage=stage):
        raise RuntimeError("Gateway archival experiment ACL did not converge")
    return state, acl


def _completion_record(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    resolver: Callable[[str], str],
    *,
    scope: GatewayModelArchiveScope,
    stage: Mapping[str, Any],
) -> dict[str, Any]:
    model_name = str(stage["model_name"])
    versions_sha256 = _assert_frozen_versions(
        model_registry,
        tracking_client,
        stage=stage,
    )
    tables = [
        _table_evidence(workspace, resolver, expected)
        for expected in stage["inference_tables"]
    ]
    state = _assert_experiment_identity(tracking_client, stage=stage)
    acl = exact_experiment_acl(workspace, experiment_id=str(stage["experiment_id"]))
    inventory, references = inventory_gateway_serving(
        workspace,
        model_name=model_name,
        inference_table_family=_table_family(scope, model_name),
    )
    protected = _protection_inventory(
        workspace, model_registry, tracking_client, scope=scope
    )
    access = zero_effective_access_evidence(
        workspace,
        experiment_acl=acl,
        model_name=model_name,
        table_names=[str(item["full_name"]) for item in tables],
        runtime_application_id=scope.runtime_application_id,
        app_application_id=scope.app_application_id,
        proxy_application_id=scope.proxy_application_id,
        verifier_application_id=scope.verifier_application_id,
    )
    unsigned = {
        key: copy.deepcopy(stage[key])
        for key in _scope_record(workspace, scope)
    }
    unsigned.update(
        {
            "version": 1,
            "kind": "gateway-model-retirement",
            "phase": "completed",
            "disposition": "archive",
            "model_name": model_name,
            "stage_record_sha256": record_sha256(stage),
            "versions_sha256": versions_sha256,
            "inference_tables": tables,
            "expected_absent_inference_tables": stage[
                "expected_absent_inference_tables"
            ],
            "model_owner": scope.archive_owner,
            "experiment_id": stage["experiment_id"],
            "experiment_original_name": stage["experiment_original_name"],
            "experiment_archive_name": stage["experiment_archive_name"],
            "experiment_artifact_location": state["artifact_location"],
            "experiment_lifecycle_state": state["lifecycle_state"],
            "experiment_owner": state["owner"],
            "experiment_tags": state["tags"],
            "experiment_tags_sha256": record_sha256(state["tags"]),
            "experiment_acl": list(acl),
            "experiment_acl_sha256": record_sha256(acl),
            "serving_inventory": list(inventory),
            "serving_inventory_sha256": record_sha256(inventory),
            "serving_references": list(references),
            "serving_references_sha256": record_sha256(references),
            "protected_allocation_contracts": list(protected),
            "protected_allocation_contracts_sha256": record_sha256(protected),
            "effective_access": list(access),
            "effective_access_sha256": record_sha256(access),
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    return sign_retirement_record(unsigned)


def archive_gateway_model(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    scope: GatewayModelArchiveScope,
    model_name: str,
    resolve_delta_version: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Converge one historical allocation to exact signed archive state."""

    resolver = resolve_delta_version or delta_version_resolver(
        workspace,
        warehouse_id=scope.warehouse_id,
    )
    assert_held = held_assertion(
        workspace,
        app_name=scope.app_name,
        lease_id=scope.lease_id,
        source_git_sha=scope.source_git_sha,
    )

    def persist(path: str, record: Mapping[str, Any]) -> None:
        persist_retirement_record(
            workspace, path, record, assert_before_mutation=assert_held
        )

    assert_held()
    head_path = archived_head_path(scope.app_name, model_name)
    head = load_retirement_record(workspace, head_path)
    completions = _operation_records(
        workspace,
        scope=scope,
        model_name=model_name,
        leaf="complete.json",
    )
    recovered_completion = _unique_record(completions, label="completion")
    if head is None and recovered_completion is not None:
        persist(head_path, recovered_completion)
        head = recovered_completion
    if head is not None:
        if recovered_completion is not None and recovered_completion != head:
            raise RuntimeError("Gateway archival head diverges from operation completion")
        from tools.databricks.gateway_model_lifecycle_audit import (
            assert_completed_gateway_archive,
        )

        return assert_completed_gateway_archive(
            workspace,
            model_registry,
            tracking_client,
            scope=scope,
            completion=head,
            resolve_delta_version=resolver,
        )
    local_stage_path = stage_path(scope.app_name, model_name, scope.lease_id)
    stage = load_retirement_record(workspace, local_stage_path)
    pointer_path = in_progress_path(scope.app_name, model_name)
    pointer = load_retirement_record(workspace, pointer_path)
    if pointer is None:
        prior_stage = _unique_record(
            _operation_records(
                workspace,
                scope=scope,
                model_name=model_name,
                leaf="stage.json",
            ),
            label="stage",
        )
        if prior_stage is not None:
            persist(pointer_path, prior_stage)
            pointer = prior_stage
    if stage is None:
        if pointer is None:
            stage = _fresh_stage(
                workspace,
                model_registry,
                tracking_client,
                resolver,
                scope=scope,
                model_name=model_name,
            )
            persist(pointer_path, stage)
        else:
            stage = _adopt_stage(workspace, scope=scope, pointer=pointer)
        persist(local_stage_path, stage)

    def mutation_fence() -> None:
        _fence(
            workspace,
            model_registry,
            tracking_client,
            resolver,
            scope=scope,
            stage=stage,
            assert_held=assert_held,
        )

    _fence(
        workspace,
        model_registry,
        tracking_client,
        resolver,
        scope=scope,
        stage=stage,
        assert_held=assert_held,
    )
    for expected in stage["inference_tables"]:
        full_name = str(expected["full_name"])

        def read_table(full_name: str = full_name) -> Any:
            return workspace.tables.get(
                full_name,
                include_browse=True,
                include_delta_metadata=True,
            )

        def update_table(full_name: str = full_name) -> Any:
            return workspace.tables.update(
                full_name,
                owner=scope.archive_owner,
            )

        _converge_owner(
            read_table,
            update_table,
            archive_owner=scope.archive_owner,
            label=f"table {full_name}",
            assert_held=mutation_fence,
        )
    _converge_owner(
        lambda: workspace.registered_models.get(model_name),
        lambda: workspace.registered_models.update(
            model_name,
            owner=scope.archive_owner,
        ),
        archive_owner=scope.archive_owner,
        label=f"model {model_name}",
        assert_held=mutation_fence,
    )
    _converge_experiment(
        workspace,
        tracking_client,
        stage=stage,
        assert_held=mutation_fence,
    )
    _fence(
        workspace,
        model_registry,
        tracking_client,
        resolver,
        scope=scope,
        stage=stage,
        assert_held=assert_held,
    )
    completion = _completion_record(
        workspace,
        model_registry,
        tracking_client,
        resolver,
        scope=scope,
        stage=stage,
    )
    operation_completion_path = completion_path(
        scope.app_name,
        model_name,
        scope.lease_id,
    )
    persist(operation_completion_path, completion)
    persist(head_path, completion)
    from tools.databricks.gateway_model_lifecycle_audit import (
        assert_completed_gateway_archive,
    )

    return assert_completed_gateway_archive(
        workspace,
        model_registry,
        tracking_client,
        scope=scope,
        completion=completion,
        resolve_delta_version=resolver,
    )
