from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.client_profiles import (
    DEMO_PROFILE,
    IDENTITY_IDS,
    env_file_text,
    paychex_shaped_profile,
)
from tools.clients.environment import KNOWN_NAMES
from tools.clients.schema import FIELD_CLASS, FieldClass
from tools.databricks import bundle_env
from tools.databricks.workspace_auth import deployment_workspace_client

REPO = Path(__file__).resolve().parents[2]
PROFILE = DEMO_PROFILE
# Every identifying value of the profile, and each application id inside its JSON.
_IDENTIFYING = sorted(
    {
        *(value for name, value in PROFILE.items() if FIELD_CLASS[name] is FieldClass.IDENTIFYING),
        *IDENTITY_IDS.values(),
    }
)


@pytest.fixture(autouse=True)
def _bundle_overrides_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """bundle_env.main() writes the bundle's variable-overrides.json: keep it out of the checkout.

    No profile field leaks in from the developer's shell either: -t client
    resolves the profile from the environment and the test's .env.local.
    """

    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.delenv("DATABRICKS_BUNDLE_ROOT", raising=False)
    for name in KNOWN_NAMES:
        monkeypatch.delenv(name, raising=False)


def _client_env_local(tmp_path: Path, **values: str | None) -> Path:
    """An .env.local holding the paychex-shaped client profile plus ``values``."""

    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile(**values)), encoding="utf-8")
    return env_local


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
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "ci"])

    assert bundle_env.main() == 0
    assert captured["DATABRICKS_HOST"] == "https://reviewed-workspace.example"
    assert captured["DATABRICKS_TOKEN"] == "deployer-pat"
    assert captured["DATABRICKS_AUTH_TYPE"] == "pat"
    assert "DATABRICKS_CONFIG_PROFILE" not in captured
    assert "DATABRICKS_CLIENT_ID" not in captured
    assert "DATABRICKS_CLIENT_SECRET" not in captured


def test_bundle_child_maps_isolated_resource_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text("", encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "warehouse-id")
    monkeypatch.setenv("GENIE_SPACE_ID", "genie-space-id")
    monkeypatch.setenv("MIP_APP_NAME", "mip-staging")
    monkeypatch.setenv("LAKEBASE_INSTANCE_NAME", "mip-staging-state")
    monkeypatch.setenv("MIP_LAKEBASE_INSTANCE", "mip-staging-state")
    monkeypatch.setenv("LAKEBASE_DATABASE", "mip_staging_state")
    monkeypatch.setenv("MIP_LAKEBASE_DATABASE_NAME", "mip_staging_state")
    monkeypatch.setenv("MIP_LAKEBASE_SYNC_CATALOG", "mip_staging_state")
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "ci"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_app_name"] == "mip-staging"
    assert captured["BUNDLE_VAR_lakebase_instance_name"] == "mip-staging-state"
    assert captured["BUNDLE_VAR_lakebase_catalog_name"] == "mip_staging_state"
    assert captured["BUNDLE_VAR_lakebase_database_name"] == "mip_staging_state"
    assert captured["MIP_LAKEBASE_INSTANCE"] == "mip-staging-state"


def test_bundle_child_uses_dotenv_resource_names_when_shell_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "MIP_DEFAULT_CATALOG=mip_staging\n"
        "MIP_APP_NAME=mip-app-staging\n"
        "MIP_LAKEBASE_INSTANCE=mip-app-state-staging\n"
        "LAKEBASE_DATABASE=mip_app_state\n"
        "MIP_LAKEBASE_SYNC_CATALOG=mip_app_state_staging\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        "GENIE_SPACE_ID=genie-space-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    for name in (
        "MIP_DEFAULT_CATALOG",
        "MIP_APP_NAME",
        "LAKEBASE_INSTANCE_NAME",
        "MIP_LAKEBASE_INSTANCE",
        "LAKEBASE_DATABASE",
        "MIP_LAKEBASE_DATABASE_NAME",
        "MIP_LAKEBASE_SYNC_CATALOG",
        "DATABRICKS_WAREHOUSE_ID",
        "GENIE_SPACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "ci"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_uc_catalog"] == "mip_staging"
    assert captured["BUNDLE_VAR_app_name"] == "mip-app-staging"
    assert captured["BUNDLE_VAR_lakebase_instance_name"] == "mip-app-state-staging"
    assert captured["BUNDLE_VAR_lakebase_catalog_name"] == "mip_app_state_staging"


def test_bundle_child_maps_runtime_secret_scope_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "MIP_RUNTIME_SECRET_SCOPE=mip-runtime-staging\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        "GENIE_SPACE_ID=genie-space-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.delenv("MIP_RUNTIME_SECRET_SCOPE", raising=False)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "ci"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_runtime_secret_scope"] == "mip-runtime-staging"


def test_bundle_child_preserves_exported_runtime_scope_over_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "MIP_RUNTIME_SECRET_SCOPE=stale-runtime-scope\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        "GENIE_SPACE_ID=genie-space-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setenv("MIP_RUNTIME_SECRET_SCOPE", "reviewed-runtime-scope")
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "ci"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_runtime_secret_scope"] == "reviewed-runtime-scope"


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (
            {
                "LAKEBASE_INSTANCE_NAME": "mip-staging-state",
                "MIP_LAKEBASE_INSTANCE": "mip-app-state",
            },
            "LAKEBASE_INSTANCE_NAME and MIP_LAKEBASE_INSTANCE must match",
        ),
        ({"MIP_APP_NAME": "MIP Unsafe"}, "MIP_APP_NAME must be a lowercase DNS-style name"),
        (
            {"MIP_LAKEBASE_SYNC_CATALOG": "mip-staging-state"},
            "MIP_LAKEBASE_SYNC_CATALOG must be a lowercase unquoted identifier",
        ),
        (
            {"MIP_LAKEBASE_SYNC_CATALOG": "MIP_STATE"},
            "MIP_LAKEBASE_SYNC_CATALOG must be a lowercase unquoted identifier",
        ),
    ],
)
def test_bundle_resource_names_fail_closed_on_drift_or_unsafe_values(
    names: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        bundle_env._deployment_resource_names(names)


def test_bundle_env_resolves_the_governed_space_by_its_pin_with_the_profiles_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1-PR3: the pin record or the signed rollback record, never a title (attack G12)."""

    from tools.clients.environment import RawProfile, resolve
    from tools.databricks import genie_space_pin

    seen: dict[str, object] = {}

    def pinned(workspace: object, **kwargs: object) -> str:
        seen.update(kwargs, workspace=workspace)
        return "governed-space-id"

    monkeypatch.setattr(genie_space_pin, "pinned_space_id", pinned)
    monkeypatch.setattr(bundle_env, "_genie_workspace_client", lambda env: "deployer-client")
    profile = resolve(RawProfile(values=paychex_shaped_profile()))

    assert bundle_env._pinned_genie_space_id({}, profile) == "governed-space-id"
    assert seen == {
        "workspace": "deployer-client",
        "app_name": profile.app_name,
        "scope": profile.app_rollback_secret_scope,
        "lakebase_instance": profile.lakebase_instance,
    }


def test_direct_bundle_deploy_binds_the_pinned_space_never_an_ambient_genie_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = _client_env_local(tmp_path, MIP_GENIE_SPACE_NAME="Mortgage Lead Intelligence Staging")
    with env_local.open("a", encoding="utf-8") as extra:
        extra.write("DATABRICKS_WAREHOUSE_ID=warehouse-id\nGENIE_SPACE_ID=stale-cross-workspace-id\n")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    for name in ("DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID", "MIP_GENIE_SPACE_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        bundle_env,
        "_pinned_genie_space_id",
        lambda _env, profile: "governed-space-id" if profile.app_name == "mip-app" else "wrong",
    )
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(
        bundle_env.sys,
        "argv",
        ["bundle_env.py", "deploy", "-t", "client", "--select", "jobs.refresh_gold"],
    )

    assert bundle_env.main() == 0
    assert "GENIE_SPACE_ID" not in captured, "GENIE_SPACE_ID is no input and never reaches the CLI"
    assert captured["BUNDLE_VAR_genie_space_id"] == "governed-space-id"
    # -t client: the verifier is the profile's MIP_IDENTITY_IDS_JSON.verifier.
    assert captured["BUNDLE_VAR_ai_gateway_verifier_client_id"] == IDENTITY_IDS["verifier"]
    assert "stale-cross-workspace-id" not in captured.values()


def test_bundle_deployment_bind_uses_the_same_reviewed_workspace_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = _client_env_local(tmp_path)
    with env_local.open("a", encoding="utf-8") as extra:
        extra.write("DATABRICKS_WAREHOUSE_ID=warehouse-id\nGENIE_SPACE_ID=genie-space-id\n")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(
        bundle_env,
        "_pinned_genie_space_id",
        lambda _env, _profile: "genie-space-id",
    )
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, object] = {}

    def fake_run(command: list[str], *, env: dict[str, str], check: bool) -> Any:
        captured["command"] = command
        captured["env"] = env
        captured["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(
        bundle_env.sys,
        "argv",
        [
            "bundle_env.py",
            "deployment",
            "bind",
            "mip_app",
            "mip-app",
            "-t",
            "client",
            "--auto-approve",
        ],
    )

    assert bundle_env.main() == 0
    assert captured["command"] == [
        "databricks",
        "bundle",
        "deployment",
        "bind",
        "mip_app",
        "mip-app",
        "-t",
        "client",
        "--auto-approve",
    ]
    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert child_env["BUNDLE_VAR_app_name"] == "mip-app"
    assert child_env["BUNDLE_VAR_genie_space_id"] == "genie-space-id"
    assert child_env["BUNDLE_VAR_ai_gateway_verifier_client_id"] == IDENTITY_IDS["verifier"]
    assert child_env["BUNDLE_VAR_client_key"] == "client-one"
    assert child_env["BUNDLE_VAR_deployer_principal"] == IDENTITY_IDS["deployer"]
    assert captured["check"] is False


def test_direct_bundle_deploy_refuses_missing_remote_verifier_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A core-tier profile has no verifier, so the deploy still has none to bind.
    core_ids = {role: value for role, value in IDENTITY_IDS.items() if role not in {"verifier", "runtime", "proxy"}}
    env_local = _client_env_local(
        tmp_path, MIP_AGENTIC_TIER="off", MIP_IDENTITY_IDS_JSON=json.dumps(core_ids)
    )
    with env_local.open("a", encoding="utf-8") as extra:
        extra.write("DATABRICKS_WAREHOUSE_ID=warehouse-id\nGENIE_SPACE_ID=genie-space-id\n")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(
        bundle_env,
        "_pinned_genie_space_id",
        lambda _env, _profile: "genie-space-id",
    )
    subprocess_called = False

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        nonlocal subprocess_called
        subprocess_called = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(
        bundle_env.sys,
        "argv",
        ["bundle_env.py", "deploy", "-t", "client", "--select", "jobs.refresh_gold"],
    )

    assert bundle_env.main() == 2
    assert subprocess_called is False


def test_bundle_deploy_preserves_reviewed_genie_name_over_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = _client_env_local(tmp_path, MIP_GENIE_SPACE_NAME="Stale Space Name")
    with env_local.open("a", encoding="utf-8") as extra:
        extra.write("DATABRICKS_WAREHOUSE_ID=warehouse-id\nGENIE_SPACE_ID=stale-id\n")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    # The profile takes a non-empty shell value over .env.local's.
    monkeypatch.setenv("MIP_GENIE_SPACE_NAME", "Reviewed Space Name")
    observed: list[str] = []

    def resolve(_env: dict[str, str], profile: Any) -> str:
        observed.append(profile.genie_space_name)
        return "reviewed-space-id"

    monkeypatch.setattr(bundle_env, "_pinned_genie_space_id", resolve)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    monkeypatch.setattr(
        bundle_env.subprocess,
        "run",
        lambda _command, *, env, check: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        bundle_env.sys,
        "argv",
        ["bundle_env.py", "deploy", "-t", "client", "--select", "jobs.refresh_gold"],
    )

    assert bundle_env.main() == 0
    assert observed == ["Reviewed Space Name"]


@pytest.mark.parametrize(
    "args",
    (
        ["-t", "client"],
        ["-t", "client", "--select", "apps.mip_app"],
        ["-t", "client", "--select", "*"],
        ["-t", "client", "--select=apps.mip_app"],
        ["-t", "client", "--select", "jobs.refresh_gold", "--plan", "full-plan.json"],
        ["-t", "client", "--plan=full-plan.json", "--select=jobs.refresh_gold"],
        ["-t", "client", "--select=jobs.refresh_gold", "--var=x=y"],
        ["-t", "client", "--select=jobs.refresh_gold", "--var", "x=y"],
        ["-t", "client", "--select=jobs.refresh_gold", "--profile", "other-workspace"],
        ["-t", "client", "--select=jobs.refresh_gold", "--profile=other-workspace"],
        ["-t", "client", "--select=jobs.refresh_gold", "--cluster-id", "cluster-1"],
        ["-t", "client", "--select=jobs.refresh_gold", "--cluster-id=cluster-1"],
        ["-t", "client", "--select=jobs.refresh_gold", "--force"],
        ["-t", "client", "--select=jobs.refresh_gold", "--target", "client"],
        # The retired targets and the validate-only ci target never deploy.
        ["-t", "dev", "--select=jobs.refresh_gold"],
        ["-t", "prod", "--select=jobs.refresh_gold"],
        ["-t", "ci", "--select=jobs.refresh_gold"],
        ["--select=jobs.refresh_gold"],
    ),
)
def test_bundle_env_rejects_unrestricted_or_app_deploy_selectors(args: list[str]) -> None:
    error = bundle_env._validate_non_app_deploy_selectors(args)

    assert error is not None
    assert error


def test_bundle_env_accepts_only_exact_non_app_deploy_selectors() -> None:
    assert (
        bundle_env._validate_non_app_deploy_selectors(
            [
                "-t",
                "client",
                "--select",
                "jobs.refresh_gold",
                "--select=pipelines.mip_silver",
            ]
        )
        is None
    )


def test_bundle_env_accepts_equals_target_with_exact_non_app_selector() -> None:
    assert (
        bundle_env._validate_non_app_deploy_selectors(
            ["--target=client", "--select=jobs.refresh_gold"]
        )
        is None
    )


@pytest.mark.parametrize(
    ("argv", "message"),
    (
        (["validate", "-t", "dev"], "bundle validate target must be exactly ci or client"),
        (["validate", "-t", "prod"], "bundle validate target must be exactly ci or client"),
        (["validate"], "bundle validate target must be exactly ci or client"),
        (["plan", "-t", "ci"], "bundle plan target must be exactly client"),
        (["summary", "-t", "dev", "-o", "json"], "bundle summary target must be exactly client"),
        (["deployment", "bind", "mip_app", "mip-app", "-t", "ci"], "bundle deployment target must be exactly client"),
    ),
)
def test_bundle_env_refuses_every_target_but_client_and_a_ci_validate(
    argv: list[str], message: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M1-PR2: the dev and prod targets left databricks.yml; ci only validates."""

    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: pytest.fail("rendered"))
    monkeypatch.setattr(bundle_env.subprocess, "run", lambda *_a, **_k: pytest.fail("reached the CLI"))
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", *argv])

    assert bundle_env.main() == 2
    assert message in capsys.readouterr().err


def test_bundle_env_client_target_refuses_a_refused_profile_before_any_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", _client_env_local(tmp_path, MIP_EXPECT_METASTORE_ID=None))
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: pytest.fail("rendered"))
    monkeypatch.setattr(bundle_env.subprocess, "run", lambda *_a, **_k: pytest.fail("reached the CLI"))
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])

    assert bundle_env.main() == 2
    err = capsys.readouterr().err
    assert "client profile refused" in err and "MIP_EXPECT_METASTORE_ID" in err
    assert not any(value in err for value in _IDENTIFYING), "refusals name fields, never values"


@pytest.mark.parametrize(
    "args",
    (
        ["migrate", "mip_app", "-t", "client"],
        ["unbind", "jobs.refresh_gold", "-t", "client", "--force-lock"],
        ["unbind", "mip_app", "-t", "client", "--auto-approve"],
        ["bind", "mip_app", "another-app", "-t", "client", "--auto-approve"],
        ["bind", "mip_app", "mip-app", "-t", "client", "--var=x=y"],
        ["bind", "mip_app", "mip-app", "-t", "client", "-t", "client"],
        ["bind", "mip_app", "mip-app", "--auto-approve"],
        ["bind", "mip_app", "mip-app", "-t", "dev", "--auto-approve"],
        ["bind", "mip_app", "mip-app", "-t", "prod", "--auto-approve"],
        ["bind", "mip_app", "mip-app", "-t", "ci", "--auto-approve"],
        ["bind", "mip_app", "mip-app", "--target=unknown", "--auto-approve"],
        ["unbind", "mip_app", "--force-lock"],
    ),
)
def test_bundle_env_rejects_unrestricted_deployment_state_mutation(
    args: list[str],
) -> None:
    error = bundle_env._validate_app_deployment_command(
        args,
        expected_app_name="mip-app",
    )

    assert error is not None


@pytest.mark.parametrize(
    "args",
    (
        ["bind", "mip_app", "mip-app", "-t", "client", "--auto-approve"],
        ["unbind", "mip_app", "--target=client", "--force-lock"],
    ),
)
def test_bundle_env_accepts_only_the_governed_app_binding(args: list[str]) -> None:
    assert (
        bundle_env._validate_app_deployment_command(
            args,
            expected_app_name="mip-app",
        )
        is None
    )


def test_bundle_env_rejects_unsafe_deployment_before_remote_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A valid profile, so the refusal is the deployment shape's, not the profile's.
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", _client_env_local(tmp_path))
    monkeypatch.setattr(
        bundle_env,
        "_pinned_genie_space_id",
        lambda *_args, **_kwargs: pytest.fail("unsafe deployment reached remote resolution"),
    )
    monkeypatch.setattr(
        bundle_env.sys,
        "argv",
        ["bundle_env.py", "deployment", "unbind", "jobs.refresh_gold", "-t", "client"],
    )

    assert bundle_env.main() == 2


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
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "ci"])

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
