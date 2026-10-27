"""Read-only postflight: the deployed mip_scheduled_refresh matches the deploy's intent.

scripts/deploy.sh decides the weekly schedule's pause status in preflight
(``BUNDLE_VAR_scheduled_refresh_pause_status``) and bundle_env decides its
failure recipients. A bundle validate cannot prove what the Jobs API stored,
so after the apply this reads the live job and fails the deploy when:

* ``settings.schedule.pause_status`` differs from ``--expect``;
* the failure email disagrees with the install (one recipient on the dev live
  install, none anywhere else);
* the live ``run_job_task`` closure is not exactly the bundle's FRED, gold,
  lifecycle and KPI jobs, reaches ``mip_init_catalog_schemas``, or reaches any
  task that publishes one of the three reviewed Growth Agent functions (a
  stale deployed gold job still carrying ``publish_fn_*`` would republish them
  weekly and drop their EXECUTE grants).

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

CHAIN_JOB_KEYS = ("mip_fred_rates_ingest", "mip_refresh_scores", "mip_sync_lifecycle_state", "mip_kpi_snapshot")
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
    recipients = list(getattr(getattr(root, "email_notifications", None), "on_failure", None) or [])
    if live_install and len(recipients) != 1:
        problems.append(f"{JOB_KEY} must email exactly one failure recipient on the live install, has {len(recipients)}")
    if not live_install and recipients:
        problems.append(f"{JOB_KEY} must email no failure recipient outside the live install, has {len(recipients)}")

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
            path = str(getattr(getattr(getattr(task, "sql_task", None), "file", None), "path", "") or "")
            if _REVIEWED_FUNCTION_FILE.search(path) or str(task.task_key).startswith("publish_fn_"):
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
    parser.add_argument("--live-install", action="store_true", help="the dev target's live install (empty install_suffix)")
    args = parser.parse_args(argv)
    try:
        summary = json.loads((stdin or sys.stdin).read())
        if not isinstance(summary, dict):
            raise ValueError("bundle summary JSON must be an object")
        if client is None:
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient()
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
    print(f"[scheduled-refresh] {JOB_KEY} is {args.expect}; chain FRED -> gold -> lifecycle -> KPI verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
