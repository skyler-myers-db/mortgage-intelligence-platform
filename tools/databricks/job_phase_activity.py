"""Is a data-phase ledger entry's run still going? Read-only (M1-PR6).

``tools/databricks/job_phase_runner.py`` never runs, closes, resumes past or
retires a generation while one of its entries is still running, and a
release refuses while one is (``quiet``). An entry is ``running``, ``idle``
or ``absent``:

* only a ``running`` or ``submitted`` entry can still be going: the run the
  ledger names decides while it can be read;
* otherwise (a run no longer readable, or a token journaled without its run
  id) the job's own active runs decide;
* ``absent``: the bound job was deleted out of band. The Jobs API answers its
  ``runs/list`` with ``InvalidParameterValue`` ("Job N does not exist."), and
  only ``jobs/get`` answering ``NotFound`` (``ResourceDoesNotExist``) proves
  it. Even then a run of that job id still active anywhere in the workspace
  keeps it ``running``. Any other error is raised, as before: unproven is
  never idle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from databricks.sdk.errors import InvalidParameterValue, NotFound
from tools.databricks.job_phase_definition import enum_value

TERMINAL_LIFECYCLE = frozenset({"TERMINATED", "SKIPPED", "INTERNAL_ERROR"})


def outcome(run: Any) -> str:
    """``running``, ``succeeded`` or ``failed``; anything not terminal is still running."""

    state = getattr(run, "state", None)
    lifecycle = enum_value(getattr(state, "life_cycle_state", None))
    if lifecycle not in TERMINAL_LIFECYCLE:
        return "running"
    result = enum_value(getattr(state, "result_state", None))
    return "succeeded" if lifecycle == "TERMINATED" and result == "SUCCESS" else "failed"


def job_present(workspace: Any, job_id: Any) -> bool:
    """False only on ``jobs/get``'s ``NotFound``; any other error is raised."""

    try:
        workspace.jobs.get(int(job_id))
    except NotFound:
        return False
    return True


def activity(workspace: Any, entry: Mapping[str, Any]) -> str:
    """``running``, ``idle``, or ``absent`` (its job is deleted and no run of it is active)."""

    if entry["status"] not in {"running", "submitted"}:
        return "idle"
    if entry["status"] == "running":
        try:
            run = workspace.jobs.get_run(int(entry["run_id"]))
        except Exception:  # noqa: BLE001 - unreadable (a purged run): the job's own active runs decide
            run = None
        if run is not None:
            return "running" if outcome(run) == "running" else "idle"
    job_id = int(entry["job_id"])
    try:
        return "running" if any(True for _ in workspace.jobs.list_runs(job_id=job_id, active_only=True)) else "idle"
    except (InvalidParameterValue, NotFound):
        if job_present(workspace, job_id):
            raise
    # The job is gone: only a run of its id still active in the workspace keeps it running.
    if any(str(getattr(run, "job_id", "") or "") == str(job_id) for run in workspace.jobs.list_runs(active_only=True)):
        return "running"
    return "absent"
