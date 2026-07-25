from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlResponse,
    ServingEndpointPermission,
    ServingEndpointPermissionLevel,
    ServingEndpointPermissions,
)

from tools.databricks.serving_endpoint_acl import (
    audit_global_serving_endpoint_access,
    converge_exact_direct_can_query,
    endpoint_has_legacy_direct_query_principal,
    grant_direct_can_query,
    inspect_exact_query_access_mode,
    revoke_all_direct_permissions,
    revoke_direct_permissions,
)
from tools.databricks.serving_query_group_access import (
    assert_managed_query_group_members,
    inspect_managed_query_group,
    managed_query_group_external_id,
    managed_query_group_name,
    remove_managed_query_membership,
    retire_managed_query_group,
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


class _Groups:
    def __init__(self) -> None:
        self.by_id: dict[str, SimpleNamespace] = {}
        self.patch_calls: list[dict[str, object]] = []
        self.on_patch: Any | None = None

    def list(self, **kwargs: object) -> object:
        filter_value = str(kwargs.get("filter") or "")
        values = list(self.by_id.values())
        if filter_value:
            name = filter_value.removeprefix("displayName eq '").removesuffix("'")
            values = [group for group in values if group.display_name == name]
        return iter(values)

    def create(self, *, display_name: str, external_id: str) -> object:
        group = SimpleNamespace(
            id=f"group-{len(self.by_id) + 1}",
            display_name=display_name,
            external_id=external_id,
            members=[],
        )
        self.by_id[group.id] = group
        return group

    def get(self, group_id: str) -> object:
        return self.by_id[group_id]

    def patch(self, **kwargs: object) -> None:
        self.patch_calls.append(kwargs)
        group = self.by_id[str(kwargs["id"])]
        operation = cast(Any, kwargs["operations"])[0]
        op = str(getattr(operation.op, "value", operation.op))
        if op == "add":
            for item in operation.value["members"]:
                group.members.append(SimpleNamespace(value=item["value"]))
        elif op == "remove":
            member_id = operation.path.split('"')[1]
            group.members = [
                member for member in group.members if member.value != member_id
            ]
        else:
            raise AssertionError(f"unexpected SCIM patch operation {op!r}")
        if self.on_patch is not None:
            self.on_patch()

    def delete(self, group_id: str) -> None:
        del self.by_id[group_id]


class _Serving:
    def __init__(self, permissions: ServingEndpointPermissions | None = None) -> None:
        baseline = permissions or ServingEndpointPermissions(access_control_list=[])
        self.permissions = {"outer": baseline, "managed": baseline}
        self.updated: list[object] = []
        self.replaced: list[object] = []
        self.on_update: Any | None = None

    def list(self) -> object:
        return iter(SimpleNamespace(name=name) for name in self.permissions)

    def get(self, name: str) -> object:
        return SimpleNamespace(id=f"{name}-id")

    def update_permissions(
        self,
        endpoint_id: str,
        *,
        access_control_list: list[object],
    ) -> None:
        self.updated.append((endpoint_id, {"access_control_list": access_control_list}))
        name = endpoint_id.removesuffix("-id")
        current = list(self.permissions[name].access_control_list or [])
        for request in access_control_list:
            group_name = getattr(request, "group_name", None)
            current = [
                entry
                for entry in current
                if getattr(entry, "group_name", None) != group_name
            ]
            current.append(
                ServingEndpointAccessControlResponse(
                    group_name=group_name,
                    all_permissions=[_permission("CAN_QUERY")],
                )
            )
        self.permissions[name] = ServingEndpointPermissions(access_control_list=current)
        if self.on_update is not None:
            self.on_update()

    def set_permissions(self, endpoint_id: str, **kwargs: object) -> None:
        self.replaced.append((endpoint_id, kwargs))
        raise AssertionError("whole-ACL replacement is forbidden")

    def get_permissions(self, endpoint_id: str) -> ServingEndpointPermissions:
        return self.permissions[endpoint_id.removesuffix("-id")]


def _client(serving: object, groups: _Groups | None = None) -> SimpleNamespace:
    group_api = groups or _Groups()

    def list_principals(**kwargs: object) -> object:
        filter_value = str(kwargs.get("filter") or "")
        application_id = filter_value.removeprefix('applicationId eq "').removesuffix('"')
        principal_id = {
            "app-sp": "sp-id",
            "proxy-sp": "proxy-id",
            "verifier-sp": "verifier-id",
        }.get(application_id, f"{application_id}-id")
        return iter(
            [SimpleNamespace(id=principal_id, application_id=application_id)]
            if application_id
            else []
        )

    return SimpleNamespace(
        serving_endpoints=serving,
        groups=group_api,
        service_principals=SimpleNamespace(list=list_principals),
    )


def _seed_managed_group(
    groups: _Groups,
    *,
    endpoint: str,
    application_id: str,
    member_ids: tuple[str, ...],
) -> None:
    endpoint_id = f"{endpoint}-id"
    group = groups.create(
        display_name=managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        external_id=managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
    )
    group.members = [SimpleNamespace(value=value) for value in member_ids]


def _client_with_managed_groups(
    serving: object,
    *bindings: tuple[str, str, str],
) -> SimpleNamespace:
    groups = _Groups()
    for endpoint, application_id, member_id in bindings:
        _seed_managed_group(
            groups,
            endpoint=endpoint,
            application_id=application_id,
            member_ids=(member_id,),
        )
    return _client(serving, groups)


def test_managed_query_group_external_id_uses_full_sha256_within_scim_limit() -> None:
    external_id = managed_query_group_external_id(
        endpoint_id="endpoint-id",
        application_id="application-id",
    )

    assert external_id == (
        "mip:serving-query:c0PGegchStNce40d1OixrE9hqq_OUFgGhDUXHwmpXWk"
    )
    assert len(external_id) == 61
    assert len(external_id) <= 64
    assert external_id != managed_query_group_external_id(
        endpoint_id="endpoint-id",
        application_id="other",
    )


def test_managed_query_group_inspection_rehydrates_exact_contract_and_members() -> None:
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("sp-id",),
    )

    state = inspect_managed_query_group(
        _client(_Serving(), groups),
        endpoint_id="managed-id",
        application_id="app-sp",
    )

    assert state is not None
    assert state.contract.name == managed_query_group_name(
        endpoint_id="managed-id",
        application_id="app-sp",
    )
    assert state.contract.external_id == managed_query_group_external_id(
        endpoint_id="managed-id",
        application_id="app-sp",
    )
    assert state.member_ids == ("sp-id",)


def test_managed_query_group_exact_inspection_rejects_unrelated_member() -> None:
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("sp-id", "unrelated-id"),
    )

    with pytest.raises(RuntimeError, match="membership contract drifted"):
        assert_managed_query_group_members(
            _client(_Serving(), groups),
            endpoint_id="managed-id",
            application_id="app-sp",
            expected_member_ids=("sp-id",),
        )


def test_managed_query_group_retirement_is_exact_and_idempotent() -> None:
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("sp-id",),
    )
    client = _client(_Serving(), groups)

    assert retire_managed_query_group(
        client,
        endpoint_id="managed-id",
        application_id="app-sp",
        service_principal_id="sp-id",
    )
    assert not retire_managed_query_group(
        client,
        endpoint_id="managed-id",
        application_id="app-sp",
        service_principal_id="sp-id",
    )


def test_managed_query_group_retirement_rejects_unrelated_member() -> None:
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("unrelated-id",),
    )

    with pytest.raises(RuntimeError, match="unrelated member"):
        retire_managed_query_group(
            _client(_Serving(), groups),
            endpoint_id="managed-id",
            application_id="app-sp",
            service_principal_id="sp-id",
        )


def test_remove_managed_query_membership_requires_exact_scim_id() -> None:
    with pytest.raises(ValueError, match="exact service-principal SCIM ID"):
        remove_managed_query_membership(
            _client(_Serving()),
            endpoint_id="managed-id",
            application_id="app-sp",
            service_principal_id="",
        )


def test_grant_is_idempotent_for_exact_managed_query_group() -> None:
    serving = _Serving()
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="outer",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names=set(),
    )
    serving.updated.clear()
    groups.patch_calls.clear()

    grant_direct_can_query(
        client,
        endpoint_name="outer",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names={
            managed_query_group_name(endpoint_id="outer-id", application_id="app-sp")
        },
    )

    assert serving.updated == []
    assert groups.patch_calls == []


def test_grant_rejects_legacy_direct_permission() -> None:
    serving = _Serving(
        ServingEndpointPermissions(
            access_control_list=[_sp_entry("app-sp", "CAN_MANAGE")]
        )
    )

    with pytest.raises(RuntimeError, match="provider has no atomic principal delete"):
        grant_direct_can_query(
            _client(serving),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )


def test_grant_creates_group_acl_and_atomic_membership() -> None:
    groups = _Groups()

    class _RequireExistingGroupServing(_Serving):
        def update_permissions(
            self,
            endpoint_id: str,
            *,
            access_control_list: list[object],
        ) -> None:
            group_name = str(getattr(access_control_list[0], "group_name", "") or "")
            assert any(
                group.display_name == group_name for group in groups.by_id.values()
            ), "serving ACL must not reference a group before SCIM creates it"
            super().update_permissions(
                endpoint_id,
                access_control_list=access_control_list,
            )

    serving = _RequireExistingGroupServing()
    grant_direct_can_query(
        _client(serving, groups),
        endpoint_name="outer",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names=set(),
    )

    request = cast(Any, serving.updated[0])[1]["access_control_list"][0]
    assert request.group_name == managed_query_group_name(
        endpoint_id="outer-id",
        application_id="app-sp",
    )
    assert request.permission_level == ServingEndpointPermissionLevel.CAN_QUERY
    assert len(groups.patch_calls) == 1
    assert serving.replaced == []


def test_grant_postflight_rejects_member_added_with_endpoint_acl() -> None:
    serving = _Serving()
    groups = _Groups()

    def inject_unrelated_member() -> None:
        next(iter(groups.by_id.values())).members.append(
            SimpleNamespace(value="concurrent-id")
        )

    serving.on_update = inject_unrelated_member
    with pytest.raises(RuntimeError, match="membership contract drifted"):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )


def test_revoke_uses_atomic_membership_and_preserves_concurrent_acl() -> None:
    serving = _Serving()
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="managed",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names=set(),
    )
    owner = ServingEndpointAccessControlResponse(
        user_name="new-owner@example.com",
        all_permissions=[_permission("CAN_MANAGE")],
    )
    groups.on_patch = lambda: cast(
        list[ServingEndpointAccessControlResponse],
        serving.permissions["managed"].access_control_list,
    ).append(owner)

    removed = revoke_direct_permissions(
        client,
        endpoint_name="managed",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names={
            managed_query_group_name(endpoint_id="managed-id", application_id="app-sp")
        },
    )

    assert removed is True
    assert owner in (serving.permissions["managed"].access_control_list or [])
    assert serving.replaced == []


def test_revoke_resolves_exact_scim_identity_when_not_supplied() -> None:
    serving = _Serving()
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="managed",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names=set(),
    )
    filters: list[str] = []

    def list_principals(**kwargs: object) -> object:
        filters.append(str(kwargs["filter"]))
        return iter([SimpleNamespace(id="sp-id", application_id="app-sp")])

    client.service_principals.list = list_principals

    assert (
        revoke_direct_permissions(
            client,
            endpoint_name="managed",
            service_principal="app-sp",
            effective_group_names=set(),
        )
        is True
    )
    assert filters == ['applicationId eq "app-sp"']


def test_revoke_rejects_legacy_direct_without_mutating_acl() -> None:
    serving = _Serving(
        ServingEndpointPermissions(
            access_control_list=[_sp_entry("app-sp", "CAN_QUERY")]
        )
    )

    with pytest.raises(RuntimeError, match="residual direct or inherited group access"):
        revoke_direct_permissions(
            _client(serving),
            endpoint_name="managed",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )

    assert serving.replaced == []


def test_revoke_removes_managed_membership_before_reporting_direct_acl() -> None:
    serving = _Serving()
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="managed",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names=set(),
    )
    cast(
        list[ServingEndpointAccessControlResponse],
        serving.permissions["managed"].access_control_list,
    ).append(_sp_entry("app-sp", "CAN_QUERY"))

    with pytest.raises(RuntimeError, match="residual direct or inherited group access"):
        revoke_direct_permissions(
            client,
            endpoint_name="managed",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names={
                managed_query_group_name(
                    endpoint_id="managed-id",
                    application_id="app-sp",
                )
            },
        )

    managed_group = next(iter(groups.by_id.values()))
    assert managed_group.members == []
    assert _sp_entry("app-sp", "CAN_QUERY") in (
        serving.permissions["managed"].access_control_list or []
    )
    assert serving.replaced == []


def test_remove_rejects_unrelated_member_after_removing_exact_target() -> None:
    serving = _Serving()
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="managed",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names=set(),
    )
    managed_group = next(iter(groups.by_id.values()))
    managed_group.members.append(SimpleNamespace(value="unrelated-id"))

    with pytest.raises(RuntimeError, match="did not converge to exactly empty"):
        remove_managed_query_membership(
            client,
            endpoint_id="managed-id",
            application_id="app-sp",
            service_principal_id="sp-id",
        )

    assert [member.value for member in managed_group.members] == ["unrelated-id"]


def test_remove_rejects_member_added_concurrently_with_exact_removal() -> None:
    serving = _Serving()
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="managed",
        service_principal="app-sp",
        service_principal_id="sp-id",
        effective_group_names=set(),
    )
    managed_group = next(iter(groups.by_id.values()))
    groups.on_patch = lambda: managed_group.members.append(
        SimpleNamespace(value="concurrent-id")
    )

    with pytest.raises(RuntimeError, match="did not converge to exactly empty"):
        remove_managed_query_membership(
            client,
            endpoint_id="managed-id",
            application_id="app-sp",
            service_principal_id="sp-id",
        )

    assert [member.value for member in managed_group.members] == ["concurrent-id"]


def test_revoke_fails_when_effective_group_retains_query_access() -> None:
    serving = _Serving(
        ServingEndpointPermissions(
            access_control_list=[_group_entry("gateway-bypass", "CAN_QUERY")]
        )
    )

    with pytest.raises(RuntimeError, match="inherited group access"):
        revoke_direct_permissions(
            _client(serving),
            endpoint_name="managed",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names={"gateway-bypass"},
        )


def test_grant_rejects_parallel_effective_group_access() -> None:
    serving = _Serving(
        ServingEndpointPermissions(
            access_control_list=[_group_entry("gateway-bypass", "CAN_MANAGE")]
        )
    )

    with pytest.raises(RuntimeError, match="require only its managed query group"):
        grant_direct_can_query(
            _client(serving),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names={"gateway-bypass"},
        )


def test_default_group_resolution_uses_exact_scim_application_id_filter() -> None:
    serving = _Serving()
    groups = _Groups()
    filters: list[str] = []

    class _ServicePrincipals:
        def list(self, *, filter: str) -> object:
            filters.append(filter)
            return iter([SimpleNamespace(id="sp-id", application_id="app-sp")])

    grant_direct_can_query(
        SimpleNamespace(
            serving_endpoints=serving,
            service_principals=_ServicePrincipals(),
            groups=groups,
        ),
        endpoint_name="outer",
        service_principal="app-sp",
    )

    assert filters == ['applicationId eq "app-sp"']


class _GlobalServing:
    def __init__(
        self,
        permissions: dict[str, ServingEndpointPermissions],
        *,
        platform_foundation_endpoints: set[str] | None = None,
    ) -> None:
        self.permissions = permissions
        self.platform_foundation_endpoints = platform_foundation_endpoints or set()

    def list(self) -> object:
        return iter(SimpleNamespace(name=name) for name in self.permissions)

    def get(self, name: str) -> object:
        if name in self.platform_foundation_endpoints:
            return SimpleNamespace(
                id=None,
                creator=None,
                config=SimpleNamespace(
                    served_entities=[
                        SimpleNamespace(
                            foundation_model=SimpleNamespace(name="system.ai.databricks-model")
                        )
                    ]
                ),
            )
        return SimpleNamespace(id=f"{name}-id")

    def get_permissions(self, endpoint_id: str) -> ServingEndpointPermissions:
        return self.permissions[endpoint_id.removesuffix("-id")]

    @staticmethod
    def _response(request: object) -> ServingEndpointAccessControlResponse:
        value = cast(Any, request)
        level = value.permission_level
        return ServingEndpointAccessControlResponse(
            user_name=getattr(request, "user_name", None),
            group_name=getattr(request, "group_name", None),
            service_principal_name=getattr(request, "service_principal_name", None),
            all_permissions=[_permission(str(getattr(level, "value", level)))],
        )

    def update_permissions(
        self,
        endpoint_id: str,
        *,
        access_control_list: list[object],
    ) -> None:
        name = endpoint_id.removesuffix("-id")
        current = list(self.permissions[name].access_control_list or [])
        for request in access_control_list:
            principal_kind = (
                "group_name"
                if getattr(request, "group_name", None)
                else "service_principal_name"
            )
            principal = getattr(request, principal_kind, None)
            current = [
                entry
                for entry in current
                if getattr(entry, principal_kind, None) != principal
            ]
            current.append(self._response(request))
        self.permissions[name] = ServingEndpointPermissions(access_control_list=current)

    def set_permissions(
        self,
        endpoint_id: str,
        *,
        access_control_list: list[object],
    ) -> None:
        raise AssertionError("whole-ACL replacement is forbidden")


def _query_group_entry(endpoint: str, principal: str) -> ServingEndpointAccessControlResponse:
    return _group_entry(
        managed_query_group_name(
            endpoint_id=f"{endpoint}-id",
            application_id=principal,
        ),
        "CAN_QUERY",
    )


def test_legacy_query_detector_excludes_only_exact_runtime_manager() -> None:
    serving = _GlobalServing(
        {
            "runtime-only": ServingEndpointPermissions(
                access_control_list=[
                    _sp_entry("runtime-sp", "CAN_MANAGE"),
                    _group_entry("admins", "CAN_MANAGE"),
                ]
            ),
            "target-query": ServingEndpointPermissions(
                access_control_list=[
                    _sp_entry("runtime-sp", "CAN_MANAGE"),
                    _sp_entry("app-sp", "CAN_QUERY"),
                ]
            ),
            "runtime-query": ServingEndpointPermissions(
                access_control_list=[_sp_entry("runtime-sp", "CAN_QUERY")]
            ),
            "legacy-group-query": ServingEndpointPermissions(
                access_control_list=[_group_entry("legacy-query-group", "CAN_QUERY")]
            ),
        }
    )
    client = _client(serving)

    assert not endpoint_has_legacy_direct_query_principal(
        client,
        endpoint_name="runtime-only",
        runtime_manager_application_id="runtime-sp",
    )
    assert endpoint_has_legacy_direct_query_principal(
        client,
        endpoint_name="target-query",
        runtime_manager_application_id="runtime-sp",
    )
    assert endpoint_has_legacy_direct_query_principal(
        client,
        endpoint_name="runtime-query",
        runtime_manager_application_id="runtime-sp",
    )
    assert endpoint_has_legacy_direct_query_principal(
        client,
        endpoint_name="legacy-group-query",
        runtime_manager_application_id="runtime-sp",
    )


def test_legacy_query_detector_accepts_only_exact_approved_managed_group() -> None:
    managed_name = managed_query_group_name(
        endpoint_id="managed-id",
        application_id="app-sp",
    )
    serving = _GlobalServing(
        {
            "managed": ServingEndpointPermissions(
                access_control_list=[
                    _sp_entry("runtime-sp", "CAN_MANAGE"),
                    _group_entry(managed_name, "CAN_QUERY"),
                ]
            )
        }
    )
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("sp-id",),
    )
    client = _client(serving, groups)

    assert not endpoint_has_legacy_direct_query_principal(
        client,
        endpoint_name="managed",
        runtime_manager_application_id="runtime-sp",
        approved_managed_query_application_ids=("app-sp",),
    )
    with pytest.raises(RuntimeError, match="empty managed serving-query group"):
        endpoint_has_legacy_direct_query_principal(
            client,
            endpoint_name="managed",
            runtime_manager_application_id="runtime-sp",
            approved_empty_managed_query_application_ids=("app-sp",),
        )
    next(iter(groups.by_id.values())).members = []
    assert not endpoint_has_legacy_direct_query_principal(
        client,
        endpoint_name="managed",
        runtime_manager_application_id="runtime-sp",
        approved_empty_managed_query_application_ids=("app-sp",),
    )
    next(iter(groups.by_id.values())).members.append(
        SimpleNamespace(value="unrelated-id")
    )
    with pytest.raises(RuntimeError, match="unrelated member"):
        endpoint_has_legacy_direct_query_principal(
            client,
            endpoint_name="managed",
            runtime_manager_application_id="runtime-sp",
            approved_managed_query_application_ids=("app-sp",),
        )


def test_inspect_exact_query_access_mode_distinguishes_all_transition_modes() -> None:
    managed_name = managed_query_group_name(
        endpoint_id="managed-id",
        application_id="proxy-sp",
    )
    mixed_name = managed_query_group_name(
        endpoint_id="mixed-id",
        application_id="proxy-sp",
    )
    serving = _GlobalServing(
        {
            "managed": ServingEndpointPermissions(
                access_control_list=[_group_entry(managed_name, "CAN_QUERY")]
            ),
            "direct": ServingEndpointPermissions(
                access_control_list=[_sp_entry("proxy-sp", "CAN_QUERY")]
            ),
            "mixed": ServingEndpointPermissions(
                access_control_list=[
                    _sp_entry("proxy-sp", "CAN_QUERY"),
                    _group_entry(mixed_name, "CAN_QUERY"),
                ]
            ),
            "none": ServingEndpointPermissions(access_control_list=[]),
        }
    )
    query_groups = _Groups()
    for endpoint in ("managed", "mixed"):
        _seed_managed_group(
            query_groups,
            endpoint=endpoint,
            application_id="proxy-sp",
            member_ids=("proxy-id",),
        )
    client = _client(serving, query_groups)
    groups = {managed_name, mixed_name}

    for endpoint, expected in (
        ("managed", "managed"),
        ("direct", "direct"),
        ("mixed", "mixed"),
        ("none", "none"),
    ):
        assert (
            inspect_exact_query_access_mode(
                client,
                endpoint_name=endpoint,
                service_principal="proxy-sp",
                service_principal_id="proxy-id",
                effective_group_names=groups,
            )
            == expected
        )


def test_inspect_exact_query_access_mode_rejects_broader_direct_acl() -> None:
    serving = _GlobalServing(
        {
            "managed": ServingEndpointPermissions(
                access_control_list=[_sp_entry("proxy-sp", "CAN_MANAGE")]
            )
        }
    )

    with pytest.raises(RuntimeError, match="non-exact direct principal ACL"):
        inspect_exact_query_access_mode(
            _client(serving),
            endpoint_name="managed",
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names=set(),
        )


def test_inspect_exact_query_access_mode_rejects_group_member_drift() -> None:
    managed_name = managed_query_group_name(
        endpoint_id="managed-id",
        application_id="proxy-sp",
    )
    serving = _GlobalServing(
        {
            "managed": ServingEndpointPermissions(
                access_control_list=[_group_entry(managed_name, "CAN_QUERY")]
            )
        }
    )
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="proxy-sp",
        member_ids=("proxy-id", "unrelated-id"),
    )

    with pytest.raises(RuntimeError, match="membership contract drifted"):
        inspect_exact_query_access_mode(
            _client(serving, groups),
            endpoint_name="managed",
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names={managed_name},
        )


def test_exact_serving_convergence_grants_targets_and_revokes_stale_access() -> None:
    serving = _GlobalServing(
        {
            "green": ServingEndpointPermissions(access_control_list=[]),
            "blue": ServingEndpointPermissions(access_control_list=[]),
            "stale": ServingEndpointPermissions(access_control_list=[]),
            "foundation": ServingEndpointPermissions(access_control_list=[]),
        },
        platform_foundation_endpoints={"foundation"},
    )
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="stale",
        service_principal="proxy-sp",
        service_principal_id="proxy-id",
        effective_group_names=set(),
    )

    converge_exact_direct_can_query(
        client,
        reviewed_endpoint_names=("green", "blue"),
        service_principal="proxy-sp",
        service_principal_id="proxy-id",
        effective_group_names=set(),
    )

    for endpoint in ("green", "blue"):
        entry = serving.permissions[endpoint].access_control_list[0]
        assert entry.group_name == managed_query_group_name(
            endpoint_id=f"{endpoint}-id",
            application_id="proxy-sp",
        )
        assert entry.all_permissions[0].permission_level == (
            ServingEndpointPermissionLevel.CAN_QUERY
        )
    stale_group = next(
        group
        for group in groups.by_id.values()
        if group.display_name
        == managed_query_group_name(
            endpoint_id="stale-id",
            application_id="proxy-sp",
        )
    )
    assert stale_group.members == []
    assert serving.permissions["stale"].access_control_list == [
        _query_group_entry("stale", "proxy-sp")
    ]


def test_exact_serving_convergence_preserves_legacy_pin_and_manages_green() -> None:
    serving = _GlobalServing(
        {
            "green": ServingEndpointPermissions(access_control_list=[]),
            "blue": ServingEndpointPermissions(
                access_control_list=[_sp_entry("proxy-sp", "CAN_QUERY")]
            ),
            "stale": ServingEndpointPermissions(access_control_list=[]),
        }
    )
    groups = _Groups()
    client = _client(serving, groups)
    grant_direct_can_query(
        client,
        endpoint_name="stale",
        service_principal="proxy-sp",
        service_principal_id="proxy-id",
        effective_group_names=set(),
    )
    blue_before = tuple(serving.permissions["blue"].access_control_list or [])

    converge_exact_direct_can_query(
        client,
        reviewed_endpoint_names=("green", "blue"),
        service_principal="proxy-sp",
        service_principal_id="proxy-id",
        effective_group_names=set(),
        legacy_pinned_endpoint_names=("blue",),
    )

    assert tuple(serving.permissions["blue"].access_control_list or []) == blue_before
    assert serving.permissions["green"].access_control_list == [
        _query_group_entry("green", "proxy-sp")
    ]
    stale_group = next(
        group
        for group in groups.by_id.values()
        if group.display_name
        == managed_query_group_name(
            endpoint_id="stale-id",
            application_id="proxy-sp",
        )
    )
    assert stale_group.members == []


def test_exact_serving_convergence_rejects_empty_pin_before_mutation() -> None:
    serving = _GlobalServing(
        {
            "green": ServingEndpointPermissions(access_control_list=[]),
            "blue": ServingEndpointPermissions(access_control_list=[]),
        }
    )

    with pytest.raises(RuntimeError, match="has no exact query access to preserve"):
        converge_exact_direct_can_query(
            _client(serving),
            reviewed_endpoint_names=("green", "blue"),
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names=set(),
            legacy_pinned_endpoint_names=("blue",),
        )

    assert serving.permissions["green"].access_control_list == []
    assert serving.permissions["blue"].access_control_list == []


def test_global_serving_revoke_all_fails_on_effective_group_access() -> None:
    serving = _GlobalServing(
        {
            "target": ServingEndpointPermissions(
                access_control_list=[_group_entry("proxy-group", "CAN_VIEW")]
            )
        }
    )

    with pytest.raises(RuntimeError, match="effective serving permission remains"):
        revoke_all_direct_permissions(
            _client(serving),
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names={"proxy-group"},
        )


def test_global_serving_revoke_all_attempts_every_endpoint_after_group_failure() -> None:
    serving = _GlobalServing(
        {
            "alpha": ServingEndpointPermissions(access_control_list=[]),
            "beta": ServingEndpointPermissions(access_control_list=[]),
        }
    )
    groups = _Groups()
    client = _client(serving, groups)
    for endpoint in ("alpha", "beta"):
        grant_direct_can_query(
            client,
            endpoint_name=endpoint,
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names=set(),
        )
    calls = 0

    def fail_first_patch() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected SCIM failure")

    groups.on_patch = fail_first_patch

    with pytest.raises(RuntimeError, match="customer-serving deny policy did not converge"):
        revoke_all_direct_permissions(
            client,
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names={
                managed_query_group_name(
                    endpoint_id=f"{endpoint}-id",
                    application_id="proxy-sp",
                )
                for endpoint in ("alpha", "beta")
            },
        )

    beta_group = next(
        group
        for group in groups.by_id.values()
        if group.display_name
        == managed_query_group_name(
            endpoint_id="beta-id",
            application_id="proxy-sp",
        )
    )
    assert beta_group.members == []


def test_global_serving_audit_rejects_unrelated_endpoint_access() -> None:
    serving = _GlobalServing(
        {
            "reviewed": ServingEndpointPermissions(
                access_control_list=[_query_group_entry("reviewed", "verifier-sp")]
            ),
            "unrelated": ServingEndpointPermissions(
                access_control_list=[_sp_entry("verifier-sp", "CAN_VIEW")]
            ),
        }
    )

    with pytest.raises(RuntimeError, match="unrelated serving endpoint 'unrelated'"):
        audit_global_serving_endpoint_access(
            _client_with_managed_groups(
                serving,
                ("reviewed", "verifier-sp", "verifier-id"),
            ),
            reviewed_endpoint_names=("reviewed",),
            service_principal="verifier-sp",
            effective_group_names={
                managed_query_group_name(
                    endpoint_id="reviewed-id",
                    application_id="verifier-sp",
                )
            },
        )


def test_global_serving_audit_accepts_exact_reviewed_endpoint_only() -> None:
    serving = _GlobalServing(
        {
            "reviewed": ServingEndpointPermissions(
                access_control_list=[_query_group_entry("reviewed", "verifier-sp")]
            ),
            "unrelated": ServingEndpointPermissions(access_control_list=[]),
        }
    )

    audit_global_serving_endpoint_access(
        _client_with_managed_groups(
            serving,
            ("reviewed", "verifier-sp", "verifier-id"),
        ),
        reviewed_endpoint_names=("reviewed",),
        service_principal="verifier-sp",
        effective_group_names={
            managed_query_group_name(
                endpoint_id="reviewed-id",
                application_id="verifier-sp",
            )
        },
    )


def test_global_serving_audit_rehydrates_and_rejects_later_unrelated_member() -> None:
    serving = _GlobalServing(
        {
            "reviewed": ServingEndpointPermissions(
                access_control_list=[_query_group_entry("reviewed", "verifier-sp")]
            )
        }
    )
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="reviewed",
        application_id="verifier-sp",
        member_ids=("verifier-id",),
    )
    client = _client(serving, groups)
    audit_global_serving_endpoint_access(
        client,
        reviewed_endpoint_names=("reviewed",),
        service_principal="verifier-sp",
        service_principal_id="verifier-id",
        effective_group_names={
            managed_query_group_name(
                endpoint_id="reviewed-id",
                application_id="verifier-sp",
            )
        },
    )
    next(iter(groups.by_id.values())).members.append(
        SimpleNamespace(value="later-unrelated-id")
    )

    with pytest.raises(RuntimeError, match="membership contract drifted"):
        audit_global_serving_endpoint_access(
            client,
            reviewed_endpoint_names=("reviewed",),
            service_principal="verifier-sp",
            service_principal_id="verifier-id",
            effective_group_names={
                managed_query_group_name(
                    endpoint_id="reviewed-id",
                    application_id="verifier-sp",
                )
            },
        )


def test_global_serving_audit_accepts_exact_legacy_pinned_transition_modes() -> None:
    mixed_group = managed_query_group_name(
        endpoint_id="mixed-id",
        application_id="verifier-sp",
    )
    managed_group = managed_query_group_name(
        endpoint_id="managed-id",
        application_id="verifier-sp",
    )
    serving = _GlobalServing(
        {
            "direct": ServingEndpointPermissions(
                access_control_list=[_sp_entry("verifier-sp", "CAN_QUERY")]
            ),
            "mixed": ServingEndpointPermissions(
                access_control_list=[
                    _sp_entry("verifier-sp", "CAN_QUERY"),
                    _group_entry(mixed_group, "CAN_QUERY"),
                ]
            ),
            "managed": ServingEndpointPermissions(
                access_control_list=[_group_entry(managed_group, "CAN_QUERY")]
            ),
        }
    )

    audit_global_serving_endpoint_access(
        _client_with_managed_groups(
            serving,
            ("mixed", "verifier-sp", "verifier-id"),
            ("managed", "verifier-sp", "verifier-id"),
        ),
        reviewed_endpoint_names=("direct", "mixed", "managed"),
        service_principal="verifier-sp",
        service_principal_id="verifier-id",
        effective_group_names={mixed_group, managed_group},
        legacy_pinned_endpoint_names=("direct", "mixed"),
    )


def test_global_serving_audit_keeps_unpinned_query_endpoints_managed_only() -> None:
    serving = _GlobalServing(
        {
            "direct": ServingEndpointPermissions(
                access_control_list=[_sp_entry("verifier-sp", "CAN_QUERY")]
            )
        }
    )

    with pytest.raises(RuntimeError, match="approved exact query-access mode"):
        audit_global_serving_endpoint_access(
            _client(serving),
            reviewed_endpoint_names=("direct",),
            service_principal="verifier-sp",
            service_principal_id="verifier-id",
            effective_group_names=set(),
        )


def test_global_serving_audit_routes_platform_foundation_models_to_uc_inventory() -> None:
    serving = _GlobalServing(
        {
            "reviewed": ServingEndpointPermissions(
                access_control_list=[_query_group_entry("reviewed", "verifier-sp")]
            ),
            "databricks-foundation": ServingEndpointPermissions(access_control_list=[]),
        },
        platform_foundation_endpoints={"databricks-foundation"},
    )

    audit_global_serving_endpoint_access(
        _client_with_managed_groups(
            serving,
            ("reviewed", "verifier-sp", "verifier-id"),
        ),
        reviewed_endpoint_names=("reviewed",),
        service_principal="verifier-sp",
        effective_group_names={
            managed_query_group_name(
                endpoint_id="reviewed-id",
                application_id="verifier-sp",
            )
        },
    )


def test_global_serving_audit_rejects_hidden_parent_with_managed_group() -> None:
    serving = _GlobalServing(
        {
            "reviewed": ServingEndpointPermissions(
                access_control_list=[_query_group_entry("reviewed", "verifier-sp")]
            ),
            "unrelated": ServingEndpointPermissions(
                access_control_list=[
                    _group_entry("hidden-account-parent", "CAN_VIEW")
                ]
            ),
        }
    )

    with pytest.raises(RuntimeError, match="unrelated serving endpoint 'unrelated'"):
        audit_global_serving_endpoint_access(
            _client_with_managed_groups(
                serving,
                ("reviewed", "verifier-sp", "verifier-id"),
            ),
            reviewed_endpoint_names=("reviewed",),
            service_principal="verifier-sp",
            effective_group_names={
                "hidden-account-parent",
                managed_query_group_name(
                    endpoint_id="reviewed-id",
                    application_id="verifier-sp",
                ),
            },
        )


def test_global_serving_audit_rejects_duplicate_service_principal_rows() -> None:
    serving = _GlobalServing(
        {
            "reviewed": ServingEndpointPermissions(
                access_control_list=[
                    _sp_entry("runtime-sp", "CAN_QUERY"),
                    _sp_entry("runtime-sp", "CAN_MANAGE"),
                ]
            )
        }
    )

    with pytest.raises(RuntimeError, match="duplicate entries"):
        audit_global_serving_endpoint_access(
            _client_with_managed_groups(
                serving,
                ("reviewed", "verifier-sp", "verifier-id"),
            ),
            reviewed_endpoint_names=("reviewed",),
            service_principal="runtime-sp",
            expected_permission_level="CAN_MANAGE",
            effective_group_names=set(),
        )


def test_global_serving_audit_rejects_duplicate_managed_group_rows() -> None:
    managed = _query_group_entry("reviewed", "verifier-sp")
    serving = _GlobalServing(
        {
            "reviewed": ServingEndpointPermissions(
                access_control_list=[managed, managed]
            )
        }
    )
    group_name = managed.group_name
    assert group_name

    with pytest.raises(RuntimeError, match="duplicate entries for group"):
        audit_global_serving_endpoint_access(
            _client(serving),
            reviewed_endpoint_names=("reviewed",),
            service_principal="verifier-sp",
            effective_group_names={group_name},
        )


def test_global_serving_audit_accepts_runtime_owner_on_exact_agent_endpoints() -> None:
    serving = _GlobalServing(
        {
            "mip-agent-supervisor": ServingEndpointPermissions(
                access_control_list=[_sp_entry("runtime-sp", "CAN_MANAGE")]
            ),
            "mip-agent-gateway": ServingEndpointPermissions(
                access_control_list=[_sp_entry("runtime-sp", "CAN_MANAGE")]
            ),
            "unrelated": ServingEndpointPermissions(access_control_list=[]),
        }
    )

    audit_global_serving_endpoint_access(
        _client(serving),
        reviewed_endpoint_names=("mip-agent-supervisor", "mip-agent-gateway"),
        service_principal="runtime-sp",
        expected_permission_level="CAN_MANAGE",
        effective_group_names=set(),
    )


def test_global_serving_audit_rejects_runtime_access_to_unrelated_endpoint() -> None:
    serving = _GlobalServing(
        {
            "mip-agent-supervisor": ServingEndpointPermissions(
                access_control_list=[_sp_entry("runtime-sp", "CAN_MANAGE")]
            ),
            "mip-agent-gateway": ServingEndpointPermissions(
                access_control_list=[_sp_entry("runtime-sp", "CAN_MANAGE")]
            ),
            "unrelated": ServingEndpointPermissions(
                access_control_list=[_sp_entry("runtime-sp", "CAN_QUERY")]
            ),
        }
    )

    with pytest.raises(RuntimeError, match="unrelated serving endpoint 'unrelated'"):
        audit_global_serving_endpoint_access(
            _client(serving),
            reviewed_endpoint_names=("mip-agent-supervisor", "mip-agent-gateway"),
            service_principal="runtime-sp",
            expected_permission_level="CAN_MANAGE",
            effective_group_names=set(),
        )


def test_global_serving_audit_rejects_runtime_group_access_on_reviewed_endpoint() -> None:
    serving = _GlobalServing(
        {
            "mip-agent-supervisor": ServingEndpointPermissions(
                access_control_list=[
                    _sp_entry("runtime-sp", "CAN_MANAGE"),
                    _group_entry("runtime-owners", "CAN_MANAGE"),
                ]
            ),
            "mip-agent-gateway": ServingEndpointPermissions(
                access_control_list=[_sp_entry("runtime-sp", "CAN_MANAGE")]
            ),
        }
    )

    with pytest.raises(
        RuntimeError,
        match="exact customer-serving CAN_MANAGE audit failed",
    ):
        audit_global_serving_endpoint_access(
            _client(serving),
            reviewed_endpoint_names=("mip-agent-supervisor", "mip-agent-gateway"),
            service_principal="runtime-sp",
            expected_permission_level="CAN_MANAGE",
            effective_group_names={"runtime-owners"},
        )
