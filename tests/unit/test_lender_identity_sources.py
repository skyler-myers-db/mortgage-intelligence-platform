"""Every lender reader takes the pinned record from the same sources, in one order.

Review of #298. The record (``MIP_LENDER_IDENTITY_JSON``) and its pin
(``MIP_LENDER_IDENTITY_SHA256``) travel in the shell or in ``.env.local``,
like every other deployment control, and a non-blank shell value wins. Two
records that each match their own pin must never split the deploy: the App
payload and the bundle variables (the SQL render and the Lakebase
migration) carry the same digest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.schemas.lender_identity_record import (
    LENDER_IDENTITY_JSON_ENV,
    LENDER_IDENTITY_SHA256_ENV,
    lender_identity_digest,
)
from tests.fixtures.client_profiles import LENDER_IDENTITY
from tools.databricks import app_deploy_payload, bundle_env

_LENDER_NAMES = ("MIP_LENDER_NAME", "MIP_LENDER_NMLS_ID", "MIP_TENANT_ID", LENDER_IDENTITY_JSON_ENV, LENDER_IDENTITY_SHA256_ENV)
SHELL_RECORD: dict[str, Any] = dict(LENDER_IDENTITY)
# Same name, NMLS id and tenant, one more alias: a different digest that the
# legacy-agreement check cannot tell apart.
DOTENV_RECORD: dict[str, Any] = {
    **LENDER_IDENTITY,
    "servicer_aliases": [*LENDER_IDENTITY["servicer_aliases"], "Other Alias Servicing"],
}


def _pinned(record: dict[str, Any]) -> dict[str, str]:
    text = json.dumps(record)
    return {LENDER_IDENTITY_JSON_ENV: text, LENDER_IDENTITY_SHA256_ENV: lender_identity_digest(text)}


def _env_local(path: Path, values: dict[str, str]) -> Path:
    # Single-quoted: python-dotenv keeps the JSON's double quotes verbatim.
    path.write_text("".join(f"{name}='{value}'\n" for name, value in values.items()), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        *app_deploy_payload.NON_SECRET_OPERATOR_VARS,
        *app_deploy_payload.SECRET_RESOURCE_BINDINGS,
        *_LENDER_NAMES,
        "MIP_PRESENTER_MODE",
        "DATABRICKS_BUNDLE_ROOT",
        "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS",
        "BUNDLE_VAR_lender_identity_json",
        "BUNDLE_VAR_lender_identity_sha256",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "test-mask-secret")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", "test-action-secret")


def _payload_digest(monkeypatch: pytest.MonkeyPatch, env_local: Path) -> str:
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    payload = app_deploy_payload.build_payload(
        source_code_path="/Workspace/app-src/release",
        target="dev",
        lakebase_instance="mip-app-state",
        campaign_treatment_runtime_enabled=True,
    )
    env = {item["name"]: item.get("value") for item in payload["env_vars"]}  # type: ignore[attr-defined]
    return lender_identity_digest(env[LENDER_IDENTITY_JSON_ENV])


def _bundle_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_local: Path) -> dict[str, str]:
    seen: dict[str, str] = {}
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))

    def fake_run(command: list[str], *, env: dict[str, str], check: bool) -> object:
        seen.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "prod"])
    assert bundle_env.main() == 0
    return seen


def test_a_shell_record_wins_over_a_dotenv_record_at_every_deploy_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shell, dotenv = _pinned(SHELL_RECORD), _pinned(DOTENV_RECORD)
    assert shell[LENDER_IDENTITY_SHA256_ENV] != dotenv[LENDER_IDENTITY_SHA256_ENV]
    env_local = _env_local(tmp_path / ".env.local", dotenv)
    for name, value in shell.items():
        monkeypatch.setenv(name, value)

    payload = _payload_digest(monkeypatch, env_local)
    variables = _bundle_vars(monkeypatch, tmp_path, env_local)

    assert payload == shell[LENDER_IDENTITY_SHA256_ENV]
    assert variables["BUNDLE_VAR_lender_identity_sha256"] == payload
    assert lender_identity_digest(variables["BUNDLE_VAR_lender_identity_json"]) == payload
    assert variables[LENDER_IDENTITY_SHA256_ENV] == payload


def test_a_dotenv_record_alone_reaches_every_deploy_reader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dotenv = _pinned(DOTENV_RECORD)
    env_local = _env_local(tmp_path / ".env.local", dotenv)

    payload = _payload_digest(monkeypatch, env_local)
    variables = _bundle_vars(monkeypatch, tmp_path, env_local)

    assert payload == variables["BUNDLE_VAR_lender_identity_sha256"] == dotenv[LENDER_IDENTITY_SHA256_ENV]


# --- Settings and the App runtime: the same record from .env.local ------------------


def _local_settings(monkeypatch: pytest.MonkeyPatch, cwd: Path, **init: Any) -> Any:
    from backend.config.settings import Settings

    monkeypatch.chdir(cwd)
    monkeypatch.setenv("MIP_DISABLE_DOTENV", "0")
    return Settings(**init)


def test_settings_load_a_record_carried_in_env_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dotenv = _pinned(DOTENV_RECORD)
    _env_local(tmp_path / ".env.local", dotenv)

    identity = _local_settings(monkeypatch, tmp_path).reviewed_lender_identity()

    assert identity.digest == dotenv[LENDER_IDENTITY_SHA256_ENV]
    assert identity.lender_name == DOTENV_RECORD["legal_name"]


def test_settings_take_the_shell_record_the_payload_takes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shell = _pinned(SHELL_RECORD)
    env_local = _env_local(tmp_path / ".env.local", _pinned(DOTENV_RECORD))
    for name, value in shell.items():
        monkeypatch.setenv(name, value)

    settings = _local_settings(monkeypatch, tmp_path)

    assert settings.reviewed_lender_identity().digest == _payload_digest(monkeypatch, env_local)


def test_settings_without_dotenv_ignore_a_dotenv_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_local(tmp_path / ".env.local", _pinned(DOTENV_RECORD))

    assert not _local_settings(monkeypatch, tmp_path, _env_file=None).reviewed_lender_identity().pinned
    monkeypatch.setenv("MIP_DISABLE_DOTENV", "1")
    from backend import runtime
    from backend.config.settings import Settings

    assert not Settings().reviewed_lender_identity().pinned
    runtime._preflight_lender_identity()  # nothing configured for either


def test_the_runtime_reads_the_dotenv_files_settings_load() -> None:
    from backend import runtime
    from backend.config.settings import Settings

    assert Settings.model_config["env_file"] == runtime.SETTINGS_DOTENV_FILES


def test_the_runtime_verifies_a_dotenv_record_before_settings_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend import runtime

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MIP_DISABLE_DOTENV", "0")
    dotenv = _pinned(DOTENV_RECORD)
    _env_local(tmp_path / ".env.local", dotenv)

    runtime._preflight_lender_identity()
    assert f"digest-pinned record {dotenv[LENDER_IDENTITY_SHA256_ENV][:12]} verified" in capsys.readouterr().err

    _env_local(tmp_path / ".env.local", {**dotenv, LENDER_IDENTITY_JSON_ENV: dotenv[LENDER_IDENTITY_JSON_ENV].replace("Harbor", "Harbour")})
    with pytest.raises(SystemExit) as exited:
        runtime._preflight_lender_identity()
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("[mip-runtime] refusing to start: lender identity: MIP_LENDER_IDENTITY_JSON")
    assert DOTENV_RECORD["legal_name"] not in err
