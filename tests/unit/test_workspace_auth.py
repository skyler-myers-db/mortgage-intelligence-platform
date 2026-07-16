from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.databricks import bundle_env
from tools.databricks.workspace_auth import deployment_workspace_client

REPO = Path(__file__).resolve().parents[2]


def _recording_factory(calls: list[dict[str, Any]]) -> Any:
    def build(**kwargs: Any) -> object:
        calls.append(kwargs)
        return object()

    return build


def test_deployer_profile_ignores_exported_app_facing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "MIP_DEPLOYER_DATABRICKS_PROFILE": "DEFAULT",
        "DATABRICKS_CLIENT_ID": "normal-app-client",
        "DATABRICKS_CLIENT_SECRET": "normal-app-secret",
        "DATABRICKS_ADMIN_CLIENT_ID": "admin-app-client",
        "DATABRICKS_VERIFIER_CLIENT_ID": "verifier-client",
    }.items():
        monkeypatch.setenv(name, value)
    calls: list[dict[str, Any]] = []

    deployment_workspace_client(factory=_recording_factory(calls))

    assert calls == [{"profile": "DEFAULT"}]


def test_deployer_pat_ignores_exported_app_facing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_TOKEN", "deployer-pat")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "normal-app-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "normal-app-secret")
    calls: list[dict[str, Any]] = []

    deployment_workspace_client(factory=_recording_factory(calls))

    assert calls == [
        {
            "host": "https://workspace.example",
            "token": "deployer-pat",
            "auth_type": "pat",
        }
    ]


@pytest.mark.parametrize(
    ("host", "token", "profile"),
    (("https://workspace.example", "", ""), ("", "deployer-pat", ""), ("h", "t", "p")),
)
def test_partial_or_ambiguous_deployer_binding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    token: str,
    profile: str,
) -> None:
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_HOST", host)
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_TOKEN", token)
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_PROFILE", profile)

    with pytest.raises(RuntimeError):
        deployment_workspace_client(factory=lambda **_kwargs: object())


def test_unbound_non_deploy_call_retains_sdk_default_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MIP_DEPLOYER_DATABRICKS_HOST",
        "MIP_DEPLOYER_DATABRICKS_TOKEN",
        "MIP_DEPLOYER_DATABRICKS_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    calls: list[dict[str, Any]] = []

    deployment_workspace_client(factory=_recording_factory(calls))

    assert calls == [{}]


def test_bundle_child_strips_app_facing_oauth_from_ambient_and_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_HOST=https://hostile-dotenv.example\n"
        "DATABRICKS_TOKEN=hostile-dotenv-pat\n"
        "DATABRICKS_AUTH_TYPE=oauth-m2m\n"
        "DATABRICKS_CONFIG_PROFILE=STALE\n"
        "DATABRICKS_CLIENT_ID=dotenv-app-client\n"
        "DATABRICKS_CLIENT_SECRET=dotenv-app-secret\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        "GENIE_SPACE_ID=genie-space-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_HOST", "https://reviewed-workspace.example")
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_TOKEN", "deployer-pat")
    monkeypatch.setenv("DATABRICKS_HOST", "https://reviewed-workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "deployer-pat")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "ambient-app-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "ambient-app-secret")
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "dev"])

    assert bundle_env.main() == 0
    assert captured["DATABRICKS_HOST"] == "https://reviewed-workspace.example"
    assert captured["DATABRICKS_TOKEN"] == "deployer-pat"
    assert captured["DATABRICKS_AUTH_TYPE"] == "pat"
    assert "DATABRICKS_CONFIG_PROFILE" not in captured
    assert "DATABRICKS_CLIENT_ID" not in captured
    assert "DATABRICKS_CLIENT_SECRET" not in captured


def test_bundle_profile_child_does_not_rehydrate_dotenv_pat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_HOST=https://hostile-dotenv.example\n"
        "DATABRICKS_TOKEN=stale-dotenv-pat\n"
        "DATABRICKS_AUTH_TYPE=pat\n"
        "DATABRICKS_CONFIG_PROFILE=STALE\n"
        "DATABRICKS_CLIENT_ID=dotenv-app-client\n"
        "DATABRICKS_CLIENT_SECRET=dotenv-app-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_PROFILE", "REVIEWED")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "REVIEWED")
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "dev"])

    assert bundle_env.main() == 0
    assert captured["DATABRICKS_CONFIG_PROFILE"] == "REVIEWED"
    assert "DATABRICKS_HOST" not in captured
    assert "DATABRICKS_TOKEN" not in captured
    assert "DATABRICKS_AUTH_TYPE" not in captured
    assert "DATABRICKS_CLIENT_ID" not in captured
    assert "DATABRICKS_CLIENT_SECRET" not in captured


def test_bundle_env_direct_script_entry_resolves_repo_imports(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "databricks" / "bundle_env.py")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "usage: bundle_env.py" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
