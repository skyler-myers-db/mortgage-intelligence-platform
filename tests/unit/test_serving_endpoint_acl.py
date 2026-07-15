from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlResponse,
    ServingEndpointPermission,
    ServingEndpointPermissionLevel,
    ServingEndpointPermissions,
)

from tools.databricks.serving_endpoint_acl import (
    grant_direct_can_query,
    revoke_direct_permissions,
)


def _permission(level: str, *, inherited: bool = False) -> ServingEndpointPermission:
    return ServingEndpointPermission(
        inherited=inherited,
        permission_level=ServingEndpointPermissionLevel(level),
    )


def _sp_entry(
    principal: str,
    level: str,
    *,
    inherited: bool = False,
) -> ServingEndpointAccessControlResponse:
    return ServingEndpointAccessControlResponse(
        service_principal_name=principal,
        all_permissions=[_permission(level, inherited=inherited)],
    )


def _group_entry(group_name: str, level: str) -> ServingEndpointAccessControlResponse:
    return ServingEndpointAccessControlResponse(
        group_name=group_name,
        all_permissions=[_permission(level)],
    )


class _Serving:
    def __init__(self, permissions: list[ServingEndpointPermissions]) -> None:
        self.permissions = list(permissions)
        self.updated: list[object] = []
        self.replaced: list[object] = []

    def get(self, name: str) -> object:
        assert name in {"outer", "managed"}
        return SimpleNamespace(id=f"{name}-id")

    def update_permissions(self, endpoint_id: str, **kwargs: object) -> None:
        self.updated.append((endpoint_id, kwargs))

    def set_permissions(self, endpoint_id: str, **kwargs: object) -> None:
        self.replaced.append((endpoint_id, kwargs))

    def get_permissions(self, endpoint_id: str) -> ServingEndpointPermissions:
        assert endpoint_id in {"outer-id", "managed-id"}
        return self.permissions.pop(0)


def test_grant_requires_exact_direct_can_query() -> None:
    serving = _Serving(
        [ServingEndpointPermissions(access_control_list=[_sp_entry("app-sp", "CAN_QUERY")])]
    )

    grant_direct_can_query(
        SimpleNamespace(serving_endpoints=serving),
        endpoint_name="outer",
        service_principal="app-sp",
        effective_group_names=set(),
    )

    request = serving.updated[0][1]["access_control_list"][0]
    assert request.permission_level == ServingEndpointPermissionLevel.CAN_QUERY


def test_grant_rejects_residual_can_manage() -> None:
    serving = _Serving(
        [ServingEndpointPermissions(access_control_list=[_sp_entry("app-sp", "CAN_MANAGE")])]
    )

    with pytest.raises(RuntimeError, match="least-privilege CAN_QUERY"):
        grant_direct_can_query(
            SimpleNamespace(serving_endpoints=serving),
            endpoint_name="outer",
            service_principal="app-sp",
            effective_group_names=set(),
        )


def test_revoke_preserves_other_direct_principals_and_proves_absence() -> None:
    current = ServingEndpointPermissions(
        access_control_list=[
            _sp_entry("app-sp", "CAN_QUERY"),
            ServingEndpointAccessControlResponse(
                user_name="owner@example.com",
                all_permissions=[_permission("CAN_MANAGE")],
            ),
        ]
    )
    postflight = ServingEndpointPermissions(
        access_control_list=[
            ServingEndpointAccessControlResponse(
                user_name="owner@example.com",
                all_permissions=[_permission("CAN_MANAGE")],
            )
        ]
    )
    serving = _Serving([current, postflight])

    removed = revoke_direct_permissions(
        SimpleNamespace(serving_endpoints=serving),
        endpoint_name="managed",
        service_principal="app-sp",
        effective_group_names=set(),
    )

    assert removed is True
    preserved = serving.replaced[0][1]["access_control_list"]
    assert len(preserved) == 1
    assert preserved[0].user_name == "owner@example.com"


def test_revoke_fails_when_inherited_query_access_remains() -> None:
    inherited = ServingEndpointPermissions(
        access_control_list=[_sp_entry("app-sp", "CAN_QUERY", inherited=True)]
    )
    serving = _Serving([inherited, inherited])

    with pytest.raises(RuntimeError, match="inherited group access"):
        revoke_direct_permissions(
            SimpleNamespace(serving_endpoints=serving),
            endpoint_name="managed",
            service_principal="app-sp",
            effective_group_names=set(),
        )

    assert serving.replaced == []


def test_revoke_fails_when_effective_group_retains_query_access() -> None:
    group_acl = ServingEndpointPermissions(
        access_control_list=[_group_entry("gateway-bypass", "CAN_QUERY")]
    )
    serving = _Serving([group_acl, group_acl])

    with pytest.raises(RuntimeError, match="inherited group access"):
        revoke_direct_permissions(
            SimpleNamespace(serving_endpoints=serving),
            endpoint_name="managed",
            service_principal="app-sp",
            effective_group_names={"gateway-bypass"},
        )


def test_grant_rejects_parallel_effective_group_access() -> None:
    permissions = ServingEndpointPermissions(
        access_control_list=[
            _sp_entry("app-sp", "CAN_QUERY"),
            _group_entry("gateway-bypass", "CAN_MANAGE"),
        ]
    )
    serving = _Serving([permissions])

    with pytest.raises(RuntimeError, match="inherited group access"):
        grant_direct_can_query(
            SimpleNamespace(serving_endpoints=serving),
            endpoint_name="outer",
            service_principal="app-sp",
            effective_group_names={"gateway-bypass"},
        )


def test_default_group_resolution_uses_exact_scim_application_id_filter() -> None:
    serving = _Serving(
        [ServingEndpointPermissions(access_control_list=[_sp_entry("app-sp", "CAN_QUERY")])]
    )
    filters: list[str] = []

    class _ServicePrincipals:
        def list(self, *, filter: str) -> object:
            filters.append(filter)
            return iter([SimpleNamespace(id="sp-id", application_id="app-sp")])

    class _Groups:
        def list(self, **_kwargs: object) -> object:
            return iter([])

    grant_direct_can_query(
        SimpleNamespace(
            serving_endpoints=serving,
            service_principals=_ServicePrincipals(),
            groups=_Groups(),
        ),
        endpoint_name="outer",
        service_principal="app-sp",
    )

    assert filters == ['applicationId eq "app-sp"']
