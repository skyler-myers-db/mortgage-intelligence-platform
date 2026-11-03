"""No failed task in a bundle job can end its run anything but FAILED (M2-PR5).

Databricks judges a job run by its leaf tasks (the tasks nothing depends on):

* every leaf succeeded and every task succeeded: SUCCESS;
* every leaf succeeded but some other task failed: SUCCESS_WITH_FAILURES
  (databricks-sdk ``RunResultState``: "leaf tasks were successful");
* a leaf failed or was skipped for an upstream failure: FAILED.

An EXCLUDED task (a run_if that handles failure, unmet) counts as successful.
CLI v1.10.0 ``bundle run`` maps only CANCELED, FAILED, SUCCESS and TIMEDOUT
to its exit code, so SUCCESS_WITH_FAILURES exits 0. scripts/deploy.sh gates
its refresh steps on that exit code (``run_job_with_retry databricks bundle
run``), the Admin Operations panel maps only FAILED and TIMEDOUT to an error,
and sql/transformations/assert_scheduled_refresh_outcome.sql assumes a failed
gold run fails its job. A task that runs on ALL_DONE as a job's only leaf (the
owner handover, converge_governed_owner) would turn every failure before it
into SUCCESS_WITH_FAILURES.

This test runs every job in databricks.yml once per task, with that one task
failing, through a model of those rules, and requires FAILED every time. The
first test proves the model reports the ALL_DONE-leaf defect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
JOBS: dict[str, Any] = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))["resources"][
    "jobs"
]
SUCCEEDED = frozenset({"SUCCESS", "EXCLUDED"})


def _depends_on(task: dict[str, Any]) -> list[str]:
    return [dependency["task_key"] for dependency in task.get("depends_on", [])]


def task_states(tasks: list[dict[str, Any]], failing: str | None) -> dict[str, str]:
    """Each task's result when only ``failing`` fails (and fails only if it runs)."""

    by_key = {task["task_key"]: task for task in tasks}
    assert len(by_key) == len(tasks), "task keys are unique"
    states: dict[str, str] = {}
    pending = list(by_key)
    while pending:
        ready = [key for key in pending if all(dep in states for dep in _depends_on(by_key[key]))]
        assert ready, f"the task graph has a cycle or a missing dependency: {pending}"
        for key in ready:
            pending.remove(key)
            dependencies = [states[dep] for dep in _depends_on(by_key[key])]
            run_if = by_key[key].get("run_if", "ALL_SUCCESS")
            if run_if == "ALL_SUCCESS":
                runs = all(state in SUCCEEDED for state in dependencies)
            elif run_if == "ALL_DONE":
                runs = True
            else:
                raise AssertionError(f"{key}: run_if {run_if} is not modelled; model it before using it")
            if not runs:
                states[key] = "UPSTREAM_FAILED"
            else:
                states[key] = "FAILED" if key == failing else "SUCCESS"
    return states


def run_result(tasks: list[dict[str, Any]], states: dict[str, str]) -> str:
    depended = {dep for task in tasks for dep in _depends_on(task)}
    leaves = [task["task_key"] for task in tasks if task["task_key"] not in depended]
    if any(states[leaf] not in SUCCEEDED for leaf in leaves):
        return "FAILED"
    if any(state not in SUCCEEDED for state in states.values()):
        return "SUCCESS_WITH_FAILURES"
    return "SUCCESS"


def test_the_model_reports_an_all_done_sole_leaf_as_success_with_failures() -> None:
    """The positive control: the shape this test exists to refuse."""

    tasks = [
        {"task_key": "refresh"},
        {"task_key": "handover", "run_if": "ALL_DONE", "depends_on": [{"task_key": "refresh"}]},
    ]
    assert run_result(tasks, task_states(tasks, failing="refresh")) == "SUCCESS_WITH_FAILURES"
    gated = [
        *tasks,
        {
            "task_key": "verdict",
            "run_if": "ALL_SUCCESS",
            "depends_on": [{"task_key": "handover"}, {"task_key": "refresh"}],
            "condition_task": {"op": "EQUAL_TO", "left": "succeeded", "right": "succeeded"},
        },
    ]
    assert run_result(gated, task_states(gated, failing="refresh")) == "FAILED"
    assert run_result(gated, task_states(gated, failing="handover")) == "FAILED"
    assert run_result(gated, task_states(gated, failing=None)) == "SUCCESS"


@pytest.mark.parametrize("job_key", sorted(JOBS))
def test_every_task_failure_fails_the_run(job_key: str) -> None:
    tasks = JOBS[job_key]["tasks"]
    assert run_result(tasks, task_states(tasks, failing=None)) == "SUCCESS", job_key
    for task in tasks:
        states = task_states(tasks, failing=task["task_key"])
        assert run_result(tasks, states) == "FAILED", f"{job_key}: {task['task_key']} failing"


def test_the_handover_jobs_end_in_the_compute_free_verdict() -> None:
    """Each ALL_DONE handover is followed by the one leaf that judges the run."""

    handover_jobs = {
        key for key, job in JOBS.items() if any(task.get("run_if") == "ALL_DONE" for task in job["tasks"])
    }
    assert handover_jobs == {"mip_refresh_silver", "mip_refresh_scores", "mip_fred_rates_ingest"}
    for key in handover_jobs:
        tasks = JOBS[key]["tasks"]
        depended = {dep for task in tasks for dep in _depends_on(task)}
        leaves = [task for task in tasks if task["task_key"] not in depended]
        assert [leaf["task_key"] for leaf in leaves] == ["assert_refresh_succeeded"], key
        verdict = leaves[0]
        assert verdict["run_if"] == "ALL_SUCCESS", key
        # A condition task needs no compute and cannot be retried into a pass.
        assert verdict["condition_task"] == {"op": "EQUAL_TO", "left": "succeeded", "right": "succeeded"}
        assert "max_retries" not in verdict, key
