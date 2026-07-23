#!/usr/bin/env python3
"""Audit the runtime's foreign UC access from a metastore-owner control plane."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_uc_baseline import (
    _MAX_INVENTORY_WORKERS,
    ControlPlaneForeignCatalogProof,
    _issue_control_plane_foreign_catalog_proof,
    authoritative_workspace_id,
)
from tools.databricks.agent_runtime_uc_inventory import (
    _assert_no_catalog_child_privileges,
    _assert_privileges,
    _catalog_name,
    _effective_privilege_sources,
    _full_name,
    _text,
)
from tools.databricks.audit_global_m2m_access import (
    assert_workspace_admin_inventory_identity,
)
from tools.databricks.converge_campaign_treatment_access import (
    target_group_membership_probe,
)
from tools.databricks.uc_owner_policy import (
    ApprovedOwnerPolicy,
    TargetServicePrincipal,
    account_client_from_env,
)

_DATABRICKS_INTERNAL_CATALOG = "__databricks_internal"
_PLATFORM_CATALOGS = frozenset({_DATABRICKS_INTERNAL_CATALOG, "samples", "system"})


def _target_identity(workspace: Any, *, application_id: str) -> TargetServicePrincipal:
    escaped = application_id.replace('"', '\\"')
    matches = [
        item
        for item in workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if _text(getattr(item, "application_id", None)).casefold() == application_id.casefold()
    ]
    if len(matches) != 1:
        raise RuntimeError("agent-runtime identity did not resolve exactly once in workspace SCIM")
    scim_id = _text(getattr(matches[0], "id", None))
    if not scim_id:
        raise RuntimeError("agent-runtime identity has no immutable workspace SCIM id")
    return TargetServicePrincipal(application_id=application_id, scim_id=scim_id)


def _assert_no_foreign_ownership(
    workspace: Any,
    *,
    application_id: str,
    objects: list[object],
    account_factory: Callable[[], Any],
    group_membership_probe: Callable[[Any, str, str, str, str], bool] | None,
) -> None:
    """Reject direct or target-credential-proven group ownership of foreign objects."""

    owners = {_text(getattr(item, "owner", None)) for item in objects}
    if "" in owners:
        raise RuntimeError("foreign UC object inventory returned an empty owner")
    workspace_host = _text(getattr(getattr(workspace, "config", None), "host", None))
    if not workspace_host:
        raise RuntimeError("UC control-plane audit found no workspace host")
    membership_probe = group_membership_probe or (
        lambda account, account_sp_id, target_id, owner_group_id, owner_group: (
            target_group_membership_probe(
                account,
                account_sp_id,
                target_id,
                owner_group_id,
                owner_group,
                workspace_host=workspace_host,
            )
        )
    )
    policy = ApprovedOwnerPolicy(
        workspace=workspace,
        target=_target_identity(workspace, application_id=application_id),
        configured_principals=owners,
        account_factory=account_factory,
        group_membership_probe=membership_probe,
    )
    policy.assert_objects(objects)


def _assert_metastore_owner_inventory_identity(
    workspace: Any,
    *,
    expected_principal: str,
) -> tuple[str, str]:
    """Bind the audit to the expected caller and its current metastore authority."""

    assert_workspace_admin_inventory_identity(
        workspace,
        expected_principal=expected_principal,
    )
    caller = workspace.current_user.me()
    caller_name = _text(getattr(caller, "user_name", None))
    metastore_id = _text(getattr(workspace.metastores.current(), "metastore_id", None))
    if not metastore_id:
        raise RuntimeError("UC control-plane audit found no current metastore identity")
    owner = _text(getattr(workspace.metastores.get(metastore_id), "owner", None))
    if not owner:
        raise RuntimeError("UC control-plane audit found no current metastore owner")
    if owner.casefold() != caller_name.casefold():
        raise RuntimeError(
            "UC control-plane audit requires the expected caller to own the current "
            "metastore directly"
        )
    workspace_id = authoritative_workspace_id(workspace)
    return metastore_id, workspace_id


def _assert_catalog_bound_for_complete_inventory(
    workspace: Any,
    *,
    catalog: str,
    isolation_mode: str,
    workspace_id: str,
) -> None:
    """Reject an unbound catalog whose child securables cannot be inventoried."""

    if isolation_mode == "OPEN":
        return
    if isolation_mode != "ISOLATED":
        raise RuntimeError(
            f"UC control-plane catalog {catalog} has an unknown isolation mode: "
            f"{isolation_mode or '<empty>'}"
        )
    if not workspace_id:
        raise RuntimeError("UC control-plane audit found no current workspace identity")
    binding_ids: set[str] = set()
    for binding in workspace.workspace_bindings.get_bindings("catalog", catalog):
        binding_workspace_id = _text(getattr(binding, "workspace_id", None))
        if not binding_workspace_id:
            raise RuntimeError(
                f"UC control-plane catalog {catalog} returned an incomplete workspace binding"
            )
        binding_ids.add(binding_workspace_id)
    if workspace_id not in binding_ids:
        raise RuntimeError(
            f"UC control-plane catalog {catalog} is unbound from workspace {workspace_id}; "
            "its child privileges cannot be completely inventoried"
        )


def audit_foreign_uc_access(
    workspace: Any,
    *,
    application_id: str,
    catalog: str,
    expected_inventory_principal: str,
    allow_missing_mip_catalog: bool = False,
    account_factory: Callable[[], Any] = account_client_from_env,
    group_membership_probe: Callable[[Any, str, str, str, str], bool] | None = None,
) -> ControlPlaneForeignCatalogProof:
    """Prove zero target-principal access on every ordinary foreign catalog."""

    principal = application_id.strip()
    mip_catalog = catalog.strip()
    inventory_principal = expected_inventory_principal.strip()
    if not principal or not mip_catalog or not inventory_principal:
        raise ValueError("application ID, MIP catalog, and inventory principal are required")
    metastore_id, workspace_id = _assert_metastore_owner_inventory_identity(
        workspace,
        expected_principal=inventory_principal,
    )
    catalog_inventory: dict[str, tuple[str, object]] = {}
    for item in workspace.catalogs.list(include_browse=True, include_unbound=True):
        name = _text(getattr(item, "name", None))
        if not name:
            raise RuntimeError("UC control-plane catalog inventory returned an empty name")
        if name in catalog_inventory:
            raise RuntimeError("UC control-plane catalog inventory returned duplicate names")
        catalog_inventory[name] = (
            _text(getattr(item, "isolation_mode", None)).upper(),
            item,
        )
    mip_catalog_exists = mip_catalog in catalog_inventory
    if not mip_catalog_exists and not allow_missing_mip_catalog:
        raise RuntimeError("configured MIP catalog is missing from control-plane inventory")
    internal = catalog_inventory.get(_DATABRICKS_INTERNAL_CATALOG)
    if internal is not None:
        isolation_mode, item = internal
        catalog_type = _text(getattr(item, "catalog_type", None)).upper()
        owner = _text(getattr(item, "owner", None))
        if isolation_mode != "OPEN" or catalog_type != "INTERNAL_CATALOG" or owner != "System user":
            raise RuntimeError(
                "Databricks internal catalog does not match the fixed platform identity"
            )
        internal_sources = _effective_privilege_sources(
            workspace,
            securable_type="catalog",
            full_name=_DATABRICKS_INTERNAL_CATALOG,
            principal=principal,
        )
        if internal_sources:
            raise RuntimeError(
                "agent-runtime has forbidden access on Databricks internal catalog: "
                f"{internal_sources}"
            )
    excluded_catalogs = set(_PLATFORM_CATALOGS)
    if mip_catalog_exists:
        excluded_catalogs.add(mip_catalog)
    foreign_catalogs = sorted(set(catalog_inventory) - excluded_catalogs)
    foreign_objects: list[object] = []
    foreign_objects_lock = Lock()

    def record_owner(item: object) -> None:
        with foreign_objects_lock:
            foreign_objects.append(item)

    def inspect_catalog(foreign_catalog: str) -> None:
        isolation_mode, catalog_object = catalog_inventory[foreign_catalog]
        record_owner(catalog_object)
        sources = _effective_privilege_sources(
            workspace,
            securable_type="catalog",
            full_name=foreign_catalog,
            principal=principal,
        )
        if sources:
            raise RuntimeError(
                f"agent-runtime has forbidden access on catalog {foreign_catalog}: {sources}"
            )
        _assert_catalog_bound_for_complete_inventory(
            workspace,
            catalog=foreign_catalog,
            isolation_mode=isolation_mode,
            workspace_id=workspace_id,
        )
        _assert_no_catalog_child_privileges(
            workspace,
            catalog=foreign_catalog,
            principal=principal,
            owner_check=record_owner,
        )

    if foreign_catalogs:
        with ThreadPoolExecutor(
            max_workers=min(_MAX_INVENTORY_WORKERS, len(foreign_catalogs)),
            thread_name_prefix="mip-uc-control-plane",
        ) as executor:
            futures = [executor.submit(inspect_catalog, name) for name in foreign_catalogs]
            for future in as_completed(futures):
                future.result()

    registered_models = list(workspace.registered_models.list(include_browse=True))
    for model in registered_models:
        model_catalog = _catalog_name(model)
        full_name = _full_name(model)
        if not model_catalog or not full_name:
            raise RuntimeError(
                "UC control-plane registered-model inventory returned an incomplete identity"
            )
        if model_catalog not in catalog_inventory:
            raise RuntimeError(
                "UC control-plane registered-model catalog is absent from catalog inventory"
            )
        if model_catalog in {mip_catalog, *_PLATFORM_CATALOGS}:
            continue
        record_owner(model)
        _assert_privileges(
            workspace,
            securable_type="function",
            full_name=full_name,
            principal=principal,
            expected=set(),
        )
    _assert_no_foreign_ownership(
        workspace,
        application_id=principal,
        objects=foreign_objects,
        account_factory=account_factory,
        group_membership_probe=group_membership_probe,
    )
    return _issue_control_plane_foreign_catalog_proof(
        application_id=principal,
        catalog=mip_catalog,
        metastore_id=metastore_id,
        workspace_id=workspace_id,
        audited_catalogs=frozenset(foreign_catalogs),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument(
        "--allow-missing-mip-catalog",
        action="store_true",
        help=(
            "For the pre-bootstrap gate only, audit every ordinary catalog when the "
            "configured MIP catalog does not exist yet."
        ),
    )
    args = parser.parse_args(argv)
    audit_foreign_uc_access(
        WorkspaceClient(),
        application_id=args.application_id,
        catalog=args.catalog,
        expected_inventory_principal=args.expected_inventory_principal,
        allow_missing_mip_catalog=args.allow_missing_mip_catalog,
    )
    print("agent-runtime foreign UC control-plane boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
