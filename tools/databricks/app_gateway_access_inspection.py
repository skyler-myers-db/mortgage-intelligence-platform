"""Signed inspection of exact Databricks App Gateway query access."""

from __future__ import annotations

from typing import Any, Literal

from tools.databricks.m2m_access_policy import resolve_effective_groups
from tools.databricks.serving_endpoint_legacy_query import (
    inspect_legacy_pre_provenance_group,
)
from tools.databricks.serving_query_group_access import (
    inspect_claimed_managed_query_group,
    managed_query_group_name,
)
from tools.databricks.serving_query_group_provenance import (
    MissingClaimedGroupProvenanceError,
)

AppGatewayAccessMode = Literal["none", "legacy", "managed", "mixed"]
GatewayQueryAccessMode = Literal["none", "direct", "managed", "mixed"]


def app_service_principal_identity(
    workspace: Any,
    *,
    app_name: str,
) -> tuple[str, str]:
    """Resolve both immutable App service-principal identifiers."""

    app = workspace.apps.get(app_name)
    client_id = str(
        getattr(app, "service_principal_client_id", None)
        or (app.get("service_principal_client_id") if isinstance(app, dict) else "")
        or ""
    ).strip()
    scim_id = str(
        getattr(app, "service_principal_id", None)
        or (app.get("service_principal_id") if isinstance(app, dict) else "")
        or ""
    ).strip()
    if not client_id or not scim_id:
        raise RuntimeError(
            f"both App service-principal identifiers are required for {app_name!r}"
        )
    return client_id, scim_id


def _permission_level(permission: object) -> str:
    value = getattr(permission, "permission_level", None)
    return str(getattr(value, "value", value) or "").strip()


def _direct_levels(entry: object) -> set[str]:
    return {
        _permission_level(permission)
        for permission in (getattr(entry, "all_permissions", None) or [])
        if getattr(permission, "inherited", None) is not True
        and _permission_level(permission)
    }


def _all_levels(entry: object) -> set[str]:
    return {
        _permission_level(permission)
        for permission in (getattr(entry, "all_permissions", None) or [])
        if _permission_level(permission)
    }


def _exact_acl_entry(
    permissions: object,
    *,
    identity_field: str,
    identity: str,
) -> object | None:
    entries = [
        entry
        for entry in (getattr(permissions, "access_control_list", None) or [])
        if str(getattr(entry, identity_field, "") or "").strip() == identity
    ]
    if len(entries) > 1:
        raise RuntimeError(
            f"serving endpoint ACL duplicates {identity_field} {identity!r}"
        )
    return entries[0] if entries else None


def _assert_exact_managed_group(
    workspace: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    scim_id: str,
    identity_label: str,
    legacy_pinned: bool,
) -> bool:
    try:
        state = inspect_claimed_managed_query_group(
            workspace,
            app_name=app_name,
            endpoint_id=endpoint_id,
            application_id=application_id,
            service_principal_id=scim_id,
        )
    except MissingClaimedGroupProvenanceError:
        if not legacy_pinned:
            raise
        state = inspect_legacy_pre_provenance_group(
            workspace,
            endpoint_id=endpoint_id,
            application_id=application_id,
            service_principal_id=scim_id,
        )
    assert state is not None
    members = set(state.member_ids)
    if members.difference({scim_id}):
        raise RuntimeError(
            f"signed {identity_label} managed query group contains an identity "
            "outside its immutable contract"
        )
    return members == {scim_id}


def inspect_gateway_query_access_mode(
    workspace: Any,
    *,
    app_name: str,
    endpoint_name: str,
    application_id: str,
    scim_id: str,
    identity_label: str,
    legacy_pinned: bool = False,
) -> GatewayQueryAccessMode:
    """Classify one exact identity's direct and managed Gateway query paths."""

    application = application_id.strip()
    principal_id = scim_id.strip()
    label = identity_label.strip()
    if not application or not principal_id or not label:
        raise ValueError("application, SCIM, and identity label are required")
    endpoint = workspace.serving_endpoints.get(endpoint_name)
    endpoint_id = str(getattr(endpoint, "id", "") or "").strip()
    if not endpoint_id:
        raise RuntimeError(f"signed {label} Gateway endpoint has no immutable ID")
    permissions = workspace.serving_endpoints.get_permissions(endpoint_id)
    direct_entry = _exact_acl_entry(
        permissions,
        identity_field="service_principal_name",
        identity=application,
    )
    direct_levels = _direct_levels(direct_entry or object())
    if _all_levels(direct_entry or object()) and (
        direct_levels != {"CAN_QUERY"}
        or _all_levels(direct_entry or object()) != {"CAN_QUERY"}
    ):
        raise RuntimeError(
            f"signed {label} direct Gateway access is not exact CAN_QUERY"
        )
    group_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application,
    )
    group_entry = _exact_acl_entry(
        permissions,
        identity_field="group_name",
        identity=group_name,
    )
    group_levels = _direct_levels(group_entry or object())
    if _all_levels(group_entry or object()) and (
        group_levels != {"CAN_QUERY"}
        or _all_levels(group_entry or object()) != {"CAN_QUERY"}
    ):
        raise RuntimeError(
            f"signed {label} managed Gateway access is not exact CAN_QUERY"
        )
    managed_membership_active = False
    if group_levels:
        managed_membership_active = _assert_exact_managed_group(
            workspace,
            app_name=app_name,
            endpoint_id=endpoint_id,
            application_id=application,
            scim_id=principal_id,
            identity_label=label,
            legacy_pinned=legacy_pinned,
        )
    effective_groups = set(
        resolve_effective_groups(workspace, sp_id=principal_id).values()
    )
    for entry in getattr(permissions, "access_control_list", None) or []:
        effective_group = str(
            getattr(entry, "group_name", "") or ""
        ).strip()
        if (
            effective_group
            and effective_group != group_name
            and effective_group in effective_groups
            and _all_levels(entry)
        ):
            raise RuntimeError(
                f"signed {label} Gateway access includes an unreviewed effective group"
            )
    if direct_levels and group_levels and managed_membership_active:
        return "mixed"
    if direct_levels:
        return "direct"
    if group_levels and managed_membership_active:
        return "managed"
    return "none"


def inspect_app_gateway_access_mode(
    workspace: Any,
    *,
    app_name: str,
    endpoint_name: str,
    app_client_id: str,
    app_scim_id: str,
    legacy_pinned: bool = False,
) -> AppGatewayAccessMode:
    """Read and classify exact App query access without mutating the ACL."""

    mode = inspect_gateway_query_access_mode(
        workspace,
        app_name=app_name,
        endpoint_name=endpoint_name,
        application_id=app_client_id,
        scim_id=app_scim_id,
        identity_label="App",
        legacy_pinned=legacy_pinned,
    )
    return "legacy" if mode == "direct" else mode
