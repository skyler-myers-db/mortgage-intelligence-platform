"""The window library's M0-PR9 shell: the job deadline and the dead-man's lifecycle.

The harness is M0-PR8's (``tests/unit/test_quiesce_window_shell.py``): the
real window and EXIT-trap libraries with a fake ``$PYTHON`` that records each
tool call. ``deploy.sh`` reads the job's deadline (``--deadline`` or
``MIP_JOB_DEADLINE_EPOCH``) and hands it to every window tool that decides on
it; the dry run prints the admission without a Databricks call.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tests.unit.test_quiesce_window_shell import _has, _run

REPO = Path(__file__).resolve().parents[2]
DEADLINE = "1800000000"


def _parse(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """deploy.sh's argument parse alone (it ends before any helper or step)."""

    text = deploy_entrypoint_text()
    parse = text[text.index("DRY_RUN=0\n") : text.index("# Pretty-print helpers")]
    ambient = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("GITHUB_", "MIP_JOB_DEADLINE_EPOCH"))
    }
    return subprocess.run(
        ["bash", "-c", parse + 'printf "deadline=%s\\n" "${MIP_JOB_DEADLINE_EPOCH:-}"\n', "deploy.sh", *args],
        cwd=REPO,
        env={**ambient, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("args", "env", "deadline"),
    (
        (("--deadline", DEADLINE), {}, DEADLINE),
        ((f"--deadline={DEADLINE}",), {}, DEADLINE),
        ((), {"MIP_JOB_DEADLINE_EPOCH": DEADLINE}, DEADLINE),
        (("--deadline", "1900000000"), {"MIP_JOB_DEADLINE_EPOCH": DEADLINE}, "1900000000"),
        ((), {}, ""),
    ),
    ids=("flag", "flag-equals", "workflow-env", "flag-wins", "none"),
)
def test_deploy_reads_the_job_deadline_from_the_flag_or_the_environment(
    args: tuple[str, ...], env: dict[str, str], deadline: str
) -> None:
    result = _parse("-t", "client", *args, env=env)
    assert result.returncode == 0, result.stderr
    assert f"deadline={deadline}\n" in result.stdout


def test_a_deadline_flag_without_a_value_refuses_before_any_step() -> None:
    result = _parse("-t", "client", "--deadline")
    assert result.returncode == 2 and "missing value for --deadline" in result.stderr


def test_the_dry_run_prints_the_admission(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        f'DRY_RUN=1; MIP_JOB_DEADLINE_EPOCH={DEADLINE}\nplan_quiesce_window pre-lease "$TMPDIR/r.json"\n',
    )
    assert result.returncode == 0, result.stderr
    assert calls == [f"-m tools.databricks.quiesce_window_budget describe --deadline-epoch {DEADLINE}"]


@pytest.mark.parametrize("deadline", (DEADLINE, ""))
def test_the_plan_and_the_open_are_handed_the_deadline(tmp_path: Path, deadline: str) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        f'MIP_JOB_DEADLINE_EPOCH="{deadline}"\nQUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS=WR3\n'
        'plan_quiesce_window pre-apply "$TMPDIR/report.json"\nensure_quiesce_window_open_for WR3\n',
        PLAN_CODE="window",
        PLAN_REASONS_JSON='["WR3"]',
    )
    assert result.returncode == 0, result.stderr
    plan = next(call for call in calls if "quiesce_window plan" in call)
    opened = next(call for call in calls if "quiesce_window open" in call)
    for call in (plan, opened):
        assert f"--deadline-epoch {deadline} --" in call, "empty: the tools refuse a window"
    assert _has(calls, "quiesce_window open") == 1
