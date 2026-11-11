"""The deploy preflight resolves ``MIP_SALESFORCE_BINDINGS`` and never drops a configured connector (S2b).

``resolve_salesforce_bindings_switch`` (scripts/lib/deploy_identity_lifecycle.sh)
runs before any mutation. Unset is off, so a client without the connector
gains none of its three bindings. But an unset switch with every connector
input set would turn a configured connector off with only a notice, and on an
App already holding the three bindings a WR1 window would remove them: the
preflight refuses that, and the owner says on or off (review of #337). The
real library runs with the real interpreter; the ``-c`` check reads an absent
``.env.local`` so a developer's own file never changes the verdict. Every
value is a placeholder.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib" / "deploy_identity_lifecycle.sh"
# The ``-c`` check with app_deploy_payload's .env.local pointed at a missing file.
WRAPPER = r"""#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then
  echo "c-check" >> "$TMPDIR/calls"
  if [[ -n "${NO_PAYLOAD_MODULE:-}" ]]; then
    echo "ModuleNotFoundError: No module named 'tools.databricks.app_deploy_payload'" >&2
    exit 1
  fi
  CODE="$2" exec "$REAL_PYTHON" -c 'import os, pathlib
import tools.databricks.app_deploy_payload as payload
payload.ENV_LOCAL = pathlib.Path(os.environ["TMPDIR"]) / "absent.env"
exec(compile(os.environ["CODE"], "<deploy>", "exec"))'
fi
exec "$REAL_PYTHON" "$@"
"""
CONFIGURED = {
    "SALESFORCE_INSTANCE_URL": "https://connector.example.invalid",
    "SALESFORCE_CLIENT_ID": "connected-app-PLACEHOLDER",
    "SALESFORCE_USERNAME": "integration-PLACEHOLDER",
    "SALESFORCE_EXTERNAL_ID_FIELD": "MIP_Activation_Id__c",
    "SALESFORCE_CLIENT_SECRET": "client-secret-PLACEHOLDER",
    "SALESFORCE_PASSWORD": "password-PLACEHOLDER",
}


def _resolve(tmp_path: Path, *, dotenv: str = "", **env: str) -> subprocess.CompletedProcess[str]:
    wrapper = tmp_path / "python"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    wrapper.chmod(0o755)
    if dotenv:
        (tmp_path / ".env.local").write_text(dotenv, encoding="utf-8")
    script = (
        "set -euo pipefail\n"
        "RED=''; RST=''\n"
        f"PYTHON={shlex.quote(str(wrapper))}\n"
        f". {shlex.quote(str(LIB))}\n"
        "resolve_salesforce_bindings_switch\n"
        'echo "switch=$MIP_SALESFORCE_BINDINGS"\n'
        'bash -c \'echo "exported=${MIP_SALESFORCE_BINDINGS:-}"\'\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            **{key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}},
            "TMPDIR": str(tmp_path),
            "REAL_PYTHON": sys.executable,
            "PYTHONPATH": str(REPO),
            **env,
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


def test_an_unset_switch_with_every_connector_input_set_refuses_before_any_mutation(tmp_path: Path) -> None:
    result = _resolve(tmp_path, **CONFIGURED)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "MIP_SALESFORCE_BINDINGS is unset: set it to on" in result.stderr
    assert "nothing was changed" in result.stderr and "switch=" not in result.stdout
    assert not any(value in result.stderr for value in CONFIGURED.values()), "names only"


@pytest.mark.parametrize("switch", ("off", "on"))
def test_an_explicit_switch_is_the_owner_s_choice(tmp_path: Path, switch: str) -> None:
    result = _resolve(tmp_path, MIP_SALESFORCE_BINDINGS=switch, **CONFIGURED)

    assert result.returncode == 0, result.stderr
    assert f"switch={switch}" in result.stdout and f"exported={switch}" in result.stdout


def test_the_switch_from_env_local_counts_as_set(tmp_path: Path) -> None:
    result = _resolve(tmp_path, dotenv="MIP_SALESFORCE_BINDINGS=off\n", **CONFIGURED)

    assert result.returncode == 0, result.stderr
    assert "exported=off" in result.stdout


@pytest.mark.parametrize(
    "inputs",
    ({}, {"SALESFORCE_INSTANCE_URL": CONFIGURED["SALESFORCE_INSTANCE_URL"]}),
    ids=("no-connector", "partial-connector"),
)
def test_an_unset_switch_without_a_configured_connector_is_off(tmp_path: Path, inputs: dict[str, str]) -> None:
    result = _resolve(tmp_path, **inputs)

    assert result.returncode == 0, result.stderr
    assert "exported=off" in result.stdout


def test_no_connector_url_never_imports_the_payload_builder(tmp_path: Path) -> None:
    """Isolated fixture trees (the deploy dry-run sandboxes) carry no payload builder."""

    inputs = {name: value for name, value in CONFIGURED.items() if name != "SALESFORCE_INSTANCE_URL"}
    result = _resolve(tmp_path, NO_PAYLOAD_MODULE="1", **inputs)

    assert result.returncode == 0, result.stderr
    assert "exported=off" in result.stdout and not (tmp_path / "calls").exists()


def test_a_check_that_cannot_run_refuses(tmp_path: Path) -> None:
    result = _resolve(tmp_path, NO_PAYLOAD_MODULE="1", **CONFIGURED)

    assert result.returncode == 2 and "nothing was changed" in result.stderr
    assert (tmp_path / "calls").read_text(encoding="utf-8").split() == ["c-check"]


def test_an_unreviewed_switch_value_refuses(tmp_path: Path) -> None:
    result = _resolve(tmp_path, MIP_SALESFORCE_BINDINGS="yes")

    assert result.returncode == 2 and "must be off or on" in result.stderr
