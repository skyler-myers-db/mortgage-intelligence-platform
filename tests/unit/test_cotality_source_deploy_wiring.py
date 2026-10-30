"""The deploy hands every reader the same Cotality source names.

scripts/deploy.sh resolves ``MIP_COTALITY_SOURCE_*`` once and exports them;
tools/databricks/bundle_env.py turns them into the ``cotality_source_*`` bundle
variables and renders the SQL with them. Unset, every reader gets the
canonical names.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.config import source_names
from backend.config.source_names import CANONICAL, CotalitySource
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import bundle_env

ACME_ENV = {
    "MIP_COTALITY_SOURCE_CATALOG": "acme_src",
    "MIP_COTALITY_SOURCE_SCHEMA": "feed",
    "MIP_COTALITY_SOURCE_TABLE_PREFIX": "acme_",
}
ACME = CotalitySource(catalog="acme_src", schema="feed", table_prefix="acme_")


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Run ``bundle_env validate -t dev``; capture the CLI env and the render call."""

    for name in source_names.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.delenv("DATABRICKS_BUNDLE_ROOT", raising=False)
    env_local = tmp_path / ".env.local"
    env_local.write_text("", encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    captured: dict[str, Any] = {"renders": []}

    def render(**kwargs: Any) -> tuple[int, int, int]:
        captured["renders"].append(kwargs)
        return (1, 0, 0)

    def cli(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        captured["env"] = env
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.render_sql, "render", render)
    monkeypatch.setattr(bundle_env.subprocess, "run", cli)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "prod"])
    captured["env_local"] = env_local
    return captured


def _variables(env: dict[str, str]) -> tuple[str, str, str]:
    return (
        env["BUNDLE_VAR_cotality_source_catalog"],
        env["BUNDLE_VAR_cotality_source_schema"],
        env["BUNDLE_VAR_cotality_source_table_prefix"],
    )


def test_bundle_env_exports_the_canonical_variables_when_unset(child: dict[str, Any]) -> None:
    assert bundle_env.main() == 0

    assert _variables(child["env"]) == (CANONICAL.catalog, CANONICAL.schema, CANONICAL.table_prefix)
    assert [call["cotality"] for call in child["renders"]] == [CANONICAL]


def test_bundle_env_renders_and_exports_one_configured_source(
    child: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in ACME_ENV.items():
        monkeypatch.setenv(name, value)

    assert bundle_env.main() == 0

    assert _variables(child["env"]) == ("acme_src", "feed", "acme_")
    assert [call["cotality"] for call in child["renders"]] == [ACME]


def test_a_shell_source_name_beats_dotenv(child: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    child["env_local"].write_text(
        "MIP_COTALITY_SOURCE_CATALOG=dotenv_src\nMIP_COTALITY_SOURCE_SCHEMA=dotenv_schema\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MIP_COTALITY_SOURCE_CATALOG", "acme_src")

    assert bundle_env.main() == 0

    assert _variables(child["env"])[:2] == ("acme_src", "dotenv_schema")


def test_bundle_env_refuses_an_invalid_source_before_the_cli(
    child: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIP_COTALITY_SOURCE_SCHEMA", "Feed")

    assert bundle_env.main() == 2

    assert "Cotality source schema must be a lowercase unquoted identifier" in capsys.readouterr().err
    assert "env" not in child and child["renders"] == []


# --- scripts/deploy.sh ---------------------------------------------------------------


def test_deploy_resolves_validates_and_exports_the_source_before_any_render() -> None:
    text = deploy_entrypoint_text()
    block = text[text.index("# >>> cotality source names >>>") : text.index("# <<< cotality source names <<<")]

    for name in source_names.ENV_NAMES:
        assert f'{name}="$(deployment_control_value {name})"' in block
    assert "export MIP_COTALITY_SOURCE_CATALOG MIP_COTALITY_SOURCE_SCHEMA MIP_COTALITY_SOURCE_TABLE_PREFIX" in block
    assert "^[a-z_][a-z0-9_]{0,254}$" in block and "^[a-z0-9_]{1,64}$" in block
    assert "exit 2" in block
    # Resolved before the first SQL render and before any bundle call.
    assert text.index("# >>> cotality source names >>>") < text.index('run "$PYTHON" tools/render_sql.py')
    assert text.index("# >>> cotality source names >>>") < text.index("-m tools.databricks.bundle_env validate")


def _preflight_block() -> str:
    text = deploy_entrypoint_text()
    return text[text.index("# >>> cotality source names >>>") : text.index("# <<< cotality source names <<<")]


def _bashes() -> list[str]:
    found: list[str] = []
    for candidate in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash"), "/bin/bash"):
        if candidate and Path(candidate).exists() and candidate not in found:
            found.append(candidate)
    return found


def _run_block(bash: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Execute the preflight block, with ``deployment_control_value`` reading the shell only."""

    harness = (
        "set -euo pipefail\n"
        "RED=''; RST=''\n"
        'deployment_control_value() { local value="${!1:-}"; printf \'%s\' "${value:-${2:-}}"; }\n'
        f"{_preflight_block()}"
        "env | grep '^MIP_COTALITY_SOURCE_' | sort\n"
    )
    base = {key: value for key, value in os.environ.items() if not key.startswith("MIP_")}
    return subprocess.run(
        [bash, "-c", harness], env={**base, **env}, capture_output=True, text=True, timeout=60, check=False
    )


@pytest.mark.parametrize("bash", _bashes())
@pytest.mark.parametrize("env_name", source_names.ENV_NAMES)
@pytest.mark.parametrize(
    "value", ["<cotality-source-catalog>", "00000000PLACEHOLDER", "Acme", "acme.src", "Acme;x", "acme src"]
)
def test_the_preflight_block_exits_2_on_a_template_or_non_identifier(
    bash: str, env_name: str, value: str
) -> None:
    """Executed, not grepped: each name alone, so a weakened join cannot pass."""

    proc = _run_block(bash, {env_name: value})

    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "must be lowercase unquoted identifiers" in proc.stderr
    assert value not in proc.stderr and proc.stdout == ""


@pytest.mark.parametrize("bash", _bashes())
def test_the_preflight_block_exports_set_names_and_leaves_unset_ones_empty(bash: str) -> None:
    unset = _run_block(bash, {})
    configured = _run_block(bash, ACME_ENV)

    assert unset.returncode == 0, unset.stderr
    assert unset.stdout.splitlines() == [f"{name}=" for name in source_names.ENV_NAMES]
    assert configured.returncode == 0, configured.stderr
    assert configured.stdout.splitlines() == [f"{name}={value}" for name, value in ACME_ENV.items()]
