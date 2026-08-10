"""Fail-closed evidence for Databricks-managed workspace system groups."""

from __future__ import annotations

import re
from typing import Any

from tools.databricks.agent_runtime_uc_inventory import _text

_WORKSPACE_USERS_GROUP = "users"
_LEGACY_WORKSPACE_USERS_ENTITLEMENTS = frozenset(
    {"workspace-access", "databricks-sql-access"}
)
# Enabling automatic identity management splits the legacy workspace ``users``
# group: ``users`` itself becomes federated and entitlement-free, and Databricks
# creates a clone under this exact generated name that carries the legacy
# entitlements forward (observed on this workspace 2026-08-03). The clone is the
# same system identity, so the runtime inherits it without any grant of ours —
# but it is only accepted with proof, below, that its membership is identical to
# the ``users`` group it was cloned from. Any other group, however named, stays
# an ordinary membership and still fails the runtime boundary closed.
_WORKSPACE_USERS_CLONE_RE = re.compile(
    r"^users-clone-\d{4}-\d{2}-\d{2}-\d{4}-UTC \(created by Databricks\)$"
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


def _exact_member_ids(workspace: Any, group_id: str, *, label: str) -> frozenset[str]:
    """Return one group's exact SCIM member ids, or fail closed."""

    group = workspace.groups.get(group_id)
    members = getattr(group, "members", None)
    if members is None:
        return frozenset()
    if not isinstance(members, list):
        raise RuntimeError(f"workspace {label} group membership is malformed")
    normalized: list[str] = []
    for item in members:
        raw = getattr(item, "value", None)
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise RuntimeError(f"workspace {label} group membership is malformed")
        normalized.append(raw)
    if len(set(normalized)) != len(normalized):
        raise RuntimeError(f"workspace {label} group membership is ambiguous")
    return frozenset(normalized)


def _scan_users_groups(workspace: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Return the exact ``users`` system group and any proven clone candidate."""

    matches: dict[str, str] = {}
    clone_candidates: dict[str, str] = {}
    for item in workspace.groups.list(
        attributes="id,displayName,meta,entitlements,roles,externalId"
    ):
        raw_display_name = getattr(item, "display_name", None)
        display_name = _text(raw_display_name)
        is_clone = bool(_WORKSPACE_USERS_CLONE_RE.fullmatch(display_name))
        if display_name.casefold() != _WORKSPACE_USERS_GROUP and not is_clone:
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
            or display_name != (raw_display_name if is_clone else _WORKSPACE_USERS_GROUP)
            or resource_type != "WorkspaceGroup"
            or getattr(item, "external_id", None) is not None
            or roles
            or entitlements not in (frozenset(), _LEGACY_WORKSPACE_USERS_ENTITLEMENTS)
            or (clone_candidates if is_clone else matches)
        ):
            raise RuntimeError(
                "workspace users system group identity is incomplete or ambiguous"
            )
        if is_clone:
            clone_candidates[group_id] = display_name
        else:
            matches[group_id] = display_name
    if len(matches) != 1:
        raise RuntimeError(
            "workspace users system group identity did not resolve exactly once"
        )
    return matches, clone_candidates


def workspace_users_group_evidence(workspace: Any) -> dict[str, str]:
    """Resolve the immutable current-workspace ``users`` system group."""

    matches, _clone_candidates = _scan_users_groups(workspace)
    return matches


def workspace_users_clone_group_evidence(workspace: Any) -> dict[str, str]:
    """Resolve the Databricks-created ``users`` clone, proven by membership.

    Empty when the workspace never went through the split. The clone is
    surfaced as a *reviewed* workspace group rather than a system group: the
    reviewed path additionally proves the group holds exactly ``USER`` on this
    workspace and nothing on any other workspace in the metastore, which is
    the stronger contract for a group that carries real entitlements.
    """

    matches, clone_candidates = _scan_users_groups(workspace)
    if not clone_candidates:
        return {}
    users_members = _exact_member_ids(workspace, next(iter(matches)), label="users")
    clone_group_id = next(iter(clone_candidates))
    clone_members = _exact_member_ids(workspace, clone_group_id, label="users clone")
    if not users_members or clone_members != users_members:
        raise RuntimeError(
            "workspace users clone group is not an exact clone of the users group"
        )
    return dict(clone_candidates)
