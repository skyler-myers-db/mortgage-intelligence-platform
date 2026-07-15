"""Fail-closed group graph and access policy for M2M identities."""

from __future__ import annotations

import sys
from typing import Any

DOCS_RUNBOOK = "docs/security/m2m-oauth-setup.md"


def _diag(msg: str) -> None:
    print(f"[mip-m2m-provision] {msg}", file=sys.stderr)


def wrap_admin_error(exc: Exception, *, step: str) -> SystemExit:
    """Turn an SDK exception into a pointed, actionable SystemExit."""
    msg = str(exc)
    is_admin_err = any(
        token in msg.lower()
        for token in ("forbidden", "permission denied", "403", "not authorized", "unauthorized")
    )
    hint_lines = [f"[mip-m2m-provision] {step} failed: {type(exc).__name__}: {msg[:400]}"]
    if is_admin_err:
        hint_lines.append(
            "[mip-m2m-provision] this step requires workspace-admin auth; your current "
            f"profile cannot perform {step!r}."
        )
        hint_lines.append(
            f"[mip-m2m-provision] ask an admin to run this tool, or follow the manual "
            f"appendix in {DOCS_RUNBOOK}."
        )
    return SystemExit("\n".join(hint_lines))


def find_group(client: Any, display_name: str) -> Any | None:
    """Return a hydrated exact SCIM group match, never a list-page stub."""
    try:
        groups = list(client.groups.list(filter=f"displayName eq '{display_name}'"))
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="list groups") from exc
    for group in groups:
        if getattr(group, "display_name", None) == display_name:
            group_id = str(getattr(group, "id", "") or "").strip()
            if not group_id:
                raise SystemExit(f"Group {display_name!r} has no SCIM id")
            try:
                # SCIM list responses may omit members. Resolve the privilege
                # boundary from the immutable-id resource, not a sparse row.
                return client.groups.get(group_id)
            except Exception as exc:  # noqa: BLE001
                raise wrap_admin_error(exc, step="get group membership") from exc
    return None


def resolve_effective_groups(client: Any, *, sp_id: str) -> dict[str, str]:
    """Return every direct or nested group containing the service principal."""
    principal_id = str(sp_id or "").strip()
    if not principal_id:
        raise SystemExit(
            "Cannot resolve effective group memberships: service principal has no SCIM id"
        )
    try:
        summaries = list(client.groups.list(attributes="id,displayName"))
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="resolve effective group memberships") from exc

    group_members: dict[str, set[str]] = {}
    group_names: dict[str, str] = {}
    for summary in summaries:
        group_id = str(getattr(summary, "id", "") or "").strip()
        if not group_id:
            raise SystemExit(
                "Cannot resolve effective group memberships: a SCIM group has no immutable id"
            )
        try:
            group = client.groups.get(group_id)
        except Exception as exc:  # noqa: BLE001
            raise wrap_admin_error(exc, step="resolve effective group memberships") from exc
        hydrated_id = str(getattr(group, "id", "") or "").strip()
        if hydrated_id != group_id:
            raise SystemExit(
                "Cannot resolve effective group memberships: hydrated SCIM group id mismatch"
            )
        display_name = str(
            getattr(group, "display_name", None) or getattr(summary, "display_name", None) or ""
        ).strip()
        if not display_name:
            raise SystemExit(
                f"Cannot resolve effective group memberships: group {group_id!r} has no name"
            )
        members: set[str] = set()
        for member in getattr(group, "members", None) or []:
            member_id = str(getattr(member, "value", "") or "").strip()
            if not member_id:
                raise SystemExit(
                    "Cannot resolve effective group memberships: a SCIM member has no immutable id"
                )
            members.add(member_id)
        group_names[group_id] = display_name
        group_members[group_id] = members

    effective_ids: set[str] = set()
    reachable_members = {principal_id}
    while True:
        parents = {
            group_id
            for group_id, member_ids in group_members.items()
            if group_id not in effective_ids and member_ids.intersection(reachable_members)
        }
        if not parents:
            break
        effective_ids.update(parents)
        reachable_members.update(parents)
    return {group_id: group_names[group_id] for group_id in effective_ids}


def ensure_group_membership(
    client: Any,
    *,
    group_name: str,
    sp_id: str,
    create_group: bool,
) -> bool:
    """Ensure the SP is a direct group member; return True only on mutation."""
    group = find_group(client, group_name)
    if group is None:
        if not create_group:
            raise SystemExit(
                f"Required admin group {group_name!r} does not exist. "
                "Re-run with --create-group only after governance review."
            )
        _diag(f"creating group display_name={group_name!r} (--create-group)")
        try:
            group = client.groups.create(display_name=group_name)
        except Exception as exc:  # noqa: BLE001
            raise wrap_admin_error(exc, step="create group") from exc

    group_id = str(getattr(group, "id", "") or "").strip()
    if not group_id:
        raise SystemExit(f"Group {group_name!r} has no SCIM id")
    members = getattr(group, "members", None) or []
    if any(str(getattr(member, "value", "") or "") == sp_id for member in members):
        _diag(f"service principal is already a member of group={group_name!r}")
        return False

    from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema

    _diag(f"adding service principal id={sp_id} to group={group_name!r}")
    try:
        client.groups.patch(
            id=group_id,
            operations=[
                Patch(
                    op=PatchOp.ADD,
                    # Patch.value is typed as Any. Keep the SCIM member
                    # payload JSON-native so Patch.as_dict() serializes it.
                    value={"members": [{"value": sp_id}]},
                )
            ],
            schemas=[PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP],
        )
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="add service_principal to group") from exc
    return True


def assert_not_admin_group_member(
    *,
    group_name: str,
    effective_groups: dict[str, str],
    identity_role: str,
) -> None:
    """Fail closed on direct or nested membership in the admin group."""
    if group_name in effective_groups.values():
        raise SystemExit(
            f"{identity_role} service principal has direct or nested membership in forbidden "
            f"admin group {group_name!r}; remove that access before provisioning"
        )


def assert_no_app_permission(
    client: Any,
    *,
    app_name: str,
    sp_application_id: str,
    sp_display_name: str,
    effective_group_names: set[str],
) -> None:
    """Fail closed on any direct, inherited, or group-derived App access."""
    try:
        permissions = client.apps.get_permissions(app_name)
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="inspect app permissions") from exc
    for entry in getattr(permissions, "access_control_list", None) or []:
        service_principal_name = str(getattr(entry, "service_principal_name", "") or "").strip()
        entry_display_name = str(getattr(entry, "display_name", "") or "").strip()
        group_name = str(getattr(entry, "group_name", "") or "").strip()
        if service_principal_name in {sp_application_id, sp_display_name} or (
            not service_principal_name and not group_name and entry_display_name == sp_display_name
        ):
            raise SystemExit(
                "verifier service principal retains forbidden direct Databricks App "
                f"permission on {app_name!r}, including inherited/effective permissions; "
                "remove it before provisioning"
            )
        if group_name and group_name in effective_group_names:
            raise SystemExit(
                "verifier service principal retains forbidden effective Databricks App "
                f"permission on {app_name!r} through group {group_name!r}; remove the group "
                "grant or membership before provisioning"
            )
