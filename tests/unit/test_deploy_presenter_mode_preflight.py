"""NB-3: presenter mode and the refusal-capture switch are refused at step 0.

``MIP_PRESENTER_MODE`` is demo-only (D-shell-deviations-e1). The App payload
already refused a truthy value for a customer install, but only at App
promotion, after the bundle, UC and Lakebase steps had mutated the workspace.
Since M1-PR3 it is the client profile's own field (tools/clients/policy.py):
the profile parses it strictly and refuses it for a customer install before
any deploy step, the preflight accepts only the profile's closed 0/1, and the
preflight parses ``MIP_GENIE_REFUSAL_TEXT_CAPTURE`` (D-audit-reads-d), all
before any workspace mutation, exiting 2.

The block is read from ``deploy_entrypoint_text()`` (the reviewed
command-of-record with its step files expanded), never the raw file, and is
EXECUTED under the newest bash on PATH (Homebrew bash first, when present)
and under ``/bin/bash`` (3.2 on macOS) so it parses there too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import customer_profile, demo_profile
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.clients import schema
from tools.clients.environment import RawProfile, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.policy import install_policy
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


def _run(bash: str, presenter: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    harness = (
        "set -euo pipefail\n"
        "RED=''; RST=''\n"
        "deployment_control_value() { local value=\"${!1:-}\"; printf '%s' \"${value:-${2:-}}\"; }\n"
        f"CLIENT_PRESENTER_MODE='{presenter}'\n"
        f"{_block()}"
        'echo "presenter=[${CLIENT_PRESENTER_MODE}] capture=[${_MIP_GENIE_REFUSAL_TEXT_CAPTURE}]"\n'
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
    assert "CLIENT_INSTALL_CLASS" not in _block(), "the profile refused a customer value already"
    assert "deployment_control_value MIP_PRESENTER_MODE" not in _block(), "only the profile's policy"


@pytest.mark.parametrize("bash", BASHES)
@pytest.mark.parametrize(
    ("presenter", "env", "message"),
    [
        ("", {}, "the client profile's MIP_PRESENTER_MODE must be 0 or 1"),
        ("true", {}, "the client profile's MIP_PRESENTER_MODE must be 0 or 1"),
        ("2", {"MIP_PRESENTER_MODE": "1"}, "the client profile's MIP_PRESENTER_MODE must be 0 or 1"),
        ("0", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "enable"}, "must be enabled or disabled"),
        ("1", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "Enabled"}, "must be enabled or disabled"),
        ("0", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "off"}, "must be enabled or disabled"),
    ],
)
def test_a_presenter_policy_that_is_not_the_profiles_and_unparseable_capture_values_exit_2(
    bash: str, presenter: str, env: dict[str, str], message: str
) -> None:
    proc = _run(bash, presenter, env)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert message in proc.stderr
    assert "presenter=[" not in proc.stdout


@pytest.mark.parametrize("bash", BASHES)
@pytest.mark.parametrize(
    ("presenter", "env", "expected"),
    [
        ("1", {}, "presenter=[1] capture=[]"),
        # The profile's value is the policy; a stray shell value never reaches the block.
        ("0", {"MIP_PRESENTER_MODE": "1"}, "presenter=[0] capture=[]"),
        ("0", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "disabled"}, "presenter=[0] capture=[disabled]"),
        ("1", {"MIP_GENIE_REFUSAL_TEXT_CAPTURE": "enabled"}, "presenter=[1] capture=[enabled]"),
    ],
)
def test_the_profiles_presenter_policy_and_the_capture_values_pass(
    bash: str, presenter: str, env: dict[str, str], expected: str
) -> None:
    proc = _run(bash, presenter, env)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert expected in proc.stdout


@pytest.mark.parametrize("value", ["1", "TRUE", " On ", "yes"])
def test_the_profile_refuses_presenter_mode_for_a_customer_install(value: str) -> None:
    with pytest.raises(ProfileRefusal) as refused:
        resolve(RawProfile(values=customer_profile(MIP_PRESENTER_MODE=value)))
    assert any(line.startswith("MIP_PRESENTER_MODE: is demo-only") for line in refused.value.lines)


@pytest.mark.parametrize(("value", "on"), [("Yes", True), ("1", True), ("off", False), (None, False)])
def test_the_profile_normalises_presenter_mode_into_the_policy(value: str | None, on: bool) -> None:
    policy = install_policy(resolve(RawProfile(values=demo_profile(MIP_PRESENTER_MODE=value))))
    assert policy.presenter_mode is on


def test_the_profile_refuses_an_unparseable_presenter_mode() -> None:
    with pytest.raises(ProfileRefusal) as refused:
        resolve(RawProfile(values=demo_profile(MIP_PRESENTER_MODE="maybe")))
    assert any("must be one of 0/1/true/false/yes/no/on/off" in line for line in refused.value.lines)


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


def test_the_profile_vocabulary_is_the_payload_vocabulary() -> None:
    assert set(schema._TRUE) == set(app_deploy_payload._PRESENTER_MODE_ON)
    assert set(schema._FALSE) | {""} == set(app_deploy_payload._PRESENTER_MODE_OFF)
