from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import ResourceDoesNotExist

from tools.databricks import app_gateway_access_mode as access


def _permission_entry(
    *,
    principal: str | None = None,
    group: str | None = None,
) -> object:
    return SimpleNamespace(
        service_principal_name=principal,
        group_name=group,
        all_permissions=[SimpleNamespace(permission_level="CAN_QUERY", inherited=False)],
    )


def _workspace(
    *,
    direct: bool,
    managed: bool,
    member_id: str | None = "app-scim-id",
    endpoint_id: str = "gateway-id",
    application_id: str = "app-client",
) -> object:
    group_name = access.managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    entries = []
    if direct:
        entries.append(_permission_entry(principal=application_id))
    if managed:
        entries.append(_permission_entry(group=group_name))
    group = SimpleNamespace(
        id="group-id",
        display_name=group_name,
        external_id=access.managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        members=[] if member_id is None else [SimpleNamespace(value=member_id)],
    )
    return SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(id=endpoint_id),
            get_permissions=lambda _id: SimpleNamespace(access_control_list=entries),
        ),
        groups=SimpleNamespace(
            list=lambda **_kw: [group] if managed else [],
            get=lambda _id: group,
        ),
    )


@pytest.mark.parametrize(
    ("direct", "managed", "expected"),
    [
        (True, False, "legacy"),
        (False, True, "managed"),
        (True, True, "mixed"),
    ],
)
def test_inspection_classifies_exact_query_access_without_mutation(
    direct: bool,
    managed: bool,
    expected: str,
) -> None:
    assert (
        access.inspect_app_gateway_access_mode(
            _workspace(direct=direct, managed=managed),
            endpoint_name="blue",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
        )
        == expected
    )


@pytest.mark.parametrize(
    ("direct", "managed", "expected"),
    [
        (False, False, "none"),
        (True, False, "direct"),
        (False, True, "managed"),
        (True, True, "mixed"),
    ],
)
def test_exact_verifier_inspection_classifies_all_query_modes(
    direct: bool,
    managed: bool,
    expected: str,
) -> None:
    assert (
        access.inspect_gateway_query_access_mode(
            _workspace(
                direct=direct,
                managed=managed,
                member_id="verifier-scim-id",
                application_id="verifier-client",
            ),
            endpoint_name="blue",
            application_id="verifier-client",
            scim_id="verifier-scim-id",
            identity_label="verifier",
        )
        == expected
    )


def test_exact_verifier_inspection_rejects_unrelated_member() -> None:
    with pytest.raises(RuntimeError, match="outside its immutable contract"):
        access.inspect_gateway_query_access_mode(
            _workspace(
                direct=False,
                managed=True,
                member_id="unrelated-scim-id",
                application_id="verifier-client",
            ),
            endpoint_name="blue",
            application_id="verifier-client",
            scim_id="verifier-scim-id",
            identity_label="verifier",
        )


def test_exact_verifier_inspection_rejects_group_identity_drift() -> None:
    workspace = _workspace(
        direct=False,
        managed=True,
        member_id="verifier-scim-id",
        application_id="verifier-client",
    )
    group = workspace.groups.get("group-id")
    group.external_id = "attacker-controlled"

    with pytest.raises(RuntimeError, match="identity drifted"):
        access.inspect_gateway_query_access_mode(
            workspace,
            endpoint_name="blue",
            application_id="verifier-client",
            scim_id="verifier-scim-id",
            identity_label="verifier",
        )


def test_inspection_rejects_managed_scim_identity_drift() -> None:
    with pytest.raises(RuntimeError, match="outside its immutable contract"):
        access.inspect_app_gateway_access_mode(
            _workspace(
                direct=False,
                managed=True,
                member_id="different-app-scim-id",
            ),
            endpoint_name="blue",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
        )


def test_empty_exact_managed_group_is_currently_no_access() -> None:
    assert (
        access.inspect_app_gateway_access_mode(
            _workspace(
                direct=False,
                managed=True,
                member_id=None,
            ),
            endpoint_name="retired-blue",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
        )
        == "none"
    )


def test_empty_exact_managed_group_cannot_claim_active_signed_blue() -> None:
    with pytest.raises(RuntimeError, match="signed-blue App has no exact query access"):
        access.preserve_blue_and_revoke_managed_candidates(
            _workspace(
                direct=False,
                managed=True,
                member_id=None,
            ),
            blue_endpoint="retired-blue",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
            assert_before_mutation=lambda: None,
        )


def test_inspection_rejects_unreviewed_effective_group_access() -> None:
    workspace = _workspace(direct=True, managed=False)
    permissions = workspace.serving_endpoints.get_permissions("gateway-id")
    permissions.access_control_list.append(_permission_entry(group="unreviewed-query-group"))
    workspace.serving_endpoints.get_permissions = lambda _id: permissions
    group = SimpleNamespace(
        id="unreviewed-group-id",
        display_name="unreviewed-query-group",
        external_id="unreviewed",
        members=[SimpleNamespace(value="app-scim-id")],
    )
    workspace.groups = SimpleNamespace(
        list=lambda **_kw: [group],
        get=lambda _id: group,
    )

    with pytest.raises(RuntimeError, match="unreviewed effective group"):
        access.inspect_app_gateway_access_mode(
            workspace,
            endpoint_name="blue",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
        )


def test_blue_is_preserved_while_reserved_managed_candidate_is_revoked_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blue = _workspace(direct=True, managed=False)
    candidate = _workspace(
        direct=False,
        managed=True,
        endpoint_id="candidate-id",
    )
    endpoints = {
        "blue": blue.serving_endpoints,
        "mip-growth-agent-gateway-deadbeef1234": candidate.serving_endpoints,
    }

    class _Endpoints:
        def list(self) -> list[object]:
            return [
                SimpleNamespace(name="blue"),
                SimpleNamespace(name="mip-growth-agent-gateway-deadbeef1234"),
                SimpleNamespace(name="unrelated"),
            ]

        def get(self, name: str) -> object:
            try:
                return endpoints[name].get(name)
            except KeyError as exc:
                raise ResourceDoesNotExist("missing") from exc

        def get_permissions(self, endpoint_id: str) -> object:
            owner = blue if endpoint_id == "gateway-id" else candidate
            return owner.serving_endpoints.get_permissions(endpoint_id)

    workspace = SimpleNamespace(
        serving_endpoints=_Endpoints(),
        groups=candidate.groups,
    )
    revoked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        access,
        "revoke_direct_permissions",
        lambda *_a, **kw: revoked.append((kw["endpoint_name"], kw["service_principal_id"])) or True,
    )

    assert (
        access.preserve_blue_and_revoke_managed_candidates(
            workspace,
            blue_endpoint="blue",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
            assert_before_mutation=lambda: None,
        )
        == "legacy"
    )
    assert revoked == [("mip-growth-agent-gateway-deadbeef1234", "app-scim-id")]


def test_mixed_candidate_is_rejected_before_any_managed_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modes = {"blue": "legacy", "candidate": "mixed"}
    revoked: list[str] = []

    def inspect(*_args: object, **kwargs: object) -> str:
        endpoint = str(kwargs["endpoint_name"])
        if endpoint in modes:
            return modes[endpoint]
        raise ResourceDoesNotExist("missing")

    monkeypatch.setattr(
        access,
        "inspect_app_gateway_access_mode",
        inspect,
    )
    monkeypatch.setattr(
        access,
        "revoke_direct_permissions",
        lambda *_a, **kw: revoked.append(kw["endpoint_name"]) or True,
    )

    with pytest.raises(RuntimeError, match="retains legacy direct App access"):
        access.preserve_blue_and_revoke_managed_candidates(
            SimpleNamespace(serving_endpoints=SimpleNamespace()),
            blue_endpoint="blue",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
            candidate_endpoints=("candidate",),
            assert_before_mutation=lambda: None,
        )

    assert revoked == []


def test_prepare_rejects_legacy_gateway_without_pinned_deletion_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        access,
        "inspect_app_gateway_access_mode",
        lambda *_a, **_kw: "legacy",
    )
    monkeypatch.setattr(
        access,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: "none",
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                id="old-gateway-id",
                creator="legacy-owner",
            )
        )
    )
    journal = {
        "canonical_name": "Mortgage Growth Agent",
        "old_gateway_endpoint": "old-gateway",
        "old_gateway_endpoint_id": "old-gateway-id",
        "old_gateway_creator": "legacy-owner",
        "old_gateway_delete_allowed": "0",
    }

    with pytest.raises(RuntimeError, match="creator policy"):
        access.assert_pinned_access_retirement_authority(
            workspace,
            journal=journal,
            canonical_name="Mortgage Growth Agent",
            green_gateway_endpoint="green-gateway",
            runtime_application_id="runtime-client",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            agent_by_id=lambda _id: None,
        )


def test_prepare_rejects_preserved_endpoint_without_signed_journal() -> None:
    with pytest.raises(RuntimeError, match="no signed cutover retirement journal"):
        access.assert_pinned_access_retirement_authority(
            SimpleNamespace(),
            journal=None,
            canonical_name="Mortgage Growth Agent",
            green_gateway_endpoint="green-gateway",
            runtime_application_id="runtime-client",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            agent_by_id=lambda _id: None,
            preserve_endpoints=("signed-blue-gateway",),
        )


def test_prepare_allows_managed_gateway_without_endpoint_deletion_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        access,
        "inspect_app_gateway_access_mode",
        lambda *_a, **_kw: "managed",
    )
    monkeypatch.setattr(
        access,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: "none",
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                id="old-gateway-id",
                creator="legacy-owner",
            )
        )
    )

    access.assert_pinned_access_retirement_authority(
        workspace,
        journal={
            "canonical_name": "Mortgage Growth Agent",
            "old_gateway_endpoint": "old-gateway",
            "old_gateway_endpoint_id": "old-gateway-id",
            "old_gateway_creator": "legacy-owner",
            "old_gateway_delete_allowed": "0",
        },
        canonical_name="Mortgage Growth Agent",
        green_gateway_endpoint="green-gateway",
        runtime_application_id="runtime-client",
        app_client_id="app-client",
        app_scim_id="app-scim-id",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        agent_by_id=lambda _id: None,
    )


@pytest.mark.parametrize(
    ("verifier_mode", "raises"),
    [
        ("none", False),
        ("managed", False),
        ("direct", True),
        ("mixed", True),
    ],
)
def test_prepare_checks_exact_verifier_mode_for_nondeletable_gateway(
    monkeypatch: pytest.MonkeyPatch,
    verifier_mode: str,
    raises: bool,
) -> None:
    inspected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        access,
        "inspect_app_gateway_access_mode",
        lambda *_a, **_kw: "none",
    )
    monkeypatch.setattr(
        access,
        "inspect_gateway_query_access_mode",
        lambda *_a, **kw: (
            inspected.append((kw["application_id"], kw["scim_id"])),
            verifier_mode,
        )[1],
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                id="old-gateway-id",
                creator="legacy-owner",
            )
        )
    )
    kwargs = {
        "workspace": workspace,
        "journal": {
            "canonical_name": "Mortgage Growth Agent",
            "old_gateway_endpoint": "old-gateway",
            "old_gateway_endpoint_id": "old-gateway-id",
            "old_gateway_creator": "legacy-owner",
            "old_gateway_delete_allowed": "0",
        },
        "canonical_name": "Mortgage Growth Agent",
        "green_gateway_endpoint": "green-gateway",
        "runtime_application_id": "runtime-client",
        "app_client_id": "app-client",
        "app_scim_id": "app-scim-id",
        "verifier_application_id": "verifier-client",
        "verifier_scim_id": "verifier-scim-id",
        "agent_by_id": lambda _id: None,
    }

    if raises:
        with pytest.raises(RuntimeError, match="verifier access"):
            access.assert_pinned_access_retirement_authority(**kwargs)
    else:
        access.assert_pinned_access_retirement_authority(**kwargs)

    assert inspected == [("verifier-client", "verifier-scim-id")]


@pytest.mark.parametrize("verifier_mode", ["direct", "mixed"])
def test_prepare_allows_deletable_gateway_to_retain_direct_verifier_access(
    monkeypatch: pytest.MonkeyPatch,
    verifier_mode: str,
) -> None:
    monkeypatch.setattr(
        access,
        "inspect_app_gateway_access_mode",
        lambda *_a, **_kw: "legacy",
    )
    monkeypatch.setattr(
        access,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: verifier_mode,
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                id="old-gateway-id",
                creator="runtime-client",
            )
        )
    )

    access.assert_pinned_access_retirement_authority(
        workspace,
        journal={
            "canonical_name": "Mortgage Growth Agent",
            "old_gateway_endpoint": "old-gateway",
            "old_gateway_endpoint_id": "old-gateway-id",
            "old_gateway_creator": "runtime-client",
            "old_gateway_delete_allowed": "1",
        },
        canonical_name="Mortgage Growth Agent",
        green_gateway_endpoint="green-gateway",
        runtime_application_id="runtime-client",
        app_client_id="app-client",
        app_scim_id="app-scim-id",
        verifier_application_id="verifier-client",
        verifier_scim_id="verifier-scim-id",
        agent_by_id=lambda _id: None,
    )


def test_prepare_rejects_pinned_supervisor_ownership_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected: list[str] = []
    monkeypatch.setattr(
        access,
        "inspect_app_gateway_access_mode",
        lambda *_a, **kw: inspected.append(kw["endpoint_name"]) or "legacy",
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                id="old-endpoint-id",
                creator="signed-owner",
            )
        )
    )

    with pytest.raises(RuntimeError, match="ownership drifted"):
        access.assert_pinned_access_retirement_authority(
            workspace,
            journal={
                "canonical_name": "Mortgage Growth Agent",
                "old_id": "old-supervisor",
                "old_endpoint": "old-endpoint",
                "old_endpoint_id": "old-endpoint-id",
                "old_creator": "signed-owner",
                "old_create_time": "signed-time",
            },
            canonical_name="Mortgage Growth Agent",
            green_gateway_endpoint="green-gateway",
            runtime_application_id="runtime-client",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
            verifier_application_id="verifier-client",
            verifier_scim_id="verifier-scim-id",
            agent_by_id=lambda _id: {
                "display_name": "Mortgage Growth Agent",
                "endpoint_name": "old-endpoint",
                "creator": "attacker-owner",
                "create_time": "signed-time",
            },
        )

    assert inspected == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("managed", ["lease", "revoke:app-scim-id"]),
        ("none", ["lease", "revoke:app-scim-id"]),
        ("legacy", []),
        ("mixed", []),
    ],
)
def test_retirement_revoke_mutates_only_exact_managed_membership(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: list[str],
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        access,
        "inspect_app_gateway_access_mode",
        lambda *_a, **_kw: mode,
    )
    def revoke(*_args: object, **kwargs: Any) -> bool:
        kwargs["assert_single_writer"]()
        events.append(f"revoke:{kwargs['service_principal_id']}")
        return True

    monkeypatch.setattr(access, "revoke_direct_permissions", revoke)

    assert (
        access.revoke_managed_app_access(
            object(),
            endpoint_name="old",
            app_client_id="app-client",
            app_scim_id="app-scim-id",
            missing_ok=True,
            assert_before_mutation=lambda: events.append("lease"),
        )
        == mode
    )
    assert events == expected
