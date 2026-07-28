from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid5

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlResponse,
    ServingEndpointPermission,
    ServingEndpointPermissionLevel,
    ServingEndpointPermissions,
)

from tools.databricks.serving_endpoint_acl import (
    audit_global_serving_endpoint_access as _audit_global_serving_endpoint_access,
)
from tools.databricks.serving_endpoint_acl import (
    converge_exact_direct_can_query as _converge_exact_direct_can_query,
)
from tools.databricks.serving_endpoint_acl import (
    endpoint_has_legacy_direct_query_principal,
)
from tools.databricks.serving_endpoint_acl import (
    grant_direct_can_query as _grant_direct_can_query,
)
from tools.databricks.serving_endpoint_acl import (
    inspect_exact_query_access_mode as _inspect_exact_query_access_mode,
)
from tools.databricks.serving_endpoint_acl import (
    revoke_all_direct_permissions as _revoke_all_direct_permissions,
)
from tools.databricks.serving_endpoint_acl import (
    revoke_direct_permissions as _revoke_direct_permissions,
)
from tools.databricks.serving_query_group_access import (
    assert_managed_query_group_members,
    inspect_managed_query_group,
    managed_query_group_external_id,
    managed_query_group_name,
)
from tools.databricks.serving_query_group_access import (
    remove_managed_query_membership as _remove_managed_query_membership,
)
from tools.databricks.serving_query_group_access import (
    retire_managed_query_group as _retire_managed_query_group,
)
from tools.databricks.serving_query_group_provenance import (
    MissingClaimedGroupProvenanceError,
    intent_external_id,
)

_APP = "mip-app"
_LEASE = "11111111-1111-4111-8111-111111111111"
_SOURCE = "a" * 40
_NONCE_NAMESPACE = UUID("22222222-2222-4222-8222-222222222222")


def _intent_record(
    *,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    group_id: str = "",
) -> dict[str, str]:
    nonce = str(
        uuid5(
            _NONCE_NAMESPACE,
            f"{endpoint_id}\0{application_id}\0{service_principal_id}",
        )
    )
    return {
        "endpoint_id": endpoint_id,
        "application_id": application_id,
        "service_principal_id": service_principal_id,
        "group_name": managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        "external_id": intent_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
            creation_nonce=nonce,
        ),
        "creation_nonce": nonce,
        "group_id": group_id,
    }


def grant_direct_can_query(*args: object, **kwargs: Any) -> None:
    """Exercise production grants with the required deployment writer."""

    kwargs.setdefault("app_name", _APP)
    kwargs.setdefault("deployment_lease_id", _LEASE)
    kwargs.setdefault("deployment_source_git_sha", _SOURCE)
    kwargs.setdefault("assert_single_writer", lambda: None)
    _grant_direct_can_query(*args, **kwargs)


def converge_exact_direct_can_query(*args: object, **kwargs: Any) -> None:
    """Exercise production convergence with the required deployment writer."""

    kwargs.setdefault("app_name", _APP)
    kwargs.setdefault("deployment_lease_id", _LEASE)
    kwargs.setdefault("deployment_source_git_sha", _SOURCE)
    kwargs.setdefault("assert_single_writer", lambda: None)
    _converge_exact_direct_can_query(*args, **kwargs)


def inspect_exact_query_access_mode(*args: object, **kwargs: Any) -> object:
    kwargs.setdefault("app_name", _APP)
    return _inspect_exact_query_access_mode(*args, **kwargs)


def audit_global_serving_endpoint_access(*args: object, **kwargs: Any) -> None:
    kwargs.setdefault("app_name", _APP)
    _audit_global_serving_endpoint_access(*args, **kwargs)


def revoke_direct_permissions(*args: object, **kwargs: Any) -> bool:
    kwargs.setdefault("app_name", _APP)
    kwargs.setdefault("assert_single_writer", lambda: None)
    return _revoke_direct_permissions(*args, **kwargs)


def revoke_all_direct_permissions(*args: object, **kwargs: Any) -> None:
    kwargs.setdefault("app_name", _APP)
    kwargs.setdefault("assert_single_writer", lambda: None)
    _revoke_all_direct_permissions(*args, **kwargs)


def remove_managed_query_membership(*args: object, **kwargs: Any) -> bool:
    kwargs.setdefault("app_name", _APP)
    kwargs.setdefault("assert_single_writer", lambda: None)
    return _remove_managed_query_membership(*args, **kwargs)


def retire_managed_query_group(*args: object, **kwargs: Any) -> bool:
    kwargs.setdefault("app_name", _APP)
    return _retire_managed_query_group(*args, **kwargs)


@pytest.fixture(autouse=True)
def _serving_query_group_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model nonce-bound intent/claim transitions without name-based adoption."""

    from tools.databricks import serving_query_group_access as access

    def prepare(
        workspace: object,
        *,
        app_name: str,
        deployment_lease_id: str,
        deployment_source_git_sha: str,
        endpoint_id: str,
        application_id: str,
        service_principal_id: str,
        group_name: str,
        **_kwargs: object,
    ) -> dict[str, str]:
        assert app_name == _APP
        assert deployment_lease_id == _LEASE
        assert deployment_source_git_sha == _SOURCE
        record = _intent_record(
            endpoint_id=endpoint_id,
            application_id=application_id,
            service_principal_id=service_principal_id,
        )
        assert group_name == record["group_name"]
        key = (endpoint_id, application_id, service_principal_id)
        return workspace.groups.provenance_records.setdefault(key, record)

    def claim(
        workspace: object,
        *,
        record: dict[str, str],
        group_id: str,
        **_kwargs: object,
    ) -> dict[str, str]:
        key = (
            record["endpoint_id"],
            record["application_id"],
            record["service_principal_id"],
        )
        if workspace.groups.provenance_records.get(key) != record:
            raise AssertionError("unit serving-query provenance changed")
        group = workspace.groups.get(group_id)
        assert group.display_name == record["group_name"]
        assert group.external_id == record["external_id"]
        claimed = {**record, "group_id": group_id}
        workspace.groups.provenance_records[key] = claimed
        return claimed

    def require_claimed(
        workspace: object,
        *,
        app_name: str,
        endpoint_id: str,
        application_id: str,
        service_principal_id: str,
        group_name: str,
    ) -> dict[str, str]:
        assert app_name == _APP
        key = (endpoint_id, application_id, service_principal_id)
        record = workspace.groups.provenance_records.get(key)
        if record is None or not record["group_id"]:
            raise MissingClaimedGroupProvenanceError(
                "managed serving-query group has no signed immutable-ID provenance"
            )
        assert group_name == record["group_name"]
        return record

    monkeypatch.setattr(access.group_provenance, "prepare", prepare)
    monkeypatch.setattr(access.group_provenance, "claim", claim)
    monkeypatch.setattr(access.group_provenance, "require_claimed", require_claimed)


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
        self.provenance_records: dict[
            tuple[str, str, str],
            dict[str, str],
        ] = {}
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
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )
        self.by_id[group.id] = group
        return group

    def get(self, group_id: str) -> object:
        if group_id not in self.by_id:
            raise NotFound("missing")
        return self.by_id[group_id]

    def patch(self, **kwargs: object) -> None:
        self.patch_calls.append(kwargs)
        group = self.by_id[str(kwargs["id"])]
        for operation in cast(Any, kwargs["operations"]):
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
                entry for entry in current if getattr(entry, "group_name", None) != group_name
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
    service_principal_id: str | None = None,
) -> None:
    endpoint_id = f"{endpoint}-id"
    principal_id = service_principal_id or {
        "app-sp": "sp-id",
        "proxy-sp": "proxy-id",
        "verifier-sp": "verifier-id",
    }.get(application_id, f"{application_id}-id")
    record = _intent_record(
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
    )
    group = groups.create(
        display_name=record["group_name"],
        external_id=record["external_id"],
    )
    group.members = [SimpleNamespace(value=value) for value in member_ids]
    groups.provenance_records[
        (endpoint_id, application_id, principal_id)
    ] = {**record, "group_id": group.id}


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

    assert external_id == ("mip:serving-query:c0PGegchStNce40d1OixrE9hqq_OUFgGhDUXHwmpXWk")
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
        expected_external_id=next(iter(groups.provenance_records.values()))[
            "external_id"
        ],
    )

    assert state is not None
    assert state.contract.name == managed_query_group_name(
        endpoint_id="managed-id",
        application_id="app-sp",
    )
    assert state.contract.external_id == next(
        iter(groups.provenance_records.values())
    )["external_id"]
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
            expected_external_id=next(iter(groups.provenance_records.values()))[
                "external_id"
            ],
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
        assert_endpoint_absent=lambda: None,
        assert_single_writer=lambda: None,
        sleep=lambda _seconds: None,
    )
    assert not retire_managed_query_group(
        client,
        endpoint_id="managed-id",
        application_id="app-sp",
        service_principal_id="sp-id",
        assert_endpoint_absent=lambda: None,
        assert_single_writer=lambda: None,
        sleep=lambda _seconds: None,
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
            assert_endpoint_absent=lambda: None,
            assert_single_writer=lambda: None,
        )


def test_managed_query_group_delete_rechecks_lease_after_hydration() -> None:
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("sp-id",),
    )

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        retire_managed_query_group(
            _client(_Serving(), groups),
            endpoint_id="managed-id",
            application_id="app-sp",
            service_principal_id="sp-id",
            assert_endpoint_absent=lambda: None,
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
        )

    assert "group-1" in groups.by_id


def test_managed_query_group_delete_rechecks_lease_after_final_state_read() -> None:
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("sp-id",),
    )
    lease_checks = 0

    def lose_lease_at_mutation_boundary() -> None:
        nonlocal lease_checks
        lease_checks += 1
        if lease_checks == 2:
            raise RuntimeError("deployment lease lost")

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        retire_managed_query_group(
            _client(_Serving(), groups),
            endpoint_id="managed-id",
            application_id="app-sp",
            service_principal_id="sp-id",
            assert_endpoint_absent=lambda: None,
            assert_single_writer=lose_lease_at_mutation_boundary,
            sleep=lambda _seconds: None,
        )

    assert lease_checks == 2
    assert "group-1" in groups.by_id


def test_managed_query_group_retirement_waits_for_delayed_scim_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=("sp-id",),
    )
    deleting = False
    postflight_cycles = 0
    original_get = groups.get
    original_list = groups.list
    deletes: list[str] = []

    def delayed_delete(group_id: str) -> None:
        nonlocal deleting
        deletes.append(group_id)
        deleting = True

    def delayed_get(group_id: str) -> object:
        if deleting and postflight_cycles >= 2:
            raise NotFound("delayed SCIM absence")
        return original_get(group_id)

    def delayed_list(**kwargs: object) -> object:
        nonlocal postflight_cycles
        if not deleting:
            return original_list(**kwargs)
        visible = postflight_cycles < 2
        postflight_cycles += 1
        return original_list(**kwargs) if visible else iter(())

    monkeypatch.setattr(groups, "delete", delayed_delete)
    monkeypatch.setattr(groups, "get", delayed_get)
    monkeypatch.setattr(groups, "list", delayed_list)
    sleeps: list[float] = []

    assert retire_managed_query_group(
        _client(_Serving(), groups),
        endpoint_id="managed-id",
        application_id="app-sp",
        service_principal_id="sp-id",
        assert_endpoint_absent=lambda: None,
        assert_single_writer=lambda: None,
        timeout_s=20,
        sleep=sleeps.append,
        clock=lambda: 0,
    )

    assert deletes == ["group-1"]
    assert postflight_cycles == 5
    assert sleeps == [2, 2, 2, 2]


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


def test_grant_requires_deployment_writer() -> None:
    with pytest.raises(RuntimeError, match="requires the deployment lease"):
        _grant_direct_can_query(
            _client(_Serving()),
            app_name=_APP,
            deployment_lease_id=_LEASE,
            deployment_source_git_sha=_SOURCE,
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )


def test_grant_rejects_legacy_direct_permission() -> None:
    serving = _Serving(
        ServingEndpointPermissions(access_control_list=[_sp_entry("app-sp", "CAN_MANAGE")])
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


def test_serving_group_create_requires_live_lease_at_mutation_boundary() -> None:
    class _NoCreateAfterLeaseLoss(_Groups):
        def create(self, *, display_name: str, external_id: str) -> object:
            pytest.fail(f"lease loss must prevent group create: {display_name=} {external_id=}")

    serving = _Serving()
    groups = _NoCreateAfterLeaseLoss()
    with pytest.raises(RuntimeError, match="deployment lease lost"):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
        )

    assert serving.updated == []
    assert groups.patch_calls == []


def test_serving_acl_update_rechecks_lease_after_group_hydration() -> None:
    serving = _Serving()
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="outer",
        application_id="app-sp",
        member_ids=(),
    )

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
        )

    assert serving.updated == []
    assert groups.patch_calls == []


def test_acl_update_commit_then_error_quiesces_signed_managed_group() -> None:
    serving = _Serving()
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="outer",
        application_id="app-sp",
        member_ids=("sp-id",),
    )
    serving.on_update = lambda: (_ for _ in ()).throw(
        TimeoutError("response lost after ACL update committed")
    )

    with pytest.raises(TimeoutError, match="ACL update committed"):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )

    assert next(iter(groups.by_id.values())).members == []
    assert len(serving.updated) == 1


def test_serving_membership_patch_rechecks_lease_after_acl_read() -> None:
    group_name = managed_query_group_name(
        endpoint_id="outer-id",
        application_id="app-sp",
    )
    serving = _Serving(
        ServingEndpointPermissions(access_control_list=[_group_entry(group_name, "CAN_QUERY")])
    )
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="outer",
        application_id="app-sp",
        member_ids=(),
    )

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
        )

    assert serving.updated == []
    assert groups.patch_calls == []


def test_grant_postflight_rejects_member_added_with_endpoint_acl() -> None:
    serving = _Serving()
    groups = _Groups()

    def inject_unrelated_member() -> None:
        next(iter(groups.by_id.values())).members.append(SimpleNamespace(value="concurrent-id"))

    serving.on_update = inject_unrelated_member
    with pytest.raises(RuntimeError, match="contains an unrelated member"):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )

    assert next(iter(groups.by_id.values())).members == []


def test_grant_quiesces_drifted_signed_group_rejected_during_ensure() -> None:
    group_name = managed_query_group_name(
        endpoint_id="outer-id",
        application_id="app-sp",
    )
    serving = _Serving(
        ServingEndpointPermissions(
            access_control_list=[_group_entry(group_name, "CAN_QUERY")]
        )
    )
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="outer",
        application_id="app-sp",
        member_ids=("sp-id", "unrelated-id"),
    )

    with pytest.raises(RuntimeError, match="contains an unrelated member"):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )

    assert next(iter(groups.by_id.values())).members == []


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


def test_revoke_uses_signed_immutable_id_when_name_list_projection_is_hidden() -> None:
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
    groups.list = lambda **_kwargs: iter(())

    assert (
        revoke_direct_permissions(
            client,
            endpoint_name="managed",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )
        is True
    )
    assert next(iter(groups.by_id.values())).members == []


def test_revoke_rejects_legacy_direct_without_mutating_acl() -> None:
    serving = _Serving(
        ServingEndpointPermissions(access_control_list=[_sp_entry("app-sp", "CAN_QUERY")])
    )
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=(),
    )

    with pytest.raises(RuntimeError, match="residual direct or inherited group access"):
        revoke_direct_permissions(
            _client(serving, groups),
            endpoint_name="managed",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names=set(),
        )

    assert serving.replaced == []


def test_revoke_rejects_direct_acl_before_managed_membership_mutation() -> None:
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
    assert [member.value for member in managed_group.members] == ["sp-id"]
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
    groups.on_patch = lambda: managed_group.members.append(SimpleNamespace(value="concurrent-id"))

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
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="managed",
        application_id="app-sp",
        member_ids=(),
    )

    with pytest.raises(RuntimeError, match="inherited group access"):
        revoke_direct_permissions(
            _client(serving, groups),
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
    groups = _Groups()

    with pytest.raises(
        RuntimeError,
        match="require only its managed query group",
    ):
        grant_direct_can_query(
            _client(serving, groups),
            endpoint_name="outer",
            service_principal="app-sp",
            service_principal_id="sp-id",
            effective_group_names={"gateway-bypass"},
        )

    assert next(iter(groups.by_id.values())).members == []


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
                "group_name" if getattr(request, "group_name", None) else "service_principal_name"
            )
            principal = getattr(request, principal_kind, None)
            current = [
                entry for entry in current if getattr(entry, principal_kind, None) != principal
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
        app_name=_APP,
        endpoint_name="runtime-only",
        runtime_manager_application_id="runtime-sp",
    )
    assert endpoint_has_legacy_direct_query_principal(
        client,
        app_name=_APP,
        endpoint_name="target-query",
        runtime_manager_application_id="runtime-sp",
    )
    assert endpoint_has_legacy_direct_query_principal(
        client,
        app_name=_APP,
        endpoint_name="runtime-query",
        runtime_manager_application_id="runtime-sp",
    )
    assert endpoint_has_legacy_direct_query_principal(
        client,
        app_name=_APP,
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
        app_name=_APP,
        endpoint_name="managed",
        runtime_manager_application_id="runtime-sp",
        approved_managed_query_application_ids=("app-sp",),
    )
    with pytest.raises(RuntimeError, match="empty managed serving-query group"):
        endpoint_has_legacy_direct_query_principal(
            client,
            app_name=_APP,
            endpoint_name="managed",
            runtime_manager_application_id="runtime-sp",
            approved_empty_managed_query_application_ids=("app-sp",),
        )
    next(iter(groups.by_id.values())).members = []
    assert not endpoint_has_legacy_direct_query_principal(
        client,
        app_name=_APP,
        endpoint_name="managed",
        runtime_manager_application_id="runtime-sp",
        approved_empty_managed_query_application_ids=("app-sp",),
    )
    next(iter(groups.by_id.values())).members.append(SimpleNamespace(value="unrelated-id"))
    with pytest.raises(RuntimeError, match="unrelated member"):
        endpoint_has_legacy_direct_query_principal(
            client,
            app_name=_APP,
            endpoint_name="managed",
            runtime_manager_application_id="runtime-sp",
            approved_managed_query_application_ids=("app-sp",),
        )


def test_legacy_query_detector_rotates_exact_pre_provenance_group() -> None:
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
    group = groups.create(
        display_name=managed_name,
        external_id=managed_query_group_external_id(
            endpoint_id="managed-id",
            application_id="app-sp",
        ),
    )
    group.members = [SimpleNamespace(value="sp-id")]

    assert endpoint_has_legacy_direct_query_principal(
        _client(serving, groups),
        app_name=_APP,
        endpoint_name="managed",
        runtime_manager_application_id="runtime-sp",
        approved_managed_query_application_ids=("app-sp",),
    )


def test_legacy_query_detector_rejects_unsigned_v2_group() -> None:
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
    group = groups.create(
        display_name=managed_name,
        external_id=intent_external_id(
            endpoint_id="managed-id",
            application_id="app-sp",
            creation_nonce="33333333-3333-4333-8333-333333333333",
        ),
    )
    group.members = [SimpleNamespace(value="sp-id")]

    with pytest.raises(RuntimeError, match="no signed immutable-ID provenance"):
        endpoint_has_legacy_direct_query_principal(
            _client(serving, groups),
            app_name=_APP,
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
    groups = _Groups()
    _seed_managed_group(
        groups,
        endpoint="target",
        application_id="proxy-sp",
        member_ids=(),
        service_principal_id="proxy-id",
    )

    with pytest.raises(RuntimeError, match="effective serving permission remains"):
        revoke_all_direct_permissions(
            _client(serving, groups),
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names={"proxy-group"},
        )


def test_global_serving_revoke_all_ignores_unrelated_endpoint_without_claim() -> None:
    serving = _GlobalServing(
        {"unrelated": ServingEndpointPermissions(access_control_list=[])}
    )

    revoke_all_direct_permissions(
        _client(serving, _Groups()),
        service_principal="proxy-sp",
        service_principal_id="proxy-id",
        effective_group_names=set(),
    )


def test_global_serving_revoke_all_rejects_unclaimed_managed_group_access() -> None:
    group_name = managed_query_group_name(
        endpoint_id="unclaimed-id",
        application_id="proxy-sp",
    )
    serving = _GlobalServing(
        {
            "unclaimed": ServingEndpointPermissions(
                access_control_list=[_group_entry(group_name, "CAN_QUERY")]
            )
        }
    )

    with pytest.raises(RuntimeError, match="unclaimed managed serving-query group"):
        revoke_all_direct_permissions(
            _client(serving, _Groups()),
            service_principal="proxy-sp",
            service_principal_id="proxy-id",
            effective_group_names={group_name},
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
    next(iter(groups.by_id.values())).members.append(SimpleNamespace(value="later-unrelated-id"))

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
                access_control_list=[_group_entry("hidden-account-parent", "CAN_VIEW")]
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
        {"reviewed": ServingEndpointPermissions(access_control_list=[managed, managed])}
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
