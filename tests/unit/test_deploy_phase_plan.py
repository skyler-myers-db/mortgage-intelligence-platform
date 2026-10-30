"""``deploy.sh --phase plan``: read-only, CI-only when live, and stopped before the lease (M0-PR4).

The dry run is driven end to end in the offline sandbox
(tests/fixtures/deploy_sandbox.py), whose stub ``databricks`` fails on
anything but ``--version``: the plan phase prints the read-only steps through
the pre-lease bundle plan gate and exits 0 there, before the lease and every
App stop. A live plan phase outside GitHub Actions is refused before any
Databricks call (attack O13), and so is any phase but ``plan``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_sandbox import (
    ANSI,
    INHERITED_PREFIXES,
    dry_run,
    planned_commands,
    sandbox,
)

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "scripts" / "deploy.sh"
O13 = "[deploy] plan is CI-only; use --dry-run or make bundle-validate"


def _deploy(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/deploy.sh", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )


def _clean_env() -> dict[str, str]:
    return {
        name: value for name, value in os.environ.items() if not name.startswith(INHERITED_PREFIXES)
    }


def test_the_plan_phase_dry_run_stops_after_the_pre_lease_gate(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)

    result = _deploy(checkout, env, "-t", "dev", "--phase", "plan", "--dry-run", "--no-confirm")

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    out = ANSI.sub("", result.stdout)
    commands = planned_commands(result.stdout)
    assert commands == [
        ".venv/bin/python tools/render_sql.py --catalog mip",
        ".venv/bin/python -m tools.databricks.bundle_env validate -t dev",
        ".venv/bin/python -m tools.databricks.bundle_env plan -t dev -o json --allow-unprovisioned-genie"
        " > /tmp/mip-dry-run-bundle-plan-pre-lease.json",
        ".venv/bin/python -m tools.databricks.bundle_plan_gate check"
        " --plan /tmp/mip-dry-run-bundle-plan-pre-lease.json --position pre-lease --app-key mip_app"
        " --report-out /tmp/mip-dry-run-bundle-plan-report-pre-lease.json",
    ]
    assert out.rstrip().endswith(
        "[deploy] plan phase complete: read-only, stopped before the lease; nothing was changed."
    )
    for later in (
        "rollback secret scope",
        "resolve governed Genie space",
        "pre-apply",
        "build frontend",
    ):
        assert later not in out, later


def test_the_full_dry_run_gates_the_plan_before_the_lease_and_again_before_the_apply(
    tmp_path: Path,
) -> None:
    checkout, env, log = sandbox(tmp_path)

    result = dry_run(checkout, env, "dev")

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"]
    out = ANSI.sub("", result.stdout)
    commands = planned_commands(result.stdout)
    gates = [index for index, command in enumerate(commands) if "bundle_plan_gate check" in command]
    plans = [command for command in commands if "bundle_env plan" in command]
    assert len(gates) == 2 and "--position pre-lease" in commands[gates[0]]
    assert "--position pre-apply" in commands[gates[1]]
    assert plans == [
        ".venv/bin/python -m tools.databricks.bundle_env plan -t dev -o json --allow-unprovisioned-genie"
        " > /tmp/mip-dry-run-bundle-plan-pre-lease.json",
        ".venv/bin/python -m tools.databricks.bundle_env plan -t dev -o json"
        " > /tmp/mip-dry-run-bundle-plan-pre-apply.json",
    ]
    genie = next(i for i, command in enumerate(commands) if "provision_genie_space" in command)
    apply = next(i for i, command in enumerate(commands) if "bundle_env deploy" in command)
    assert gates[0] < genie < gates[1] < apply
    assert out.index("and gate it (pre-lease)") < out.index(
        "prove or create the deterministic owned App rollback"
    )
    assert "plan phase complete" not in out


@pytest.mark.parametrize("ci", ({}, {"GITHUB_ACTIONS": "false"}), ids=("local", "not-actions"))
def test_a_live_plan_phase_outside_github_actions_is_refused_before_any_call(
    tmp_path: Path, ci: dict[str, str]
) -> None:
    checkout, env, log = sandbox(tmp_path)

    result = _deploy(checkout, {**env, **ci}, "-t", "dev", "--phase", "plan", "--no-confirm")

    assert result.returncode == 2
    assert result.stderr.strip() == O13
    assert (
        not log.exists() or log.read_text(encoding="utf-8") == ""
    ), "refused before any Databricks call"
    assert "step 1" not in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    (
        (["--phase", "release"], "phase must be exactly plan; refusing 'release'"),
        (["--phase=data"], "phase must be exactly plan; refusing 'data'"),
        (["--phase="], "phase must be exactly plan; refusing ''"),
        (["--phase"], "missing value for --phase (expected plan)"),
        (["--phase", "plan", "--phase", "plan"], "phase may be supplied only once"),
        (["--phase=plan", "--phase", "plan"], "phase may be supplied only once"),
    ),
)
def test_a_phase_other_than_one_plan_is_refused_at_parse_time(
    args: list[str], message: str
) -> None:
    result = _deploy(REPO, _clean_env(), "-t", "dev", "--dry-run", *args)

    assert result.returncode == 2
    assert result.stderr.strip() == f"[deploy] {message}"
    assert result.stdout == ""


def test_the_help_text_documents_the_plan_phase() -> None:
    result = _deploy(REPO, _clean_env(), "--help")

    assert result.returncode == 0
    assert "--phase plan --dry-run" in result.stdout
    assert result.stdout.rstrip().endswith("refresh to bind trusted assets.)\n#".rstrip())
