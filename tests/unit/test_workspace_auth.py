from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
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


def test_bundle_child_maps_isolated_resource_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text("", encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "warehouse-id")
    monkeypatch.setenv("GENIE_SPACE_ID", "genie-space-id")
    monkeypatch.setenv("MIP_APP_NAME", "mip-pr105-staging")
    monkeypatch.setenv("LAKEBASE_INSTANCE_NAME", "mip-pr105-state")
    monkeypatch.setenv("MIP_LAKEBASE_INSTANCE", "mip-pr105-state")
    monkeypatch.setenv("LAKEBASE_DATABASE", "mip_pr105_state")
    monkeypatch.setenv("MIP_LAKEBASE_DATABASE_NAME", "mip_pr105_state")
    monkeypatch.setenv("MIP_LAKEBASE_SYNC_CATALOG", "mip_pr105_state")
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    captured: dict[str, str] = {}

    def fake_run(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "dev"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_app_name"] == "mip-pr105-staging"
    assert captured["BUNDLE_VAR_lakebase_instance_name"] == "mip-pr105-state"
    assert captured["BUNDLE_VAR_lakebase_catalog_name"] == "mip_pr105_state"
    assert captured["BUNDLE_VAR_lakebase_database_name"] == "mip_pr105_state"
    assert captured["MIP_LAKEBASE_INSTANCE"] == "mip-pr105-state"


def test_bundle_child_uses_dotenv_resource_names_when_shell_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "MIP_DEFAULT_CATALOG=mip_pr105_staging\n"
        "MIP_APP_NAME=mip-app-pr105-staging\n"
        "MIP_LAKEBASE_INSTANCE=mip-app-state-pr105-staging\n"
        "LAKEBASE_DATABASE=mip_app_state\n"
        "MIP_LAKEBASE_SYNC_CATALOG=mip_app_state_pr105_staging\n"
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
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "dev"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_uc_catalog"] == "mip_pr105_staging"
    assert captured["BUNDLE_VAR_app_name"] == "mip-app-pr105-staging"
    assert captured["BUNDLE_VAR_lakebase_instance_name"] == "mip-app-state-pr105-staging"
    assert captured["BUNDLE_VAR_lakebase_catalog_name"] == "mip_app_state_pr105_staging"


def test_bundle_child_maps_runtime_secret_scope_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "MIP_RUNTIME_SECRET_SCOPE=mip-runtime-pr105-staging\n"
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
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "dev"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_runtime_secret_scope"] == "mip-runtime-pr105-staging"


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
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "dev"])

    assert bundle_env.main() == 0
    assert captured["BUNDLE_VAR_runtime_secret_scope"] == "reviewed-runtime-scope"


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (
            {
                "LAKEBASE_INSTANCE_NAME": "mip-pr105-state",
                "MIP_LAKEBASE_INSTANCE": "mip-app-state",
            },
            "LAKEBASE_INSTANCE_NAME and MIP_LAKEBASE_INSTANCE must match",
        ),
        ({"MIP_APP_NAME": "MIP Unsafe"}, "MIP_APP_NAME must be a lowercase DNS-style name"),
        (
            {"MIP_LAKEBASE_SYNC_CATALOG": "mip-pr105-state"},
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


def test_governed_genie_resolution_pages_by_exact_title_and_round_trips() -> None:
    class Genie:
        def __init__(self) -> None:
            self.pages: list[str | None] = []

        def list_spaces(self, *, page_token: str | None) -> SimpleNamespace:
            self.pages.append(page_token)
            if page_token is None:
                return SimpleNamespace(
                    spaces=[SimpleNamespace(title="Other Space", space_id="other-id")],
                    next_page_token="next",
                )
            return SimpleNamespace(
                spaces=[
                    SimpleNamespace(
                        title="Mortgage Lead Intelligence PR105 Staging",
                        space_id="governed-space-id",
                    )
                ],
                next_page_token=None,
            )

        def get_space(self, space_id: str) -> SimpleNamespace:
            assert space_id == "governed-space-id"
            return SimpleNamespace(title="Mortgage Lead Intelligence PR105 Staging")

    genie = Genie()
    resolved = bundle_env._resolve_governed_genie_space_id(
        {},
        space_name="Mortgage Lead Intelligence PR105 Staging",
        client=SimpleNamespace(genie=genie),
    )

    assert resolved == "governed-space-id"
    assert genie.pages == [None, "next"]


def test_direct_bundle_deploy_overwrites_stale_genie_id_with_governed_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        "GENIE_SPACE_ID=stale-cross-workspace-id\n"
        "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID=verifier-client-id\n"
        "MIP_GENIE_SPACE_NAME=Mortgage Lead Intelligence PR105 Staging\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    for name in ("DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID", "MIP_GENIE_SPACE_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        bundle_env,
        "_resolve_governed_genie_space_id",
        lambda _env, *, space_name: (
            "governed-space-id"
            if space_name == "Mortgage Lead Intelligence PR105 Staging"
            else "wrong"
        ),
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
        ["bundle_env.py", "deploy", "-t", "dev", "--select", "jobs.refresh_gold"],
    )

    assert bundle_env.main() == 0
    assert captured["GENIE_SPACE_ID"] == "governed-space-id"
    assert captured["BUNDLE_VAR_genie_space_id"] == "governed-space-id"
    assert captured["BUNDLE_VAR_ai_gateway_verifier_client_id"] == "verifier-client-id"
    assert "stale-cross-workspace-id" not in captured.values()


def test_bundle_deployment_bind_uses_the_same_reviewed_workspace_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        "GENIE_SPACE_ID=genie-space-id\n"
        "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID=verifier-client-id\n"
        "MIP_APP_NAME=mip-app\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(
        bundle_env,
        "_resolve_governed_genie_space_id",
        lambda _env, *, space_name: "genie-space-id",
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
            "dev",
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
        "dev",
        "--auto-approve",
    ]
    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert child_env["BUNDLE_VAR_app_name"] == "mip-app"
    assert child_env["BUNDLE_VAR_genie_space_id"] == "genie-space-id"
    assert child_env["BUNDLE_VAR_ai_gateway_verifier_client_id"] == "verifier-client-id"
    assert captured["check"] is False


def test_direct_bundle_deploy_refuses_missing_remote_verifier_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n" "GENIE_SPACE_ID=genie-space-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.delenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", raising=False)
    monkeypatch.setattr(
        bundle_env,
        "_resolve_governed_genie_space_id",
        lambda _env, *, space_name: "genie-space-id",
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
        ["bundle_env.py", "deploy", "-t", "dev", "--select", "jobs.refresh_gold"],
    )

    assert bundle_env.main() == 2
    assert subprocess_called is False


def test_bundle_deploy_preserves_reviewed_genie_name_over_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        "GENIE_SPACE_ID=stale-id\n"
        "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID=verifier-client-id\n"
        "MIP_GENIE_SPACE_NAME=Stale Space Name\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setenv("MIP_GENIE_SPACE_NAME", "Reviewed Space Name")
    observed: list[str] = []

    def resolve(_env: dict[str, str], *, space_name: str) -> str:
        observed.append(space_name)
        return "reviewed-space-id"

    monkeypatch.setattr(bundle_env, "_resolve_governed_genie_space_id", resolve)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    monkeypatch.setattr(
        bundle_env.subprocess,
        "run",
        lambda _command, *, env, check: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        bundle_env.sys,
        "argv",
        ["bundle_env.py", "deploy", "-t", "dev", "--select", "jobs.refresh_gold"],
    )

    assert bundle_env.main() == 0
    assert observed == ["Reviewed Space Name"]


@pytest.mark.parametrize(
    "args",
    (
        ["-t", "dev"],
        ["-t", "dev", "--select", "apps.mip_app"],
        ["-t", "dev", "--select", "*"],
        ["-t", "dev", "--select=apps.mip_app"],
        ["-t", "dev", "--select", "jobs.refresh_gold", "--plan", "full-plan.json"],
        ["-t", "dev", "--plan=full-plan.json", "--select=jobs.refresh_gold"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--var=x=y"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--var", "x=y"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--profile", "other-workspace"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--profile=other-workspace"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--cluster-id", "cluster-1"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--cluster-id=cluster-1"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--force"],
        ["-t", "dev", "--select=jobs.refresh_gold", "--target", "prod"],
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
                "dev",
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
            ["--target=prod", "--select=jobs.refresh_gold"]
        )
        is None
    )


@pytest.mark.parametrize(
    "args",
    (
        ["migrate", "mip_app", "-t", "dev"],
        ["unbind", "jobs.refresh_gold", "-t", "dev", "--force-lock"],
        ["unbind", "mip_app", "-t", "dev", "--auto-approve"],
        ["bind", "mip_app", "another-app", "-t", "dev", "--auto-approve"],
        ["bind", "mip_app", "mip-app", "-t", "dev", "--var=x=y"],
        ["bind", "mip_app", "mip-app", "-t", "dev", "-t", "prod"],
        ["bind", "mip_app", "mip-app", "--auto-approve"],
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
        ["bind", "mip_app", "mip-app", "-t", "dev", "--auto-approve"],
        ["unbind", "mip_app", "--target=prod", "--force-lock"],
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
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", tmp_path / "missing.env")
    monkeypatch.setenv("MIP_APP_NAME", "mip-app")
    monkeypatch.setattr(
        bundle_env,
        "_resolve_governed_genie_space_id",
        lambda *_args, **_kwargs: pytest.fail("unsafe deployment reached remote resolution"),
    )
    monkeypatch.setattr(
        bundle_env.sys,
        "argv",
        ["bundle_env.py", "deployment", "unbind", "jobs.refresh_gold", "-t", "dev"],
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
