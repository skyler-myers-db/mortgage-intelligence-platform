"""Read-only postflight: the deployed mip_scheduled_refresh matches the deploy's intent.

scripts/deploy.sh decides the weekly schedule's pause status in preflight
(``BUNDLE_VAR_scheduled_refresh_pause_status``) and bundle_env decides its
failure recipients. A bundle validate cannot prove what the Jobs API stored,
so after the apply this reads the live job and fails the deploy when:

* ``settings.schedule.pause_status`` differs from ``--expect``;
* the failure email disagrees with the install: exactly one recipient on a
  demo install (``--live-install``; an email address whenever the schedule is UNPAUSED, so a
  service-principal deployer's application id never silently swallows the
  alert), none anywhere else;
* the orchestrator's task graph is not exactly :data:`ORCHESTRATOR_TASKS`:
  FRED -> gold -> lifecycle -> KPI, each task running the bundle's own job and
  depending only on the step before it, plus the SELECT-only outcome gate after
  gold, every task on ALL_SUCCESS;
* the live ``run_job_task`` closure is not exactly the bundle's FRED, gold,
  lifecycle and KPI jobs, reaches ``mip_init_catalog_schemas``, or reaches any
  task that publishes one of the three reviewed Growth Agent functions, by task
  key (``publish_fn_*``) or by SQL file (a stale deployed gold job still
  carrying ``publish_fn_*`` would republish them weekly and drop their EXECUTE
  grants).

The bundle summary JSON (``bundle_env summary -t <target> -o json``) arrives on
stdin. It never prints a recipient address. Exit 0 on match, 2 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from typing import Any

from tools.databricks.scheduled_refresh_posture import JOB_KEY, PAUSE_STATES

#: The orchestrator's task graph as databricks.yml declares it: task_key ->
#: (the bundle job its run_job_task runs, or None for the SQL gate; the task
#: keys it depends on). tests/unit/test_verify_scheduled_refresh_state.py pins
#: it to databricks.yml.
ORCHESTRATOR_TASKS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "refresh_market_rates": ("mip_fred_rates_ingest", ()),
    "refresh_gold_scores": ("mip_refresh_scores", ("refresh_market_rates",)),
    "sync_lifecycle_state": ("mip_sync_lifecycle_state", ("refresh_gold_scores",)),
    "snapshot_headline_kpis": ("mip_kpi_snapshot", ("sync_lifecycle_state",)),
    "assert_refresh_outcome": (None, ("refresh_gold_scores",)),
}
GATE_FILE = "sql/_rendered/transformations/assert_scheduled_refresh_outcome.sql"
CHAIN_JOB_KEYS = tuple(job for job, _deps in ORCHESTRATOR_TASKS.values() if job is not None)
PUBLISHER_JOB_KEY = "mip_init_catalog_schemas"
_REVIEWED_FUNCTION_FILE = re.compile(r"uc_functions/fn_(build_cohort|segment_counts|lead_queue_url)\.sql\Z")


def _summary_job_ids(summary: dict[str, Any]) -> dict[str, str]:
    jobs = (summary.get("resources") or {}).get("jobs") or {}
    ids: dict[str, str] = {}
    for key in (JOB_KEY, PUBLISHER_JOB_KEY, *CHAIN_JOB_KEYS):
        job_id = str((jobs.get(key) or {}).get("id") or "").strip()
        if not job_id.isdigit():
            raise ValueError(f"bundle summary has no deployed id for jobs.{key}")
        ids[key] = job_id
    return ids


def _value(item: Any, name: str) -> Any:
    value = getattr(item, name, None)
    return getattr(value, "value", value)


def _sql_file_path(task: Any) -> str:
    return str(getattr(getattr(getattr(task, "sql_task", None), "file", None), "path", "") or "")


def _recipient_problems(root: Any, *, expect_pause: str, live_install: bool) -> list[str]:
    recipients = [str(item) for item in getattr(getattr(root, "email_notifications", None), "on_failure", None) or []]
    if not live_install:
        if recipients:
            return [f"{JOB_KEY} must email no failure recipient outside the live install, has {len(recipients)}"]
        return []
    if len(recipients) != 1:
        return [f"{JOB_KEY} must email exactly one failure recipient on the live install, has {len(recipients)}"]
    if expect_pause == "UNPAUSED" and "@" not in recipients[0]:
        return [
            f"{JOB_KEY}'s failure recipient on the live install is not an email address (a service-principal "
            "deployer?): deploy the live install as a user, or set MIP_SCHEDULED_REFRESH=off"
        ]
    return []


def _task_graph_problems(root: Any, ids: dict[str, str]) -> list[str]:
    tasks = list(getattr(root, "tasks", None) or [])
    by_key = {str(task.task_key): task for task in tasks}
    problems: list[str] = []
    if len(by_key) != len(tasks) or set(by_key) != set(ORCHESTRATOR_TASKS):
        problems.append(f"{JOB_KEY} has tasks {sorted(str(task.task_key) for task in tasks)}, expected {sorted(ORCHESTRATOR_TASKS)}")
    for key, (job_key, depends_on) in ORCHESTRATOR_TASKS.items():
        task = by_key.get(key)
        if task is None:
            continue
        deps = sorted(str(dep.task_key) for dep in getattr(task, "depends_on", None) or [])
        if deps != sorted(depends_on):
            problems.append(f"{JOB_KEY} task {key} depends on {deps}, expected {sorted(depends_on)}")
        run_if = _value(task, "run_if")
        if run_if not in (None, "ALL_SUCCESS"):
            problems.append(f"{JOB_KEY} task {key} runs if {run_if}, expected ALL_SUCCESS")
        run_job = getattr(task, "run_job_task", None)
        if job_key is None:
            if run_job is not None or not _sql_file_path(task).endswith(f"/{GATE_FILE}"):
                problems.append(f"{JOB_KEY} task {key} is not the SELECT-only outcome gate {GATE_FILE}")
        elif run_job is None or str(run_job.job_id) != ids[job_key]:
            problems.append(f"{JOB_KEY} task {key} does not run {job_key}")
    return problems


def verify(
    get_job: Callable[[int], Any],
    summary: dict[str, Any],
    *,
    expect_pause: str,
    live_install: bool,
) -> list[str]:
    """Every mismatch between the live job and the intent; empty means it matches."""

    ids = _summary_job_ids(summary)
    problems: list[str] = []
    root = get_job(int(ids[JOB_KEY])).settings
    pause = _value(getattr(root, "schedule", None), "pause_status")
    if pause != expect_pause:
        problems.append(f"{JOB_KEY} pause_status is {pause}, expected {expect_pause}")
    problems.extend(_recipient_problems(root, expect_pause=expect_pause, live_install=live_install))
    problems.extend(_task_graph_problems(root, ids))

    reached: set[str] = set()
    pending = [ids[JOB_KEY]]
    while pending:
        job_id = pending.pop()
        if job_id in reached:
            continue
        reached.add(job_id)
        settings = root if job_id == ids[JOB_KEY] else get_job(int(job_id)).settings
        for task in getattr(settings, "tasks", None) or []:
            run_job = getattr(task, "run_job_task", None)
            if run_job is not None:
                pending.append(str(run_job.job_id))
            if _REVIEWED_FUNCTION_FILE.search(_sql_file_path(task)) or str(task.task_key).startswith("publish_fn_"):
                problems.append(f"job {job_id} task {task.task_key} publishes a reviewed Growth Agent function")
    if ids[PUBLISHER_JOB_KEY] in reached:
        problems.append(f"{JOB_KEY} reaches {PUBLISHER_JOB_KEY}")
    expected = {ids[key] for key in (JOB_KEY, *CHAIN_JOB_KEYS)}
    if reached != expected:
        problems.append(f"{JOB_KEY} reaches jobs {sorted(reached)}, expected the bundle's chain {sorted(expected)}")
    return problems


def main(argv: list[str] | None = None, *, stdin: Any = None, client: Any = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the deployed mip_scheduled_refresh against the deploy intent.")
    parser.add_argument("--expect", required=True, choices=sorted(PAUSE_STATES))
    parser.add_argument("--live-install", action="store_true", help="a demo install, which runs the schedule")
    args = parser.parse_args(argv)
    try:
        summary = json.loads((stdin or sys.stdin).read())
        if not isinstance(summary, dict):
            raise ValueError("bundle summary JSON must be an object")
        if client is None:
            # The deployer identity deploy.sh bound, never the App's runtime
            # OAuth variables (the SDK itself retries transient read errors).
            from tools.databricks.workspace_auth import deployment_workspace_client

            client = deployment_workspace_client()
        problems = verify(
            lambda job_id: client.jobs.get(job_id), summary, expect_pause=args.expect, live_install=args.live_install
        )
    except Exception as exc:  # noqa: BLE001 - a postflight fails closed on any read error
        print(f"[scheduled-refresh] cannot verify the deployed job: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"[scheduled-refresh] {problem}", file=sys.stderr)
        return 2
    print(
        f"[scheduled-refresh] {JOB_KEY} is {args.expect}; tasks FRED -> gold -> lifecycle -> KPI plus the outcome "
        "gate after gold, and closure = {FRED, gold, lifecycle, KPI} verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
