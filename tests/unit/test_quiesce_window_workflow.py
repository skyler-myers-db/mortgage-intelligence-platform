"""deploy-dev.yml sets the job deadline at job start and records the window journal, read-only (M0-PR9).

The workflow stays parked (M1-PR7): its ``parked`` job refuses every dispatch
and the deploy job needs it, so neither step runs today. When it does, the
first step turns ``timeout-minutes`` into ``MIP_JOB_DEADLINE_EPOCH`` (the
quiesce window's admission and its dead-man's deadline), and the last,
``if: always()``, reads the window journal and the dead-man's marker and
records their states as codes: it never stops, starts, cancels or writes.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from tools.databricks import stop_primitive_map as stops

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
DEADLINE_STEP = "Record the job deadline"
RECORD_STEP = "Record the quiesce window journal state"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    return _workflow()["jobs"]["deploy"]["steps"]


def test_the_workflow_stays_parked_before_either_step() -> None:
    jobs = _workflow()["jobs"]
    assert jobs["deploy"]["needs"] == "parked"
    assert jobs["parked"]["steps"][0]["run"].rstrip().endswith("exit 1")


def test_the_deadline_is_recorded_first_from_the_jobs_own_timeout(tmp_path: Path) -> None:
    job = _workflow()["jobs"]["deploy"]
    first = _steps()[0]
    assert first["name"] == DEADLINE_STEP and "if" not in first
    assert int(first["env"]["MIP_JOB_TIMEOUT_MINUTES"]) == job["timeout-minutes"]
    github_env = tmp_path / "github.env"
    before = int(time.time())
    subprocess.run(
        ["bash", "-c", first["run"]],
        env={"PATH": os.environ["PATH"], "GITHUB_ENV": str(github_env), **first["env"]},
        check=True,
        timeout=30,
    )
    name, _, value = github_env.read_text(encoding="utf-8").strip().partition("=")
    assert name == "MIP_JOB_DEADLINE_EPOCH"
    assert before + job["timeout-minutes"] * 60 <= int(value) <= int(time.time()) + job["timeout-minutes"] * 60


def test_the_journal_is_recorded_last_whatever_happened_and_read_only() -> None:
    last = _steps()[-1]
    assert last["name"] == RECORD_STEP and last["if"] == "always()"
    assert last["env"] == {
        "DATABRICKS_CONFIG_PROFILE": "DEFAULT",
        "MIP_APP_NAME": "${{ vars.MIP_APP_NAME || 'mip-app' }}",
    }, "no App client pair, no secret"
    run = last["run"]
    assert "-m tools.databricks.quiesce_window status" in run and "--out-env" not in run
    assert not stops.ENTRY_POINTS["tools.databricks.quiesce_window"].modes["status"], "a read-only mode"
    assert stops.shell_violations("scripts/lib/workflow-step.sh", run) == []
    for mutating in ("deploy.sh", "cancel-deadman", "repin", " open", " start", "apps ", "jobs "):
        assert mutating not in run, mutating


def test_the_deploy_step_sits_between_them_and_keeps_its_own_env() -> None:
    names = [step.get("name") for step in _steps()]
    assert names.index(DEADLINE_STEP) == 0
    assert names.index("Deploy dev Databricks App") == len(names) - 2
    deploy = _steps()[names.index("Deploy dev Databricks App")]
    assert "MIP_JOB_DEADLINE_EPOCH" not in deploy["env"], "it arrives through GITHUB_ENV, set at job start"
