"""Exact Unity Catalog state helpers for foreign-catalog remediation."""

from __future__ import annotations

from typing import Any, cast

from databricks.sdk.errors import PermissionDenied
from databricks.sdk.service.catalog import (
    WorkspaceBinding,
    WorkspaceBindingBindingType,
)
from tools.databricks.agent_runtime_uc_inventory import _text

_BINDING_TYPES = {
    item.value: item
    for item in (
        WorkspaceBindingBindingType.BINDING_TYPE_READ_ONLY,
        WorkspaceBindingBindingType.BINDING_TYPE_READ_WRITE,
    )
}


def bindings(workspace: Any, catalog: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in workspace.workspace_bindings.get_bindings("catalog", catalog):
        workspace_id = _text(getattr(item, "workspace_id", None))
        binding_type = _text(getattr(item, "binding_type", None)).upper()
        if (
            not workspace_id
            or not workspace_id.isdecimal()
            or binding_type not in _BINDING_TYPES
            or workspace_id in seen
        ):
            raise RuntimeError(f"catalog {catalog} returned invalid workspace bindings")
        seen.add(workspace_id)
        result.append({"workspace_id": workspace_id, "binding_type": binding_type})
    return sorted(result, key=lambda item: (item["workspace_id"], item["binding_type"]))


def direct_grants(workspace: Any, catalog: str) -> list[dict[str, object]]:
    token: str | None = None
    seen_tokens: set[str] = set()
    assignments: dict[str, set[str]] = {}
    while True:
        try:
            page = workspace.grants.get(
                "catalog",
                catalog,
                max_results=1000,
                page_token=token,
            )
        except PermissionDenied:
            if token is not None:
                raise RuntimeError(
                    f"catalog {catalog} direct grants became inaccessible during pagination"
                ) from None
            raise
        for assignment in getattr(page, "privilege_assignments", None) or []:
            principal = _text(getattr(assignment, "principal", None))
            privileges = {
                _text(item).upper() for item in (getattr(assignment, "privileges", None) or [])
            }
            if not principal or "" in privileges or not privileges:
                raise RuntimeError(f"catalog {catalog} returned an incomplete direct grant")
            assignments.setdefault(principal, set()).update(privileges)
        next_token = _text(getattr(page, "next_page_token", None))
        if not next_token:
            break
        if next_token in seen_tokens:
            raise RuntimeError(f"catalog {catalog} repeated a direct-grant page token")
        seen_tokens.add(next_token)
        token = next_token
    return [
        {"principal": principal, "privileges": sorted(privileges)}
        for principal, privileges in sorted(assignments.items())
    ]


def snapshot(
    workspace: Any,
    catalog: str,
    *,
    mip_workspace_id: str,
) -> dict[str, object]:
    matches = [
        item
        for item in workspace.catalogs.list(
            include_browse=True,
            include_unbound=True,
        )
        if _text(getattr(item, "name", None)) == catalog
    ]
    if len(matches) != 1:
        raise RuntimeError(f"catalog {catalog} did not resolve exactly once in unbound inventory")
    item = matches[0]
    name = _text(getattr(item, "name", None))
    owner = getattr(item, "owner", None)
    catalog_type = _text(getattr(item, "catalog_type", None)).upper()
    isolation_mode = _text(getattr(item, "isolation_mode", None)).upper()
    if (
        name != catalog
        or not isinstance(owner, str)
        or not owner
        or owner != owner.strip()
        or not catalog_type
        or isolation_mode not in {"OPEN", "ISOLATED"}
    ):
        raise RuntimeError(f"catalog {catalog} returned incomplete metadata")
    current_bindings = bindings(workspace, catalog)
    try:
        grants: list[dict[str, object]] | None = direct_grants(workspace, catalog)
    except PermissionDenied:
        bound_workspace_ids = {binding["workspace_id"] for binding in current_bindings}
        if isolation_mode != "ISOLATED" or mip_workspace_id in bound_workspace_ids:
            raise RuntimeError(
                f"catalog {catalog} direct grants were unexpectedly inaccessible"
            ) from None
        grants = None
    return {
        "catalog": name,
        "owner": owner,
        "catalog_type": catalog_type,
        "isolation_mode": isolation_mode,
        "bindings": current_bindings,
        "direct_grants": grants,
    }


def policy_payload(policy: dict[str, Any]) -> list[dict[str, object]]:
    return [
        {
            "catalog": item.catalog,
            "owner": item.owner,
            "catalog_type": item.catalog_type,
            "isolation_mode": item.isolation_mode,
            "bindings": [
                {"workspace_id": workspace_id, "binding_type": binding_type}
                for workspace_id, binding_type in item.bindings
            ],
        }
        for item in (policy[name] for name in sorted(policy))
    ]


def desired_bindings(policy_item: Any) -> list[dict[str, str]]:
    return [
        {"workspace_id": workspace_id, "binding_type": binding_type}
        for workspace_id, binding_type in policy_item.bindings
    ]


def _binding_objects(values: list[dict[str, str]]) -> list[WorkspaceBinding]:
    return [
        WorkspaceBinding(
            workspace_id=int(item["workspace_id"]),
            binding_type=_BINDING_TYPES[item["binding_type"]],
        )
        for item in values
    ]


def converge_bindings(
    workspace: Any,
    *,
    catalog: str,
    current: list[dict[str, str]],
    desired: list[dict[str, str]],
) -> None:
    current_by_id = {item["workspace_id"]: item for item in current}
    desired_by_id = {item["workspace_id"]: item for item in desired}
    remove = [
        item
        for workspace_id, item in current_by_id.items()
        if desired_by_id.get(workspace_id) != item
    ]
    add = [
        item
        for workspace_id, item in desired_by_id.items()
        if current_by_id.get(workspace_id) != item
    ]
    if add or remove:
        workspace.workspace_bindings.update_bindings(
            "catalog",
            catalog,
            add=_binding_objects(add),
            remove=_binding_objects(remove),
        )


def desired_snapshot(pre: dict[str, object], expected: Any) -> dict[str, object]:
    return {
        **pre,
        "isolation_mode": "ISOLATED",
        "bindings": desired_bindings(expected),
        "direct_grants": None,
    }


def state_kind(
    current: dict[str, object],
    *,
    pre: dict[str, object],
    desired: dict[str, object],
) -> str:
    stable = {"catalog", "owner", "catalog_type"}
    if any(current[field] != pre[field] for field in stable):
        raise RuntimeError(f"catalog {pre['catalog']} stable governance evidence drifted")
    if current == pre:
        return "prestate"
    if current == desired:
        return "desired"
    desired_values = cast(list[dict[str, str]], desired["bindings"])
    current_values = cast(list[dict[str, str]], current["bindings"])
    desired_bindings_set = {(item["workspace_id"], item["binding_type"]) for item in desired_values}
    current_bindings_set = {(item["workspace_id"], item["binding_type"]) for item in current_values}
    if (
        pre["isolation_mode"] == "OPEN"
        and current["isolation_mode"] == "ISOLATED"
        and current["direct_grants"] is None
        and current_bindings_set.issubset(desired_bindings_set)
    ):
        return "transitional"
    raise RuntimeError(f"catalog {pre['catalog']} is outside the signed recovery states")
