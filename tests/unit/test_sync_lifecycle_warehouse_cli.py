"""Authentication contracts for the lifecycle-sync CLI."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import SecretStr

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "tools" / "sync_lifecycle_warehouse.py"
SPEC = importlib.util.spec_from_file_location("mip_sync_lifecycle_warehouse_cli", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_direct_cli_execution_does_not_shadow_databricks_sdk() -> None:
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Mirror Lakebase approvals/outreach state" in result.stdout


def test_workspace_token_reuses_pat_for_exact_configured_host(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example.com/")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test-pat")

    with patch.object(MODULE, "_cli_token") as cli_token:
        assert MODULE._workspace_token("https://dbc.example.com") == "dapi-test-pat"

    cli_token.assert_not_called()


def test_workspace_token_never_forwards_pat_to_other_host(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test-pat")

    with patch.object(MODULE, "_cli_token", return_value="oauth-token") as cli_token:
        assert MODULE._workspace_token("https://other.example.com") == "oauth-token"

    cli_token.assert_called_once_with("https://other.example.com")


def test_workspace_token_mints_oauth_when_pat_is_absent(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example.com")
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    with patch.object(MODULE, "_cli_token", return_value="oauth-token") as cli_token:
        assert MODULE._workspace_token("https://dbc.example.com") == "oauth-token"

    cli_token.assert_called_once_with("https://dbc.example.com")


def test_static_lakebase_auth_still_honors_explicit_database(monkeypatch) -> None:
    settings = SimpleNamespace(lakebase_database="wrong_ambient_database")
    monkeypatch.setenv("LAKEBASE_HOST", "live-lakebase.example")
    monkeypatch.setenv("LAKEBASE_PASSWORD", "static-password")
    monkeypatch.setenv("LAKEBASE_DATABASE", "wrong_ambient_database")
    monkeypatch.setenv("PGDATABASE", "wrong_ambient_database")

    MODULE._ensure_lakebase_env(
        settings,
        database_name="mip_pr105_database",
    )

    assert os.environ["LAKEBASE_DATABASE"] == "mip_pr105_database"
    assert os.environ["PGDATABASE"] == "mip_pr105_database"
    assert settings.lakebase_database == "mip_pr105_database"


def test_bound_deployer_workspace_owns_sql_and_lakebase_auth(monkeypatch) -> None:
    class _Config:
        host = "https://reviewed-workspace.example"

        @staticmethod
        def authenticate() -> dict[str, str]:
            return {"Authorization": "Bearer reviewed-workspace-token"}

    class _Database:
        @staticmethod
        def get_database_instance(_name: str) -> object:
            return type("Instance", (), {"read_write_dns": "reviewed-lakebase.example"})()

        @staticmethod
        def generate_database_credential(**_kwargs: object) -> object:
            return type("Credential", (), {"token": "reviewed-lakebase-token"})()

    workspace = SimpleNamespace(
        config=_Config(),
        database=_Database(),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="reviewed-deployer")),
    )
    settings = SimpleNamespace(
        databricks_host="https://hostile-dotenv.example",
        databricks_warehouse_id="warehouse-id",
        lakebase_host="hostile-dotenv-lakebase.example",
        lakebase_port=5432,
        lakebase_database="hostile-db",
        lakebase_user="hostile-user",
        lakebase_password=SecretStr("hostile-password"),
        lakebase_sslmode="disable",
    )
    for name, value in {
        "LAKEBASE_HOST": "hostile-ambient-lakebase.example",
        "LAKEBASE_PASSWORD": "hostile-ambient-password",
        "PGHOST": "hostile-pg.example",
        "PGPASSWORD": "hostile-pg-password",
    }.items():
        monkeypatch.setenv(name, value)

    MODULE._ensure_lakebase_env(
        settings,
        workspace=workspace,
        instance_name="mip-pr105-state",
        database_name="mip_pr105_database",
    )
    client = MODULE._build_client(17, settings, workspace)

    assert os.environ["LAKEBASE_HOST"] == "reviewed-lakebase.example"
    assert os.environ["LAKEBASE_USER"] == "reviewed-deployer"
    assert os.environ["LAKEBASE_PASSWORD"] == "reviewed-lakebase-token"
    assert os.environ["PGHOST"] == "reviewed-lakebase.example"
    assert os.environ["PGUSER"] == "reviewed-deployer"
    assert os.environ["PGPASSWORD"] == "reviewed-lakebase-token"
    assert os.environ["LAKEBASE_DATABASE"] == "mip_pr105_database"
    assert os.environ["PGDATABASE"] == "mip_pr105_database"
    assert settings.lakebase_host == "reviewed-lakebase.example"
    assert settings.lakebase_password.get_secret_value() == "reviewed-lakebase-token"
    assert settings.lakebase_database == "mip_pr105_database"
    assert client._host == "https://reviewed-workspace.example"
    assert client._warehouse_id == "warehouse-id"
    assert client._token_provider() == "reviewed-workspace-token"


def test_lifecycle_loader_does_not_rehydrate_dotenv_auth_when_deployer_bound(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.local").write_text(
        "DATABRICKS_HOST=https://hostile-dotenv.example\n"
        "DATABRICKS_TOKEN=hostile-dotenv-pat\n"
        "DATABRICKS_AUTH_TYPE=pat\n"
        "DATABRICKS_CONFIG_PROFILE=STALE\n"
        "DATABRICKS_CLIENT_ID=hostile-app-client\n"
        "DATABRICKS_CLIENT_SECRET=hostile-app-secret\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_PROFILE", "REVIEWED")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "REVIEWED")
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_WAREHOUSE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    MODULE._load_local_env()

    assert os.environ["DATABRICKS_CONFIG_PROFILE"] == "REVIEWED"
    assert os.environ["DATABRICKS_WAREHOUSE_ID"] == "warehouse-id"
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ):
        assert name not in os.environ
