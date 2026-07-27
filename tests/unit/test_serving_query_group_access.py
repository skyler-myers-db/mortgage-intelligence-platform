from __future__ import annotations

from types import SimpleNamespace

import pytest

import tools.databricks.serving_query_group_access as access


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
        external_id=access.managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
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
    state = access.assert_managed_query_group_administration_isolated(
        _client(),
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
        access.assert_managed_query_group_administration_isolated(
            _client(),
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
    state = access.assert_managed_query_group_administration_isolated(
        _client(
            member_ids=(),
        ),
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
        access.assert_managed_query_group_administration_isolated(
            _client(
                member_ids=(),
                resource_type="Group",
            ),
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={
                "nested-group-id": "nested-query-manager",
            },
        )


def test_managed_group_governance_rejects_unrelated_members() -> None:
    with pytest.raises(RuntimeError, match="neither active nor safely retired"):
        access.assert_managed_query_group_administration_isolated(
            _client(
                member_ids=("unrelated-scim",),
            ),
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={},
        )


def test_missing_managed_group_needs_no_management_probe() -> None:
    client = SimpleNamespace(
        groups=SimpleNamespace(list=lambda **_kwargs: []),
    )
    assert (
        access.assert_managed_query_group_administration_isolated(
            client,
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={},
        )
        is None
    )
