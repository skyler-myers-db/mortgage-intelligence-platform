from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound, ResourceConflict

import tools.databricks.serving_query_group_access as access
import tools.databricks.serving_query_group_governance as governance

_APP = "mip-app"
_LEASE = "11111111-1111-4111-8111-111111111111"
_SOURCE = "a" * 40
_NONCE = "22222222-2222-4222-8222-222222222222"
_EXTERNAL_ID = access.group_provenance.intent_external_id(
    endpoint_id="endpoint-id",
    application_id="app-client",
    creation_nonce=_NONCE,
)


@pytest.fixture(autouse=True)
def _claimed_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    def require_claimed(
        _workspace: object,
        *,
        app_name: str,
        endpoint_id: str,
        application_id: str,
        service_principal_id: str,
        group_name: str,
    ) -> dict[str, str]:
        assert app_name == _APP
        assert endpoint_id == "endpoint-id"
        assert application_id == "app-client"
        assert service_principal_id == "app-scim"
        assert group_name == access.managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        return {
            "group_id": "managed-group-id",
            "external_id": _EXTERNAL_ID,
        }

    monkeypatch.setattr(
        access.group_provenance,
        "require_claimed",
        require_claimed,
    )


def _client(
    *,
    member_ids: tuple[str, ...] = ("app-scim",),
    resource_type: str = "WorkspaceGroup",
) -> object:
    endpoint_id = "endpoint-id"
    application_id = "app-client"
    group = SimpleNamespace(
        id="managed-group-id",
        display_name=access.managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        external_id=_EXTERNAL_ID,
        members=[SimpleNamespace(value=value) for value in member_ids],
        meta=SimpleNamespace(resource_type=resource_type),
    )
    return SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: [group],
            get=lambda group_id: group
            if group_id == "managed-group-id"
            else (_ for _ in ()).throw(AssertionError(group_id)),
        ),
    )


def test_workspace_group_admin_boundary_allows_non_admin_membership() -> None:
    state = governance.assert_managed_query_group_administration_isolated(
        _client(),
        app_name=_APP,
        account_id="account-id",
        endpoint_id="endpoint-id",
        application_id="app-client",
        service_principal_id="app-scim",
        authoritative_effective_groups={
            "managed-group-id": "managed-query-group",
        },
    )

    assert state is not None
    assert state.contract.id == "managed-group-id"


def test_workspace_group_admin_boundary_rejects_workspace_admin() -> None:
    with pytest.raises(RuntimeError, match="workspace-administration authority"):
        governance.assert_managed_query_group_administration_isolated(
            _client(),
            app_name=_APP,
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={
                "managed-group-id": "managed-query-group",
                "workspace-admins-id": "admins",
            },
        )


def test_empty_retired_group_receives_same_administration_governance() -> None:
    state = governance.assert_managed_query_group_administration_isolated(
        _client(
            member_ids=(),
        ),
        app_name=_APP,
        account_id="account-id",
        endpoint_id="endpoint-id",
        application_id="app-client",
        service_principal_id="app-scim",
        authoritative_effective_groups={
            "nested-group-id": "nested-query-manager",
        },
    )

    assert state is not None
    assert state.member_ids == ()


def test_workspace_group_admin_boundary_rejects_wrong_resource_plane() -> None:
    with pytest.raises(RuntimeError, match="workspace-local SCIM"):
        governance.assert_managed_query_group_administration_isolated(
            _client(
                member_ids=(),
                resource_type="Group",
            ),
            app_name=_APP,
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={
                "nested-group-id": "nested-query-manager",
            },
        )


def test_managed_query_inspection_rejects_wrong_resource_plane() -> None:
    with pytest.raises(RuntimeError, match="workspace-local SCIM"):
        access.inspect_managed_query_group(
            _client(resource_type="Group"),
            endpoint_id="endpoint-id",
            application_id="app-client",
        )


def test_managed_group_governance_rejects_unrelated_members() -> None:
    with pytest.raises(RuntimeError, match="neither active nor safely retired"):
        governance.assert_managed_query_group_administration_isolated(
            _client(
                member_ids=("unrelated-scim",),
            ),
            app_name=_APP,
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={},
        )


def test_missing_managed_group_needs_no_management_probe() -> None:
    client = SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: [],
            get=lambda _group_id: (_ for _ in ()).throw(NotFound("missing")),
        ),
    )
    assert (
        governance.assert_managed_query_group_administration_isolated(
            client,
            app_name=_APP,
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={},
        )
        is None
    )


def _managed_group(*, external_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="managed-group-id",
        display_name=access.managed_query_group_name(
            endpoint_id="endpoint-id",
            application_id="app-client",
        ),
        external_id=external_id or _EXTERNAL_ID,
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )


def test_successful_create_waits_for_exact_name_list_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _managed_group()
    list_results = iter(([], [], [group]))
    sleeps: list[float] = []
    create_calls: list[dict[str, str]] = []
    provenance = {"group_id": "", "external_id": _EXTERNAL_ID}
    client = SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: next(list_results),
            create=lambda **kwargs: create_calls.append(kwargs) or group,
            get=lambda group_id: group
            if group_id == group.id
            else (_ for _ in ()).throw(AssertionError(group_id)),
        ),
    )
    monkeypatch.setattr(
        access.group_provenance,
        "prepare",
        lambda *_args, **_kwargs: provenance,
    )
    monkeypatch.setattr(
        access.group_provenance,
        "claim",
        lambda *_args, group_id, **_kwargs: {"group_id": group_id},
    )

    state = access.ensure_managed_query_group(
        client,
        app_name=_APP,
        deployment_lease_id=_LEASE,
        deployment_source_git_sha=_SOURCE,
        endpoint_id="endpoint-id",
        application_id="app-client",
        service_principal_id="app-scim",
        assert_single_writer=lambda: None,
        timeout_s=5,
        sleep=sleeps.append,
        clock=iter((0.0, 0.0)).__next__,
    )

    assert state.contract.id == group.id
    assert create_calls == [
        {
            "display_name": group.display_name,
            "external_id": group.external_id,
        }
    ]
    assert sleeps == [2]


def test_create_conflict_rejects_a_group_with_the_wrong_intent_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_checks: list[str] = []
    conflict = ResourceConflict("group already exists")
    wrong_group = _managed_group(external_id="mip:sq:v2:wrong")
    list_results = iter(([], [wrong_group]))
    client = SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: next(list_results),
            get=lambda _group_id: wrong_group,
            create=lambda **_kwargs: (_ for _ in ()).throw(conflict),
        ),
    )
    monkeypatch.setattr(
        access.group_provenance,
        "prepare",
        lambda *_args, **_kwargs: {
            "group_id": "",
            "external_id": _EXTERNAL_ID,
        },
    )

    with pytest.raises(RuntimeError, match="contract drifted"):
        access.ensure_managed_query_group(
            client,
            app_name=_APP,
            deployment_lease_id=_LEASE,
            deployment_source_git_sha=_SOURCE,
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            assert_single_writer=lambda: writer_checks.append("lease"),
        )

    assert writer_checks == ["lease", "lease"]


def test_exact_marker_spoof_is_rejected_before_acl_or_membership_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _managed_group()
    create_calls: list[str] = []
    client = SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: [group],
            create=lambda **_kwargs: create_calls.append("create"),
            get=lambda _group_id: group,
        ),
    )
    monkeypatch.setattr(
        access.group_provenance,
        "prepare",
        lambda *_args, **_kwargs: {
            "group_id": "",
            "external_id": "mip:sq:v2:different",
        },
    )

    with pytest.raises(RuntimeError, match="contract drifted"):
        access.ensure_managed_query_group(
            client,
            app_name=_APP,
            deployment_lease_id=_LEASE,
            deployment_source_git_sha=_SOURCE,
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            assert_single_writer=lambda: None,
        )

    assert create_calls == []


def test_claimed_group_id_must_match_exact_name_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _managed_group()
    group.id = "replacement-group-id"
    client = SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: [group],
            get=lambda _group_id: group,
        ),
    )
    provenance = {
        "endpoint_id": "endpoint-id",
        "application_id": "app-client",
        "service_principal_id": "app-scim",
        "group_id": "signed-group-id",
        "external_id": _EXTERNAL_ID,
    }
    monkeypatch.setattr(
        access.group_provenance,
        "prepare",
        lambda *_args, **_kwargs: provenance,
    )

    with pytest.raises(RuntimeError, match="contract drifted"):
        access.ensure_managed_query_group(
            client,
            app_name=_APP,
            deployment_lease_id=_LEASE,
            deployment_source_git_sha=_SOURCE,
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            assert_single_writer=lambda: None,
        )
