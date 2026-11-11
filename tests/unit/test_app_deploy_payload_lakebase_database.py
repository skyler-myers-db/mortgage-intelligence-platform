"""The App payload names the database the deploy binds when it is not the App's default (M3-PR4).

The App reads ``LAKEBASE_DATABASE`` (``settings.lakebase_database``, default
``mip_app_state``) before the ``PGDATABASE`` its database binding injects
(backend/services/lakebase.py), and the payload never named it: a second
client whose profile binds another database (``LAKEBASE_DATABASE``, the
bundle's ``lakebase_database_name``) got an App that opened the default
database on its instance. The second-client portability proof found it
(tests/unit/test_client_portability_proof.py).

scripts/deploy.sh now hands ``tools/databricks/app_deploy_payload.py`` the
database it binds (``--lakebase-database``), and the payload names it only
when it is not the default, so the default install's env-name set stays
exactly D0's (tests/unit/test_settings_d0_env_parity.py) and the emergency
recipe, which passes no flag, builds the same payload. An ambient or
``.env.local`` value is still never read (test_app_deploy_payload.py's
``..._does_not_overlay_lakebase_dsn_fragments``).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from backend.config import source_names
from backend.config.settings import Settings
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import app_deploy_payload
from tools.databricks.app_deploy_payload import DEFAULT_LAKEBASE_DATABASE, build_payload, main

REPO = Path(__file__).resolve().parents[2]
ALIASES = ("LAKEBASE_DATABASE", "MIP_LAKEBASE_DATABASE_NAME", "PGDATABASE")


@pytest.fixture(autouse=True)
def _hermetic_operator_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    env_local = tmp_path / ".env.local"
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    for name in (
        *app_deploy_payload.NON_SECRET_OPERATOR_VARS,
        *app_deploy_payload.SECRET_RESOURCE_BINDINGS,
        app_deploy_payload.PREVIOUS_SECRET_KID_ENV,
        "MIP_PRESENTER_MODE",
        *source_names.ENV_NAMES,
        *ALIASES,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "test-mask-secret")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", "test-action-secret")
    return env_local


def _env_vars(**kwargs: str) -> list[dict[str, str]]:
    payload = build_payload(source_code_path="/Workspace/app-src/release", install_class="customer", app_env="prod", **kwargs)
    return payload["env_vars"]  # type: ignore[return-value]


def _database_entries(**kwargs: str) -> list[dict[str, str]]:
    return [item for item in _env_vars(**kwargs) if item["name"] in ALIASES]


def test_the_default_is_the_apps_own_default() -> None:
    assert Settings.model_fields["lakebase_database"].default == DEFAULT_LAKEBASE_DATABASE


@pytest.mark.parametrize("given", [None, DEFAULT_LAKEBASE_DATABASE, f"  {DEFAULT_LAKEBASE_DATABASE} "], ids=["unset", "default", "padded"])
def test_the_default_database_adds_no_name(given: str | None) -> None:
    kwargs = {} if given is None else {"lakebase_database": given}

    assert _env_vars(**kwargs) == _env_vars()
    assert _database_entries(**kwargs) == []


def test_another_database_rides_once_beside_the_instance() -> None:
    baseline = _env_vars()

    env_vars = _env_vars(lakebase_database="client_two_state")

    assert [item for item in env_vars if item not in baseline] == [{"name": "LAKEBASE_DATABASE", "value": "client_two_state"}]
    assert [item for item in env_vars if item in baseline] == baseline
    names = [item["name"] for item in env_vars]
    assert names.index("LAKEBASE_DATABASE") == names.index("MIP_LAKEBASE_INSTANCE") + 1


@pytest.mark.parametrize("name", ALIASES)
def test_an_ambient_or_env_local_database_is_never_read(
    _hermetic_operator_config: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Only the database the deploy binds reaches the App, never a local DSN hint."""

    monkeypatch.setenv(name, "stale_local_hint")
    _hermetic_operator_config.write_text(f"{name}=another_local_hint\n", encoding="utf-8")

    assert _database_entries() == []
    assert _database_entries(lakebase_database="client_two_state") == [{"name": "LAKEBASE_DATABASE", "value": "client_two_state"}]


@pytest.mark.parametrize("given", ["", "   ", "<lakebase-database>", "Client_Two", "client-two"])
def test_a_malformed_database_refuses_the_payload(given: str) -> None:
    with pytest.raises(ValueError, match="--lakebase-database must be a lowercase unquoted identifier"):
        _env_vars(lakebase_database=given)


def test_the_cli_flag_reaches_the_payload() -> None:
    out = io.StringIO()
    argv = ["--source-code-path", "/Workspace/app-src/release", "--install-class", "customer", "--lakebase-database", "client_two_state"]
    with redirect_stdout(out):
        assert main(argv) == 0

    env_vars = json.loads(out.getvalue())["env_vars"]
    assert [item for item in env_vars if item["name"] in ALIASES] == [{"name": "LAKEBASE_DATABASE", "value": "client_two_state"}]


def test_the_governed_payload_passes_the_database_the_deploy_binds() -> None:
    script = deploy_entrypoint_text()
    start = script.index("emit_app_deploy_payload() {")
    function = script[start : script.index("\n}\n", start)]

    assert function.count('--lakebase-database "$LAKEBASE_DATABASE"') == 1
    # The preflight resolves the one name the bundle also binds (BUNDLE_VAR_lakebase_database_name).
    assert 'LAKEBASE_DATABASE="${_LAKEBASE_DATABASE:-${_MIP_LAKEBASE_DATABASE_NAME:-mip_app_state}}"' in script
    assert 'export BUNDLE_VAR_lakebase_database_name="$LAKEBASE_DATABASE"' in script


def test_the_app_booted_with_the_payload_opens_the_named_database(tmp_path: Path) -> None:
    """In a fresh process with exactly the payload's literal env: the App's setting is the bound database."""

    literal = {item["name"]: item["value"] for item in _env_vars(lakebase_database="client_two_state") if "value" in item}
    probe = "from backend.config.settings import settings; print(settings.lakebase_database)"
    completed = subprocess.run(
        [sys.executable, "-P", "-c", probe],
        cwd=tmp_path,  # no .env / .env.local here
        env={**literal, "PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.splitlines()[-1] == "client_two_state"
