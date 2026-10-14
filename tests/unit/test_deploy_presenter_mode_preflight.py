"""NB-3: presenter mode and the refusal-capture switch are refused at step 0.

``MIP_PRESENTER_MODE`` is demo-only (D-shell-deviations-e1). The App payload
already refused a truthy value for ``-t prod``, but only at App promotion,
after the bundle, UC and Lakebase steps had mutated the workspace. The deploy
preflight now parses it, and ``MIP_GENIE_REFUSAL_TEXT_CAPTURE``
(D-audit-reads-d), before any workspace mutation, and exits 2.

The block is read from ``deploy_entrypoint_text()`` (the reviewed
command-of-record with its step files expanded), never the raw file, and is
EXECUTED under the newest bash on PATH (Homebrew bash first, when present)
and under ``/bin/bash`` (3.2 on macOS) so the portable lower-casing holds.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import app_deploy_payload

BLOCK_START = "# Demo-only presenter mode (D-shell-deviations-e1; W5b NB-3)"
BLOCK_END = "    exit 2\n    ;;\nesac\n"
REFUSAL_CAPTURE_LINE = '_MIP_GENIE_REFUSAL_TEXT_CAPTURE="$(deployment_control_value'


def _block() -> str:
    text = deploy_entrypoint_text()
    start = text.index(BLOCK_START)
    capture = text.index(REFUSAL_CAPTURE_LINE, start)
    end = text.index(BLOCK_END, capture) + len(BLOCK_END)
    return text[start:end]


def _bashes() -> list[str]:
    found: list[str] = []
    for candidate in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash"), "/bin/bash"):
        if candidate and Path(candidate).exists() and candidate not in found:
            found.append(candidate)
    return found


BASHES = _bashes()


def _run(bash: str, target: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    harness = (
        "set -euo pipefail\n"
        "RED=''; RST=''\n"
        "deployment_control_value() { local value=\"${!1:-}\"; printf '%s' \"${value:-${2:-}}\"; }\n"
        f"TARGET={target}\n"
        f"{_block()}"
        'echo "presenter=[${_MIP_PRESENTER_MODE}] capture=[${_MIP_GENIE_REFUSAL_TEXT_CAPTURE}]"\n'
    )
    base = {key: value for key, value in os.environ.items() if not key.startswith("MIP_")}
    return subprocess.run(
        [bash, "-c", harness],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_block_runs_in_preflight_before_any_bundle_uc_or_lakebase_mutation() -> None:
    text = deploy_entrypoint_text()
    block_at = text.index(BLOCK_START)
    for mutation in (
        "# Step 0a: prove signed-blue state before any workspace mutation",
        'step "resolve governed Genie space before App secret and bundle mutation"',
        "# Step 1a: render SQL for the target UC catalog",
        "bundle run mip_lakebase_migrate",
    ):
        assert block_at < text.index(mutation), mutation
    assert text.count(BLOCK_START) == 1
    assert "${_MIP_PRESENTER_MODE,,}" not in _block()


@pytest.mark.parametrize("bash", BASHES)
@pytest.mark.parametrize(
    ("target", "env", "message"),
    [
        ("prod", {"MIP_PRESENTER_MODE": "1"}, "refused for target prod"),
        ("prod", {"MIP_PRESENTER_MODE": "TRUE"}, "refused for target prod"),
        ("prod", {"MIP_PRESENTER_MODE": " On "}, "refused for target prod"),
        ("dev", {"MIP_PRESENTER_MODE": "maybe"}, "must be one of 0/1/true/false/yes/no/on/off"),
        ("prod", {"MIP_PRESENTER_MODE": "2"}, "must be one of 0/1/true/false/yes/no/on/off"),
        ("dev", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "enable"}, "must be enabled or disabled"),
        ("dev", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "Enabled"}, "must be enabled or disabled"),
        ("prod", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "off"}, "must be enabled or disabled"),
    ],
)
def test_prod_presenter_mode_and_unparseable_values_exit_2(
    bash: str, target: str, env: dict[str, str], message: str
) -> None:
    proc = _run(bash, target, env)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert message in proc.stderr
    assert "presenter=[" not in proc.stdout


@pytest.mark.parametrize("bash", BASHES)
@pytest.mark.parametrize(
    ("target", "env", "expected"),
    [
        ("dev", {"MIP_PRESENTER_MODE": "1"}, "presenter=[1] capture=[]"),
        ("dev", {"MIP_PRESENTER_MODE": "Yes"}, "presenter=[yes] capture=[]"),
        ("prod", {"MIP_PRESENTER_MODE": "off"}, "presenter=[off] capture=[]"),
        ("prod", {}, "presenter=[] capture=[]"),
        ("prod", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "disabled"}, "presenter=[] capture=[disabled]"),
        ("dev", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "enabled"}, "presenter=[] capture=[enabled]"),
    ],
)
def test_dev_presenter_mode_prod_off_and_the_capture_values_pass(
    bash: str, target: str, env: dict[str, str], expected: str
) -> None:
    proc = _run(bash, target, env)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert expected in proc.stdout


def test_at_least_one_bash_4_or_newer_ran_the_block() -> None:
    versions = [
        subprocess.run(
            [bash, "-c", "echo ${BASH_VERSINFO[0]}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
        for bash in BASHES
    ]
    assert any(int(version) >= 4 for version in versions), versions


def test_the_preflight_vocabulary_is_the_payload_vocabulary() -> None:
    block = _block()
    on = block[block.index("  1|true|yes|on)") :].split(")", 1)[0].strip()
    off = block[block.index("  ''|0|false|no|off)") :].split(")", 1)[0].strip()
    assert set(on.split("|")) == set(app_deploy_payload._PRESENTER_MODE_ON)
    assert {"" if token == "''" else token for token in off.split("|")} == set(
        app_deploy_payload._PRESENTER_MODE_OFF
    )
