"""Fail-closed group graph and access policy for M2M identities."""

from __future__ import annotations

import sys
from collections.abc import Iterable
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
    """Return workspace-visible direct and nested groups containing the principal.

    Databricks automatic identity management can make some account-level nested
    memberships effective without exposing them through workspace SCIM. Callers
    must therefore pair this useful preflight with verifier-credential negative
    authorization probes before making an authoritative least-privilege claim.
    """
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
                f"Required identity group {group_name!r} does not exist. "
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


def assert_non_admin_service_principal(
    client: Any,
    *,
    sp_id: str,
    effective_groups: dict[str, str],
    identity_role: str,
) -> None:
    """Reject visible built-in admin membership, roles, and powerful entitlements."""
    try:
        principal = client.service_principals.get(sp_id)
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="inspect service principal roles") from exc
    hydrated_id = str(getattr(principal, "id", "") or "").strip()
    if hydrated_id != sp_id:
        raise SystemExit("Cannot inspect service principal roles: hydrated SCIM id mismatch")

    forbidden_groups = {"admins", "account admins", "workspace admins", "metastore admins"}
    visible = {name.casefold() for name in effective_groups.values()}
    matched_groups = sorted(forbidden_groups.intersection(visible))
    if matched_groups:
        raise SystemExit(
            f"{identity_role} service principal has forbidden built-in administrator group "
            f"membership: {', '.join(matched_groups)}"
        )

    def _values(items: Iterable[Any] | None) -> set[str]:
        return {
            str(getattr(item, "value", None) or getattr(item, "display", None) or item or "")
            .strip()
            .casefold()
            for item in (items or [])
            if str(
                getattr(item, "value", None) or getattr(item, "display", None) or item or ""
            ).strip()
        }

    roles = _values(getattr(principal, "roles", None))
    forbidden_roles = {
        role for role in roles if any(token in role for token in ("admin", "manager", "metastore"))
    }
    if forbidden_roles:
        raise SystemExit(
            f"{identity_role} service principal has forbidden administrative role(s): "
            f"{', '.join(sorted(forbidden_roles))}"
        )

    entitlements = _values(getattr(principal, "entitlements", None))
    powerful_entitlements = {
        "allow-cluster-create",
        "allow-instance-pool-create",
    }
    forbidden_entitlements = sorted(entitlements.intersection(powerful_entitlements))
    if forbidden_entitlements:
        raise SystemExit(
            f"{identity_role} service principal has forbidden powerful entitlement(s): "
            f"{', '.join(forbidden_entitlements)}"
        )


def assert_agent_runtime_infrastructure_isolation(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
    effective_group_names: set[str],
    identity_role: str = "agent_runtime",
) -> None:
    """Reject Lakebase roles on every visible instance and SQL access."""

    del instance_name  # Compatibility-only: isolation is workspace-global.
    assert_lakebase_role_scope(
        client,
        application_id=application_id,
        allowed_instance_names=set(),
        identity_role=identity_role,
    )

    from tools.databricks.warehouse_acl import assert_no_warehouse_access

    assert_no_warehouse_access(
        client,
        service_principal=application_id,
        effective_group_names=effective_group_names,
    )


def assert_lakebase_role_scope(
    client: Any,
    *,
    application_id: str,
    allowed_instance_names: set[str],
    identity_role: str,
) -> None:
    """Reject an identity role on every Lakebase instance outside its scope."""

    try:
        instances = list(client.database.list_database_instances())
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="list Lakebase instances") from exc
    seen_instances: set[str] = set()
    for instance in instances:
        name = str(
            (instance.get("name") if isinstance(instance, dict) else getattr(instance, "name", ""))
            or ""
        ).strip()
        if not name:
            raise SystemExit("Cannot audit Lakebase isolation: a visible instance has no name")
        if name in seen_instances:
            raise SystemExit(f"Cannot audit Lakebase isolation: duplicate instance {name!r}")
        seen_instances.add(name)
        try:
            roles = list(client.database.list_database_instance_roles(name))
        except Exception as exc:  # noqa: BLE001
            raise wrap_admin_error(exc, step=f"audit Lakebase roles on {name}") from exc
        has_role = any(
            str((role.get("name") if isinstance(role, dict) else getattr(role, "name", "")) or "")
            == application_id
            for role in roles
        )
        if has_role and name not in allowed_instance_names:
            raise SystemExit(
                f"{identity_role} service principal has forbidden Lakebase role "
                f"{application_id!r} on instance {name!r}; remove it before deployment"
            )


def assert_no_app_permission(
    client: Any,
    *,
    app_name: str,
    sp_application_id: str,
    sp_display_name: str,
    effective_group_names: set[str],
    identity_role: str = "verifier",
) -> None:
    """Fail closed on App access across every workspace-visible App.

    ``app_name`` remains in the public signature for compatibility. It names
    the reviewed product App in diagnostics; it does not narrow the audit.
    """

    del app_name
    try:
        apps = list(client.apps.list())
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="list workspace Apps") from exc
    seen_apps: set[str] = set()
    for app in apps:
        visible_name = str(
            (app.get("name") if isinstance(app, dict) else getattr(app, "name", "")) or ""
        ).strip()
        if not visible_name:
            raise SystemExit("Cannot audit App isolation: a visible App has no name")
        if visible_name in seen_apps:
            raise SystemExit(f"Cannot audit App isolation: duplicate App {visible_name!r}")
        seen_apps.add(visible_name)
        try:
            permissions = client.apps.get_permissions(visible_name)
        except Exception as exc:  # noqa: BLE001
            raise wrap_admin_error(exc, step="inspect app permissions") from exc
        for entry in getattr(permissions, "access_control_list", None) or []:
            service_principal_name = str(getattr(entry, "service_principal_name", "") or "").strip()
            entry_display_name = str(getattr(entry, "display_name", "") or "").strip()
            group_name = str(getattr(entry, "group_name", "") or "").strip()
            if service_principal_name in {sp_application_id, sp_display_name} or (
                not service_principal_name
                and not group_name
                and entry_display_name == sp_display_name
            ):
                raise SystemExit(
                    f"{identity_role} service principal retains forbidden direct "
                    f"Databricks App permission on {visible_name!r}, including "
                    "inherited/effective permissions; remove it before provisioning"
                )
            if group_name and group_name in effective_group_names:
                raise SystemExit(
                    f"{identity_role} service principal retains forbidden effective "
                    f"Databricks App permission on {visible_name!r} through group "
                    f"{group_name!r}; remove the group grant or membership before provisioning"
                )


def assert_no_app_manager_permission(
    client: Any,
    *,
    sp_application_id: str,
    sp_display_name: str,
    effective_group_names: set[str],
    identity_role: str,
) -> None:
    """Reject effective CAN_MANAGE on every visible App for a runtime user role."""

    try:
        apps = list(client.apps.list())
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="list workspace Apps") from exc
    seen_apps: set[str] = set()
    for app in apps:
        visible_name = str(
            (app.get("name") if isinstance(app, dict) else getattr(app, "name", "")) or ""
        ).strip()
        if not visible_name:
            raise SystemExit("Cannot audit App manager isolation: a visible App has no name")
        if visible_name in seen_apps:
            raise SystemExit(f"Cannot audit App manager isolation: duplicate App {visible_name!r}")
        seen_apps.add(visible_name)
        try:
            permissions = client.apps.get_permissions(visible_name)
        except Exception as exc:  # noqa: BLE001
            raise wrap_admin_error(exc, step="inspect app manager permissions") from exc
        for entry in getattr(permissions, "access_control_list", None) or []:
            levels = getattr(entry, "all_permissions", None)
            if not isinstance(levels, list | tuple) or not levels:
                raise SystemExit("Cannot audit App manager isolation: malformed permission list")
            normalized_levels: set[str] = set()
            for permission in levels:
                raw_level = getattr(permission, "permission_level", None)
                level = str(getattr(raw_level, "value", raw_level) or "").strip().upper()
                if level not in {"CAN_MANAGE", "CAN_USE"}:
                    raise SystemExit(
                        "Cannot audit App manager isolation: unexpected permission level"
                    )
                normalized_levels.add(level)
            if "CAN_MANAGE" not in normalized_levels:
                continue
            service_principal_name = str(getattr(entry, "service_principal_name", "") or "").strip()
            entry_display_name = str(getattr(entry, "display_name", "") or "").strip()
            group_name = str(getattr(entry, "group_name", "") or "").strip()
            direct_match = service_principal_name in {
                sp_application_id,
                sp_display_name,
            } or (
                not service_principal_name
                and not group_name
                and entry_display_name == sp_display_name
            )
            group_match = bool(group_name and group_name in effective_group_names)
            if direct_match or group_match:
                path = f" through group {group_name!r}" if group_match else ""
                raise SystemExit(
                    f"{identity_role} service principal retains forbidden effective "
                    f"Databricks App CAN_MANAGE on {visible_name!r}{path}; remove manager "
                    "access before provisioning"
                )


def grant_can_query_on_endpoint(
    client: Any,
    endpoint_name: str,
    sp_application_id: str,
    *,
    sp_id: str,
    effective_group_names: set[str],
) -> None:
    """Converge the verifier's exact, group-aware CAN_QUERY grant."""

    from tools.databricks.serving_endpoint_acl import grant_direct_can_query

    _diag(f"resolving serving endpoint id for endpoint={endpoint_name!r}")
    try:
        _diag(f"granting CAN_QUERY on endpoint={endpoint_name!r} to verifier identity")
        grant_direct_can_query(
            client,
            endpoint_name=endpoint_name,
            service_principal=sp_application_id,
            service_principal_id=sp_id,
            effective_group_names=effective_group_names,
        )
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="update serving endpoint permissions") from exc


def revoke_can_query_on_obsolete_endpoint(
    client: Any,
    endpoint_name: str,
    sp_application_id: str,
    *,
    sp_id: str,
    effective_group_names: set[str],
) -> None:
    """Remove and disprove direct or group-derived access to an obsolete endpoint."""

    from tools.databricks.serving_endpoint_acl import revoke_direct_permissions

    try:
        removed = revoke_direct_permissions(
            client,
            endpoint_name=endpoint_name,
            service_principal=sp_application_id,
            missing_ok=True,
            service_principal_id=sp_id,
            effective_group_names=effective_group_names,
        )
        _diag(
            f"{'revoked' if removed else 'verified absent'} verifier query access "
            f"on obsolete endpoint={endpoint_name!r}"
        )
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="revoke obsolete serving endpoint permissions") from exc


def grant_can_use_on_warehouse(
    client: Any,
    warehouse_id: str,
    sp_application_id: str,
    *,
    effective_group_names: set[str],
) -> None:
    """Converge exact verifier CAN_USE on one SQL warehouse."""

    from tools.databricks.warehouse_acl import converge_exact_can_use

    _diag(f"granting CAN_USE on warehouse={warehouse_id!r} to verifier identity")
    try:
        converge_exact_can_use(
            client,
            warehouse_id=warehouse_id,
            service_principal=sp_application_id,
            effective_group_names=effective_group_names,
        )
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step="update SQL warehouse permissions") from exc
