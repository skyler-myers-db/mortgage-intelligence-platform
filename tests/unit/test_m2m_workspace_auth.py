from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from tools.databricks.m2m_workspace_auth import (
    bind_exact_workspace_m2m_auth,
    reviewed_databricks_account_origin,
)


def _admin_workspace(host: str = "https://workspace.cloud.databricks.com") -> object:
    return SimpleNamespace(config=SimpleNamespace(host=host))


def test_binds_exact_m2m_after_removing_ambient_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_TOKEN", "admin-token")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_ID", "account-client")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_SECRET", "account-secret")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_ID", "account-id")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", "/tmp/ambient-databrickscfg")
    monkeypatch.setenv("DATABRICKS_DISCOVERY_URL", "https://attacker.invalid")
    monkeypatch.setenv("DATABRICKS_CLOUD", "azure")
    monkeypatch.setenv("DATABRICKS_WORKSPACE_ID", "workspace-id")
    monkeypatch.setenv("DATABRICKS_TOKEN_AUDIENCE", "ambient-audience")
    monkeypatch.setenv("DATABRICKS_OIDC_TOKEN_ENV", "AMBIENT_OIDC_VALUE")
    monkeypatch.setenv("DATABRICKS_OIDC_TOKEN_FILEPATH", "/tmp/ambient-oidc")
    monkeypatch.setenv("DATABRICKS_METADATA_SERVICE_URL", "https://attacker.invalid")
    monkeypatch.setenv("AMBIENT_OIDC_VALUE", "ambient-oidc-token")
    monkeypatch.setenv("ARM_CLIENT_ID", "azure-client")
    monkeypatch.setenv("ARM_CLIENT_SECRET", "azure-secret")
    monkeypatch.setenv("ARM_TENANT_ID", "azure-tenant")
    monkeypatch.setenv("ARM_USE_MSI", "true")
    monkeypatch.setenv("ARM_ENVIRONMENT", "PUBLIC")
    monkeypatch.setenv("GOOGLE_CREDENTIALS", "google-secret")
    monkeypatch.setenv(
        "DATABRICKS_GOOGLE_SERVICE_ACCOUNT",
        "ambient@example.invalid",
    )
    monkeypatch.setenv("UNRELATED_SIGNING_KEY", "unrelated-signing-key")
    monkeypatch.setenv("SAFE_SETTING", "retained")
    monkeypatch.setenv(
        "MIP_DEPLOYER_DATABRICKS_HOST",
        "https://workspace.cloud.databricks.com",
    )
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_PROFILE", "DEFAULT")
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_TOKEN", "deployer-token")
    monkeypatch.setenv("DATABRICKS_HOST", "https://ambient.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "ambient-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "ambient-secret")
    monkeypatch.setenv("MIP_DISABLE_DOTENV", "0")
    monkeypatch.setenv("EXACT_CLIENT_ID", "target-client")
    monkeypatch.setenv("EXACT_CLIENT_SECRET", "target-secret")

    client_id, client_secret = bind_exact_workspace_m2m_auth(
        admin_workspace=_admin_workspace(),
        expected_application_id="target-client",
        client_id_env="EXACT_CLIENT_ID",
        client_secret_env="EXACT_CLIENT_SECRET",
        label="target",
    )

    assert (client_id, client_secret) == ("target-client", "target-secret")
    assert os.environ["DATABRICKS_HOST"] == "https://workspace.cloud.databricks.com"
    assert os.environ["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
    assert os.environ["DATABRICKS_CLIENT_ID"] == "target-client"
    assert os.environ["DATABRICKS_CLIENT_SECRET"] == "target-secret"
    assert os.environ["MIP_DISABLE_DOTENV"] == "1"
    assert "DATABRICKS_TOKEN" not in os.environ
    assert "DATABRICKS_ACCOUNT_CLIENT_ID" not in os.environ
    assert "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in os.environ
    assert "DATABRICKS_ACCOUNT_ID" not in os.environ
    assert "DATABRICKS_CONFIG_FILE" not in os.environ
    assert "DATABRICKS_DISCOVERY_URL" not in os.environ
    assert "DATABRICKS_CLOUD" not in os.environ
    assert "DATABRICKS_WORKSPACE_ID" not in os.environ
    assert "DATABRICKS_TOKEN_AUDIENCE" not in os.environ
    assert "DATABRICKS_OIDC_TOKEN_ENV" not in os.environ
    assert "DATABRICKS_OIDC_TOKEN_FILEPATH" not in os.environ
    assert "DATABRICKS_METADATA_SERVICE_URL" not in os.environ
    assert "AMBIENT_OIDC_VALUE" not in os.environ
    assert "ARM_CLIENT_ID" not in os.environ
    assert "ARM_CLIENT_SECRET" not in os.environ
    assert "ARM_TENANT_ID" not in os.environ
    assert "ARM_USE_MSI" not in os.environ
    assert "ARM_ENVIRONMENT" not in os.environ
    assert "GOOGLE_CREDENTIALS" not in os.environ
    assert "DATABRICKS_GOOGLE_SERVICE_ACCOUNT" not in os.environ
    assert "UNRELATED_SIGNING_KEY" not in os.environ
    assert "MIP_DEPLOYER_DATABRICKS_HOST" not in os.environ
    assert "MIP_DEPLOYER_DATABRICKS_PROFILE" not in os.environ
    assert "MIP_DEPLOYER_DATABRICKS_TOKEN" not in os.environ
    assert os.environ["SAFE_SETTING"] == "retained"
    assert "EXACT_CLIENT_SECRET" not in os.environ


@pytest.mark.parametrize(
    ("configured_id", "secret", "host"),
    (
        ("wrong-client", "target-secret", "https://workspace.cloud.databricks.com"),
        ("target-client", "", "https://workspace.cloud.databricks.com"),
        ("target-client", "target-secret", ""),
    ),
)
def test_rejects_incomplete_or_mismatched_binding_before_mutating_auth(
    monkeypatch: pytest.MonkeyPatch,
    configured_id: str,
    secret: str,
    host: str,
) -> None:
    monkeypatch.setenv("DATABRICKS_TOKEN", "admin-token")
    monkeypatch.setenv("EXACT_CLIENT_ID", configured_id)
    monkeypatch.setenv("EXACT_CLIENT_SECRET", secret)

    with pytest.raises(RuntimeError, match="exact OAuth credential"):
        bind_exact_workspace_m2m_auth(
            admin_workspace=_admin_workspace(host),
            expected_application_id="target-client",
            client_id_env="EXACT_CLIENT_ID",
            client_secret_env="EXACT_CLIENT_SECRET",
            label="target",
        )

    assert os.environ["DATABRICKS_TOKEN"] == "admin-token"


@pytest.mark.parametrize(
    "host",
    (
        "http://workspace.cloud.databricks.com",
        "https://workspace.cloud.databricks.com.evil.example",
        "https://workspace.cloud.databricks.com/path",
        "https://user@workspace.cloud.databricks.com",
        "https://workspace.cloud.databricks.com:443",
    ),
)
def test_rejects_unreviewed_workspace_origin_before_mutating_auth(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    monkeypatch.setenv("DATABRICKS_TOKEN", "admin-token")
    monkeypatch.setenv("EXACT_CLIENT_ID", "target-client")
    monkeypatch.setenv("EXACT_CLIENT_SECRET", "target-secret")

    with pytest.raises(RuntimeError, match="reviewed HTTPS Databricks origin"):
        bind_exact_workspace_m2m_auth(
            admin_workspace=_admin_workspace(host),
            expected_application_id="target-client",
            client_id_env="EXACT_CLIENT_ID",
            client_secret_env="EXACT_CLIENT_SECRET",
            label="target",
        )

    assert os.environ["DATABRICKS_TOKEN"] == "admin-token"
    assert os.environ["EXACT_CLIENT_SECRET"] == "target-secret"


@pytest.mark.parametrize(
    "origin",
    (
        "https://accounts.cloud.databricks.com",
        "https://accounts.gcp.databricks.com",
        "https://accounts.azuredatabricks.net",
    ),
)
def test_accepts_canonical_databricks_account_origins(origin: str) -> None:
    assert (
        reviewed_databricks_account_origin(origin, label="account host")
        == origin
    )


@pytest.mark.parametrize(
    "origin",
    (
        "http://accounts.cloud.databricks.com",
        "https://accounts.cloud.databricks.com.evil.example",
        "https://workspace.cloud.databricks.com",
        "https://accounts.cloud.databricks.com/path",
        "https://user@accounts.cloud.databricks.com",
        "https://accounts.cloud.databricks.com:443",
    ),
)
def test_rejects_unreviewed_databricks_account_origins(origin: str) -> None:
    with pytest.raises(RuntimeError, match="reviewed Databricks account origin"):
        reviewed_databricks_account_origin(origin, label="account host")
