"""Converge and prove the dedicated agent-runtime identity boundary."""

from __future__ import annotations

import argparse
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.m2m_access_policy import resolve_effective_groups

AGENT_RUNTIME_DISPLAY_NAME = "mip-agent-runtime-ci-sp"


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _items(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("Databricks ACL inventory returned a malformed collection")
    return value


def creator_values(*, application_id: str) -> frozenset[str]:
    """Return the immutable creator label accepted for this SP."""

    application_id = application_id.strip()
    if not application_id:
        raise ValueError("agent-runtime application ID is required")
    return frozenset({application_id})


def assert_runtime_creator(
    value: object,
    *,
    application_id: str,
    resource: str,
) -> str:
    """Fail closed when an immutable resource creator is not the runtime SP."""

    creator = _text(value)
    if creator not in creator_values(application_id=application_id):
        raise RuntimeError(
            f"{resource} creator {creator or '<missing>'!r} is not dedicated agent runtime "
            f"{AGENT_RUNTIME_DISPLAY_NAME!r} ({application_id})"
        )
    return creator


def assert_current_runtime_identity(workspace: Any, *, application_id: str) -> None:
    """Prove that the active SDK token belongs to the configured runtime SP."""

    me = workspace.current_user.me()
    user_name = _text(_field(me, "user_name"))
    display_name = _text(_field(me, "display_name"))
    if user_name != application_id:
        raise RuntimeError(
            "active Databricks identity is not the configured agent-runtime service principal: "
            f"user_name={user_name or '<missing>'!r}, expected={application_id!r}"
        )
    if display_name and display_name != AGENT_RUNTIME_DISPLAY_NAME:
        raise RuntimeError(
            "configured agent-runtime application ID resolves to an unexpected display name: "
            f"{display_name!r}"
        )


def _permission_levels(entry: object, *, direct_only: bool = False) -> list[str]:
    return [
        _text(_field(permission, "permission_level"))
        for permission in _items(_field(entry, "all_permissions"))
        if not direct_only or _field(permission, "inherited") is not True
    ]


def _principal_entry(permissions: object, *, application_id: str) -> object | None:
    entries = _items(_field(permissions, "access_control_list"))
    for entry in entries:
        if _text(_field(entry, "service_principal_name")) != application_id:
            continue
        return entry
    return None


def _service_principal_id(workspace: Any, *, application_id: str) -> str:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        principal
        for principal in workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if _text(_field(principal, "application_id")) == application_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one agent-runtime service principal for {application_id!r}, "
            f"found {len(matches)}"
        )
    principal_id = _text(_field(matches[0], "id"))
    if not principal_id:
        raise RuntimeError("agent-runtime service principal has no immutable SCIM id")
    return principal_id


def _assert_exact_genie_can_run(
    permissions: object,
    *,
    application_id: str,
    effective_group_names: set[str],
) -> None:
    entry = _principal_entry(permissions, application_id=application_id)
    direct = _permission_levels(entry or {}, direct_only=True)
    effective = _permission_levels(entry or {})
    if direct != ["CAN_RUN"] or set(effective) != {"CAN_RUN"}:
        raise RuntimeError(
            "exact effective CAN_RUN postflight failed for the dedicated agent-runtime "
            "identity; remove direct or inherited broader access before retrying"
        )
    entries = _items(_field(permissions, "access_control_list"))
    inherited_groups = [
        _text(_field(candidate, "group_name"))
        for candidate in entries
        if _text(_field(candidate, "group_name")) in effective_group_names
        and _permission_levels(candidate)
    ]
    if inherited_groups:
        raise RuntimeError(
            "exact effective CAN_RUN postflight failed for the dedicated agent-runtime "
            "identity through group access: " + ", ".join(sorted(inherited_groups))
        )


def _genie_spaces(workspace: Any) -> dict[str, str]:
    """Return every admin-visible Genie space ID and title across all pages."""

    spaces: dict[str, str] = {}
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        try:
            response = workspace.genie.list_spaces(page_token=page_token)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot list Genie spaces for global ACL audit: {exc}") from exc
        for space in _items(_field(response, "spaces")):
            space_id = _text(_field(space, "space_id"))
            title = _text(_field(space, "title"))
            if not space_id:
                raise RuntimeError("cannot audit Genie ACLs: a visible space has no immutable ID")
            if space_id in spaces:
                raise RuntimeError(f"cannot audit Genie ACLs: duplicate space ID {space_id!r}")
            spaces[space_id] = title
        next_token = _text(_field(response, "next_page_token")) or None
        if next_token is None:
            return spaces
        if next_token in seen_tokens:
            raise RuntimeError("cannot audit Genie ACLs: pagination token cycle detected")
        seen_tokens.add(next_token)
        page_token = next_token


def _assert_no_genie_access(
    permissions: object,
    *,
    space_id: str,
    application_id: str,
    effective_group_names: set[str],
) -> None:
    principal = _principal_entry(permissions, application_id=application_id)
    if principal is not None and _permission_levels(principal):
        raise RuntimeError(
            f"agent-runtime identity retains forbidden access to unrelated Genie space "
            f"{space_id!r}"
        )
    entries = _items(_field(permissions, "access_control_list"))
    group_access = sorted(
        {
            _text(_field(entry, "group_name"))
            for entry in entries
            if _text(_field(entry, "group_name")) in effective_group_names
            and _permission_levels(entry)
        }
    )
    if group_access:
        raise RuntimeError(
            f"agent-runtime identity retains forbidden effective access to unrelated Genie "
            f"space {space_id!r} through group(s): {', '.join(group_access)}"
        )


def audit_global_genie_access(
    workspace: Any,
    *,
    reviewed_genie_space_id: str,
    application_id: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> None:
    """Admin-side proof that runtime can access exactly one reviewed Genie space."""

    target = reviewed_genie_space_id.strip()
    principal = application_id.strip()
    if not target or not principal:
        raise ValueError("reviewed Genie space ID and agent-runtime application ID are required")
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else set(
            resolve_effective_groups(
                workspace,
                sp_id=service_principal_id
                or _service_principal_id(workspace, application_id=principal),
            ).values()
        )
    )
    spaces = _genie_spaces(workspace)
    if target not in spaces:
        raise RuntimeError(f"reviewed Genie space {target!r} is not visible to the admin audit")
    for space_id in sorted(spaces):
        path = f"/api/2.0/permissions/genie/{space_id}"
        try:
            permissions = workspace.api_client.do("GET", path)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot inspect Genie ACL for {space_id!r}: {exc}") from exc
        if space_id == target:
            _assert_exact_genie_can_run(
                permissions,
                application_id=principal,
                effective_group_names=group_names,
            )
        else:
            _assert_no_genie_access(
                permissions,
                space_id=space_id,
                application_id=principal,
                effective_group_names=group_names,
            )


def audit_global_no_genie_access(
    workspace: Any,
    *,
    application_id: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> None:
    """Admin-side proof that an identity has no access to any Genie space."""

    principal = application_id.strip()
    if not principal:
        raise ValueError("application ID is required for the global Genie denial audit")
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else set(
            resolve_effective_groups(
                workspace,
                sp_id=service_principal_id
                or _service_principal_id(workspace, application_id=principal),
            ).values()
        )
    )
    for space_id in sorted(_genie_spaces(workspace)):
        try:
            permissions = workspace.api_client.do(
                "GET", f"/api/2.0/permissions/genie/{space_id}"
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot inspect Genie ACL for {space_id!r}: {exc}") from exc
        _assert_no_genie_access(
            permissions,
            space_id=space_id,
            application_id=principal,
            effective_group_names=group_names,
        )


def grant_and_verify_genie_can_run(
    workspace: Any,
    *,
    genie_space_id: str,
    application_id: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> None:
    """Grant direct CAN_RUN and prove no broader effective or group access."""

    if not genie_space_id.strip() or not application_id.strip():
        raise ValueError("Genie space ID and agent-runtime application ID are required")
    path = f"/api/2.0/permissions/genie/{genie_space_id}"
    workspace.api_client.do(
        "PATCH",
        path,
        body={
            "access_control_list": [
                {
                    "service_principal_name": application_id,
                    "permission_level": "CAN_RUN",
                }
            ]
        },
    )
    permissions = workspace.api_client.do("GET", path)
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else set(
            resolve_effective_groups(
                workspace,
                sp_id=service_principal_id
                or _service_principal_id(workspace, application_id=application_id),
            ).values()
        )
    )
    _assert_exact_genie_can_run(
        permissions,
        application_id=application_id,
        effective_group_names=group_names,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genie-space-id", required=True)
    parser.add_argument("--application-id", required=True)
    args = parser.parse_args(argv)
    grant_and_verify_genie_can_run(
        WorkspaceClient(),
        genie_space_id=args.genie_space_id,
        application_id=args.application_id,
    )
    print(
        "[agent-runtime] verified direct CAN_RUN on Genie space "
        f"{args.genie_space_id} for {args.application_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
