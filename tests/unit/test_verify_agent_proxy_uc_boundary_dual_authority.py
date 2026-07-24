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
    ]


def test_dual_authority_couples_control_and_proxy_in_one_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example"))
    proxy = SimpleNamespace(name="proxy")
    clients = iter([admin, proxy])
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(dual, "WorkspaceClient", lambda: next(clients))
    account = object()
    monkeypatch.setattr(dual, "account_client_from_env", lambda: account)
    monkeypatch.setattr(
        dual,
        "audit_foreign_uc_access",
        lambda workspace, **kwargs: (
            events.append(("control", (workspace, kwargs["account_factory"]())))
            or _proof()
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
    monkeypatch.setattr(dual, "account_client_from_env", lambda: object())
    monkeypatch.setattr(
        dual,
        "audit_foreign_uc_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("foreign grant found")
        ),
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
