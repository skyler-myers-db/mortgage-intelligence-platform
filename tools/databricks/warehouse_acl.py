"""Fail-closed SQL warehouse ACL convergence for the verifier identity."""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.sql import (
    WarehouseAccessControlRequest,
    WarehousePermissionLevel,
)

_LEVEL_RANK = {
    "CAN_VIEW": 1,
    "CAN_USE": 2,
    "CAN_MONITOR": 3,
    "CAN_MANAGE": 4,
    "IS_OWNER": 5,
}


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _direct_level(entry: object) -> str | None:
    levels = [
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
        if getattr(permission, "inherited", None) is not True
    ]
    valid = [level for level in levels if level in _LEVEL_RANK]
    return max(valid, key=_LEVEL_RANK.__getitem__) if valid else None


def _effective_level(entry: object) -> str | None:
    levels = [
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
    ]
    valid = [level for level in levels if level in _LEVEL_RANK]
    return max(valid, key=_LEVEL_RANK.__getitem__) if valid else None


def _principal_entry(permissions: object, principal: str) -> object | None:
    for entry in getattr(permissions, "access_control_list", None) or []:
        if str(getattr(entry, "service_principal_name", "") or "") == principal:
            return entry
    return None


def _group_access(
    permissions: object,
    *,
    effective_group_names: set[str],
) -> tuple[str, str] | None:
    for entry in getattr(permissions, "access_control_list", None) or []:
        group_name = str(getattr(entry, "group_name", "") or "").strip()
        if group_name not in effective_group_names:
            continue
        level = _effective_level(entry)
        if level is not None:
            return group_name, level
    return None


def _preserved_direct_acl(
    permissions: object,
    *,
    excluded_service_principal: str,
) -> list[WarehouseAccessControlRequest]:
    preserved: list[WarehouseAccessControlRequest] = []
    for entry in getattr(permissions, "access_control_list", None) or []:
        if str(getattr(entry, "service_principal_name", "") or "") == (
            excluded_service_principal
        ):
            continue
        level = _direct_level(entry)
        if level is None:
            continue
        identity = {
            "user_name": getattr(entry, "user_name", None),
            "group_name": getattr(entry, "group_name", None),
            "service_principal_name": getattr(entry, "service_principal_name", None),
        }
        if sum(bool(value) for value in identity.values()) != 1:
            raise RuntimeError("warehouse ACL contains an ambiguous direct principal")
        preserved.append(
            WarehouseAccessControlRequest(
                permission_level=WarehousePermissionLevel(level),
                **identity,
            )
        )
    return preserved


def _warehouse_id(warehouse: object) -> str:
    warehouse_id = str(getattr(warehouse, "id", "") or "").strip()
    if not warehouse_id:
        raise RuntimeError("listed SQL warehouse has no immutable id")
    return warehouse_id


def converge_exact_can_use(
    client: Any,
    *,
    warehouse_id: str,
    service_principal: str,
    effective_group_names: set[str],
) -> None:
    """Grant exact direct CAN_USE and remove direct access to every other warehouse.

    The workspace-visible group postflight is intentionally not described as an
    authoritative identity proof. Automatic identity management can hide nested
    account membership from SCIM; the live verifier-credential denial probes are
    the independent release gate for that boundary.
    """
    target = warehouse_id.strip()
    if not target:
        raise ValueError("warehouse_id is required")

    listed_ids = {_warehouse_id(warehouse) for warehouse in client.warehouses.list()}
    if target not in listed_ids:
        raise RuntimeError(f"target SQL warehouse {target!r} was not returned by the workspace")

    for candidate_id in sorted(listed_ids - {target}):
        permissions = client.warehouses.get_permissions(candidate_id)
        entry = _principal_entry(permissions, service_principal)
        if entry is not None and _direct_level(entry) is not None:
            client.warehouses.set_permissions(
                candidate_id,
                access_control_list=_preserved_direct_acl(
                    permissions,
                    excluded_service_principal=service_principal,
                ),
            )
        postflight = client.warehouses.get_permissions(candidate_id)
        remaining = _principal_entry(postflight, service_principal)
        group_access = _group_access(
            postflight,
            effective_group_names=effective_group_names,
        )
        if (remaining is not None and _effective_level(remaining) is not None) or (
            group_access is not None
        ):
            raise RuntimeError(
                f"verifier retains effective access to non-target warehouse {candidate_id!r}"
            )

    client.warehouses.update_permissions(
        target,
        access_control_list=[
            WarehouseAccessControlRequest(
                service_principal_name=service_principal,
                permission_level=WarehousePermissionLevel.CAN_USE,
            )
        ],
    )
    postflight = client.warehouses.get_permissions(target)
    entry = _principal_entry(postflight, service_principal)
    group_access = _group_access(
        postflight,
        effective_group_names=effective_group_names,
    )
    if (
        _direct_level(entry or object()) != "CAN_USE"
        or _effective_level(entry or object()) != "CAN_USE"
        or group_access is not None
    ):
        raise RuntimeError(
            f"exact least-privilege CAN_USE postflight failed on warehouse {target!r}; "
            "remove inherited group access before retrying"
        )
