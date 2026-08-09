#!/usr/bin/env python3
"""Audit the runtime's foreign UC access from a metastore-owner control plane."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_uc_baseline import (
    _MAX_INVENTORY_WORKERS,
    CatalogBindingEvidence,
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
from tools.databricks.converge_campaign_treatment_access import target_identity_groups_probe
from tools.databricks.oauth_credential_boundary import (
    held_deployment_credential_assertion,
)
from tools.databricks.uc_owner_policy import (
    ApprovedOwnerPolicy,
    TargetServicePrincipal,
    account_client_from_env,
)
from tools.databricks.uc_target_identity import (
    account_target_identity,
    workspace_target_identity,
)
from tools.databricks.workspace_system_group_evidence import (
    workspace_users_group_evidence,
)

_DATABRICKS_INTERNAL_CATALOG = "__databricks_internal"
_PLATFORM_CATALOGS = frozenset({_DATABRICKS_INTERNAL_CATALOG, "samples", "system"})
_ACCOUNT_USERS_GROUP = "account users"
_DEFAULT_TARGET_GROUPS_PROBE = target_identity_groups_probe


def _exact_owner(item: object, *, context: str) -> str:
    owner = getattr(item, "owner", None)
    if not isinstance(owner, str) or not owner or owner != owner.strip():
        raise RuntimeError(f"{context} returned a noncanonical owner")
    return owner


def _assert_no_foreign_ownership(
    workspace: Any,
    *,
    target: TargetServicePrincipal,
    objects: list[object],
    account_factory: Callable[[], Any],
    group_membership_probe: Callable[[Any, str, str, str, str], bool] | None,
) -> None:
    """Reject direct or target-credential-proven group ownership of foreign objects."""

    owners = {_exact_owner(item, context="foreign UC object inventory") for item in objects}
    policy = ApprovedOwnerPolicy(
        workspace=workspace,
        target=target,
        configured_principals=owners,
        account_factory=account_factory,
        group_membership_probe=group_membership_probe,
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
    caller_name = getattr(caller, "user_name", None)
    if (
        not isinstance(caller_name, str)
        or not caller_name
        or caller_name != caller_name.strip()
        or caller_name != expected_principal
    ):
        raise RuntimeError(
            "UC control-plane audit is running as a noncanonical inventory principal"
        )
    metastore_id = _text(getattr(workspace.metastores.current(), "metastore_id", None))
    if not metastore_id:
        raise RuntimeError("UC control-plane audit found no current metastore identity")
    owner = _exact_owner(
        workspace.metastores.get(metastore_id),
        context="UC control-plane metastore inventory",
    )
    if owner != caller_name:
        raise RuntimeError(
            "UC control-plane audit requires the expected caller to own the current "
            "metastore directly"
        )
    workspace_id = authoritative_workspace_id(workspace)
    return metastore_id, workspace_id


_WORKSPACE_BINDING_TYPES = frozenset(
    {
        "BINDING_TYPE_READ_ONLY",
        "BINDING_TYPE_READ_WRITE",
    }
)


class _DuplicatePolicyKey(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicatePolicyKey(key)
        result[key] = value
    return result


def parse_foreign_catalog_binding_policy(raw: str) -> dict[str, CatalogBindingEvidence]:
    """Parse the exact reviewed binding-denial policy without permissive defaults."""

    value = raw.strip()
    if not value:
        return {}
    try:
        payload = json.loads(value, object_pairs_hook=_strict_json_object)
    except json.JSONDecodeError as exc:
        raise ValueError("foreign catalog binding policy is not valid JSON") from exc
    except _DuplicatePolicyKey as exc:
        raise ValueError("foreign catalog binding policy contains a duplicate key") from exc
    if not isinstance(payload, dict) or set(payload) != {"version", "catalogs"}:
        raise ValueError("foreign catalog binding policy has an invalid top-level contract")
    if (
        type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["catalogs"], dict)
    ):
        raise ValueError("foreign catalog binding policy has an unsupported version or catalogs")
    policy: dict[str, CatalogBindingEvidence] = {}
    for catalog, contract in payload["catalogs"].items():
        if (
            not isinstance(catalog, str)
            or not catalog.strip()
            or catalog != catalog.strip()
            or not isinstance(contract, dict)
            or set(contract) != {"owner", "catalog_type", "bindings"}
        ):
            raise ValueError("foreign catalog binding policy has an invalid catalog contract")
        owner = contract["owner"]
        catalog_type = contract["catalog_type"]
        bindings = contract["bindings"]
        if (
            not isinstance(owner, str)
            or not owner.strip()
            or owner != owner.strip()
            or not isinstance(catalog_type, str)
            or not catalog_type.strip()
            or catalog_type != catalog_type.strip()
            or not isinstance(bindings, list)
            or not bindings
        ):
            raise ValueError("foreign catalog binding policy has incomplete catalog evidence")
        normalized_bindings: list[tuple[str, str]] = []
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {
                "workspace_id",
                "binding_type",
            }:
                raise ValueError("foreign catalog binding policy has an invalid binding")
            binding_workspace_id = binding["workspace_id"]
            binding_type = binding["binding_type"]
            if (
                not isinstance(binding_workspace_id, str)
                or not binding_workspace_id.isdecimal()
                or int(binding_workspace_id) <= 0
                or str(int(binding_workspace_id)) != binding_workspace_id
                or binding_workspace_id != binding_workspace_id.strip()
                or not isinstance(binding_type, str)
                or binding_type not in _WORKSPACE_BINDING_TYPES
            ):
                raise ValueError("foreign catalog binding policy has an incomplete binding")
            normalized_bindings.append((binding_workspace_id, binding_type))
        if len({item[0] for item in normalized_bindings}) != len(normalized_bindings):
            raise ValueError("foreign catalog binding policy has duplicate workspace bindings")
        policy[catalog] = CatalogBindingEvidence(
            catalog=catalog,
            owner=owner,
            catalog_type=catalog_type,
            isolation_mode="ISOLATED",
            bindings=tuple(sorted(normalized_bindings)),
        )
    return policy


def _account_group_inventory_evidence(
    account: Any,
    *,
    target_scim_id: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return effective, system, and complete account-group evidence."""

    groups_by_id: dict[str, tuple[str, tuple[str, ...]]] = {}
    group_ids_by_name: dict[str, str] = {}
    system_groups: dict[str, str] = {}
    for listed in account.groups.list(attributes="id,displayName,members"):
        group_id = _text(getattr(listed, "id", None))
        if not group_id:
            raise RuntimeError("account group inventory returned an empty immutable ID")
        item = account.groups.get(group_id)
        hydrated_id = _text(getattr(item, "id", None))
        members = getattr(item, "members", None)
        display_name = _text(getattr(item, "display_name", None))
        if hydrated_id != group_id or not display_name or members is None:
            raise RuntimeError("account group inventory returned incomplete group evidence")
        member_ids: list[str] = []
        for member in members:
            member_id = _text(getattr(member, "value", None))
            if not member_id:
                raise RuntimeError("account group inventory returned an incomplete member")
            member_ids.append(member_id)
        if group_id in groups_by_id:
            raise RuntimeError("account group inventory returned a duplicate immutable ID")
        canonical_name = display_name.casefold()
        if canonical_name in group_ids_by_name:
            raise RuntimeError("account group inventory returned a duplicate display name")
        groups_by_id[group_id] = (display_name, tuple(member_ids))
        group_ids_by_name[canonical_name] = group_id
        if canonical_name == "account users":
            system_groups[group_id] = display_name
    effective_ids: set[str] = set()
    frontier = {target_scim_id}
    while frontier:
        next_frontier: set[str] = set()
        for group_id, (_display_name, group_member_ids) in groups_by_id.items():
            if group_id not in effective_ids and frontier.intersection(group_member_ids):
                effective_ids.add(group_id)
                next_frontier.add(group_id)
        frontier = next_frontier
    return (
        {group_id: groups_by_id[group_id][0] for group_id in effective_ids},
        system_groups,
        {
            group_id: display_name
            for group_id, (display_name, _members) in groups_by_id.items()
        },
    )


def _account_group_evidence(
    account: Any,
    *,
    target_scim_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Retain the effective/system compatibility contract for other auditors."""

    effective, system, _inventory = _account_group_inventory_evidence(
        account,
        target_scim_id=target_scim_id,
    )
    return effective, system


def _normalized_target_groups(value: object) -> dict[str, str]:
    """Validate an injected or credential-derived target group snapshot."""

    if not isinstance(value, dict):
        raise RuntimeError("credentialed target group inventory is malformed")
    normalized: dict[str, str] = {}
    ids_by_name: dict[str, str] = {}
    for raw_group_id, raw_group_name in value.items():
        if not isinstance(raw_group_id, str) or not isinstance(raw_group_name, str):
            raise RuntimeError("credentialed target group inventory is malformed")
        group_id = raw_group_id
        group_name = raw_group_name
        canonical_name = group_name.casefold()
        if not group_id or not group_name:
            raise RuntimeError("credentialed target group inventory is incomplete")
        if group_id != group_id.strip() or group_name != group_name.strip():
            raise RuntimeError("credentialed target group inventory is malformed")
        if group_id in normalized or canonical_name in ids_by_name:
            raise RuntimeError("credentialed target group inventory is ambiguous")
        normalized[group_id] = group_name
        ids_by_name[canonical_name] = group_id
    return normalized


def _snapshot_membership_probe(
    *,
    account: Any,
    account_target_scim_id: str,
    application_id: str,
    effective_groups: dict[str, str],
    implicit_system_groups: dict[str, str],
) -> Callable[[Any, str, str, str, str], bool]:
    """Bind approved-owner checks to the audit's frozen target snapshot."""

    def probe(
        observed_account: Any,
        observed_account_sp_id: str,
        observed_application_id: str,
        owner_group_id: str,
        owner_group_name: str,
    ) -> bool:
        if (
            observed_account is not account
            or observed_account_sp_id != account_target_scim_id
            or observed_application_id.casefold() != application_id.casefold()
        ):
            raise RuntimeError("approved-owner identity differs from the frozen audit snapshot")
        group_id = owner_group_id.strip()
        group_name = owner_group_name.strip()
        implicit_name = implicit_system_groups.get(group_id)
        if implicit_name is not None:
            if implicit_name.casefold() != group_name.casefold():
                raise RuntimeError("managed system group snapshot returned a mismatched group name")
            return True
        if group_name.casefold() == "account users":
            if implicit_system_groups:
                raise RuntimeError("managed system group snapshot returned a mismatched group id")
            return True
        observed_name = effective_groups.get(group_id)
        if observed_name is not None:
            if observed_name.casefold() != group_name.casefold():
                raise RuntimeError(
                    "credentialed target group snapshot returned a mismatched group name"
                )
            return True
        if any(name.casefold() == group_name.casefold() for name in effective_groups.values()):
            raise RuntimeError("credentialed target group snapshot returned a mismatched group id")
        return False

    return probe


def _assert_no_binding_denied_runtime_ownership(
    *,
    target_aliases: set[str],
    effective_groups: dict[str, str],
    implicit_system_groups: dict[str, str],
    objects: list[object],
) -> None:
    """Reject target ownership without resolving unrelated owners in MIP SCIM."""

    owner_aliases = {
        *(value.casefold() for value in target_aliases if value.strip()),
        *(group_id.casefold() for group_id in effective_groups),
        *(group_name.casefold() for group_name in effective_groups.values()),
        *(group_id.casefold() for group_id in implicit_system_groups),
        *(group_name.casefold() for group_name in implicit_system_groups.values()),
        "account users",
    }
    for item in objects:
        owner = _exact_owner(
            item,
            context="binding-denied foreign UC object inventory",
        )
        if owner.casefold() in owner_aliases:
            raise RuntimeError("agent-runtime service principal cannot own governed UC objects")


def _assert_runtime_workspace_assignment_boundary(
    account: Any,
    *,
    application_id: str,
    target_scim_ids: set[str],
    account_target_scim_id: str,
    account_effective_groups: dict[str, str],
    account_group_inventory: dict[str, str],
    effective_target_groups: dict[str, str],
    implicit_system_groups: dict[str, str],
    workspace_system_groups: dict[str, str],
    allowed_workspace_groups: dict[str, str],
    metastore_id: str,
    workspace_id: str,
    approved_foreign_workspace_ids: set[str],
) -> set[str]:
    """Prove the runtime is assigned only to MIP, never to bound foreign workspaces."""

    normalized_target_ids = {value.strip() for value in target_scim_ids if value.strip()}
    if account_target_scim_id not in normalized_target_ids:
        raise RuntimeError("agent-runtime immutable SCIM identity snapshot is inconsistent")
    for group_id, group_name in account_effective_groups.items():
        if group_name.casefold() == "account users":
            continue
        observed_name = effective_target_groups.get(group_id)
        if observed_name is None or observed_name.casefold() != group_name.casefold():
            raise RuntimeError("agent-runtime account and credentialed group identities disagree")
    target_group_ids_by_name = {
        group_name.casefold(): group_id for group_id, group_name in effective_target_groups.items()
    }
    for group_id, group_name in workspace_system_groups.items():
        observed_name = effective_target_groups.get(group_id)
        observed_id = target_group_ids_by_name.get(group_name.casefold())
        if observed_name is None or observed_name != group_name or observed_id != group_id:
            raise RuntimeError("agent-runtime workspace users system group identity disagrees")
    for group_id, group_name in implicit_system_groups.items():
        observed_name = effective_target_groups.get(group_id)
        observed_id = target_group_ids_by_name.get(group_name.casefold())
        if (observed_name is not None and observed_name.casefold() != group_name.casefold()) or (
            observed_id is not None and observed_id != group_id
        ):
            raise RuntimeError("agent-runtime managed system group identities disagree")
    effective_groups = dict(effective_target_groups)
    ordinary_groups = {
        group_id: name
        for group_id, name in effective_groups.items()
        if name.casefold() != _ACCOUNT_USERS_GROUP and group_id not in workspace_system_groups
    }
    account_group_names = {
        group_name.casefold() for group_name in account_group_inventory.values()
    }
    if set(allowed_workspace_groups).intersection(account_group_inventory) or any(
        group_name.casefold() in account_group_names
        for group_name in allowed_workspace_groups.values()
    ):
        raise RuntimeError("a reviewed workspace group collides with the account group plane")
    if ordinary_groups != allowed_workspace_groups:
        raise RuntimeError("agent-runtime has forbidden ordinary account group membership")
    if not implicit_system_groups and "account users" not in target_group_ids_by_name:
        raise RuntimeError("agent-runtime managed system group identity could not be proven")
    assignment_group_ids = {*effective_groups, *implicit_system_groups}
    assignment_group_names = {
        *(name.casefold() for name in effective_groups.values()),
        *(name.casefold() for name in implicit_system_groups.values()),
        "account users",
    }
    metastore_workspace_ids = {
        _text(item) for item in account.metastore_assignments.list(metastore_id)
    }
    if "" in metastore_workspace_ids or workspace_id not in metastore_workspace_ids:
        raise RuntimeError("account metastore workspace assignment inventory is incomplete")
    if (
        workspace_id in approved_foreign_workspace_ids
        or not approved_foreign_workspace_ids.issubset(metastore_workspace_ids)
    ):
        raise RuntimeError("foreign catalog policy references an invalid metastore workspace")
    for assigned_workspace_id in sorted(metastore_workspace_ids):
        direct_assignments: list[tuple[str, ...]] = []
        reviewed_group_assignments: dict[str, list[tuple[str, ...]]] = {}
        forbidden_group_assignments: list[tuple[str, tuple[str, ...]]] = []
        for assignment in account.workspace_assignment.list(int(assigned_workspace_id)):
            principal = getattr(assignment, "principal", None)
            principal_id = _text(getattr(principal, "principal_id", None))
            service_principal_name = _text(getattr(principal, "service_principal_name", None))
            group_name = _text(getattr(principal, "group_name", None))
            user_name = _text(getattr(principal, "user_name", None))
            permissions = tuple(
                sorted(_text(item).upper() for item in getattr(assignment, "permissions", []))
            )
            if (
                principal is None
                or not principal_id
                or not permissions
                or _text(getattr(assignment, "error", None))
                or sum(bool(value) for value in (service_principal_name, group_name, user_name))
                != 1
            ):
                raise RuntimeError("agent-runtime workspace assignment inventory is incomplete")
            direct_id_match = principal_id in normalized_target_ids
            direct_name_match = service_principal_name.casefold() == application_id.casefold()
            if direct_name_match != direct_id_match:
                raise RuntimeError("agent-runtime workspace assignment identity fields disagree")
            group_id_match = principal_id in assignment_group_ids
            group_name_match = group_name.casefold() in assignment_group_names
            if group_id_match != group_name_match:
                raise RuntimeError(
                    "agent-runtime workspace group assignment identity fields disagree"
                )
            if group_id_match:
                expected_group_name = {
                    **implicit_system_groups,
                    **effective_groups,
                }[principal_id]
                if expected_group_name.casefold() != group_name.casefold():
                    raise RuntimeError(
                        "agent-runtime workspace group assignment identity fields disagree"
                    )
            if direct_id_match:
                direct_assignments.append(permissions)
            if group_name_match:
                if principal_id in allowed_workspace_groups:
                    reviewed_group_assignments.setdefault(principal_id, []).append(permissions)
                else:
                    forbidden_group_assignments.append((principal_id, permissions))
        expected_direct = [("USER",)] if assigned_workspace_id == workspace_id else []
        expected_reviewed_groups = (
            {group_id: [("USER",)] for group_id in allowed_workspace_groups}
            if assigned_workspace_id == workspace_id
            else {}
        )
        if (
            direct_assignments != expected_direct
            or reviewed_group_assignments != expected_reviewed_groups
            or forbidden_group_assignments
        ):
            raise RuntimeError(
                "agent-runtime has an unexpected account workspace assignment on "
                f"{assigned_workspace_id}"
            )
    return metastore_workspace_ids


def _binding_evidence(
    workspace: Any,
    *,
    catalog: str,
    owner: str,
    catalog_type: str,
    isolation_mode: str,
    workspace_id: str,
) -> CatalogBindingEvidence | None:
    """Return exact workspace-denial evidence, or None for an accessible catalog."""

    if isolation_mode != "ISOLATED":
        if isolation_mode == "OPEN":
            return None
        raise RuntimeError(
            f"UC control-plane catalog {catalog} has an unknown isolation mode: "
            f"{isolation_mode or '<empty>'}"
        )
    if not workspace_id:
        raise RuntimeError("UC control-plane audit found no current workspace identity")
    bindings_by_workspace: dict[str, str] = {}
    for binding in workspace.workspace_bindings.get_bindings("catalog", catalog):
        binding_workspace_id = _text(getattr(binding, "workspace_id", None))
        binding_type = _text(getattr(binding, "binding_type", None)).upper()
        if not binding_workspace_id or binding_type not in _WORKSPACE_BINDING_TYPES:
            raise RuntimeError(
                f"UC control-plane catalog {catalog} returned an incomplete workspace binding"
            )
        if binding_workspace_id in bindings_by_workspace:
            raise RuntimeError(
                f"UC control-plane catalog {catalog} returned duplicate workspace bindings"
            )
        bindings_by_workspace[binding_workspace_id] = binding_type
    if workspace_id in bindings_by_workspace:
        return None
    return CatalogBindingEvidence(
        catalog=catalog,
        owner=owner,
        catalog_type=catalog_type,
        isolation_mode=isolation_mode,
        bindings=tuple(sorted(bindings_by_workspace.items())),
    )


def audit_foreign_uc_access(
    workspace: Any,
    *,
    application_id: str,
    catalog: str,
    expected_inventory_principal: str,
    allow_missing_mip_catalog: bool = False,
    foreign_catalog_binding_policy: str = "",
    account_factory: Callable[[], Any] = account_client_from_env,
    group_membership_probe: Callable[[Any, str, str, str, str], bool] | None = None,
    target_groups_probe: Callable[..., dict[str, str]] = target_identity_groups_probe,
    assert_single_writer: Callable[[], None] | None = None,
    allowed_workspace_groups: Mapping[str, str] | None = None,
) -> ControlPlaneForeignCatalogProof:
    """Prove zero target-principal access on every ordinary foreign catalog."""

    principal = application_id.strip()
    mip_catalog = catalog.strip()
    inventory_principal = expected_inventory_principal.strip()
    if (
        not principal
        or not mip_catalog
        or not inventory_principal
        or application_id != principal
        or expected_inventory_principal != inventory_principal
    ):
        raise ValueError("application ID, MIP catalog, and inventory principal are required")
    metastore_id, workspace_id = _assert_metastore_owner_inventory_identity(
        workspace,
        expected_principal=inventory_principal,
    )
    binding_policy = parse_foreign_catalog_binding_policy(foreign_catalog_binding_policy)
    account = account_factory()
    workspace_target = workspace_target_identity(
        workspace,
        application_id=principal,
    )
    account_target_scim_id, account_target_display_name = account_target_identity(
        account,
        application_id=principal,
    )
    workspace_host = _text(getattr(getattr(workspace, "config", None), "host", None))
    if not workspace_host:
        raise RuntimeError("UC control-plane audit found no workspace host")
    probe_kwargs: dict[str, object] = {
        "expected_workspace_scim_id": workspace_target.scim_id,
        "workspace_host": workspace_host,
    }
    if target_groups_probe is _DEFAULT_TARGET_GROUPS_PROBE:
        credential_lease = assert_single_writer or held_deployment_credential_assertion(workspace)
        probe_kwargs["assert_single_writer"] = credential_lease
    effective_target_groups = _normalized_target_groups(
        target_groups_probe(
            account,
            account_target_scim_id,
            principal,
            **probe_kwargs,
        )
    )
    normalized_allowed_workspace_groups = _normalized_target_groups(
        dict(allowed_workspace_groups or {})
    )
    (
        account_effective_groups,
        implicit_system_groups,
        account_group_inventory,
    ) = _account_group_inventory_evidence(
        account,
        target_scim_id=account_target_scim_id,
    )
    workspace_system_groups = workspace_users_group_evidence(workspace)
    target = TargetServicePrincipal(
        application_id=principal,
        scim_id=workspace_target.scim_id,
        display_name=workspace_target.display_name,
        additional_aliases=frozenset(
            {
                account_target_scim_id,
                account_target_display_name,
            }
        ),
    )
    frozen_membership_probe = group_membership_probe or _snapshot_membership_probe(
        account=account,
        account_target_scim_id=account_target_scim_id,
        application_id=principal,
        effective_groups=effective_target_groups,
        implicit_system_groups=implicit_system_groups,
    )
    approved_foreign_workspace_ids = {
        binding_workspace_id
        for evidence in binding_policy.values()
        for binding_workspace_id, _binding_type in evidence.bindings
    }
    _assert_runtime_workspace_assignment_boundary(
        account,
        application_id=principal,
        target_scim_ids={account_target_scim_id, workspace_target.scim_id},
        account_target_scim_id=account_target_scim_id,
        account_effective_groups=account_effective_groups,
        account_group_inventory=account_group_inventory,
        effective_target_groups=effective_target_groups,
        implicit_system_groups=implicit_system_groups,
        workspace_system_groups=workspace_system_groups,
        allowed_workspace_groups=normalized_allowed_workspace_groups,
        metastore_id=metastore_id,
        workspace_id=workspace_id,
        approved_foreign_workspace_ids=approved_foreign_workspace_ids,
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
        owner = _exact_owner(
            item,
            context="Databricks internal catalog inventory",
        )
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
    binding_denied_objects: list[object] = []
    binding_denied_catalogs: dict[str, CatalogBindingEvidence] = {}
    foreign_objects_lock = Lock()

    def record_owner(item: object) -> None:
        with foreign_objects_lock:
            foreign_objects.append(item)

    def record_binding_denied_owner(item: object) -> None:
        with foreign_objects_lock:
            binding_denied_objects.append(item)

    def inspect_catalog(foreign_catalog: str) -> None:
        isolation_mode, catalog_object = catalog_inventory[foreign_catalog]
        catalog_owner = _exact_owner(
            catalog_object,
            context=f"UC control-plane catalog {foreign_catalog}",
        )
        evidence = _binding_evidence(
            workspace,
            catalog=foreign_catalog,
            owner=catalog_owner,
            catalog_type=_text(getattr(catalog_object, "catalog_type", None)).upper(),
            isolation_mode=isolation_mode,
            workspace_id=workspace_id,
        )
        if evidence is not None:
            expected_evidence = binding_policy.get(foreign_catalog)
            if evidence != expected_evidence:
                raise RuntimeError(
                    f"UC control-plane catalog {foreign_catalog} does not match the "
                    "reviewed binding-denial policy"
                )
            record_binding_denied_owner(catalog_object)
            with foreign_objects_lock:
                binding_denied_catalogs[foreign_catalog] = evidence
            return
        record_owner(catalog_object)
        if foreign_catalog in binding_policy:
            raise RuntimeError(
                f"UC control-plane catalog {foreign_catalog} is not binding-denied as reviewed"
            )
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
        _assert_no_catalog_child_privileges(
            workspace,
            catalog=foreign_catalog,
            catalog_type=_text(getattr(catalog_object, "catalog_type", None)),
            catalog_owner=catalog_owner,
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
        if model_catalog in binding_denied_catalogs:
            record_binding_denied_owner(model)
            continue
        record_owner(model)
        _assert_privileges(
            workspace,
            securable_type="function",
            full_name=full_name,
            principal=principal,
            expected=set(),
        )
    _assert_no_binding_denied_runtime_ownership(
        target_aliases=target.aliases,
        effective_groups=effective_target_groups,
        implicit_system_groups=implicit_system_groups,
        objects=binding_denied_objects,
    )
    _assert_no_foreign_ownership(
        workspace,
        target=target,
        objects=foreign_objects,
        account_factory=lambda: account,
        group_membership_probe=frozen_membership_probe,
    )
    if set(binding_policy) != set(binding_denied_catalogs):
        raise RuntimeError("foreign catalog binding-denial policy was not fully proven")
    return _issue_control_plane_foreign_catalog_proof(
        application_id=principal,
        catalog=mip_catalog,
        metastore_id=metastore_id,
        workspace_id=workspace_id,
        grant_audited_catalogs=frozenset(set(foreign_catalogs) - set(binding_denied_catalogs)),
        binding_denied_catalogs=tuple(
            binding_denied_catalogs[name] for name in sorted(binding_denied_catalogs)
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument(
        "--foreign-catalog-binding-policy-json",
        default=os.environ.get("MIP_UC_FOREIGN_CATALOG_BINDING_POLICY", ""),
        help=("Exact versioned JSON contract for ordinary catalogs denied by workspace binding."),
    )
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
        # Same stalled-read posture as the account client in
        # uc_owner_policy.account_client_from_env (2026-08-09).
        WorkspaceClient(http_timeout_seconds=120),
        application_id=args.application_id,
        catalog=args.catalog,
        expected_inventory_principal=args.expected_inventory_principal,
        allow_missing_mip_catalog=args.allow_missing_mip_catalog,
        foreign_catalog_binding_policy=args.foreign_catalog_binding_policy_json,
    )
    print("agent-runtime foreign UC control-plane boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
