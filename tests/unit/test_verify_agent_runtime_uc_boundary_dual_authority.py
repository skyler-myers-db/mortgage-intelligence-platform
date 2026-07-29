from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import verify_agent_runtime_uc_boundary_dual_authority as dual
from tools.databricks.agent_runtime_uc_baseline import (
    ControlPlaneForeignCatalogProof,
    _issue_control_plane_foreign_catalog_proof,
)

APPLICATION_ID = "runtime-client"


def _lifecycle_proof(**changes: object) -> SimpleNamespace:
    values = {
        "application_id": APPLICATION_ID,
        "inventory_principal": "deployer@example.com",
        "catalog": "mip",
        "metastore_id": "metastore-id",
        "workspace_id": "workspace-id",
        "model_family": "mip.audit.gateway_model",
        "candidate_model": "mip.audit.gateway_model",
        "states": (),
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _stub_lifecycle_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dual, "MlflowClient", lambda **_kwargs: object())
    monkeypatch.setattr(
        dual,
        "delta_version_resolver",
        lambda _workspace, **_kwargs: (lambda _table_name: "1"),
    )
    monkeypatch.setattr(
        dual,
        "audit_gateway_model_lifecycle",
        lambda *_args, **_kwargs: _lifecycle_proof(),
    )


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
        "--proxy-caller-application-id",
        "proxy-client",
        "--proxy-caller-credential-id",
        "proxy-credential",
        "--proxy-caller-secret-reference",
        "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}",
        "--app-name",
        "mortgage-intelligence-platform",
        "--deployment-lease-id",
        "11111111-1111-4111-8111-111111111111",
        "--deployment-source-git-sha",
        "1" * 40,
        "--app-application-id",
        "app-client",
        "--verifier-application-id",
        "verifier-client",
        "--archive-owner",
        "deployer@example.com",
        "--governance-group",
        "mip-admin",
        "--rollback-scope",
        "mip-rollback",
        "--lakebase-instance",
        "mip-lakebase",
        "--warehouse-id",
        "warehouse-id",
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
    lifecycle_calls: list[dict[str, object]] = []
    lifecycle_proof = _lifecycle_proof()
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
            events.append(
                (
                    "runtime",
                    (
                        workspace,
                        kwargs["foreign_control_plane_proof"],
                        kwargs["gateway_model_lifecycle_proof"],
                        kwargs["expected_inventory_principal"],
                    ),
                )
            )
            if "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in dual.os.environ
            else (_ for _ in ()).throw(AssertionError("account secret leaked"))
        ),
    )
    monkeypatch.setattr(
        dual,
        "audit_gateway_model_lifecycle",
        lambda *_args, **kwargs: (
            lifecycle_calls.append(kwargs) or lifecycle_proof
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
        (
            "runtime",
            (
                runtime,
                _proof(),
                lifecycle_proof,
                "deployer@example.com",
            ),
        ),
        ("control", (admin, account_client)),
    ]
    assert len(lifecycle_calls) == 2
    assert all(
        call["expected_candidate_model"] == "mip.audit.gateway_model"
        and call["expected_inventory_principal"] == "deployer@example.com"
        for call in lifecycle_calls
    )
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


def test_dual_authority_cli_never_binds_runtime_when_lifecycle_audit_fails(
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
    monkeypatch.setattr(dual, "audit_foreign_uc_access", lambda *_args, **_kwargs: _proof())
    monkeypatch.setattr(
        dual,
        "audit_gateway_model_lifecycle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("historical model is unclassified")
        ),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "deployer-profile")
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)

    with pytest.raises(RuntimeError, match="historical model is unclassified"):
        dual.main(_args())

    assert calls == 1
    assert dual.os.environ["DATABRICKS_CONFIG_PROFILE"] == "deployer-profile"
    assert "DATABRICKS_CLIENT_ID" not in dual.os.environ


def test_dual_authority_cli_rejects_lifecycle_drift_after_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example.invalid"))
    runtime = SimpleNamespace(name="runtime")
    clients = iter([admin, runtime])
    lifecycle_proofs = iter(
        [
            _lifecycle_proof(),
            _lifecycle_proof(states=(SimpleNamespace(model_name="new"),)),
        ]
    )
    monkeypatch.setattr(dual, "WorkspaceClient", lambda: next(clients))
    monkeypatch.setattr(dual, "account_client_from_env", lambda: object())
    monkeypatch.setattr(dual, "audit_foreign_uc_access", lambda *_args, **_kwargs: _proof())
    monkeypatch.setattr(
        dual,
        "audit_gateway_model_lifecycle",
        lambda *_args, **_kwargs: next(lifecycle_proofs),
    )
    monkeypatch.setattr(dual, "verify_effective_uc_boundary", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")

    with pytest.raises(RuntimeError, match="lifecycle proof changed during runtime audit"):
        dual.main(_args())
