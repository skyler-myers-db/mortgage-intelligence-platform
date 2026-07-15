"""Fail-closed serving-endpoint ACL convergence helpers."""

from __future__ import annotations

from typing import Any

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
