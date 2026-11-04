"""deploy-dev.yml sets the job deadline at job start and records the window journal, read-only (M0-PR9).

The workflow stays parked (M1-PR7): its ``parked`` job refuses every dispatch
and the deploy job needs it, so neither step runs today. When it does, the
first step turns ``timeout-minutes`` into ``MIP_JOB_DEADLINE_EPOCH`` (the
quiesce window's admission and its dead-man's deadline), and the last,
``if: always()``, reads the window journal and the dead-man's marker and
records their states as codes: it never stops, starts, cancels or writes.
It has no key registry (deploy.sh derives the verify key inside its own
process), so it records the RAW state with ``status --unverified``, and the
test runs it under exactly the step's env against a real signed journal.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures.deploy_script import deploy_contract_text
from tests.fixtures.quiesce_window_estate import APP_NAME, run_deadman
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    _env,
    _estate,
    _open,
)
from tools.databricks import quiesce_window as window
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
    assert "-m tools.databricks.quiesce_window status --unverified" in run and "--out-env" not in run
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


# Every key the journal's signature check reads: none reaches the record step.
KEY_ENV = (
    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
    "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
    "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY",
    "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS",
)


def _record(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], estate: Any) -> str:
    """Run the step's own command and grep, under exactly its env, against ``estate``."""

    step = _steps()[-1]
    for name in KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in step["env"].items():
        monkeypatch.setenv(name, "mip-app" if name == "MIP_APP_NAME" else value)
    found = re.search(r"python -m tools\.databricks\.quiesce_window ([^|]+)\| grep '([^']+)'", step["run"])
    assert found, "the step runs status and greps its line"
    argv = shlex.split(found.group(1).replace('"$MIP_APP_NAME"', os.environ["MIP_APP_NAME"]))
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    out = capsys.readouterr().out
    grepped = subprocess.run(["grep", found.group(2)], input=out, text=True, capture_output=True, check=False)
    assert grepped.returncode == 0, f"the step's grep misses the line it records: {out!r}"
    return grepped.stdout.strip()


def test_the_record_step_reads_a_real_open_journal_under_its_own_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Review: under this env the verified status raised (no key registry), so
    # the step only ever recorded "absent". It must record the open window.
    assert APP_NAME == "mip-app"
    estate = _estate()
    _open(estate)  # signed under the deploy's key, before the step's env
    absent = _record(monkeypatch, capsys, _estate())
    assert absent.endswith("journal absent, blue restorable False, dead-man absent")
    line = _record(monkeypatch, capsys, estate)
    assert "status (unverified): journal window_open, blue restorable True, dead-man absent" in line
    with pytest.raises(RuntimeError, match="key registry"):
        window.main(["status", "--app-name", APP_NAME], workspace_factory=lambda: estate.workspace)


def test_the_record_step_records_a_dead_mans_restart(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate()
    _open(estate)
    run = estate.jobs.only_run()
    parameters = list(run.tasks[0].spark_python_task.parameters)
    deadline = float(parameters[parameters.index("--deadline") + 1])
    now = [deadline - 30]
    assert run_deadman(estate, run.run_id, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s)) == 0
    assert _record(monkeypatch, capsys, estate).endswith(
        "journal window_open, blue restorable True, dead-man deadman_restarted"
    )


def test_an_unverified_status_is_never_an_env_file_and_never_a_deploy_input(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="never an env file"):
        window.main(
            ["status", "--unverified", "--app-name", APP_NAME, "--out-env", str(tmp_path / "x.env")],
            workspace_factory=lambda: None,
        )
    assert "--unverified" not in deploy_contract_text().replace("--rebase-unverified-app", "")
