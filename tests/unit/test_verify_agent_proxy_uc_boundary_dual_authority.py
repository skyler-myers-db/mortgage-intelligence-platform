from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import verify_agent_proxy_uc_boundary_dual_authority as dual
from tools.databricks.agent_runtime_uc_baseline import (
    _issue_control_plane_foreign_catalog_proof,
)

_APPLICATION_ID = "proxy-client"


def _proof():
    return _issue_control_plane_foreign_catalog_proof(
        application_id=_APPLICATION_ID,
        catalog="mip",
        metastore_id="metastore-id",
        workspace_id="workspace-id",
        grant_audited_catalogs=frozenset({"foreign"}),
        binding_denied_catalogs=(),
    )


def _args() -> list[str]:
    return [
        "--application-id",
        _APPLICATION_ID,
        "--expected-inventory-principal",
        "deployer@example.com",
        "--catalog",
        "mip",
        "--supervisor-id",
        "supervisor-id",
        "--supervisor-endpoint-id",
        "endpoint-id",
        "--genie-space-id",
        "genie-id",
    ]


def _attestation(
    *,
    workspace_principal_id: str = "proxy-scim",
    account_principal_id: str = "account-proxy-scim",
) -> dual.ReviewedProxyCapabilityAttestation:
    return dual.ReviewedProxyCapabilityAttestation(
        application_id=_APPLICATION_ID,
        workspace_principal_scim_id=workspace_principal_id,
        account_principal_scim_id=account_principal_id,
        groups=(
            dual.ReviewedWorkspaceCapabilityGroup(
                resource_plane="workspace_scim",
                resource_kind="supervisor",
                resource_id="supervisor-id",
                group_id="group-id",
                group_name="capability-group",
                group_external_id="capability-external-id",
                member_ids=(workspace_principal_id,),
            ),
        ),
    )


def test_dual_authority_couples_control_and_proxy_in_one_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example"))
    proxy = SimpleNamespace(name="proxy")
    clients = iter([admin, proxy])
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(dual, "WorkspaceClient", lambda: next(clients))
    monkeypatch.setattr(
        dual,
        "_reviewed_workspace_capability_groups",
        lambda *_args, **_kwargs: _attestation(),
    )
    account = object()
    monkeypatch.setattr(dual, "account_client_from_env", lambda: account)
    monkeypatch.setattr(
        dual,
        "audit_foreign_uc_access",
        lambda workspace, **kwargs: (
            events.append(("control", (workspace, kwargs["account_factory"]()))) or _proof()
        ),
    )
    monkeypatch.setattr(
        dual,
        "verify_effective_agent_proxy_uc_boundary",
        lambda workspace, **kwargs: events.append(
            ("proxy", (workspace, kwargs["foreign_control_plane_proof"]))
        ),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", _APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "proxy-secret")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "deployer")
    monkeypatch.setenv("DATABRICKS_TOKEN", "deployer-token")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_SECRET", "account-secret")

    assert dual.main(_args()) == 0

    assert events == [
        ("control", (admin, account)),
        ("proxy", (proxy, _proof())),
        ("control", (admin, account)),
    ]
    assert dual.os.environ["DATABRICKS_CLIENT_ID"] == _APPLICATION_ID
    assert dual.os.environ["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
    assert "DATABRICKS_TOKEN" not in dual.os.environ
    assert "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in dual.os.environ


def test_dual_authority_never_binds_proxy_when_control_plane_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example"))
    calls = 0

    def workspace_client() -> object:
        nonlocal calls
        calls += 1
        return admin

    monkeypatch.setattr(dual, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(
        dual,
        "_reviewed_workspace_capability_groups",
        lambda *_args, **_kwargs: _attestation(),
    )
    monkeypatch.setattr(dual, "account_client_from_env", lambda: object())
    monkeypatch.setattr(
        dual,
        "audit_foreign_uc_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("foreign grant found")),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", _APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "proxy-secret")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "deployer")
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)

    with pytest.raises(RuntimeError, match="foreign grant found"):
        dual.main(_args())

    assert calls == 1
    assert dual.os.environ["DATABRICKS_CONFIG_PROFILE"] == "deployer"
    assert "DATABRICKS_CLIENT_ID" not in dual.os.environ


def test_dual_authority_rejects_principal_replacement_between_authority_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example"))
    proxy = SimpleNamespace(name="proxy")
    clients = iter([admin, proxy])
    attestations = iter(
        (
            _attestation(workspace_principal_id="original-proxy-scim"),
            _attestation(workspace_principal_id="replacement-proxy-scim"),
        )
    )
    monkeypatch.setattr(dual, "WorkspaceClient", lambda: next(clients))
    monkeypatch.setattr(
        dual,
        "_reviewed_workspace_capability_groups",
        lambda *_args, **_kwargs: next(attestations),
    )
    monkeypatch.setattr(dual, "account_client_from_env", object)
    monkeypatch.setattr(
        dual,
        "audit_foreign_uc_access",
        lambda *_args, **_kwargs: _proof(),
    )
    monkeypatch.setattr(
        dual,
        "verify_effective_agent_proxy_uc_boundary",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", _APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "proxy-secret")

    with pytest.raises(RuntimeError, match="capability groups changed"):
        dual.main(_args())


def test_reviewed_capability_groups_bind_exact_resource_and_member_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dual,
        "workspace_target_identity",
        lambda *_args, **_kwargs: SimpleNamespace(scim_id="proxy-scim"),
    )
    monkeypatch.setattr(
        dual,
        "account_target_identity",
        lambda *_args, **_kwargs: ("account-proxy-scim", "proxy"),
    )

    def capability(
        _workspace: object,
        *,
        resource_kind: str,
        resource_id: str,
        **_kwargs: object,
    ) -> object:
        return SimpleNamespace(
            contract=SimpleNamespace(
                id=f"{resource_kind}-{resource_id}-group-id",
                name=f"{resource_kind}-{resource_id}-group",
                external_id=f"{resource_kind}-{resource_id}-external-id",
            ),
            member_ids=("proxy-scim",),
        )

    monkeypatch.setattr(dual, "inspect_managed_agent_proxy_group", capability)
    monkeypatch.setattr(
        dual,
        "inspect_managed_query_group",
        lambda _workspace, *, endpoint_id, **_kwargs: SimpleNamespace(
            contract=SimpleNamespace(
                id=f"query-{endpoint_id}-group-id",
                name=f"query-{endpoint_id}-group",
                external_id=f"query-{endpoint_id}-external-id",
            ),
            member_ids=("proxy-scim",),
        ),
    )

    attestation = dual._reviewed_workspace_capability_groups(
        object(),
        account=object(),
        application_id=_APPLICATION_ID,
        supervisor_ids=("supervisor-a", "supervisor-b"),
        supervisor_endpoint_ids=("endpoint-a", "endpoint-b"),
        genie_space_id="genie",
    )

    assert attestation.workspace_principal_scim_id == "proxy-scim"
    assert attestation.account_principal_scim_id == "account-proxy-scim"
    assert attestation.allowed_workspace_groups() == {
        "supervisor-supervisor-a-group-id": "supervisor-supervisor-a-group",
        "query-endpoint-a-group-id": "query-endpoint-a-group",
        "supervisor-supervisor-b-group-id": "supervisor-supervisor-b-group",
        "query-endpoint-b-group-id": "query-endpoint-b-group",
        "genie-genie-group-id": "genie-genie-group",
    }


def test_reviewed_capability_groups_reject_unrelated_query_group_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dual,
        "workspace_target_identity",
        lambda *_args, **_kwargs: SimpleNamespace(scim_id="proxy-scim"),
    )
    monkeypatch.setattr(
        dual,
        "account_target_identity",
        lambda *_args, **_kwargs: ("account-proxy-scim", "proxy"),
    )
    monkeypatch.setattr(
        dual,
        "inspect_managed_agent_proxy_group",
        lambda *_args, resource_kind, resource_id, **_kwargs: SimpleNamespace(
            contract=SimpleNamespace(
                id=f"{resource_kind}-{resource_id}-id",
                name=f"{resource_kind}-{resource_id}",
                external_id=f"{resource_kind}-{resource_id}-external-id",
            ),
            member_ids=("proxy-scim",),
        ),
    )
    monkeypatch.setattr(
        dual,
        "inspect_managed_query_group",
        lambda *_args, **_kwargs: SimpleNamespace(
            contract=SimpleNamespace(
                id="query-id",
                name="query-name",
                external_id="query-external-id",
            ),
            member_ids=("unrelated-scim",),
        ),
    )

    with pytest.raises(RuntimeError, match="unrelated member"):
        dual._reviewed_workspace_capability_groups(
            object(),
            account=object(),
            application_id=_APPLICATION_ID,
            supervisor_ids=("supervisor",),
            supervisor_endpoint_ids=("endpoint",),
            genie_space_id="genie",
        )
