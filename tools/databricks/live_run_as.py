"""The identity a job or pipeline runs as, read live by id (spike S4).

Configuration is not proof. ``bundle summary`` reports the run_as the bundle
declares, and the direct-engine plan cannot see a pipeline's run_as at all
(``run_as.*`` plans ``skip, reason: input_only``): spike S4 changed a bundle
pipeline's run_as out of band, planned ``skip`` and deployed, and the pipeline
kept the drifted identity. Only a live read shows what runs.

The live identity is the effective ``run_as_user_name`` that ``jobs.get`` and
``pipelines.get`` both answer (spike S4 read it on each: an SP's application
id, or a user's email). A pipeline's ``spec`` never carries run_as, and the
SDK documents both ``run_as`` settings as write-only (a job's came back on
the spike's reads, but nothing promises it), so neither setting is read here.
A blank answer is unreadable, never "the creator": the caller refuses it.

Read-only: one GET per object. A not-found answers blank as well (the object
the summary names is gone); any other error propagates.
"""

from __future__ import annotations

from typing import Any

from databricks.sdk.errors import NotFound

KINDS = ("jobs", "pipelines")


def _effective(detail: object) -> str:
    return str(getattr(detail, "run_as_user_name", "") or "").strip()


def job_run_as(workspace: Any, job_id: str) -> str:
    """The job's live ``run_as_user_name``; ``""`` when it is blank or the job is gone."""

    if not str(job_id).isdigit():
        raise ValueError("a job id is a decimal number")
    try:
        return _effective(workspace.jobs.get(job_id=int(job_id)))
    except NotFound:
        return ""


def pipeline_run_as(workspace: Any, pipeline_id: str) -> str:
    """The pipeline's live ``run_as_user_name``; ``""`` when it is blank or the pipeline is gone."""

    if not str(pipeline_id).strip():
        raise ValueError("a pipeline id is required")
    try:
        return _effective(workspace.pipelines.get(pipeline_id=str(pipeline_id).strip()))
    except NotFound:
        return ""


def run_as(workspace: Any, kind: str, live_id: str) -> str:
    """``job_run_as`` or ``pipeline_run_as`` by bundle kind."""

    if kind == "jobs":
        return job_run_as(workspace, live_id)
    if kind == "pipelines":
        return pipeline_run_as(workspace, live_id)
    raise ValueError(f"no live run_as read for kind {kind}")


def same_identity(left: str, right: str) -> bool:
    """One principal: an application id or a workspace user name, compared without case."""

    return bool(left.strip()) and left.strip().casefold() == right.strip().casefold()


__all__ = ["KINDS", "job_run_as", "pipeline_run_as", "run_as", "same_identity"]
