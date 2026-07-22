#!/usr/bin/env python3
"""Converge the Databricks App ACL across release lifecycle phases."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Literal

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import AppAccessControlRequest, AppPermissionLevel

Mode = Literal["quarantine", "probe", "runtime"]
Principal = tuple[Literal["group_name", "service_principal_name", "user_name"], str]

_IDENTITY_FIELDS = ("group_name", "service_principal_name", "user_name")
_SUPPORTED_LEVELS = {"CAN_MANAGE", "CAN_USE"}


@dataclass(frozen=True)
class _AclState:
    direct: dict[Principal, str]
    inherited_managers: frozenset[Principal]


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _items(value: object, *, label: str, allow_none: bool = False) -> list[object]:
    if value is None and allow_none:
        return []
    if not isinstance(value, list | tuple):
        raise RuntimeError(f"Databricks App ACL returned a malformed {label}")
    return list(value)


def _principal(entry: object) -> Principal:
    identities = [
        (field, _text(_field(entry, field)))
        for field in _IDENTITY_FIELDS
        if _text(_field(entry, field))
    ]
    if len(identities) != 1:
        raise RuntimeError("Databricks App ACL contains an ambiguous principal")
    field, value = identities[0]
    return field, value  # type: ignore[return-value]


def _inspect_acl(permissions: object) -> _AclState:
    direct: dict[Principal, str] = {}
    inherited_managers: set[Principal] = set()
    entries = _items(
        _field(permissions, "access_control_list"),
        label="access-control list",
        allow_none=True,
    )
    for entry in entries:
        principal = _principal(entry)
        levels = _items(_field(entry, "all_permissions"), label="permission list")
        if not levels:
            raise RuntimeError("Databricks App ACL contains a principal without permissions")
        direct_levels: set[str] = set()
        for permission in levels:
            level = _text(_field(permission, "permission_level"))
            if level not in _SUPPORTED_LEVELS:
                raise RuntimeError(
                    f"Databricks App ACL contains unexpected permission {level or '<missing>'!r}"
                )
            if _field(permission, "inherited") is True:
                if level != "CAN_MANAGE":
                    raise RuntimeError("Databricks App ACL contains inherited non-manager access")
                inherited_managers.add(principal)
            else:
                direct_levels.add(level)
        if len(direct_levels) > 1:
            raise RuntimeError(
                "Databricks App ACL contains conflicting direct permissions for one principal"
            )
        if direct_levels:
            level = next(iter(direct_levels))
            if principal in direct:
                raise RuntimeError("Databricks App ACL contains a duplicate direct principal")
            direct[principal] = level
    return _AclState(
        direct=direct,
        inherited_managers=frozenset(inherited_managers),
    )


def _validated_inputs(
    *,
    app_name: str,
    mode: Mode,
    release_probe_application_id: str,
    normal_application_id: str,
    operator2_application_id: str,
    admin_application_id: str,
) -> tuple[str, dict[str, str]]:
    app = str(app_name or "").strip()
    if not app:
        raise ValueError("app name is required")
    if mode not in {"quarantine", "probe", "runtime"}:
        raise ValueError("mode must be quarantine, probe, or runtime")
    identities = {
        "release-probe": str(release_probe_application_id or "").strip(),
        "normal": str(normal_application_id or "").strip(),
        "operator2": str(operator2_application_id or "").strip(),
        "admin": str(admin_application_id or "").strip(),
    }
    blank_roles = [role for role, application_id in identities.items() if not application_id]
    if blank_roles:
        raise ValueError("application IDs must be non-blank: " + ", ".join(blank_roles))
    canonical = [application_id.casefold() for application_id in identities.values()]
    if len(set(canonical)) != len(canonical):
        raise ValueError(
            "release-probe, normal, operator2, and admin application IDs must be pairwise distinct"
        )
    return app, identities


def _requested_can_use(mode: Mode, identities: dict[str, str]) -> tuple[str, ...]:
    if mode == "quarantine":
        return ()
    if mode == "probe":
        return (identities["release-probe"],)
    return (
        identities["normal"],
        identities["operator2"],
        identities["admin"],
    )


def _assert_app_identity(
    workspace: Any,
    *,
    app_name: str,
    expected_app_id: str,
    expected_client_id: str,
    expected_scim_id: str,
) -> None:
    expected = (
        expected_app_id.strip(),
        expected_client_id.strip(),
        expected_scim_id.strip(),
    )
    if any(expected) != all(expected):
        raise ValueError("expected App identity must provide ID, client ID, and SCIM ID together")
    if not all(expected):
        return
    app = workspace.apps.get(app_name)
    actual = (
        _text(_field(app, "id")),
        _text(_field(app, "service_principal_client_id")),
        _text(_field(app, "service_principal_id")),
    )
    if actual != expected:
        raise RuntimeError("Databricks App identity changed during release ACL convergence")


def _request(principal: Principal, level: AppPermissionLevel) -> AppAccessControlRequest:
    field, value = principal
    return AppAccessControlRequest(permission_level=level, **{field: value})


def _assert_postflight(
    permissions: object,
    *,
    direct_managers: frozenset[Principal],
    inherited_managers: frozenset[Principal],
    can_use_application_ids: tuple[str, ...],
) -> None:
    observed = _inspect_acl(permissions)
    expected_direct = {
        **{principal: "CAN_MANAGE" for principal in direct_managers},
        **{
            ("service_principal_name", application_id): "CAN_USE"
            for application_id in can_use_application_ids
        },
    }
    if observed.direct != expected_direct:
        raise RuntimeError(
            "Databricks App ACL postflight did not match the exact direct release access"
        )
    if observed.inherited_managers != inherited_managers:
        raise RuntimeError("Databricks App ACL postflight changed the inherited manager boundary")


def converge_app_release_access(
    workspace: Any,
    *,
    app_name: str,
    mode: Mode,
    release_probe_application_id: str,
    normal_application_id: str,
    operator2_application_id: str,
    admin_application_id: str,
    expected_app_id: str = "",
    expected_client_id: str = "",
    expected_scim_id: str = "",
) -> None:
    """Replace direct App access with the exact ACL for one release phase."""

    app, identities = _validated_inputs(
        app_name=app_name,
        mode=mode,
        release_probe_application_id=release_probe_application_id,
        normal_application_id=normal_application_id,
        operator2_application_id=operator2_application_id,
        admin_application_id=admin_application_id,
    )
    desired_can_use = _requested_can_use(mode, identities)
    identity_args = {
        "expected_app_id": expected_app_id,
        "expected_client_id": expected_client_id,
        "expected_scim_id": expected_scim_id,
    }
    # Databricks Apps v2 exposes ACL mutation only by App name, without an
    # immutable-ID or conditional-version parameter. App managers are therefore
    # a trusted control-plane boundary; exact pre/post checks make any breach
    # fail rather than allowing the release to claim convergence.
    _assert_app_identity(workspace, app_name=app, **identity_args)
    current = _inspect_acl(workspace.apps.get_permissions(app))
    direct_managers = frozenset(
        principal for principal, level in current.direct.items() if level == "CAN_MANAGE"
    )
    lifecycle_manager_overlap = (direct_managers | current.inherited_managers).intersection(
        {("service_principal_name", application_id) for application_id in identities.values()}
    )
    if lifecycle_manager_overlap:
        raise RuntimeError("release lifecycle identity overlaps an App manager")

    requests = [
        _request(principal, AppPermissionLevel.CAN_MANAGE) for principal in sorted(direct_managers)
    ]
    requests.extend(
        _request(("service_principal_name", application_id), AppPermissionLevel.CAN_USE)
        for application_id in desired_can_use
    )
    workspace.apps.set_permissions(app_name=app, access_control_list=requests)
    _assert_app_identity(workspace, app_name=app, **identity_args)
    postflight = workspace.apps.get_permissions(app)
    _assert_postflight(
        postflight,
        direct_managers=direct_managers,
        inherited_managers=current.inherited_managers,
        can_use_application_ids=desired_can_use,
    )
    print(f"[mip-app-access] exact {mode} ACL verified for app {app!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("quarantine", "probe", "runtime"), required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument(
        "--release-probe-application-id",
        "--release-probe-client-id",
        dest="release_probe_application_id",
        required=True,
    )
    for role in ("normal", "operator2", "admin"):
        parser.add_argument(
            f"--{role}-application-id",
            f"--{role}-client-id",
            dest=f"{role}_application_id",
            required=True,
        )
    parser.add_argument("--expected-app-id", default="")
    parser.add_argument("--expected-client-id", default="")
    parser.add_argument("--expected-scim-id", default="")
    args = parser.parse_args(argv)
    converge_app_release_access(
        WorkspaceClient(),
        app_name=args.app_name,
        mode=args.mode,
        release_probe_application_id=args.release_probe_application_id,
        normal_application_id=args.normal_application_id,
        operator2_application_id=args.operator2_application_id,
        admin_application_id=args.admin_application_id,
        expected_app_id=args.expected_app_id,
        expected_client_id=args.expected_client_id,
        expected_scim_id=args.expected_scim_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
