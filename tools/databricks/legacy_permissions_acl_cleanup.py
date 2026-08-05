"""Fenced one-time cleanup for legacy direct Databricks permission entries."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

_MAX_DIRECT_ACL_ENTRIES = 1000
_PRINCIPAL_FIELDS = ("group_name", "service_principal_name", "user_name")


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _items(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("permissions ACL inventory is malformed")
    return value


def stopped_deployment_app_assertion(workspace: Any) -> Callable[[], None]:
    """Return a lazy exact-App STOPPED assertion for one-time ACL migration."""

    def assert_stopped() -> None:
        app_name = os.environ.get("MIP_APP_NAME", "").strip()
        expected = (
            os.environ.get("MIP_DEPLOYMENT_APP_OBJECT_ID", "").strip(),
            os.environ.get("MIP_DEPLOYMENT_APP_APPLICATION_ID", "").strip(),
            os.environ.get("MIP_DEPLOYMENT_APP_SCIM_ID", "").strip(),
        )
        if not app_name or not all(expected):
            raise RuntimeError(
                "legacy ACL cleanup requires the exact deployment App identity"
            )
        app = workspace.apps.get(app_name)
        observed = (
            _text(_field(app, "id")),
            _text(_field(app, "service_principal_client_id")),
            _text(_field(app, "service_principal_id")),
        )
        if observed != expected:
            raise RuntimeError(
                "deployment App identity drifted before legacy ACL cleanup"
            )
        state = _text(_field(_field(app, "compute_status") or {}, "state"))
        state = state.split(".")[-1].upper()
        if state != "STOPPED" or _field(app, "pending_deployment") is not None:
            raise RuntimeError(
                "deployment App must be STOPPED without a pending deployment "
                "before legacy ACL cleanup"
            )

    return assert_stopped


def _levels(entry: object) -> set[str]:
    return {
        _text(_field(permission, "permission_level")).upper()
        for permission in _items(_field(entry, "all_permissions"))
        if _field(permission, "inherited") is not True
    }


def _direct_acl_snapshot(
    permissions: object,
) -> tuple[tuple[str, str, str], ...]:
    """Return one exact, bounded direct ACL entry per named principal."""

    entries = _items(_field(permissions, "access_control_list"))
    if len(entries) > _MAX_DIRECT_ACL_ENTRIES:
        raise RuntimeError("ACL direct-entry inventory is unbounded")
    direct: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        levels = sorted(_levels(entry))
        if not levels:
            continue
        principals = tuple(
            (field, _text(_field(entry, field)))
            for field in _PRINCIPAL_FIELDS
            if _text(_field(entry, field))
        )
        if len(principals) != 1 or len(levels) != 1:
            raise RuntimeError("ACL direct entry is incomplete or ambiguous")
        field, principal = principals[0]
        identity = (field, principal.casefold())
        if identity in seen:
            raise RuntimeError("ACL contains duplicate direct principal entries")
        seen.add(identity)
        direct.append((field, principal, levels[0]))
    return tuple(sorted(direct, key=lambda item: (item[0], item[1].casefold(), item[2])))


def replace_direct_acl_without_principal(
    workspace: Any,
    *,
    path: str,
    permissions: object,
    application_id: str,
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    """Remove one legacy direct SP ACL through the provider replacement API.

    This helper is exclusively for one-time migration of historical direct
    entries. Normal access and compensation must use atomic managed-group
    membership instead.
    """

    before = _direct_acl_snapshot(permissions)
    preserved = tuple(
        entry
        for entry in before
        if not (
            entry[0] == "service_principal_name"
            and entry[1] == application_id
        )
    )
    if preserved == before:
        return
    assert_legacy_cleanup_quiesced()
    refreshed = _direct_acl_snapshot(workspace.api_client.do("GET", path))
    if refreshed != before:
        raise RuntimeError("ACL changed before legacy principal cleanup")
    assert_single_writer()
    latest = _direct_acl_snapshot(workspace.api_client.do("GET", path))
    if latest != before:
        raise RuntimeError("ACL changed at the signed legacy cleanup boundary")
    # Remote reads can outlive either invariant; re-assert both at mutation.
    assert_legacy_cleanup_quiesced()
    assert_single_writer()
    workspace.api_client.do(
        "PUT",
        path,
        body={
            "access_control_list": [
                {
                    field: principal,
                    "permission_level": permission_level,
                }
                for field, principal, permission_level in preserved
            ]
        },
    )
    after = workspace.api_client.do("GET", path)
    if _direct_acl_snapshot(after) != preserved:
        raise RuntimeError(
            "ACL principal omission did not preserve the exact direct boundary"
        )
