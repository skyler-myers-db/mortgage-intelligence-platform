"""Fire-and-forget Databricks Jobs trigger for lifecycle sync.

Module 0 runs ``mip_sync_lifecycle_state`` on a **daily** fallback cron
(04:00 America/Chicago) plus on-demand event triggers from the backend
approval path. The on-demand path is what this module provides.

Shutdown-drain caveat
---------------------

FastAPI ``BackgroundTasks`` does NOT support shutdown drain -- if the
process receives SIGTERM between the ``return`` of the approval handler
and the background task actually running, the scheduled call is
silently dropped. We deliberately accept this:

1. Every enqueue emits ``event=lifecycle_trigger_enqueued`` via
   :func:`enqueue_lifecycle_trigger` so dropped invocations leave an
   audit breadcrumb (the approval_id in Lakebase correlates with the
   absence of a later ``job_trigger_fired`` log).
2. The daily 04:00 America/Chicago cron declared in
   ``jobs/mip_sync_lifecycle_state.yml`` is the authoritative safety
   net. At worst a dropped trigger means the gold lifecycle table is
   stale until next 04:00 -- the metric views still recover, and the
   UI's degraded-state semantics are unaffected.

A proper drain would require moving to starlette's lifespan-managed
task queue or an external queue (RQ/Celery); both are materially
heavier than the daily cron that already exists. Re-evaluate only if
dropped triggers are observed in production telemetry.

Why event-triggered, not hourly cron
------------------------------------

The lifecycle sync mirrors Lakebase approvals + outreach rows into
``mip.gold.borrower_lifecycle_state`` so UC metric views (segment +
lead_generation) resolve ``approval_rate`` / ``outreach_rate`` without
a federated runtime join. Data only changes when an operator approves
or dispatches outreach — so an hourly cron against an idle workspace
was burning Serverless compute for nothing (observed 2026-04-22).

Commercial posture
------------------

MIP is a product we sell to mortgage lenders. Scheduled Serverless jobs
the customer doesn't need are a packaging bug — the customer's cost
line should only reflect real activity. Event-trigger + daily fallback
gives a tight freshness SLA without baseline cost.

Authority + safety model
------------------------

* **Never blocks the approval response.** We fire the trigger as a
  FastAPI ``BackgroundTasks`` coroutine AFTER the Lakebase approval row
  has been committed.
* **Swallows failures.** A broken trigger never fails an approval. The
  daily cron is the safety net.
* **Debounces.** A module-level ``_last_trigger_at`` timestamp keeps
  clustered approvals (same-minute bursts) from triggering multiple
  redundant sync runs. Default window: 60 s.

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
import threading
import time
from typing import Any

from backend.services.observability import emit

_log = logging.getLogger("mip.job_trigger")

# Name of the lifecycle-sync job declared in ``databricks.yml`` under
# ``resources.jobs.mip_sync_lifecycle_state``. Resolved to an integer
# job id at trigger time via ``WorkspaceClient.jobs.list(name=...)``.
_LIFECYCLE_JOB_NAME = "mip_sync_lifecycle_state"

# Debounce window. Multiple approvals inside this window coalesce into
# a single ``jobs.run_now`` call. 60 s is long enough to swallow a
# reviewer clicking through a queue; short enough that a single lagging
# approval still gets its own freshness pass.
_DEBOUNCE_SECONDS = 60.0

# Module-level state (guarded by ``_STATE_LOCK`` -- FastAPI may call
# ``trigger_lifecycle_sync`` from multiple request threads concurrently).
# ``_last_trigger_at`` sentinel is ``-inf`` so the first approval after
# process start always fires, regardless of ``time.monotonic()`` epoch.
_STATE_LOCK = threading.Lock()
_last_trigger_at: float = float("-inf")
# Cached job id + the monotonic timestamp at which it was resolved. Must
# expire after ``_JOB_ID_TTL_SECONDS``: if an operator redeploys the
# bundle (new job_id) or rotates SDK OAuth between process starts,
# ``run_now`` would otherwise hit a stale id forever and the error path
# would be swallowed silently. 15 min matches the default bundle
# redeploy cadence and is short enough that a manual redeploy self-heals
# within one debounce cycle.
_JOB_ID_TTL_SECONDS = 15 * 60.0
_cached_job_id: int | None = None
_cached_job_id_at: float = float("-inf")


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
    if (
        _cached_job_id is not None
        and (now - _cached_job_id_at) < _JOB_ID_TTL_SECONDS
    ):
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


def _should_trigger(now: float) -> bool:
    """Return True iff we're outside the debounce window.

    Called under ``_STATE_LOCK``. Updates ``_last_trigger_at`` as a side
    effect iff the answer is True, so callers can race without double-
    firing.
    """
    global _last_trigger_at
    if now - _last_trigger_at < _DEBOUNCE_SECONDS:
        return False
    _last_trigger_at = now
    return True


def trigger_lifecycle_sync(*, reason: str = "approval") -> None:
    """Fire ``mip_sync_lifecycle_state`` non-blocking.

    Safe to call from any FastAPI handler via ``BackgroundTasks``:
    never raises, never blocks on the job run, swallows every error
    class. The daily fallback cron is the safety net for any call
    that drops.

    ``reason`` is stamped into the structured log line so an operator
    grepping ``job_trigger_fired`` sees which endpoint initiated the
    run (approval, outreach dispatch, etc).
    """
    now = time.monotonic()
    with _STATE_LOCK:
        if not _should_trigger(now):
            emit(
                _log,
                "job_trigger_debounced",
                job_name=_LIFECYCLE_JOB_NAME,
                reason=reason,
                debounce_seconds=_DEBOUNCE_SECONDS,
            )
            return

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
            return
        # ``run_now`` is non-blocking -- returns a ``RunNowResponse`` with
        # a ``run_id`` once the workspace has accepted the request. We
        # don't wait on completion; the daily cron is the safety net.
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


def enqueue_lifecycle_trigger(background: Any, *, reason: str = "approval") -> None:
    """Schedule ``trigger_lifecycle_sync`` on a FastAPI BackgroundTasks.

    Emits ``event=lifecycle_trigger_enqueued`` *before* scheduling so
    every enqueue is traceable even when SIGTERM drops the scheduled
    task between response commit and execution. See the module
    docstring for the shutdown-drain caveat.

    ``background`` is typed as ``Any`` to avoid pulling the FastAPI
    import into services/; routers already hold a ``BackgroundTasks``
    and pass it straight through.
    """
    emit(
        _log,
        "lifecycle_trigger_enqueued",
        job_name=_LIFECYCLE_JOB_NAME,
        reason=reason,
    )
    background.add_task(trigger_lifecycle_sync, reason=reason)


# ---------------------------------------------------------------------------
# Test helpers -- not part of the production surface. Tests reset state
# to ensure the debounce doesn't leak across test cases.
# ---------------------------------------------------------------------------


def _reset_for_tests() -> None:
    """Drop cached state. Called by unit-test fixtures only."""
    global _last_trigger_at, _cached_job_id, _cached_job_id_at
    with _STATE_LOCK:
        _last_trigger_at = float("-inf")
        _cached_job_id = None
        _cached_job_id_at = float("-inf")
