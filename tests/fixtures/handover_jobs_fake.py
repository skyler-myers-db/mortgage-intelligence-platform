"""SDK-shaped fakes for the handover's read-only job and pipeline checks (M2-PR8).

Listings and reads are built with the SDK's own ``from_dict``, so a field the
real type does not carry (``BaseJob`` has no ``run_as_user_name``) is absent
here too. ``PagedRunsApi`` serves ``/api/2.2/jobs/runs/list`` the way the
workspace does, ``limit`` runs per page with a ``next_page_token``, behind the
real ``JobsAPI`` pager, and counts every request.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from databricks.sdk.service.jobs import BaseJob, Job, JobsAPI
from databricks.sdk.service.pipelines import GetPipelineResponse, PipelineStateInfo


def jobs_workspace(
    jobs: dict[int, dict[str, Any]],
    pipelines: list[dict[str, Any]] | None = None,
    pipeline_details: dict[str, dict[str, Any]] | None = None,
    *,
    me: str = "",
) -> SimpleNamespace:
    """``jobs``: job id -> the ``jobs.get`` body (``creator_user_name``, ``run_as_user_name``, ``settings``)."""

    reads: list[str] = []

    def get_job(job_id: int) -> Job:
        reads.append(f"jobs.get {job_id}")
        return Job.from_dict({"job_id": job_id, **jobs[job_id]})

    def list_jobs() -> list[BaseJob]:
        return [BaseJob.from_dict({"job_id": job_id, **body}) for job_id, body in jobs.items()]

    def get_pipeline(pipeline_id: str) -> GetPipelineResponse:
        reads.append(f"pipelines.get {pipeline_id}")
        return GetPipelineResponse.from_dict({"pipeline_id": pipeline_id, **(pipeline_details or {}).get(pipeline_id, {})})

    return SimpleNamespace(
        reads=reads,
        jobs=SimpleNamespace(list=list_jobs, get=get_job),
        pipelines=SimpleNamespace(
            list_pipelines=lambda: [PipelineStateInfo.from_dict(body) for body in pipelines or []],
            get=get_pipeline,
        ),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=me)),
    )


class PagedRunsApi:
    """An ``ApiClient`` stand-in for ``jobs/runs/list``: newest first, ``limit`` per page."""

    def __init__(self, runs: list[dict[str, Any]]) -> None:
        self.runs = runs
        self.requests = 0
        self._cfg = SimpleNamespace(workspace_id="")

    def do(self, method: str, path: str, query: dict[str, Any] | None = None, headers: Any = None) -> dict[str, Any]:
        assert (method, path) == ("GET", "/api/2.2/jobs/runs/list"), (method, path)
        self.requests += 1
        query = query or {}
        newest = sorted(self.runs, key=lambda run: -int(run["run_id"]))
        start = int(query.get("page_token") or 0)
        limit = int(query.get("limit") or 25)
        page = newest[start : start + limit]
        body: dict[str, Any] = {"runs": page}
        if start + limit < len(newest):
            body["next_page_token"] = str(start + limit)
        return body


def run(run_id: int, life_cycle_state: str = "TERMINATED", result_state: str = "SUCCESS") -> dict[str, Any]:
    state: dict[str, Any] = {"life_cycle_state": life_cycle_state}
    if result_state:
        state["result_state"] = result_state
    return {"run_id": run_id, "state": state}


def runs_workspace(api: PagedRunsApi) -> SimpleNamespace:
    return SimpleNamespace(jobs=JobsAPI(api))  # type: ignore[arg-type]


__all__ = ["PagedRunsApi", "jobs_workspace", "run", "runs_workspace"]
