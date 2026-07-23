"""Fail-closed evidence for Databricks-managed workspace system groups."""

from __future__ import annotations

from typing import Any

from tools.databricks.agent_runtime_uc_inventory import _text

_WORKSPACE_USERS_GROUP = "users"
_LEGACY_WORKSPACE_USERS_ENTITLEMENTS = frozenset(
    {"workspace-access", "databricks-sql-access"}
)


def _exact_scim_complex_values(value: object, *, label: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise RuntimeError(f"workspace users system group {label} are malformed")
    normalized: list[str] = []
    for item in value:
        raw = getattr(item, "value", None)
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise RuntimeError(f"workspace users system group {label} are malformed")
        normalized.append(raw)
    if len(set(normalized)) != len(normalized):
        raise RuntimeError(f"workspace users system group {label} are ambiguous")
    return frozenset(normalized)


def workspace_users_group_evidence(workspace: Any) -> dict[str, str]:
    """Resolve the immutable current-workspace ``users`` system group."""

    matches: dict[str, str] = {}
    for item in workspace.groups.list(
        attributes="id,displayName,meta,entitlements,roles,externalId"
    ):
        raw_display_name = getattr(item, "display_name", None)
        display_name = _text(raw_display_name)
        if display_name.casefold() != _WORKSPACE_USERS_GROUP:
            continue
        raw_group_id = getattr(item, "id", None)
        group_id = _text(raw_group_id)
        meta = getattr(item, "meta", None)
        raw_resource_type = getattr(meta, "resource_type", None)
        resource_type = _text(raw_resource_type)
        roles = _exact_scim_complex_values(
            getattr(item, "roles", None),
            label="roles",
        )
        entitlements = _exact_scim_complex_values(
            getattr(item, "entitlements", None),
            label="entitlements",
        )
        if (
            not isinstance(raw_group_id, str)
            or raw_group_id != group_id
            or not isinstance(raw_display_name, str)
            or raw_display_name != display_name
            or not isinstance(raw_resource_type, str)
            or raw_resource_type != resource_type
            or not group_id
            or display_name != _WORKSPACE_USERS_GROUP
            or resource_type != "WorkspaceGroup"
            or getattr(item, "external_id", None) is not None
            or roles
            or entitlements not in (frozenset(), _LEGACY_WORKSPACE_USERS_ENTITLEMENTS)
            or matches
        ):
            raise RuntimeError(
                "workspace users system group identity is incomplete or ambiguous"
            )
        matches[group_id] = display_name
    if len(matches) != 1:
        raise RuntimeError(
            "workspace users system group identity did not resolve exactly once"
        )
    return matches
