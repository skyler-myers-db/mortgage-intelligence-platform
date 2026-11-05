"""A fake Jobs, Pipelines and Workspace Files estate for the data phase's runner (M1-PR6).

Every job, run and pipeline the fakes return is built from the SDK's own
dataclasses (``Job.from_dict``, ``Run.from_dict``,
``GetPipelineResponse.from_dict``), so the runner reads exactly the shapes the
SDK parses. The bundle state is the direct-engine document
``tools/databricks/bundle_state.py`` reads, at the client target's state path.
The job graph mirrors databricks.yml: the silver refresh starts the feature
pipeline; the weekly scheduled refresh starts FRED, gold, the lifecycle mirror
and the KPI snapshot and ends in a SELECT-only gate.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound
from databricks.sdk.service._internal import Wait
from databricks.sdk.service.jobs import Job, Run, RunNowResponse
from databricks.sdk.service.pipelines import GetPipelineResponse

from tests.fixtures.client_profiles import IDENTITY_IDS
from tests.fixtures.quiesce_window_estate import FakeFiles
from tools.databricks import bundle_state
from tools.databricks.app_release_contract import api_path, bundle_root

APP_NAME = "mip-app"
CLIENT = "client-one"
DEPLOYER = IDENTITY_IDS["deployer"]
ROOT = bundle_root(principal=DEPLOYER, target="client", root_leaf=CLIENT)
FILES = f"{ROOT}/files"
JOB_IDS = {
    "mip_refresh_silver": 101,
    "mip_scheduled_refresh": 102,
    "mip_fred_rates_ingest": 103,
    "mip_refresh_scores": 104,
    "mip_sync_lifecycle_state": 105,
    "mip_kpi_snapshot": 106,
}
PIPELINE_ID = "pipeline-feature-0001"


def _deployment() -> dict[str, str]:
    return {"kind": "BUNDLE", "metadata_file_path": f"{ROOT}/state/metadata.json"}


def _sql(key: str, path: str, **extra: Any) -> dict[str, Any]:
    return {"task_key": key, "sql_task": {"file": {"path": f"{FILES}/{path}"}, "warehouse_id": "warehouse-1"}, **extra}


def job_settings() -> dict[str, dict[str, Any]]:
    """The live settings of every bundle job the two data-phase jobs reach."""

    def job(key: str, tasks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {"name": key, "deployment": _deployment(), "max_concurrent_runs": 1, "tasks": tasks, **extra}

    return {
        "mip_refresh_silver": job(
            "mip_refresh_silver",
            [
                _sql("init_catalog_schemas", "sql/_rendered/ddl/001_catalogs_schemas.sql"),
                _sql("seed_ref_lender_dictionary", "sql/_rendered/ref/lender_dictionary_seed.sql"),
                {"task_key": "refresh_silver_pipeline", "pipeline_task": {"pipeline_id": PIPELINE_ID}},
                {
                    "task_key": "converge_governed_owner",
                    "spark_python_task": {"python_file": f"{FILES}/jobs/converge_governed_owner.py"},
                },
            ],
        ),
        "mip_scheduled_refresh": job(
            "mip_scheduled_refresh",
            [
                {"task_key": "refresh_market_rates", "run_job_task": {"job_id": JOB_IDS["mip_fred_rates_ingest"]}},
                {"task_key": "refresh_gold_scores", "run_job_task": {"job_id": JOB_IDS["mip_refresh_scores"]}},
                {"task_key": "sync_lifecycle_state", "run_job_task": {"job_id": JOB_IDS["mip_sync_lifecycle_state"]}},
                {"task_key": "snapshot_headline_kpis", "run_job_task": {"job_id": JOB_IDS["mip_kpi_snapshot"]}},
                _sql("assert_refresh_outcome", "sql/_rendered/transformations/assert_scheduled_refresh_outcome.sql"),
            ],
            timeout_seconds=5400,
        ),
        "mip_fred_rates_ingest": job(
            "mip_fred_rates_ingest",
            [
                _sql("init_silver_ddl", "sql/_rendered/ddl/silver_market_rates_weekly.sql"),
                {"task_key": "refresh_from_fred", "spark_python_task": {"python_file": f"{FILES}/jobs/fred_rates_ingest.py"}},
            ],
        ),
        "mip_refresh_scores": job(
            "mip_refresh_scores",
            [
                _sql("init_fn_rate_spread", "sql/_rendered/uc_functions/fn_rate_spread.sql"),
                _sql("ctas_lead_scores", "sql/_rendered/transformations/gold_lead_scores.sql"),
            ],
        ),
        "mip_sync_lifecycle_state": job(
            "mip_sync_lifecycle_state",
            [
                {
                    "task_key": "sync_from_lakebase",
                    "spark_python_task": {"python_file": f"{FILES}/jobs/sync_lifecycle_state_job.py"},
                }
            ],
        ),
        "mip_kpi_snapshot": job(
            "mip_kpi_snapshot",
            [{"task_key": "snapshot_headline_kpis", "spark_python_task": {"python_file": f"{FILES}/jobs/kpi_snapshot.py"}}],
        ),
    }


def state_document(*, jobs: dict[str, int] | None = None, pipelines: dict[str, str] | None = None) -> dict[str, Any]:
    entries: dict[str, Any] = {f"resources.jobs.{key}": {"__id__": str(value)} for key, value in (JOB_IDS if jobs is None else jobs).items()}
    for key, value in (pipelines if pipelines is not None else {"mip_feature_pipeline": PIPELINE_ID}).items():
        entries[f"resources.pipelines.{key}"] = {"__id__": value}
    entries["resources.apps.mip_app"] = {"__id__": APP_NAME}
    return {"state_version": bundle_state.STATE_VERSION, "lineage": "lineage-1", "serial": 7, "state": entries}


@dataclass
class FakeJobs:
    """Jobs API by job id; run-now honours the idempotency token like the service does.

    Each run reports the job id it was started for, as ``runs/get`` does.
    """

    settings: dict[str, dict[str, Any]]
    # run_id -> the life-cycle/result states each get_run returns, in order (the last repeats).
    scripts: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    # The states a newly started run will report, in order.
    next_script: list[tuple[str, str]] = field(default_factory=lambda: [("RUNNING", ""), ("TERMINATED", "SUCCESS")])
    run_now_calls: list[tuple[int, str]] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)
    active: dict[int, list[int]] = field(default_factory=dict)
    next_run_id: int = 9000
    # bundle key -> live job id (a job recreated under a new id is rebound here).
    ids: dict[str, int] = field(default_factory=lambda: dict(JOB_IDS))
    run_jobs: dict[int, int] = field(default_factory=dict)

    def _by_id(self) -> dict[int, str]:
        return {self.ids[key]: key for key in self.settings}

    def get(self, job_id: int) -> Job:
        key = self._by_id().get(int(job_id))
        if key is None:
            raise NotFound("job")
        return Job.from_dict({"job_id": int(job_id), "settings": self.settings[key]})

    def run_now(self, *, job_id: int, idempotency_token: str) -> Wait[Run]:
        self.run_now_calls.append((int(job_id), idempotency_token))
        if idempotency_token not in self.tokens:
            self.next_run_id += 1
            self.tokens[idempotency_token] = self.next_run_id
            self.scripts[self.next_run_id] = list(self.next_script)
            self.run_jobs[self.next_run_id] = int(job_id)
        run_id = self.tokens[idempotency_token]
        return Wait(lambda **_: None, response=RunNowResponse.from_dict({"run_id": run_id}), run_id=run_id)

    def get_run(self, run_id: int) -> Run:
        script = self.scripts[int(run_id)]
        lifecycle, result = script.pop(0) if len(script) > 1 else script[0]
        state: dict[str, str] = {"life_cycle_state": lifecycle}
        if result:
            state["result_state"] = result
        return Run.from_dict({"run_id": int(run_id), "job_id": self.run_jobs.get(int(run_id)), "state": state})

    def list_runs(self, *, job_id: int, active_only: bool) -> Iterator[Run]:
        assert active_only
        for run_id in self.active.get(int(job_id), []):
            yield Run.from_dict({"run_id": run_id, "state": {"life_cycle_state": "RUNNING"}})


class FakePipelines:
    def __init__(self, spec: dict[str, Any] | None = None) -> None:
        self.spec = spec if spec is not None else {"name": "mip_feature_pipeline", "deployment": _deployment()}

    def get(self, pipeline_id: str) -> GetPipelineResponse:
        if pipeline_id != PIPELINE_ID:
            raise NotFound("pipeline")
        return GetPipelineResponse.from_dict({"pipeline_id": pipeline_id, "spec": self.spec})


class DataPhaseEstate:
    """A workspace client: files (the bundle state and the ledger), jobs and pipelines."""

    def __init__(self, *, document: dict[str, Any] | None = None) -> None:
        self.workspace = FakeFiles()
        self.jobs = FakeJobs(job_settings())
        self.pipelines = FakePipelines()
        self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name=DEPLOYER))
        state = document if document is not None else state_document()
        self.workspace.data[api_path(bundle_state.state_path(deployer=DEPLOYER, client_key=CLIENT))] = json.dumps(
            state
        ).encode("utf-8")

    def rebind(self, key: str, job_id: int) -> None:
        """The job is recreated under ``job_id`` with the same settings, and the bundle state binds it."""

        self.jobs.ids[key] = job_id
        self.workspace.data[api_path(bundle_state.state_path(deployer=DEPLOYER, client_key=CLIENT))] = json.dumps(
            state_document(jobs=self.jobs.ids)
        ).encode("utf-8")

    def ledger_bytes(self) -> bytes | None:
        from tools.databricks.job_phase_ledger import ledger_path

        return self.workspace.data.get(ledger_path(APP_NAME))

    def put_ledger(self, value: dict[str, Any]) -> None:
        from tools.databricks.job_phase_ledger import ledger_path

        self.workspace.upload(ledger_path(APP_NAME), io.BytesIO(json.dumps(value).encode("utf-8")), overwrite=True)
