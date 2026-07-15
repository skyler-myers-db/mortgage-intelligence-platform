"""Warehouse-first lifecycle sync with durable Databricks Jobs recovery.

Module 0 mirrors lifecycle state after accepted approval/rejection writes and
through explicit Admin Data operations. The accepted Lakebase row is the
durable source of truth. The app first attempts a cheap sparse warehouse
MERGE; if that fails, it submits the bundle job so Databricks persists run
state and applies configured retries.

Shutdown-drain caveat
---------------------

FastAPI ``BackgroundTasks`` does not drain on SIGTERM. Correctness therefore
does not depend on the process-local task: approvals and call dispositions are
already committed in Lakebase before enqueue, and every later app/admin/job
sync re-reads that durable current state. A dropped task delays freshness but
does not lose the event. Warehouse failures additionally submit a Databricks
job whose run and retry history are workspace-durable.

Why event-triggered, not fixed-interval schedules
-------------------------------------------------

The lifecycle sync mirrors Lakebase approvals + outreach rows into
``mip.gold.borrower_lifecycle_state`` so UC metric views (segment +
lead_generation) resolve ``approval_rate`` / ``outreach_rate`` without
a federated runtime join. Data only changes when an operator approves or
dispatches outreach, so a fixed-interval schedule against an idle workspace
was burning Serverless compute for nothing (observed 2026-04-22). The normal
path uses the already-provisioned SQL warehouse; the serverless Python job is
recovery, not the per-click primary mechanism.

Commercial posture
------------------

MIP is a product we sell to mortgage lenders. Scheduled Serverless jobs
the customer doesn't need are a packaging bug — the customer's cost
line should only reflect real activity. Event-triggered sync and Admin
Data operations provide freshness without baseline scheduled compute.

Authority + safety model
------------------------

* **Never blocks the approval response.** We fire the trigger as a
  FastAPI ``BackgroundTasks`` coroutine AFTER the Lakebase approval row
  has been committed.
* **Durable retry on failure.** A broken warehouse attempt submits the bound
  lifecycle job. Databricks persists the run and retries the task; if job
  submission also fails, an ERROR event identifies the durable Lakebase source
  and the explicit repair command.
* **No process-local debounce.** Each accepted hook gets a merge attempt.
  Delta's changed-row predicate makes repeated/coincident calls idempotent
  without letting one app replica suppress another replica's work.

Public surface
--------------

* :func:`trigger_lifecycle_sync` -- fire-and-forget entry called from
  ``backend/api/outreach.py`` (and any future approval path). Never
  raises; emits a structured log line for every decision it made.
* :func:`enqueue_lifecycle_trigger` -- thin helper that (a) logs
  ``event=lifecycle_trigger_enqueued`` and (b) schedules the trigger
  on a FastAPI ``BackgroundTasks``. Use this from routers instead of
  calling ``background.add_task(trigger_lifecycle_sync, ...)`` directly
  so every enqueue is auditable.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from backend.services.observability import emit

_log = logging.getLogger("mip.job_trigger")

# Name of the lifecycle-sync job declared in ``databricks.yml`` under
# ``resources.jobs.mip_sync_lifecycle_state``. Resolved to an integer
# job id at trigger time via ``WorkspaceClient.jobs.list(name=...)``.
_LIFECYCLE_JOB_NAME = "mip_sync_lifecycle_state"

# Cached job id + the monotonic timestamp at which it was resolved. Must
# expire after ``_JOB_ID_TTL_SECONDS``: if an operator redeploys the
# bundle (new job_id) or rotates SDK OAuth between process starts,
# ``run_now`` would otherwise hit a stale id forever and the error path
# would be swallowed silently. 15 min matches the default bundle
# redeploy cadence and is short enough that a later recovery self-heals.
_JOB_ID_TTL_SECONDS = 15 * 60.0
_cached_job_id: int | None = None
_cached_job_id_at: float = float("-inf")


@dataclass(frozen=True)
class JobRetrySubmission:
    job_id: int
    run_id: int | None


def _resolve_job_id(workspace: Any) -> int | None:
    """Return the integer job_id for ``mip_sync_lifecycle_state``.

    Databricks Apps bind the lifecycle-sync job as an app resource and
    expose its id through ``MIP_LIFECYCLE_SYNC_JOB_ID``. That is the primary
    path because the app service principal may have CAN_MANAGE_RUN on this
    one job without workspace-wide job-list visibility. Local/dev fall back
    to name resolution below.

    On the bundle-deployed workspace the job name is the literal
    ``mip_sync_lifecycle_state``; under ``mode: development`` the CLI
    prefixes it with ``[dev <user>]`` so a simple name lookup must
    tolerate that. We list jobs that match the base name prefix and
    pick the first one owned by a ``mip_sync_lifecycle_state`` task
    surface. The list call is O(n_jobs_in_workspace) -- cached for
    ``_JOB_ID_TTL_SECONDS`` so subsequent triggers re-resolve on a
    bounded cadence. Cache is also invalidated by
    ``_invalidate_cached_job_id`` when ``run_now`` raises a terminal
    error (404 missing, 401 auth rotated, InvalidArgumentError).
    """
    global _cached_job_id, _cached_job_id_at
    now = time.monotonic()
    if _cached_job_id is not None and (now - _cached_job_id_at) < _JOB_ID_TTL_SECONDS:
        return _cached_job_id

    configured_job_id = _job_id_from_env()
    if configured_job_id is not None:
        _cached_job_id = configured_job_id
        _cached_job_id_at = now
        return _cached_job_id

    # ``jobs.list`` honours a ``name`` filter; for Development-mode
    # prefixed jobs we pass the base name and scan results for a match
    # that ends in the base name. The SDK returns ``BaseJob`` objects
    # with ``job_id`` + ``settings.name``.
    try:
        results = list(workspace.jobs.list(name=_LIFECYCLE_JOB_NAME))
    except Exception:
        # Name-filter may reject partial matches on some SDK versions;
        # fall back to a full list + client-side filter.
        try:
            results = list(workspace.jobs.list())
        except Exception:
            return None

    for job in results:
        settings = getattr(job, "settings", None)
        name = getattr(settings, "name", None) if settings is not None else None
        if name and (name == _LIFECYCLE_JOB_NAME or name.endswith(_LIFECYCLE_JOB_NAME)):
            job_id = getattr(job, "job_id", None)
            if job_id is not None:
                _cached_job_id = int(job_id)
                _cached_job_id_at = now
                return _cached_job_id
    return None


def _job_id_from_env() -> int | None:
    raw = os.environ.get("MIP_LIFECYCLE_SYNC_JOB_ID")
    if raw is None or not raw.strip():
        return None
    try:
        job_id = int(raw.strip())
    except ValueError:
        emit(
            _log,
            "job_trigger_config_invalid",
            level=logging.WARNING,
            job_name=_LIFECYCLE_JOB_NAME,
            env_var="MIP_LIFECYCLE_SYNC_JOB_ID",
        )
        return None
    return job_id if job_id > 0 else None


def _invalidate_cached_job_id() -> None:
    """Drop the cached job_id so the next trigger re-resolves by name.

    Called when ``run_now`` surfaces a terminal error that suggests the
    cached id is stale (job was deleted/recreated, OAuth rotated, SDK
    rejected the argument). Safe to call unlocked -- worst case we race
    with a resolver writing a fresh value and one legitimate id gets
    cleared; the next trigger resolves again cheaply.
    """
    global _cached_job_id, _cached_job_id_at
    _cached_job_id = None
    _cached_job_id_at = float("-inf")


def _looks_stale_error(exc: BaseException) -> bool:
    """True when the exception string / type smells like a stale cache.

    The databricks-sdk surfaces 404/401/InvalidArgumentError with
    specific classes that are not always importable without an optional
    dependency, so we match on class name + message substring. Narrow
    on purpose: a transient 5xx or network blip must NOT wipe the cache
    (that just forces a redundant list-jobs call on every retry).
    """
    name = type(exc).__name__
    if name in {"ResourceDoesNotExist", "NotFound", "PermissionDenied", "Unauthorized"}:
        return True
    if name == "InvalidArgumentError":
        return True
    msg = str(exc).lower()
    return any(needle in msg for needle in ("404", "401", "does not exist", "not found"))


def trigger_lifecycle_sync(*, reason: str = "approval") -> None:
    """Run the lifecycle mirror after an approval/reject response.

    The default mode applies a sparse MERGE through the already-provisioned
    SQL Warehouse. A warehouse error immediately submits the bound Databricks
    job, whose run state and retries are durable. Set
    ``MIP_LIFECYCLE_SYNC_MODE=job`` only to force that recovery path.

    ``reason`` is stamped into the structured log line so an operator
    grepping lifecycle sync events sees which endpoint initiated the
    run (approval, outreach dispatch, etc).
    """
    if os.environ.get("MIP_LIFECYCLE_SYNC_MODE", "warehouse").strip().lower() == "job":
        _trigger_lifecycle_sync_job(reason=reason)
        return

    try:
        from backend.services.lifecycle_sync import sync_lifecycle_state_via_warehouse

        result = sync_lifecycle_state_via_warehouse(
            record_funnel_snapshot=False,
            prune_legacy_defaults=False,
        )
        emit(
            _log,
            "lifecycle_sync_completed",
            mode="warehouse",
            reason=reason,
            lakebase_rows=result.lakebase_rows,
            mirrored_rows=result.mirrored_rows,
            funnel_snapshot_rows=result.funnel_snapshot_rows,
        )
    except Exception as exc:  # noqa: BLE001 -- approval is already durable
        emit(
            _log,
            "lifecycle_sync_error",
            level=logging.WARNING,
            mode="warehouse",
            reason=reason,
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )
        retry = _trigger_lifecycle_sync_job(reason=f"{reason}:warehouse_failure")
        if retry is None:
            emit(
                _log,
                "lifecycle_sync_retry_unavailable",
                level=logging.ERROR,
                mode="warehouse",
                reason=reason,
                durable_source="mip_app.approvals,mip_app.call_dispositions",
                repair_command="databricks bundle run mip_sync_lifecycle_state -t <target>",
            )
        else:
            emit(
                _log,
                "lifecycle_sync_retry_queued",
                mode="job",
                reason=reason,
                job_id=retry.job_id,
                run_id=retry.run_id,
            )


def _trigger_lifecycle_sync_job(*, reason: str = "approval") -> JobRetrySubmission | None:
    """Launch the durable Databricks lifecycle repair job.

    Kept as an explicit rollback mode and for the unit tests that pin
    bounded job-id caching. The default product path should not call this.
    """
    # Outside the lock: the WorkspaceClient constructor + network call
    # can be slow; we do NOT want to serialise unrelated trigger
    # callers behind each other.
    try:
        # Local import keeps the databricks-sdk wheel out of the hot
        # import path for callers that never trigger the job (unit
        # tests, config endpoints, etc.).
        from databricks.sdk import WorkspaceClient

        workspace = WorkspaceClient()
        job_id = _resolve_job_id(workspace)
        if job_id is None:
            emit(
                _log,
                "job_trigger_unresolved",
                level=logging.WARNING,
                job_name=_LIFECYCLE_JOB_NAME,
                reason=reason,
            )
            return None
        # ``run_now`` is non-blocking -- returns a ``RunNowResponse`` with
        # a ``run_id`` once the workspace has accepted the request. We
        # don't wait on completion; Admin Data operations is the operator
        # repair path if a fire-and-forget trigger drops.
        try:
            run = workspace.jobs.run_now(job_id=job_id)
        except Exception as run_exc:  # noqa: BLE001 -- swallowed below
            # If the cached id is stale (operator redeployed the job,
            # OAuth rotated, SDK rejected the arg) clear the cache so
            # the *next* trigger re-resolves by name. Transient 5xx
            # from run_now must not nuke a perfectly valid cached id.
            if _looks_stale_error(run_exc):
                _invalidate_cached_job_id()
            raise
        emit(
            _log,
            "job_trigger_fired",
            job_name=_LIFECYCLE_JOB_NAME,
            job_id=job_id,
            run_id=getattr(run, "run_id", None),
            reason=reason,
        )
        return JobRetrySubmission(
            job_id=job_id,
            run_id=(int(run.run_id) if getattr(run, "run_id", None) is not None else None),
        )
    except Exception as exc:  # noqa: BLE001 -- must never raise to caller
        emit(
            _log,
            "job_trigger_error",
            level=logging.WARNING,
            job_name=_LIFECYCLE_JOB_NAME,
            reason=reason,
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )
        return None


def enqueue_lifecycle_trigger(background: Any, *, reason: str = "approval") -> None:
    """Schedule ``trigger_lifecycle_sync`` on a FastAPI BackgroundTasks.

    Emits ``event=lifecycle_trigger_enqueued`` before scheduling. The approval
    or disposition row is already durable in Lakebase, so loss of this
    process-local task affects freshness only; a later run retries the state.

    ``background`` is typed as ``Any`` to avoid pulling the FastAPI
    import into services/; routers already hold a ``BackgroundTasks``
    and pass it straight through.
    """
    emit(
        _log,
        "lifecycle_trigger_enqueued",
        job_name=_LIFECYCLE_JOB_NAME,
        reason=reason,
        durable_source="lakebase",
    )
    background.add_task(trigger_lifecycle_sync, reason=reason)


# ---------------------------------------------------------------------------
# Test helpers -- not part of the production surface.
# ---------------------------------------------------------------------------


def _reset_for_tests() -> None:
    """Drop cached state. Called by unit-test fixtures only."""
    global _cached_job_id, _cached_job_id_at
    _cached_job_id = None
    _cached_job_id_at = float("-inf")
