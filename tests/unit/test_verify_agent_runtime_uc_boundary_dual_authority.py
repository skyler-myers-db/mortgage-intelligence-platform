from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import verify_agent_runtime_uc_boundary_dual_authority as dual
from tools.databricks.agent_runtime_uc_baseline import (
    ControlPlaneForeignCatalogProof,
    _issue_control_plane_foreign_catalog_proof,
)

APPLICATION_ID = "runtime-client"


def _args() -> list[str]:
    return [
        "--application-id",
        APPLICATION_ID,
        "--expected-inventory-principal",
        "deployer@example.com",
        "--supervisor-id",
        "supervisor-id",
        "--supervisor-endpoint-id",
        "supervisor-endpoint-id",
        "--catalog",
        "mip",
        "--gateway-model",
        "mip.audit.gateway_model",
        "--genie-space-id",
        "genie-id",
        "--inference-table-prefix",
        "gateway_table",
    ]


def _proof() -> ControlPlaneForeignCatalogProof:
    return _issue_control_plane_foreign_catalog_proof(
        application_id=APPLICATION_ID,
        catalog="mip",
        metastore_id="metastore-id",
        workspace_id="workspace-id",
        grant_audited_catalogs=frozenset({"other"}),
        binding_denied_catalogs=(),
    )


def test_dual_authority_cli_couples_control_and_runtime_in_one_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example.invalid"))
    runtime = SimpleNamespace(name="runtime")
    clients = iter([admin, runtime])
    events: list[tuple[str, object]] = []
    policies: list[str] = []
    monkeypatch.setattr(dual, "WorkspaceClient", lambda: next(clients))
    account_client = object()
    monkeypatch.setattr(dual, "account_client_from_env", lambda: account_client)
    monkeypatch.setattr(
        dual,
        "audit_foreign_uc_access",
        lambda workspace, **kwargs: (
            policies.append(kwargs["foreign_catalog_binding_policy"])
            or events.append(("control", (workspace, kwargs["account_factory"]())))
            or _proof()
        ),
    )
    monkeypatch.setattr(
        dual,
        "verify_effective_uc_boundary",
        lambda workspace, **kwargs: (
            events.append(("runtime", (workspace, kwargs["foreign_control_plane_proof"])))
            if "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in dual.os.environ
            else (_ for _ in ()).throw(AssertionError("account secret leaked"))
        ),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "deployer-profile")
    monkeypatch.setenv("DATABRICKS_TOKEN", "deployer-token")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_ID", "account-client")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_SECRET", "account-secret")
    monkeypatch.setenv(
        "MIP_UC_FOREIGN_CATALOG_BINDING_POLICY",
        '{"version":1,"catalogs":{}}',
    )

    assert dual.main(_args()) == 0

    assert events == [
        ("control", (admin, account_client)),
        ("runtime", (runtime, _proof())),
        ("control", (admin, account_client)),
    ]
    assert "DATABRICKS_CONFIG_PROFILE" not in dual.os.environ
    assert "DATABRICKS_TOKEN" not in dual.os.environ
    assert dual.os.environ["DATABRICKS_CLIENT_ID"] == APPLICATION_ID
    assert dual.os.environ["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
    assert policies == [
        '{"version":1,"catalogs":{}}',
        '{"version":1,"catalogs":{}}',
    ]


def test_dual_authority_cli_never_binds_runtime_when_control_plane_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example.invalid"))
    calls = 0

    def client() -> object:
        nonlocal calls
        calls += 1
        return admin

    monkeypatch.setattr(dual, "WorkspaceClient", client)
    monkeypatch.setattr(dual, "account_client_from_env", lambda: object())
    monkeypatch.setattr(
        dual,
        "audit_foreign_uc_access",
        lambda _workspace, **_kwargs: (_ for _ in ()).throw(RuntimeError("foreign grant found")),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "deployer-profile")
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)

    with pytest.raises(RuntimeError, match="foreign grant found"):
        dual.main(_args())

    assert calls == 1
    assert dual.os.environ["DATABRICKS_CONFIG_PROFILE"] == "deployer-profile"
    assert "DATABRICKS_CLIENT_ID" not in dual.os.environ


def test_dual_authority_cli_requires_exact_runtime_credential_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example.invalid"))
    monkeypatch.setattr(dual, "WorkspaceClient", lambda: admin)
    monkeypatch.setattr(dual, "account_client_from_env", lambda: object())
    monkeypatch.setattr(dual, "audit_foreign_uc_access", lambda *_args, **_kwargs: _proof())
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "other-runtime")
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")

    with pytest.raises(RuntimeError, match="exact agent-runtime OAuth"):
        dual.main(_args())


def test_dual_authority_cli_rejects_control_plane_drift_after_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example.invalid"))
    runtime = SimpleNamespace(name="runtime")
    clients = iter([admin, runtime])
    proofs = iter(
        [
            _proof(),
            _issue_control_plane_foreign_catalog_proof(
                application_id=APPLICATION_ID,
                catalog="mip",
                metastore_id="metastore-id",
                workspace_id="workspace-id",
                grant_audited_catalogs=frozenset({"other", "new-catalog"}),
                binding_denied_catalogs=(),
            ),
        ]
    )
    monkeypatch.setattr(dual, "WorkspaceClient", lambda: next(clients))
    monkeypatch.setattr(dual, "account_client_from_env", lambda: object())
    monkeypatch.setattr(dual, "audit_foreign_uc_access", lambda *_args, **_kwargs: next(proofs))
    monkeypatch.setattr(dual, "verify_effective_uc_boundary", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")

    with pytest.raises(RuntimeError, match="changed during runtime audit"):
        dual.main(_args())
