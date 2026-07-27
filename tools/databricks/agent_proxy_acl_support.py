"""Shared exact-ACL and managed-group checks for the agent-proxy boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tools.databricks.agent_proxy_capability_group_access import (
    MANAGED_AGENT_PROXY_GROUP_PREFIX,
    ManagedAgentProxyResourceKind,
    inspect_managed_agent_proxy_group,
    managed_agent_proxy_groups_for_application,
    wait_for_managed_agent_proxy_group_projection,
)
from tools.databricks.legacy_permissions_acl_cleanup import (
    replace_direct_acl_without_principal,
)


def field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def items(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("Supervisor Agent ACL inventory is malformed")
    return value


def levels(entry: object, *, direct_only: bool = False) -> set[str]:
    return {
        text(field(permission, "permission_level")).upper()
        for permission in items(field(entry, "all_permissions"))
        if not direct_only or field(permission, "inherited") is not True
    }


def principal_entries(
    permissions: object,
    application_id: str,
) -> list[object]:
    return [
        entry
        for entry in items(field(permissions, "access_control_list"))
        if text(field(entry, "service_principal_name")) == application_id
    ]


def principal_entry(
    permissions: object,
    application_id: str,
) -> object | None:
    matches = principal_entries(permissions, application_id)
    if len(matches) > 1:
        raise RuntimeError(
            "ACL contains duplicate entries for the agent-proxy service principal"
        )
    return matches[0] if matches else None


def group_entry(permissions: object, group_name: str) -> object | None:
    matches = [
        entry
        for entry in items(field(permissions, "access_control_list"))
        if text(field(entry, "group_name")) == group_name
    ]
    if len(matches) > 1:
        raise RuntimeError(
            "ACL contains duplicate entries for a managed capability group"
        )
    return matches[0] if matches else None


def migrate_legacy_direct_acl_if_unmanaged(
    workspace: Any,
    *,
    path: str,
    permissions: object,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    """Remove a predecessor direct grant only before its managed group exists."""

    direct = levels(
        principal_entry(permissions, application_id) or {},
        direct_only=True,
    )
    if not direct:
        return
    state = inspect_managed_agent_proxy_group(
        workspace,
        resource_kind=resource_kind,
        resource_id=resource_id,
        application_id=application_id,
        missing_ok=True,
    )
    if state is not None:
        raise RuntimeError(
            "unexpected direct agent-proxy ACL exists after managed-group migration"
        )
    replace_direct_acl_without_principal(
        workspace,
        path=path,
        permissions=permissions,
        application_id=application_id,
        assert_single_writer=assert_single_writer,
        assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
    )


def assert_managed_capability_acl(
    permissions: object,
    *,
    application_id: str,
    effective_group_names: set[str],
    managed_group_name: str,
    expect_active: bool,
    expected_level: str,
    resource: str,
) -> None:
    entry = principal_entry(permissions, application_id)
    direct = levels(entry or {}, direct_only=True)
    effective = levels(entry or {})
    if direct or effective:
        raise RuntimeError(
            f"agent-proxy {resource} retains a legacy direct service-principal ACL"
        )
    acl_entries = items(field(permissions, "access_control_list"))
    reserved_group_names = {
        text(field(candidate, "group_name"))
        for candidate in acl_entries
        if text(field(candidate, "group_name")).startswith(
            MANAGED_AGENT_PROXY_GROUP_PREFIX
        )
    }
    if reserved_group_names.difference({managed_group_name}):
        raise RuntimeError(
            f"agent-proxy {resource} has an unrelated reserved capability-group ACL"
        )
    active_groups = {
        text(field(candidate, "group_name"))
        for candidate in acl_entries
        if text(field(candidate, "group_name")) in effective_group_names
        and levels(candidate)
    }
    expected_groups = {managed_group_name} if expect_active else set()
    if active_groups != expected_groups:
        raise RuntimeError(
            f"agent-proxy {resource} effective managed-group boundary drifted"
        )
    managed_entry = group_entry(permissions, managed_group_name)
    if managed_entry is None:
        if expect_active:
            raise RuntimeError(
                f"agent-proxy {resource} managed-group ACL is missing"
            )
        return
    if (
        levels(managed_entry, direct_only=True) != {expected_level}
        or levels(managed_entry) != {expected_level}
    ):
        raise RuntimeError(
            f"agent-proxy {resource} managed-group ACL postflight failed"
        )


def wait_exact_capability_projection(
    workspace: Any,
    *,
    application_id: str,
    service_principal_id: str,
) -> set[str]:
    states = managed_agent_proxy_groups_for_application(
        workspace,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    active: set[str] = set()
    for state in states:
        members = set(state.member_ids)
        if members not in (set(), {service_principal_id}):
            raise RuntimeError(
                "managed agent-proxy group contains an unrelated member"
            )
        if members:
            active.add(state.contract.name)
    return wait_for_managed_agent_proxy_group_projection(
        workspace,
        application_id=application_id,
        service_principal_id=service_principal_id,
        expected_active_group_names=active,
    )
