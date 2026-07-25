from __future__ import annotations

from types import SimpleNamespace

import pytest

import tools.databricks.audit_global_m2m_access as audit
import tools.databricks.serving_query_group_access as query_groups

_TARGET_IDENTITY_PROOF = (
    "runtime-scim-id",
    {"hidden-parent-id": "hidden-account-parent"},
)


def _CREDENTIAL_LEASE() -> None:
    pass


@pytest.fixture(autouse=True)
def _bind_credential_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        audit,
        "held_deployment_credential_assertion",
        lambda _workspace: _CREDENTIAL_LEASE,
    )


def _workspace(*, principal: str = "deployer@example.com", admin: bool = True) -> object:
    groups = [SimpleNamespace(display="admins")] if admin else []
    return SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=principal, groups=groups))
    )


def test_main_audits_serving_and_genie_globally(monkeypatch) -> None:
    workspace = _workspace()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        audit,
        "audit_global_serving_endpoint_access",
        lambda client, **kwargs: calls.append(("serving", (client, kwargs))),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_genie_access",
        lambda client, **kwargs: calls.append(("genie", (client, kwargs))),
    )
    monkeypatch.setattr(
        audit,
        "_audit_managed_query_group_governance",
        lambda *_args, **_kwargs: _TARGET_IDENTITY_PROOF,
    )

    assert (
        audit.main(
            [
                "--application-id",
                "runtime-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "supervisor-endpoint",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_MANAGE",
                "--genie-space-id",
                "space-id",
            ]
        )
        == 0
    )

    assert calls == [
        (
            "serving",
            (
                workspace,
                {
                    "reviewed_endpoint_names": (
                        "supervisor-endpoint",
                        "gateway-endpoint",
                    ),
                    "service_principal": "runtime-id",
                    "expected_permission_level": "CAN_MANAGE",
                    "service_principal_id": "runtime-scim-id",
                    "effective_group_names": {"hidden-account-parent"},
                    "legacy_pinned_endpoint_names": (),
                },
            ),
        ),
        (
            "genie",
            (
                workspace,
                {
                    "reviewed_genie_space_id": "space-id",
                    "application_id": "runtime-id",
                    "service_principal_id": "runtime-scim-id",
                    "effective_group_names": {"hidden-account-parent"},
                },
            ),
        ),
    ]


def test_main_can_audit_verifier_without_genie(monkeypatch) -> None:
    workspace = _workspace()
    serving: list[dict[str, object]] = []
    no_genie: list[dict[str, object]] = []
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        audit,
        "audit_global_serving_endpoint_access",
        lambda _client, **kwargs: serving.append(kwargs),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_genie_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_no_genie_access",
        lambda _client, **kwargs: no_genie.append(kwargs),
    )
    monkeypatch.setattr(
        audit,
        "_audit_managed_query_group_governance",
        lambda *_args, **_kwargs: _TARGET_IDENTITY_PROOF,
    )

    assert (
        audit.main(
            [
                "--application-id",
                "verifier-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_QUERY",
                "--forbid-all-genie",
            ]
        )
        == 0
    )
    assert serving == [
        {
            "reviewed_endpoint_names": ("gateway-endpoint",),
            "service_principal": "verifier-id",
            "expected_permission_level": "CAN_QUERY",
            "service_principal_id": "runtime-scim-id",
            "effective_group_names": {"hidden-account-parent"},
            "legacy_pinned_endpoint_names": (),
        }
    ]
    assert no_genie == [
        {
            "application_id": "verifier-id",
            "service_principal_id": "runtime-scim-id",
            "effective_group_names": {"hidden-account-parent"},
        }
    ]


def test_workspace_admin_inventory_principal_returns_verified_identity() -> None:
    assert audit.workspace_admin_inventory_principal(_workspace()) == "deployer@example.com"


@pytest.mark.parametrize(
    ("workspace", "message"),
    [
        (_workspace(principal=""), "could not identify"),
        (_workspace(admin=False), "workspace-admin"),
    ],
)
def test_workspace_admin_inventory_principal_rejects_incomplete_authority(
    workspace: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        audit.workspace_admin_inventory_principal(workspace)


@pytest.mark.parametrize(
    ("workspace", "message"),
    [
        (_workspace(principal="other@example.com"), "unexpected principal"),
        (_workspace(admin=False), "workspace-admin"),
    ],
)
def test_main_rejects_incomplete_inventory_authority(
    monkeypatch: pytest.MonkeyPatch,
    workspace: object,
    message: str,
) -> None:
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)

    with pytest.raises(RuntimeError, match=message):
        audit.main(
            [
                "--application-id",
                "runtime-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_MANAGE",
                "--forbid-all-genie",
            ]
        )


def test_main_requires_explicit_genie_access_policy() -> None:
    with pytest.raises(SystemExit):
        audit.main(
            [
                "--application-id",
                "runtime-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_MANAGE",
            ]
        )


def test_main_forwards_narrow_legacy_pinned_serving_subset(monkeypatch) -> None:
    workspace = _workspace()
    serving: list[dict[str, object]] = []
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        audit,
        "audit_global_serving_endpoint_access",
        lambda _client, **kwargs: serving.append(kwargs),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_no_genie_access",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        audit,
        "_audit_managed_query_group_governance",
        lambda *_args, **_kwargs: _TARGET_IDENTITY_PROOF,
    )

    assert (
        audit.main(
            [
                "--application-id",
                "verifier-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "green-gateway",
                "--serving-endpoint",
                "signed-blue-gateway",
                "--legacy-pinned-serving-endpoint",
                "signed-blue-gateway",
                "--expected-serving-permission",
                "CAN_QUERY",
                "--forbid-all-genie",
            ]
        )
        == 0
    )
    assert serving[0]["legacy_pinned_endpoint_names"] == ("signed-blue-gateway",)


def test_main_can_prove_customer_serving_and_genie_denial(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace()
    no_serving: list[dict[str, object]] = []
    no_genie: list[dict[str, object]] = []
    managed_governance: list[dict[str, object]] = []
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        audit,
        "audit_global_no_serving_endpoint_access",
        lambda _client, **kwargs: no_serving.append(kwargs),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_no_genie_access",
        lambda _client, **kwargs: no_genie.append(kwargs),
    )
    monkeypatch.setattr(
        audit,
        "_audit_managed_query_group_governance",
        lambda _client, **kwargs: (
            managed_governance.append(kwargs) or _TARGET_IDENTITY_PROOF
        ),
    )

    assert (
        audit.main(
            [
                "--application-id",
                "verifier-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--forbid-customer-serving",
                "--forbid-all-genie",
            ]
        )
        == 0
    )
    assert no_serving == [
        {
            "service_principal": "verifier-id",
            "service_principal_id": "runtime-scim-id",
            "effective_group_names": {"hidden-account-parent"},
        }
    ]
    assert no_genie == [
        {
            "application_id": "verifier-id",
            "service_principal_id": "runtime-scim-id",
            "effective_group_names": {"hidden-account-parent"},
        }
    ]
    assert managed_governance == [
        {
            "account_id": "account-id",
            "application_id": "verifier-id",
            "assert_single_writer": _CREDENTIAL_LEASE,
        }
    ]
    output = capsys.readouterr().out
    assert "customer-created serving endpoints" in output
    assert "global access" not in output


def test_main_fails_before_resource_audits_when_target_proof_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        audit,
        "_audit_managed_query_group_governance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("target-credential membership proof was inconclusive")
        ),
    )

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("resource audit ran before target proof")

    monkeypatch.setattr(audit, "audit_global_serving_endpoint_access", unexpected)
    monkeypatch.setattr(audit, "audit_global_genie_access", unexpected)

    with pytest.raises(RuntimeError, match="target-credential membership proof"):
        audit.main(
            [
                "--application-id",
                "runtime-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_MANAGE",
                "--genie-space-id",
                "space-id",
            ]
        )


@pytest.mark.parametrize(
    "conflict",
    [
        ["--expected-serving-permission", "CAN_QUERY"],
        ["--legacy-pinned-serving-endpoint", "signed-blue-gateway"],
    ],
)
def test_forbid_customer_serving_rejects_positive_policy_options(
    conflict: list[str],
) -> None:
    with pytest.raises(SystemExit):
        audit.main(
            [
                "--application-id",
                "verifier-id",
                "--account-id",
                "account-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--forbid-customer-serving",
                "--forbid-all-genie",
                *conflict,
            ]
        )


def test_managed_group_governance_requires_account_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.cloud.databricks.com"),
        serving_endpoints=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="gateway")],
            get=lambda name: SimpleNamespace(name=name, id=f"{name}-id"),
        )
    )
    monkeypatch.setattr(
        audit,
        "inspect_managed_query_group",
        lambda *_args, **_kwargs: SimpleNamespace(
            contract=SimpleNamespace(id="managed-group-id")
        ),
    )

    with pytest.raises(RuntimeError, match="requires the Databricks account id"):
        audit._audit_managed_query_group_governance(
            workspace,
            account_id=None,
            application_id="app-client",
            assert_single_writer=lambda: None,
        )


def test_target_membership_proof_runs_without_managed_query_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.cloud.databricks.com"),
        serving_endpoints=SimpleNamespace(list=lambda: []),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id="app-client")
            ]
        ),
    )
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id="app-client")
            ]
        )
    )
    probes: list[dict[str, object]] = []

    def credential_lease() -> None:
        pass
    monkeypatch.setattr(
        audit,
        "assert_managed_query_group_administration_isolated",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("no managed group exists")
        ),
    )

    proof = audit._audit_managed_query_group_governance(
        workspace,
        account_id="account-id",
        application_id="app-client",
        assert_single_writer=credential_lease,
        account_factory=lambda: account,
        effective_group_probe=lambda *args, **kwargs: (
            probes.append({"args": args, "kwargs": kwargs})
            or {"hidden-parent-id": "hidden-account-parent"}
        ),
    )

    assert proof == (
        "app-scim",
        {"hidden-parent-id": "hidden-account-parent"},
    )
    assert probes == [
        {
            "args": (account,),
            "kwargs": {
                "account_sp_id": "app-scim",
                "application_id": "app-client",
                "expected_workspace_scim_id": "app-scim",
                "workspace_host": "https://workspace.cloud.databricks.com",
                "account_id": "account-id",
                "group_ids": (),
                "assert_single_writer": credential_lease,
            },
        }
    ]


def test_managed_group_governance_uses_exact_endpoint_and_principal_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.cloud.databricks.com"),
        serving_endpoints=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(name="green"),
                SimpleNamespace(name="legacy-direct"),
                SimpleNamespace(name="retired-empty"),
            ],
            get=lambda name: SimpleNamespace(
                name=name,
                id=f"{name}-immutable",
            ),
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id="app-client")
            ]
        ),
    )
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id="app-client")
            ]
        )
    )
    calls: list[dict[str, object]] = []
    probes: list[dict[str, object]] = []

    def credential_lease() -> None:
        pass
    monkeypatch.setattr(
        audit,
        "inspect_managed_query_group",
        lambda *_args, endpoint_id, **_kwargs: (
            SimpleNamespace(
                contract=SimpleNamespace(id=f"{endpoint_id}-group")
            )
            if endpoint_id in {"green-immutable", "retired-empty-immutable"}
            else None
        ),
    )
    monkeypatch.setattr(
        audit,
        "assert_managed_query_group_administration_isolated",
        lambda _workspace, **kwargs: calls.append(kwargs),
    )

    proof = audit._audit_managed_query_group_governance(
        workspace,
        account_id="account-id",
        application_id="app-client",
        assert_single_writer=credential_lease,
        account_factory=lambda: account,
        effective_group_probe=lambda *args, **kwargs: (
            probes.append({"args": args, "kwargs": kwargs})
            or {"hidden-parent-id": "hidden-account-parent"}
        ),
    )

    assert proof == (
        "app-scim",
        {"hidden-parent-id": "hidden-account-parent"},
    )
    assert probes == [
        {
            "args": (account,),
            "kwargs": {
                "account_sp_id": "app-scim",
                "application_id": "app-client",
                "expected_workspace_scim_id": "app-scim",
                "workspace_host": "https://workspace.cloud.databricks.com",
                "account_id": "account-id",
                "group_ids": (
                    "green-immutable-group",
                    "retired-empty-immutable-group",
                ),
                "assert_single_writer": credential_lease,
            },
        }
    ]
    assert calls == [
        {
            "account_id": "account-id",
            "endpoint_id": "green-immutable",
            "application_id": "app-client",
            "service_principal_id": "app-scim",
            "authoritative_effective_groups": {
                "hidden-parent-id": "hidden-account-parent"
            },
        },
        {
            "account_id": "account-id",
            "endpoint_id": "retired-empty-immutable",
            "application_id": "app-client",
            "service_principal_id": "app-scim",
            "authoritative_effective_groups": {
                "hidden-parent-id": "hidden-account-parent"
            },
        },
    ]


def test_empty_group_rejects_hidden_account_parent_manager_from_target_probe() -> None:
    endpoint_id = "retired-endpoint-id"
    application_id = "app-client"
    managed_group = SimpleNamespace(
        id="managed-group-id",
        display_name=query_groups.managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        external_id=query_groups.managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        members=[],
    )
    rule_name = "accounts/account-id/groups/managed-group-id/ruleSets/default"
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.cloud.databricks.com"),
        serving_endpoints=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="retired-gateway")],
            get=lambda name: SimpleNamespace(name=name, id=endpoint_id),
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id=application_id)
            ]
        ),
        # The workspace graph deliberately omits the account-only parent.
        groups=SimpleNamespace(
            list=lambda **_kwargs: [managed_group],
            get=lambda group_id: managed_group
            if group_id == managed_group.id
            else (_ for _ in ()).throw(AssertionError(group_id)),
        ),
        account_access_control_proxy=SimpleNamespace(
            get_rule_set=lambda name, etag: SimpleNamespace(
                name=name if name == rule_name else "",
                etag="rule-etag" if etag == "" else "",
                grant_rules=[
                    SimpleNamespace(
                        role="roles/group.manager",
                        principals=["groups/hidden-account-parent-id"],
                    )
                ],
            )
        ),
    )
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id=application_id)
            ]
        )
    )

    with pytest.raises(RuntimeError, match="administration authority"):
        audit._audit_managed_query_group_governance(
            workspace,
            account_id="account-id",
            application_id=application_id,
            assert_single_writer=lambda: None,
            account_factory=lambda: account,
            effective_group_probe=lambda *_args, **_kwargs: {
                "hidden-account-parent-id": "hidden-account-parent"
            },
        )
