"""Fail-closed serving-endpoint ACL convergence helpers."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Literal

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlRequest,
    ServingEndpointPermissionLevel,
)
from tools.databricks.m2m_access_policy import resolve_effective_groups

_LEVEL_RANK = {"CAN_VIEW": 1, "CAN_QUERY": 2, "CAN_MANAGE": 3}


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _direct_level(entry: object) -> str | None:
    direct = [
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
        if getattr(permission, "inherited", None) is not True
    ]
    valid = [level for level in direct if level in _LEVEL_RANK]
    return max(valid, key=_LEVEL_RANK.__getitem__) if valid else None


def _effective_query_or_manage(entry: object) -> str | None:
    effective = [
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
    ]
    query_capable = [level for level in effective if level in {"CAN_QUERY", "CAN_MANAGE"}]
    return max(query_capable, key=_LEVEL_RANK.__getitem__) if query_capable else None


def _all_levels(entry: object) -> set[str]:
    return {
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
        if _text(getattr(permission, "permission_level", None))
    }


def _principal_entry(permissions: object, principal: str) -> object | None:
    for entry in getattr(permissions, "access_control_list", None) or []:
        if str(getattr(entry, "service_principal_name", "") or "") == principal:
            return entry
    return None


def _service_principal_id(client: Any, application_id: str) -> str:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        principal
        for principal in client.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if str(getattr(principal, "application_id", "") or "") == application_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one service principal for application_id {application_id!r}, "
            f"found {len(matches)}"
        )
    principal_id = str(getattr(matches[0], "id", "") or "").strip()
    if not principal_id:
        raise RuntimeError(f"service principal {application_id!r} has no immutable SCIM id")
    return principal_id


def _effective_group_names(
    client: Any,
    *,
    service_principal: str,
    service_principal_id: str | None,
) -> set[str]:
    principal_id = service_principal_id or _service_principal_id(client, service_principal)
    return set(resolve_effective_groups(client, sp_id=principal_id).values())


def _group_query_or_manage(
    permissions: object,
    *,
    effective_group_names: set[str],
) -> tuple[str, str] | None:
    for entry in getattr(permissions, "access_control_list", None) or []:
        group_name = str(getattr(entry, "group_name", "") or "").strip()
        if group_name not in effective_group_names:
            continue
        level = _effective_query_or_manage(entry)
        if level is not None:
            return group_name, level
    return None


def _groups_with_access(
    permissions: object,
    *,
    effective_group_names: set[str],
) -> set[str]:
    return {
        str(getattr(entry, "group_name", "") or "").strip()
        for entry in getattr(permissions, "access_control_list", None) or []
        if str(getattr(entry, "group_name", "") or "").strip() in effective_group_names
        and _all_levels(entry)
    }


def _preserved_direct_acl(
    permissions: object,
    *,
    excluded_service_principal: str,
) -> list[ServingEndpointAccessControlRequest]:
    preserved: list[ServingEndpointAccessControlRequest] = []
    for entry in getattr(permissions, "access_control_list", None) or []:
        if str(getattr(entry, "service_principal_name", "") or "") == excluded_service_principal:
            continue
        level = _direct_level(entry)
        if level is None:
            continue
        permission_level = ServingEndpointPermissionLevel(level)
        identity = {
            "user_name": getattr(entry, "user_name", None),
            "group_name": getattr(entry, "group_name", None),
            "service_principal_name": getattr(entry, "service_principal_name", None),
        }
        if sum(bool(value) for value in identity.values()) != 1:
            raise RuntimeError("serving endpoint ACL contains an ambiguous direct principal")
        preserved.append(
            ServingEndpointAccessControlRequest(
                permission_level=permission_level,
                **identity,
            )
        )
    return preserved


def _endpoint_id(client: Any, endpoint_name: str, *, missing_ok: bool) -> str | None:
    try:
        details = client.serving_endpoints.get(endpoint_name)
    except (NotFound, ResourceDoesNotExist):
        if missing_ok:
            return None
        raise
    endpoint_id = str(getattr(details, "id", "") or "").strip()
    if not endpoint_id:
        raise RuntimeError(f"serving endpoint {endpoint_name!r} has no immutable id")
    return endpoint_id


def _is_platform_foundation_endpoint(details: object) -> bool:
    """Recognize only Databricks system foundation endpoints without ACL IDs.

    Their model entitlements are audited through the fixed ``system.ai`` UC
    inventory; they are not customer-created serving securables.
    """

    if (
        str(getattr(details, "id", "") or "").strip()
        or str(getattr(details, "creator", "") or "").strip()
    ):
        return False
    entities = getattr(getattr(details, "config", None), "served_entities", None) or []
    if not entities:
        return False
    for entity in entities:
        foundation = getattr(entity, "foundation_model", None)
        full_name = str(getattr(foundation, "name", "") or "").strip()
        if foundation is None or not full_name.startswith("system.ai."):
            return False
    return True


def audit_global_serving_endpoint_access(
    client: Any,
    *,
    reviewed_endpoint_names: Collection[str],
    service_principal: str,
    expected_permission_level: Literal["CAN_QUERY", "CAN_MANAGE"] = "CAN_QUERY",
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> None:
    """Admin-side proof of one exact direct level on only reviewed endpoints."""

    reviewed = {str(name).strip() for name in reviewed_endpoint_names if str(name).strip()}
    if not reviewed or len(reviewed) != len(reviewed_endpoint_names):
        raise ValueError("reviewed endpoint names must be non-empty and distinct")
    principal = service_principal.strip()
    if not principal:
        raise ValueError("service principal application ID is required")
    if expected_permission_level not in {"CAN_QUERY", "CAN_MANAGE"}:
        raise ValueError("expected permission level must be CAN_QUERY or CAN_MANAGE")
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=service_principal_id,
        )
    )
    try:
        visible = list(client.serving_endpoints.list())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"cannot list serving endpoints for global ACL audit: {exc}") from exc
    endpoint_ids: dict[str, str] = {}
    for endpoint in visible:
        name = str(
            (endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", ""))
            or ""
        ).strip()
        if not name:
            raise RuntimeError("cannot audit serving ACLs: a visible endpoint has no name")
        if name in endpoint_ids:
            raise RuntimeError(f"cannot audit serving ACLs: duplicate endpoint {name!r}")
        details = client.serving_endpoints.get(name)
        endpoint_id = str(getattr(details, "id", "") or "").strip()
        if not endpoint_id:
            if _is_platform_foundation_endpoint(details):
                continue
            raise RuntimeError(f"serving endpoint {name!r} has no immutable id")
        endpoint_ids[name] = endpoint_id
    missing = reviewed.difference(endpoint_ids)
    if missing:
        raise RuntimeError(
            "reviewed serving endpoint(s) are not visible to the admin audit: "
            + ", ".join(sorted(missing))
        )
    for name, endpoint_id in sorted(endpoint_ids.items()):
        try:
            permissions = client.serving_endpoints.get_permissions(endpoint_id)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot inspect serving ACL for {name!r}: {exc}") from exc
        entry = _principal_entry(permissions, principal)
        groups = _groups_with_access(
            permissions,
            effective_group_names=group_names,
        )
        if name in reviewed:
            if (
                _direct_level(entry or object()) != expected_permission_level
                or _all_levels(entry or object()) != {expected_permission_level}
                or groups
            ):
                raise RuntimeError(
                    f"exact global {expected_permission_level} audit failed for {principal!r} "
                    f"on reviewed endpoint {name!r}; remove inherited, group, or broader access"
                )
        elif (entry is not None and _all_levels(entry)) or groups:
            raise RuntimeError(
                f"{principal!r} retains forbidden access to unrelated serving endpoint "
                f"{name!r}; remove direct or effective group access"
            )


def audit_global_no_serving_endpoint_access(
    client: Any,
    *,
    service_principal: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> None:
    """Admin-side proof that an identity has no effective serving access."""

    principal = service_principal.strip()
    if not principal:
        raise ValueError("service principal application ID is required")
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=service_principal_id,
        )
    )
    try:
        visible = list(client.serving_endpoints.list())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"cannot list serving endpoints for global ACL audit: {exc}") from exc
    for endpoint in visible:
        name = str(
            (endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", ""))
            or ""
        ).strip()
        if not name:
            raise RuntimeError("cannot audit serving ACLs: a visible endpoint has no name")
        details = client.serving_endpoints.get(name)
        endpoint_id = str(getattr(details, "id", "") or "").strip()
        if not endpoint_id:
            if _is_platform_foundation_endpoint(details):
                continue
            raise RuntimeError(f"serving endpoint {name!r} has no immutable id")
        permissions = client.serving_endpoints.get_permissions(endpoint_id)
        entry = _principal_entry(permissions, principal)
        groups = _groups_with_access(
            permissions,
            effective_group_names=group_names,
        )
        if (entry is not None and _all_levels(entry)) or groups:
            raise RuntimeError(
                f"{principal!r} retains forbidden access to serving endpoint {name!r}"
            )


def grant_direct_can_query(
    client: Any,
    *,
    endpoint_name: str,
    service_principal: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> None:
    """Grant direct CAN_QUERY and prove the direct ACL entry exists."""

    endpoint_id = _endpoint_id(client, endpoint_name, missing_ok=False)
    assert endpoint_id is not None
    client.serving_endpoints.update_permissions(
        endpoint_id,
        access_control_list=[
            ServingEndpointAccessControlRequest(
                service_principal_name=service_principal,
                permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
            )
        ],
    )
    permissions = client.serving_endpoints.get_permissions(endpoint_id)
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=service_principal,
            service_principal_id=service_principal_id,
        )
    )
    level = _direct_level(_principal_entry(permissions, service_principal) or object())
    entry = _principal_entry(permissions, service_principal)
    group_access = _group_query_or_manage(
        permissions,
        effective_group_names=group_names,
    )
    if (
        level != "CAN_QUERY"
        or _effective_query_or_manage(entry or object()) != "CAN_QUERY"
        or group_access is not None
    ):
        raise RuntimeError(
            f"exact least-privilege CAN_QUERY postflight failed for {service_principal!r} "
            f"on {endpoint_name!r}; remove inherited group access before retrying"
        )


def revoke_direct_permissions(
    client: Any,
    *,
    endpoint_name: str,
    service_principal: str,
    missing_ok: bool = False,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> bool:
    """Remove one principal's direct ACL without replacing other principals."""

    endpoint_id = _endpoint_id(client, endpoint_name, missing_ok=missing_ok)
    if endpoint_id is None:
        return False
    permissions = client.serving_endpoints.get_permissions(endpoint_id)
    entry = _principal_entry(permissions, service_principal)
    if entry is not None and _direct_level(entry) is not None:
        preserved = _preserved_direct_acl(
            permissions,
            excluded_service_principal=service_principal,
        )
        client.serving_endpoints.set_permissions(
            endpoint_id,
            access_control_list=preserved,
        )
    postflight = client.serving_endpoints.get_permissions(endpoint_id)
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=service_principal,
            service_principal_id=service_principal_id,
        )
    )
    remaining = _principal_entry(postflight, service_principal)
    group_access = _group_query_or_manage(
        postflight,
        effective_group_names=group_names,
    )
    if (
        remaining is not None and _effective_query_or_manage(remaining) is not None
    ) or group_access is not None:
        raise RuntimeError(
            f"effective query permission remains for {service_principal!r} on "
            f"{endpoint_name!r}; remove inherited group access before retrying"
        )
    return entry is not None and _direct_level(entry) is not None
