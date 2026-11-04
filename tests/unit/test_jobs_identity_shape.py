"""No job the App can trigger runs as the deployer (M2-PR5, plan section 4.3).

The App holds CAN_MANAGE_RUN on four bundle jobs (its ``job`` resource
bindings), so it can start them, with any parameters, whenever it is
compromised. They, the weekly ``mip_scheduled_refresh``, every job it starts
(``mip_kpi_snapshot`` included) and the feature pipeline run as
``var.jobs_principal``, the non-admin jobs identity; only the deploy-time jobs
keep the deployer (workspace admin).

* The App's CAN_MANAGE_RUN bindings are exactly the jobs Admin Operations can
  start (backend/services/databricks_jobs.MANAGED_JOBS), and each runs as the
  jobs principal.
* Nothing a jobs-identity job starts (a ``run_job_task`` or a
  ``pipeline_task``, transitively) runs as the deployer, so the non-admin
  identity never needs run permission on an admin-identity job.
* The deployer keeps exactly the deploy-time jobs; the monitor scheduler, which
  the App only reads, is one of them and stays PAUSED.
* The pipeline runs as the jobs principal too. The CLI v1.10.0 bundle schema
  (``databricks bundle schema``) declares ``run_as`` on ``resources.Pipeline``
  (service_principal_name or user_name), so spike S4's fallback (keep the
  pipeline on the deployer) is not needed for the bundle to express it; the
  live half of S4 (ALTER OWNER on its published datasets after a run_as
  change) stays with the cutover.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from backend.services.databricks_jobs import GROWTH_AGENT_SCHEDULER_JOB, MANAGED_JOBS

ROOT = Path(__file__).resolve().parents[2]
BUNDLE: dict[str, Any] = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
JOBS: dict[str, Any] = BUNDLE["resources"]["jobs"]
PIPELINES: dict[str, Any] = BUNDLE["resources"]["pipelines"]
DEPLOYER = {"service_principal_name": "${var.deployer_principal}"}
JOBS_IDENTITY = {"service_principal_name": "${var.jobs_principal}"}
# The deploy-time jobs: scripts/deploy.sh runs them as the deployer, and the
# App holds no run permission on any of them.
DEPLOY_TIME_JOBS = frozenset(
    {
        "mip_init_catalog_schemas",
        "mip_ref_seed",
        "mip_lakebase_migrate",
        "mip_growth_agent_monitor_scheduler",
    }
)


def _app_job_bindings() -> dict[str, str]:
    """Job key -> the permission the App holds on it, from its ``job`` resources."""

    bindings: dict[str, str] = {}
    for resource in BUNDLE["resources"]["apps"]["mip_app"]["resources"]:
        job = resource.get("job")
        if job is None:
            continue
        reference = job["id"]
        assert reference.startswith("${resources.jobs.") and reference.endswith(".id}"), reference
        bindings[reference[len("${resources.jobs.") : -len(".id}")]] = job["permission"]
    return bindings


def _app_triggerable() -> set[str]:
    return {key for key, permission in _app_job_bindings().items() if permission != "CAN_VIEW"}


def _started_by(job_key: str) -> set[str]:
    """The jobs and pipelines a run of ``job_key`` starts, transitively."""

    seen: set[str] = set()
    pending = [job_key]
    while pending:
        key = pending.pop()
        for task in JOBS[key]["tasks"]:
            for field, kind in (("run_job_task", "jobs"), ("pipeline_task", "pipelines")):
                body = task.get(field)
                if body is None:
                    continue
                reference = body["job_id" if kind == "jobs" else "pipeline_id"]
                prefix = f"${{resources.{kind}."
                assert reference.startswith(prefix) and reference.endswith(".id}"), reference
                target = f"{kind}.{reference[len(prefix) : -len('.id}')]}"
                if target not in seen:
                    seen.add(target)
                    if kind == "jobs":
                        pending.append(target.split(".", 1)[1])
    return seen


def test_the_apps_run_bindings_are_exactly_the_admin_operations_jobs() -> None:
    bindings = _app_job_bindings()
    assert {key: permission for key, permission in bindings.items() if permission == "CAN_MANAGE_RUN"} == {
        definition.job_name: "CAN_MANAGE_RUN" for definition in MANAGED_JOBS.values()
    }
    # The one other job binding is read-only: the scheduler's pause state.
    assert set(bindings) - _app_triggerable() == {GROWTH_AGENT_SCHEDULER_JOB.job_name}
    assert bindings[GROWTH_AGENT_SCHEDULER_JOB.job_name] == "CAN_VIEW"


def test_no_app_triggerable_job_runs_as_the_deployer() -> None:
    triggerable = _app_triggerable()
    assert triggerable == {
        "mip_sync_lifecycle_state",
        "mip_fred_rates_ingest",
        "mip_refresh_silver",
        "mip_refresh_scores",
    }
    for key in triggerable:
        assert JOBS[key]["run_as"] == JOBS_IDENTITY, key


def test_nothing_a_jobs_identity_job_starts_runs_as_the_deployer() -> None:
    on_jobs = {key for key, job in JOBS.items() if job["run_as"] == JOBS_IDENTITY}
    assert _app_triggerable() <= on_jobs
    for key in sorted(on_jobs):
        for started in _started_by(key):
            kind, name = started.split(".", 1)
            resource = (JOBS if kind == "jobs" else PIPELINES)[name]
            assert resource["run_as"] == JOBS_IDENTITY, f"{key} starts {started}"
    # The silver refresh starts the pipeline, and the weekly refresh starts the
    # KPI snapshot, so both are covered above.
    assert "pipelines.mip_feature_pipeline" in _started_by("mip_refresh_silver")
    assert "jobs.mip_kpi_snapshot" in _started_by("mip_scheduled_refresh")


def test_the_deployer_keeps_exactly_the_deploy_time_jobs() -> None:
    on_deployer = {key for key, job in JOBS.items() if job["run_as"] == DEPLOYER}
    on_jobs = {key for key, job in JOBS.items() if job["run_as"] == JOBS_IDENTITY}
    assert on_deployer == DEPLOY_TIME_JOBS
    assert on_jobs == _app_triggerable() | {"mip_scheduled_refresh", "mip_kpi_snapshot"}
    assert on_deployer | on_jobs == set(JOBS), "every job names one of the two identities"
    assert not DEPLOY_TIME_JOBS & _app_triggerable()
    assert PIPELINES["mip_feature_pipeline"]["run_as"] == JOBS_IDENTITY


def test_the_monitor_scheduler_the_app_only_reads_stays_paused() -> None:
    scheduler = JOBS[GROWTH_AGENT_SCHEDULER_JOB.job_name]
    assert scheduler["schedule"]["pause_status"] == "PAUSED"
    assert "trigger" not in scheduler and "continuous" not in scheduler

