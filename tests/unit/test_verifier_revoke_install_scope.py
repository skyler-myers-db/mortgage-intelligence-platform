"""The verifier's obsolete-endpoint revoke stays inside its own install (post-W5).

``m2m_access_policy.revoke_can_query_on_obsolete_endpoint`` removes the
verifier's query access on every reserved-family Gateway outside this
install's green and preserved endpoints. Serving endpoints carry no
install-scoped name, so with a verifier shared with the live install that
revoke would cut the live install off; docs/deployment.md recorded it as
"not guarded". With a non-empty install suffix (BUNDLE_VAR_install_suffix,
the CI staging install) the Gateway convergence now proves, read-only and
before its first grant, that the verifier is this install's own (its
warehouse ACL is the install-scoped witness the ``check-warehouse`` probe
reads), and the revoke repeats that proof before removing anything. An
empty suffix (the live install) is unchanged and reads no warehouse.
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

from tools.databricks import install_scope, m2m_access_policy, serving_endpoint_acl
from tools.databricks.install_scope import InstallScopeRefusal
from tools.databricks.m2m_gateway_access_convergence import converge_gateway_query_access

VERIFIER = "verifier-application-id"
LEASE = "11111111-1111-4111-8111-111111111111"
SHA = "a" * 40


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
    """A read-only warehouse inventory; any mutation is a test failure."""

    def __init__(self, live_acl: WarehousePermissions) -> None:
        self.by_id = {
            "wh-ci": ("mip_serverless_sql_ci", _acl(VERIFIER)),
            "wh-live": ("mip_serverless_sql", live_acl),
        }
        self.reads = 0

    def list(self) -> Any:
        return iter(SimpleNamespace(id=key, name=name) for key, (name, _acl) in self.by_id.items())

    def get_permissions(self, warehouse_id: str) -> WarehousePermissions:
        self.reads += 1
        return self.by_id[warehouse_id][1]


class _NoWarehouses:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the live install never reads warehouses ({name})")


def _shared_client() -> SimpleNamespace:
    """The live install's verifier: it holds the live (unsuffixed) warehouse grant."""

    return SimpleNamespace(warehouses=_Warehouses(_acl(VERIFIER, "live-app")))


def _owned_client() -> SimpleNamespace:
    return SimpleNamespace(warehouses=_Warehouses(_acl("live-app")))


def _converge(client: Any, events: list[tuple[str, str]], **kwargs: Any) -> bool:
    arguments: dict[str, Any] = {
        "gateway_endpoint": "green-gateway",
        "revoke_gateway_endpoints": ("mip-agent-gateway",),
        "preserved_gateway_endpoints": ("signed-blue-gateway",),
        "app_name": "mip-app-pr105-staging",
        "deployment_lease_id": LEASE,
        "deployment_source_git_sha": SHA,
        "application_id": VERIFIER,
        "service_principal_id": "verifier-scim-id",
        "effective_group_names": set(),
        "assert_single_writer": None,
        "reserved_gateway_endpoints": lambda _client: {"green-gateway", "signed-blue-gateway", "live-green-gateway"},
        "grant": lambda _client, endpoint, *_a, **_k: events.append(("grant", endpoint)),
        "revoke": lambda _client, endpoint, *_a, **k: events.append(("revoke", f"{endpoint}@{k['install_warehouse_id']}")),
        "install_warehouse_id": "wh-ci",
    }
    arguments.update(kwargs)
    return converge_gateway_query_access(client, **arguments)


# ----------------------------------------------------------- the convergence


def test_a_suffixed_install_refuses_a_shared_verifier_before_its_first_grant(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(install_scope.INSTALL_SUFFIX_ENV, "_ci")
    events: list[tuple[str, str]] = []

    with pytest.raises(InstallScopeRefusal) as refusal:
        _converge(_shared_client(), events)

    assert refusal.value.code == 2
    assert events == [], "no grant and no revoke ran"
    err = capsys.readouterr().err
    assert "1 SQL warehouse(s)" in err and VERIFIER not in err and "mip_serverless_sql" not in err


def test_a_suffixed_install_without_a_warehouse_to_prove_against_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install_scope.INSTALL_SUFFIX_ENV, "_ci")
    events: list[tuple[str, str]] = []

    for missing in (None, "", "  "):
        with pytest.raises(InstallScopeRefusal) as refusal:
            _converge(_owned_client(), events, install_warehouse_id=missing)
        assert refusal.value.code == 2 and "--warehouse-id" in refusal.value.reason
    with pytest.raises(InstallScopeRefusal) as refusal:
        _converge(_owned_client(), events, install_warehouse_id="wh-missing")
    assert "not returned" in refusal.value.reason
    assert events == []


def test_an_install_owned_verifier_converges_and_each_revoke_carries_the_witness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install_scope.INSTALL_SUFFIX_ENV, "_ci")
    events: list[tuple[str, str]] = []
    client = _owned_client()

    assert _converge(client, events) is True

    assert events == [
        ("grant", "green-gateway"),
        ("revoke", "live-green-gateway@wh-ci"),
        ("revoke", "mip-agent-gateway@wh-ci"),
    ]
    assert client.warehouses.reads == 1, "one read-only proof before the grant (the target is skipped)"


def test_the_live_install_converges_exactly_as_before_and_reads_no_warehouse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(install_scope.INSTALL_SUFFIX_ENV, raising=False)
    events: list[tuple[str, str]] = []

    assert _converge(SimpleNamespace(warehouses=_NoWarehouses()), events, install_warehouse_id=None) is True

    assert [event[0] for event in events] == ["grant", "revoke", "revoke"]


def test_a_suffixed_grant_with_nothing_to_revoke_needs_no_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install_scope.INSTALL_SUFFIX_ENV, "_ci")
    events: list[tuple[str, str]] = []

    _converge(
        SimpleNamespace(warehouses=_NoWarehouses()),
        events,
        revoke_gateway_endpoints=(),
        reserved_gateway_endpoints=lambda _client: {"green-gateway", "signed-blue-gateway"},
    )

    assert events == [("grant", "green-gateway")]


# ---------------------------------------------------- the revoke on its own


def _revoke(client: Any, **kwargs: Any) -> None:
    m2m_access_policy.revoke_can_query_on_obsolete_endpoint(
        client,
        "live-green-gateway",
        VERIFIER,
        app_name="mip-app-pr105-staging",
        deployment_lease_id=LEASE,
        deployment_source_git_sha=SHA,
        sp_id="verifier-scim-id",
        effective_group_names=set(),
        assert_single_writer=lambda: None,
        **kwargs,
    )


def test_the_revoke_itself_refuses_a_shared_verifier_before_removing_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install_scope.INSTALL_SUFFIX_ENV, "_ci")
    removed: list[str] = []
    monkeypatch.setattr(serving_endpoint_acl, "revoke_direct_permissions", lambda *_a, **k: removed.append(k["endpoint_name"]))

    with pytest.raises(InstallScopeRefusal):
        _revoke(_shared_client(), install_warehouse_id="wh-ci")
    with pytest.raises(InstallScopeRefusal):
        _revoke(_owned_client())
    assert removed == []

    _revoke(_owned_client(), install_warehouse_id="wh-ci")
    assert removed == ["live-green-gateway"], "an install-owned verifier revokes as before"


def test_the_live_revoke_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(install_scope.INSTALL_SUFFIX_ENV, raising=False)
    removed: list[str] = []
    monkeypatch.setattr(serving_endpoint_acl, "revoke_direct_permissions", lambda *_a, **k: removed.append(k["endpoint_name"]))

    _revoke(SimpleNamespace(warehouses=_NoWarehouses()))
    _revoke(_shared_client(), install_suffix="")

    assert removed == ["live-green-gateway", "live-green-gateway"]


def test_the_check_warehouse_probe_and_the_revoke_guard_share_one_proof() -> None:
    """The deploy-level probe and the backstop cannot drift: both call check_warehouse_owned."""

    import inspect

    assert "check_warehouse_owned(" in inspect.getsource(install_scope.main)
    assert "check_warehouse_owned(" in inspect.getsource(install_scope.assert_verifier_revokes_owned)
