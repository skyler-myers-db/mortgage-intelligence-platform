from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.service.sql import (
    WarehouseAccessControlResponse,
    WarehousePermission,
    WarehousePermissionLevel,
    WarehousePermissions,
)

from tools.databricks.warehouse_acl import converge_exact_can_use


def _permission(level: str, *, inherited: bool = False) -> WarehousePermission:
    return WarehousePermission(
        inherited=inherited,
        permission_level=WarehousePermissionLevel(level),
    )


def _sp_entry(principal: str, level: str) -> WarehouseAccessControlResponse:
    return WarehouseAccessControlResponse(
        service_principal_name=principal,
        all_permissions=[_permission(level)],
    )


def _group_entry(group: str, level: str) -> WarehouseAccessControlResponse:
    return WarehouseAccessControlResponse(
        group_name=group,
        all_permissions=[_permission(level)],
    )


class _Warehouses:
    def __init__(self, responses: dict[str, list[WarehousePermissions]]) -> None:
        self.responses = {key: list(value) for key, value in responses.items()}
        self.set_calls: list[tuple[str, list[object]]] = []
        self.update_calls: list[tuple[str, list[object]]] = []

    def list(self):
        return iter(SimpleNamespace(id=warehouse_id) for warehouse_id in self.responses)

    def get_permissions(self, warehouse_id: str) -> WarehousePermissions:
        values = self.responses[warehouse_id]
        return values.pop(0) if len(values) > 1 else values[0]

    def set_permissions(self, warehouse_id: str, *, access_control_list: list[object]) -> None:
        self.set_calls.append((warehouse_id, access_control_list))

    def update_permissions(self, warehouse_id: str, *, access_control_list: list[object]) -> None:
        self.update_calls.append((warehouse_id, access_control_list))


def _client(responses: dict[str, list[WarehousePermissions]]) -> SimpleNamespace:
    return SimpleNamespace(warehouses=_Warehouses(responses))


def _acl(*entries: WarehouseAccessControlResponse) -> WarehousePermissions:
    return WarehousePermissions(access_control_list=list(entries))


def test_converges_target_and_revokes_direct_access_from_other_warehouses() -> None:
    client = _client(
        {
            "target": [_acl(_sp_entry("verifier", "CAN_USE"))],
            "obsolete": [
                _acl(_sp_entry("verifier", "CAN_MANAGE"), _sp_entry("other", "CAN_USE")),
                _acl(_sp_entry("other", "CAN_USE")),
            ],
        }
    )

    converge_exact_can_use(
        client,
        warehouse_id="target",
        service_principal="verifier",
        effective_group_names=set(),
    )

    warehouses = client.warehouses
    assert warehouses.set_calls[0][0] == "obsolete"
    assert warehouses.set_calls[0][1][0].service_principal_name == "other"
    assert warehouses.update_calls[0][0] == "target"
    assert warehouses.update_calls[0][1][0].permission_level == WarehousePermissionLevel.CAN_USE


def test_rejects_inherited_manage_on_target() -> None:
    client = _client(
        {
            "target": [
                _acl(
                    WarehouseAccessControlResponse(
                        service_principal_name="verifier",
                        all_permissions=[
                            _permission("CAN_USE"),
                            _permission("CAN_MANAGE", inherited=True),
                        ],
                    )
                )
            ]
        }
    )

    with pytest.raises(RuntimeError, match="least-privilege CAN_USE postflight"):
        converge_exact_can_use(
            client,
            warehouse_id="target",
            service_principal="verifier",
            effective_group_names=set(),
        )


def test_rejects_effective_group_access_on_non_target_warehouse() -> None:
    client = _client(
        {
            "target": [_acl(_sp_entry("verifier", "CAN_USE"))],
            "obsolete": [_acl(_group_entry("hidden-from-direct-acl", "CAN_USE"))],
        }
    )

    with pytest.raises(RuntimeError, match="non-target warehouse"):
        converge_exact_can_use(
            client,
            warehouse_id="target",
            service_principal="verifier",
            effective_group_names={"hidden-from-direct-acl"},
        )


def test_rejects_missing_target_from_workspace_list() -> None:
    client = _client({"other": [_acl()]})

    with pytest.raises(RuntimeError, match="was not returned"):
        converge_exact_can_use(
            client,
            warehouse_id="target",
            service_principal="verifier",
            effective_group_names=set(),
        )
