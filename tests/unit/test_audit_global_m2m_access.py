from __future__ import annotations

from types import SimpleNamespace

import pytest

import tools.databricks.audit_global_m2m_access as audit
import tools.databricks.serving_query_group_access as query_groups
from tools.databricks.serving_query_group_provenance import (
    MissingClaimedGroupProvenanceError,
)

_TARGET_IDENTITY_PROOF = (
    "runtime-scim-id",
    {"hidden-parent-id": "hidden-account-parent"},
)


def _CREDENTIAL_LEASE() -> None:
    pass


def _main(argv: list[str]) -> int:
    return audit.main(["--app-name", "mip-app", *argv])


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
        _main(
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
                        "app_name": "mip-app",
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
        _main(
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
            "app_name": "mip-app",
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
        _main(
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
        _main(
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
        _main(
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
        _main(
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
            "app_name": "mip-app",
            "legacy_pinned_endpoint_names": (),
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
        _main(
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
        _main(
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
            app_name="mip-app",
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
        groups=SimpleNamespace(
            list=lambda **kwargs: (
                [
                    SimpleNamespace(
                        display_name=kwargs["filter"]
                        .removeprefix("displayName eq '")
                        .removesuffix("'")
                    )
                ]
                if any(
                    name in kwargs["filter"]
                    for name in ("green-immutable", "retired-empty-immutable")
                )
                else []
            )
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
        app_name="mip-app",
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
                "group_bindings": (),
                "assert_single_writer": credential_lease,
                "admin_workspace": workspace,
            },
        }
    ]


def test_managed_group_governance_uses_exact_endpoint_and_principal_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visible_group_names = {
        query_groups.managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id="app-client",
        )
        for endpoint_id in ("green-immutable", "retired-empty-immutable")
    }
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
        groups=SimpleNamespace(
            list=lambda **kwargs: (
                [
                    SimpleNamespace(
                        display_name=kwargs["filter"]
                        .removeprefix("displayName eq '")
                        .removesuffix("'")
                    )
                ]
                if kwargs["filter"]
                .removeprefix("displayName eq '")
                .removesuffix("'")
                in visible_group_names
                else []
            )
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
                contract=SimpleNamespace(
                    id=f"{endpoint_id}-group",
                    name=f"{endpoint_id}-managed",
                    external_id=f"mip:serving-query:{endpoint_id}",
                )
            )
            if endpoint_id in {"green-immutable", "retired-empty-immutable"}
            else None
        ),
    )
    monkeypatch.setattr(
        audit,
        "inspect_claimed_managed_query_group",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        audit,
        "managed_workspace_group_binding",
        lambda _workspace, *, group_id: audit.ManagedWorkspaceGroupBinding(
            id=group_id,
            name=f"{group_id.removesuffix('-group')}-managed",
            external_id=(
                "mip:serving-query:"
                f"{group_id.removesuffix('-immutable-group')}-immutable"
            ),
            resource_type="WorkspaceGroup",
        ),
    )
    monkeypatch.setattr(
        audit,
        "assert_legacy_managed_query_group_administration_isolated",
        lambda _workspace, **kwargs: calls.append(kwargs),
    )

    proof = audit._audit_managed_query_group_governance(
        workspace,
        app_name="mip-app",
        account_id="account-id",
        application_id="app-client",
        legacy_pinned_endpoint_names=("green", "retired-empty"),
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
                "group_bindings": (
                    audit.ManagedWorkspaceGroupBinding(
                        id="green-immutable-group",
                        name="green-immutable-managed",
                        external_id="mip:serving-query:green-immutable",
                        resource_type="WorkspaceGroup",
                    ),
                    audit.ManagedWorkspaceGroupBinding(
                        id="retired-empty-immutable-group",
                        name="retired-empty-immutable-managed",
                        external_id="mip:serving-query:retired-empty-immutable",
                        resource_type="WorkspaceGroup",
                    ),
                ),
                "assert_single_writer": credential_lease,
                "admin_workspace": workspace,
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


def test_unsigned_group_is_rejected_outside_explicit_legacy_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_id = "green-immutable"
    group_name = query_groups.managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id="app-client",
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="green")],
            get=lambda name: SimpleNamespace(name=name, id=endpoint_id),
            get_permissions=lambda _endpoint_id: SimpleNamespace(
                access_control_list=[]
            ),
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id="app-client")
            ]
        ),
        groups=SimpleNamespace(
            list=lambda **_kwargs: [SimpleNamespace(display_name=group_name)]
        ),
    )
    monkeypatch.setattr(
        audit,
        "inspect_claimed_managed_query_group",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            MissingClaimedGroupProvenanceError(
                "managed serving-query group has no signed immutable-ID provenance"
            )
        ),
    )
    monkeypatch.setattr(
        audit,
        "inspect_managed_query_group",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsigned group must not use the legacy path without a pin")
        ),
    )

    with pytest.raises(
        MissingClaimedGroupProvenanceError,
        match="no signed immutable-ID provenance",
    ):
        audit._audit_managed_query_group_governance(
            workspace,
            app_name="mip-app",
            account_id="account-id",
            application_id="app-client",
            assert_single_writer=lambda: None,
        )


def test_signed_group_audit_uses_immutable_id_when_name_projection_is_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_id = "hidden-endpoint-id"
    application_id = "app-client"
    group_name = query_groups.managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    external_id = query_groups.group_provenance.intent_external_id(
        endpoint_id=endpoint_id,
        application_id=application_id,
        creation_nonce="22222222-2222-4222-8222-222222222222",
    )
    managed_group = SimpleNamespace(
        id="hidden-group-id",
        display_name=group_name,
        external_id=external_id,
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.cloud.databricks.com"),
        serving_endpoints=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="hidden-endpoint")],
            get=lambda name: SimpleNamespace(name=name, id=endpoint_id),
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id=application_id)
            ]
        ),
        groups=SimpleNamespace(
            list=lambda **_kwargs: [],
            get=lambda group_id: managed_group
            if group_id == managed_group.id
            else (_ for _ in ()).throw(AssertionError(group_id)),
        ),
    )
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id=application_id)
            ]
        )
    )
    monkeypatch.setattr(
        query_groups.group_provenance,
        "require_claimed",
        lambda *_args, **_kwargs: {
            "group_id": managed_group.id,
            "external_id": managed_group.external_id,
        },
    )
    probes: list[tuple[audit.ManagedWorkspaceGroupBinding, ...]] = []
    isolated: list[str] = []
    monkeypatch.setattr(
        audit,
        "assert_managed_query_group_administration_isolated",
        lambda _workspace, **kwargs: isolated.append(str(kwargs["endpoint_id"])),
    )

    audit._audit_managed_query_group_governance(
        workspace,
        app_name="mip-app",
        account_id="account-id",
        application_id=application_id,
        assert_single_writer=lambda: None,
        account_factory=lambda: account,
        effective_group_probe=lambda *_args, **kwargs: (
            probes.append(tuple(kwargs["group_bindings"])) or {}
        ),
    )

    assert probes == [
        (
            audit.ManagedWorkspaceGroupBinding(
                id=managed_group.id,
                name=group_name,
                external_id=external_id,
                resource_type="WorkspaceGroup",
            ),
        )
    ]
    assert isolated == [endpoint_id]


def test_hidden_unsigned_acl_group_fails_closed_before_credential_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_id = "hidden-unsigned-endpoint-id"
    application_id = "app-client"
    group_name = query_groups.managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="hidden-unsigned")],
            get=lambda name: SimpleNamespace(name=name, id=endpoint_id),
            get_permissions=lambda _endpoint_id: SimpleNamespace(
                access_control_list=[
                    SimpleNamespace(group_name=group_name)
                ]
            ),
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id=application_id)
            ]
        ),
        groups=SimpleNamespace(list=lambda **_kwargs: []),
    )
    monkeypatch.setattr(
        audit,
        "inspect_claimed_managed_query_group",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            MissingClaimedGroupProvenanceError("missing proof")
        ),
    )

    with pytest.raises(
        MissingClaimedGroupProvenanceError,
        match="permission-bearing",
    ):
        audit._audit_managed_query_group_governance(
            workspace,
            app_name="mip-app",
            account_id="account-id",
            application_id=application_id,
            assert_single_writer=lambda: None,
            effective_group_probe=lambda *_args, **_kwargs: pytest.fail(
                "credential probe must not receive an incomplete group set"
            ),
        )


def test_empty_group_rejects_workspace_admin_from_target_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_id = "retired-endpoint-id"
    application_id = "app-client"
    managed_group = SimpleNamespace(
        id="managed-group-id",
        display_name=query_groups.managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        external_id=query_groups.group_provenance.intent_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
            creation_nonce="22222222-2222-4222-8222-222222222222",
        ),
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
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
    )
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(id="app-scim", application_id=application_id)
            ]
        )
    )
    monkeypatch.setattr(
        query_groups.group_provenance,
        "require_claimed",
        lambda *_args, **_kwargs: {
            "group_id": managed_group.id,
            "external_id": managed_group.external_id,
        },
    )

    with pytest.raises(RuntimeError, match="workspace-administration authority"):
        audit._audit_managed_query_group_governance(
            workspace,
            app_name="mip-app",
            account_id="account-id",
            application_id=application_id,
            assert_single_writer=lambda: None,
            account_factory=lambda: account,
            effective_group_probe=lambda *_args, **_kwargs: {
                "workspace-admins-id": "admins"
            },
        )
