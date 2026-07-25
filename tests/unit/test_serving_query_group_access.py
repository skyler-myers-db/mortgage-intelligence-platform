from __future__ import annotations

from types import SimpleNamespace

import pytest

import tools.databricks.serving_query_group_access as access


def _client(
    *,
    manager_principals: list[str],
    member_ids: tuple[str, ...] = ("app-scim",),
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
    )
    rule_name = "accounts/account-id/groups/managed-group-id/ruleSets/default"
    return SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: [group],
            get=lambda group_id: group
            if group_id == "managed-group-id"
            else (_ for _ in ()).throw(AssertionError(group_id)),
        ),
        account_access_control_proxy=SimpleNamespace(
            get_rule_set=lambda name, etag: SimpleNamespace(
                name=name if name == rule_name else "",
                etag="rule-etag" if etag == "" else "",
                grant_rules=[
                    SimpleNamespace(
                        role="roles/group.manager",
                        principals=manager_principals,
                    )
                ],
            )
        ),
    )


def test_managed_group_admin_rule_allows_unrelated_reviewed_manager() -> None:
    state = access.assert_managed_query_group_administration_isolated(
        _client(manager_principals=["groups/platform-admins-id"]),
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


@pytest.mark.parametrize(
    "manager_principal",
    [
        "servicePrincipals/app-scim",
        "servicePrincipals/app-client",
        "groups/managed-group-id",
        "groups/nested-group-id",
        "groups/nested-query-manager",
        "all-users",
        "groups/account-users",
    ],
)
def test_managed_group_admin_rule_rejects_effective_member_authority(
    manager_principal: str,
) -> None:
    with pytest.raises(RuntimeError, match="administration authority"):
        access.assert_managed_query_group_administration_isolated(
            _client(manager_principals=[manager_principal]),
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={
                "managed-group-id": "managed-query-group",
                "nested-group-id": "nested-query-manager",
            },
        )


def test_empty_retired_group_receives_same_administration_governance() -> None:
    state = access.assert_managed_query_group_administration_isolated(
        _client(
            manager_principals=["groups/platform-admins-id"],
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


def test_empty_retired_group_rejects_hidden_account_parent_manager_self_readd() -> None:
    with pytest.raises(RuntimeError, match="administration authority"):
        access.assert_managed_query_group_administration_isolated(
            _client(
                manager_principals=["groups/nested-group-id"],
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


def test_managed_group_governance_rejects_unrelated_members() -> None:
    with pytest.raises(RuntimeError, match="neither active nor safely retired"):
        access.assert_managed_query_group_administration_isolated(
            _client(
                manager_principals=["groups/platform-admins-id"],
                member_ids=("unrelated-scim",),
            ),
            account_id="account-id",
            endpoint_id="endpoint-id",
            application_id="app-client",
            service_principal_id="app-scim",
            authoritative_effective_groups={},
        )


def test_missing_managed_group_needs_no_rule_set() -> None:
    client = SimpleNamespace(
        groups=SimpleNamespace(list=lambda **_kwargs: []),
        account_access_control_proxy=SimpleNamespace(
            get_rule_set=lambda *_args: (_ for _ in ()).throw(
                AssertionError("unexpected rule-set lookup")
            )
        ),
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
