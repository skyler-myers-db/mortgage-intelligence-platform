"""The CI staging install never revokes access it does not own (W5e C7c).

tools/databricks/install_scope.py: with a non-empty install suffix
(BUNDLE_VAR_install_suffix, the isolated CI staging install) the warehouse
CAN_USE convergence and the agent-proxy endpoint convergence refuse, with
exit 2 and NO mutation, when the identity holds access on an object this
install does not own. With an empty suffix (the live install, every operator
deploy) both behave exactly as before. Refusals print counts only.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.sql import (
    WarehouseAccessControlResponse,
    WarehousePermission,
    WarehousePermissionLevel,
    WarehousePermissions,
)

from tools.databricks import agent_proxy_access, install_scope
from tools.databricks.install_scope import InstallScopeRefusal, assert_endpoint_revokes_owned
from tools.databricks.serving_query_group_access import managed_query_group_name
from tools.databricks.warehouse_acl import converge_exact_can_use

LIVE_WAREHOUSE = "mip_serverless_sql"


def _acl(*principals: str) -> WarehousePermissions:
    return WarehousePermissions(
        access_control_list=[
            WarehouseAccessControlResponse(
                service_principal_name=principal,
                all_permissions=[WarehousePermission(inherited=False, permission_level=WarehousePermissionLevel.CAN_USE)],
            )
            for principal in principals
        ]
    )


class _Warehouses:
    def __init__(self, warehouses: dict[str, tuple[str, WarehousePermissions]]) -> None:
        self.warehouses = warehouses
        self.mutations: list[str] = []

    def list(self) -> Any:
        return iter(SimpleNamespace(id=key, name=name) for key, (name, _acl) in self.warehouses.items())

    def get_permissions(self, warehouse_id: str) -> WarehousePermissions:
        return self.warehouses[warehouse_id][1]

    def set_permissions(self, warehouse_id: str, *, access_control_list: list[Any]) -> None:
        self.mutations.append(f"set {warehouse_id}")
        kept = [str(entry.service_principal_name) for entry in access_control_list if entry.service_principal_name]
        self.warehouses[warehouse_id] = (self.warehouses[warehouse_id][0], _acl(*kept))

    def update_permissions(self, warehouse_id: str, *, access_control_list: list[object]) -> None:
        self.mutations.append(f"update {warehouse_id}")
        self.warehouses[warehouse_id] = (self.warehouses[warehouse_id][0], _acl("verifier"))


def _client(live_acl: WarehousePermissions, live_name: str = LIVE_WAREHOUSE) -> SimpleNamespace:
    return SimpleNamespace(
        warehouses=_Warehouses({"wh-ci": ("mip_serverless_sql_ci", _acl()), "wh-live": (live_name, live_acl)})
    )


def _converge(client: SimpleNamespace, **kwargs: Any) -> None:
    converge_exact_can_use(client, warehouse_id="wh-ci", service_principal="verifier", effective_group_names=set(), **kwargs)


def test_a_suffixed_install_refuses_to_revoke_a_live_warehouse_and_changes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    client = _client(_acl("verifier", "live-app"))

    with pytest.raises(InstallScopeRefusal) as refusal:
        _converge(client, install_suffix="_ci")

    assert refusal.value.code == 2
    assert client.warehouses.mutations == [], "refused before any mutation"
    err = capsys.readouterr().err
    assert "1 SQL warehouse(s)" in err
    assert LIVE_WAREHOUSE not in err and "wh-live" not in err and "verifier" not in err


def test_the_suffix_is_read_from_the_bundle_var_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install_scope.INSTALL_SUFFIX_ENV, "_ci")
    client = _client(_acl("verifier"))

    with pytest.raises(InstallScopeRefusal):
        _converge(client)
    assert client.warehouses.mutations == []


def test_the_live_install_converges_exactly_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(install_scope.INSTALL_SUFFIX_ENV, raising=False)
    client = _client(_acl("verifier", "live-app"))

    _converge(client)

    assert client.warehouses.mutations == ["set wh-live", "update wh-ci"], "the empty suffix keeps the base revoke"
    assert [e.service_principal_name for e in client.warehouses.warehouses["wh-live"][1].access_control_list] == ["live-app"]


def test_a_suffixed_install_converges_on_warehouses_it_owns() -> None:
    client = _client(_acl("verifier"), live_name="mip_other_warehouse_ci")

    _converge(client, install_suffix="_ci")

    assert client.warehouses.mutations == ["set wh-live", "update wh-ci"]


# ----------------------------------------------------------------- agent proxy


def _endpoints(acls: dict[str, list[dict[str, object]]], ids: dict[str, str] | None = None) -> SimpleNamespace:
    ids = ids if ids is not None else {name: f"{name}-id" for name in acls}
    return SimpleNamespace(
        list=lambda: iter(SimpleNamespace(name=name) for name in acls),
        get=lambda name: SimpleNamespace(id=ids.get(name, "")),
        get_permissions=lambda endpoint_id: SimpleNamespace(
            access_control_list=[
                SimpleNamespace(**entry)
                for name, entries in acls.items()
                if ids.get(name) == endpoint_id
                for entry in entries
            ]
        ),
    )


def test_the_endpoint_guard_refuses_proxy_access_outside_the_reviewed_set() -> None:
    managed = managed_query_group_name(endpoint_id="live-endpoint-id", application_id="proxy-client")

    direct = SimpleNamespace(serving_endpoints=_endpoints({"ci-endpoint": [], "live-endpoint": [{"service_principal_name": "proxy-client"}]}))
    grouped = SimpleNamespace(serving_endpoints=_endpoints({"ci-endpoint": [], "live-endpoint": [{"group_name": managed}]}))
    for workspace in (direct, grouped):
        with pytest.raises(InstallScopeRefusal) as refusal:
            assert_endpoint_revokes_owned(workspace, reviewed={"ci-endpoint"}, application_id="proxy-client")
        assert refusal.value.code == 2

    owned = SimpleNamespace(serving_endpoints=_endpoints({"ci-endpoint": [{"service_principal_name": "proxy-client"}], "live-endpoint": [{"service_principal_name": "someone-else"}]}))
    assert_endpoint_revokes_owned(owned, reviewed={"ci-endpoint"}, application_id="proxy-client")
    foundation = SimpleNamespace(serving_endpoints=_endpoints({"databricks-foundation": [{"service_principal_name": "proxy-client"}]}, ids={}))
    assert_endpoint_revokes_owned(foundation, reviewed={"ci-endpoint"}, application_id="proxy-client")


def _patch_proxy_convergence(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    for name, value in {
        "assert_workspace_admin_inventory_identity": lambda *_a, **_k: None,
        "resolve_effective_groups": lambda *_a, **_k: {},
        "_wait_exact_capability_projection": lambda *_a, **_k: set(),
        "_reviewed_bindings": lambda *_a, **_k: (("ci-agent", "ci-endpoint", "ci-endpoint-id"),),
        "inspect_exact_query_access_mode": lambda *_a, **_k: "managed",
        "_supervisor_agents": lambda _w: {"ci-agent": "CI"},
        "_converge_supervisor_agent_acls": lambda *_a, **_k: calls.append("supervisor"),
        "_converge_genie_acl": lambda *_a, **_k: calls.append("genie"),
        "converge_exact_direct_can_query": lambda *_a, **_k: calls.append("endpoints"),
    }.items():
        monkeypatch.setattr(agent_proxy_access, name, value)


def _grant(workspace: SimpleNamespace) -> None:
    agent_proxy_access.grant_and_audit_agent_proxy_access(
        workspace,
        app_name="mip-app-staging",
        deployment_lease_id="11111111-1111-4111-8111-111111111111",
        deployment_source_git_sha="a" * 40,
        supervisor_id="ci-agent",
        supervisor_endpoint="ci-endpoint",
        supervisor_endpoint_id="ci-endpoint-id",
        genie_space_id="genie-space",
        application_id="proxy-client",
        runtime_application_id="runtime-client",
        expected_inventory_principal="operator@summit.example",
        assert_single_writer=lambda: None,
        assert_legacy_cleanup_quiesced=lambda: None,
    )


def _proxy_workspace() -> SimpleNamespace:
    return SimpleNamespace(
        service_principals=SimpleNamespace(list=lambda **_k: iter([SimpleNamespace(application_id="proxy-client", id="proxy-scim")])),
        serving_endpoints=_endpoints({"ci-endpoint": [], "live-endpoint": [{"service_principal_name": "proxy-client"}]}),
    )


def test_the_proxy_convergence_refuses_before_its_first_mutation_in_a_suffixed_install(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_proxy_convergence(monkeypatch, calls)
    monkeypatch.setenv(install_scope.INSTALL_SUFFIX_ENV, "_ci")

    with pytest.raises(InstallScopeRefusal):
        _grant(_proxy_workspace())
    assert calls == [], "no Supervisor, Genie or endpoint convergence ran"


def test_the_live_proxy_convergence_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_proxy_convergence(monkeypatch, calls)
    monkeypatch.delenv(install_scope.INSTALL_SUFFIX_ENV, raising=False)

    _grant(_proxy_workspace())
    assert calls == ["supervisor", "genie", "endpoints"]
