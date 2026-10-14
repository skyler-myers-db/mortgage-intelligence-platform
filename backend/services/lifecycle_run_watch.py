"""Request-triggered watch over job-mode lifecycle sync runs (delivery-06).

The workflow counts (approved, in outreach) are cached under a key that moves
with ``gold_cache.bump_workflow_generation``. A warehouse-mode lifecycle sync
bumps it when its MERGE returns; a job-mode sync (``MIP_LIFECYCLE_SYNC_MODE=
job``, or the recovery submit after a warehouse failure) only gets a run id
back from ``run_now`` and finishes minutes later in the Jobs service. Without
this watch its mirror change reached the cached counts only when their soft
TTL ran out, plus one stale serve.

How it works, with no timer, loop or sleep:

* ``job_trigger`` calls :func:`note_submitted` after a successful ``run_now``.
  The run (and the workspace client that submitted it) becomes pending, and
  :func:`observe` is registered as a ``gold_cache`` generation observer.
* Every ``workflow_key`` build (a request reading workflow counts) calls
  :func:`observe` on the request thread. It never blocks and never raises: it
  expires runs pending for over an hour (WARNING, no bump) and, when no check
  is in flight, hands the runs not checked in the last 15 s to ONE task on a
  single-worker ``mip-lifecycle-watch`` executor.
* The task calls ``jobs.get_run`` per due run. A terminal life-cycle state
  (``TERMINATED``, ``SKIPPED``, ``INTERNAL_ERROR``) drops the run and bumps the
  generation, whatever the result state: a failed run may already have merged
  its first task, a spurious bump costs one re-read of the counts, and a
  missed bump is the defect. Anything else keeps the run pending; an error
  keeps it pending and logs the exception type only.

Cost: with nobody reading workflow counts, zero Jobs API calls; otherwise at
most one ``get_run`` per pending run per 15 s. No new grant: the App's service
principal already holds CAN_MANAGE_RUN on ``mip_sync_lifecycle_state``.

Scope: the process that submitted a run observes it through ``get_run``.
Runs this process did NOT submit (the job's schedule, which ships PAUSED, if
an operator unpauses it; a Jobs-UI run; another App process's submit) are
observed by the foreign arm (W5c, delivery-06 remainder), which
:func:`enable_foreign_run_watch` turns on at startup when the App has the
job id bound (``MIP_LIFECYCLE_SYNC_JOB_ID``; there is no name lookup on this
read path). While workflow counts are read, at most once a minute per
process, ONE ``jobs.list_runs(job_id=..., completed_only=True, limit=5)``
page is read on the same executor: a run that ended after the baseline and
is not one this process submitted (those are the pending arm's) bumps the
generation once and moves the baseline. Zero Jobs calls while nobody reads
workflow counts; CAN_MANAGE_RUN already covers the read. The tasks bind no
request context (no correlation id) and write no audit row.
"""
from __future__ import annotations

import atexit
import contextlib
import itertools
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any

from backend.services.databricks_jobs import TERMINAL_LIFECYCLE_STATES, describe_run
from backend.services.gold_cache import (
    bump_workflow_generation,
    register_generation_observer,
    unregister_generation_observer,
)
from backend.services.observability import emit

log = logging.getLogger(__name__)

WATCH_THREAD_PREFIX = "mip-lifecycle-watch"
_CHECK_INTERVAL_S = 15.0
_PENDING_WINDOW_S = 3600.0
_MAX_PENDING = 8
_FOREIGN_INTERVAL_S = 60.0
_FOREIGN_PAGE = 5
_MAX_SUBMITTED = 32


@dataclass
class _PendingRun:
    job_id: int
    run_id: int
    workspace: Any
    submitted_at: float
    checked_at: float = -math.inf


_LOCK = Lock()
_PENDING: OrderedDict[int, _PendingRun] = OrderedDict()
_CHECK_IN_FLIGHT = False
# Moves on every reset, so a task started before a reset leaves no trace.
_EPOCH = 0
_OBSERVING = False
_CLOCK: Callable[[], float] = time.monotonic
_EXECUTOR_OVERRIDE: Executor | None = None
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = Lock()


@dataclass
class _ForeignWatch:
    """The foreign arm: the bound job and the newest end time already counted."""

    job_id: int
    baseline_end_ms: int
    checked_at: float = -math.inf
    in_flight: bool = False


_FOREIGN: _ForeignWatch | None = None
#: Run ids this process submitted (the pending arm bumps those), newest last.
_SUBMITTED: OrderedDict[int, None] = OrderedDict()
_WALL_MS: Callable[[], int] = lambda: int(time.time() * 1000)  # noqa: E731 -- injectable clock
_WORKSPACE_FACTORY: Callable[[], Any] | None = None
_FOREIGN_WORKSPACE: Any = None


def _default_workspace() -> Any:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _foreign_workspace() -> Any:
    """The lazily built, cached client of the foreign arm (executor thread only)."""
    global _FOREIGN_WORKSPACE
    if _FOREIGN_WORKSPACE is None:
        _FOREIGN_WORKSPACE = (_WORKSPACE_FACTORY or _default_workspace)()
    return _FOREIGN_WORKSPACE


def _get_executor() -> Executor:
    global _EXECUTOR
    if _EXECUTOR_OVERRIDE is not None:
        return _EXECUTOR_OVERRIDE
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix=WATCH_THREAD_PREFIX)
        return _EXECUTOR


def _shutdown_executor() -> None:
    """Atexit hook mirroring gold_cache's: drop a queued check."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        pool = _EXECUTOR
        _EXECUTOR = None
    if pool is not None:
        with contextlib.suppress(Exception):
            pool.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_executor)


def note_submitted(
    workspace: Any, *, job_id: int, run_id: int | None, now: float | None = None
) -> None:
    """Start watching a lifecycle run the App just submitted."""
    global _OBSERVING
    if run_id is None:
        emit(log, "lifecycle_job_unobservable", level=logging.WARNING, job_id=job_id)
        return
    dropped: list[_PendingRun] = []
    with _LOCK:
        at = now if now is not None else _CLOCK()
        _PENDING[run_id] = _PendingRun(job_id=job_id, run_id=run_id, workspace=workspace, submitted_at=at)
        _PENDING.move_to_end(run_id)
        _SUBMITTED[run_id] = None
        _SUBMITTED.move_to_end(run_id)
        while len(_SUBMITTED) > _MAX_SUBMITTED:
            _SUBMITTED.popitem(last=False)
        while len(_PENDING) > _MAX_PENDING:
            dropped.append(_PENDING.popitem(last=False)[1])
        register = not _OBSERVING
        _OBSERVING = True
    for run in dropped:
        emit(log, "lifecycle_job_watch_dropped", level=logging.DEBUG, job_id=run.job_id, run_id=run.run_id)
    if register:
        register_generation_observer(observe)


def enable_foreign_run_watch(*, now_ms: int | None = None) -> None:
    """Observe lifecycle runs this process did not submit (called once at startup)."""
    global _FOREIGN, _OBSERVING
    from backend.services import job_trigger

    job_id = job_trigger._job_id_from_env()
    if job_id is None:
        emit(log, "lifecycle_foreign_watch_unbound")
        return
    with _LOCK:
        _FOREIGN = _ForeignWatch(job_id=job_id, baseline_end_ms=now_ms if now_ms is not None else _WALL_MS())
        _OBSERVING = True
    register_generation_observer(observe)


def observe(*, now: float | None = None, executor: Executor | None = None) -> None:
    """Schedule a check of the due pending runs. Never blocks, never raises."""
    try:
        _observe(now, executor)
        _observe_foreign(now, executor)
    except Exception as exc:  # noqa: BLE001 -- a read never fails on the watch
        emit(log, "lifecycle_job_watch_error", level=logging.WARNING, exc_type=type(exc).__name__)


def _observe_foreign(now: float | None, executor: Executor | None) -> None:
    """At most one list_runs per minute per process, never two at once."""
    with _LOCK:
        foreign = _FOREIGN
        if foreign is None or foreign.in_flight:
            return
        at = now if now is not None else _CLOCK()
        if at - foreign.checked_at < _FOREIGN_INTERVAL_S:
            return
        foreign.checked_at = at
        foreign.in_flight = True
        epoch = _EPOCH
    try:
        (executor or _get_executor()).submit(_check_foreign, foreign.job_id, epoch)
    except RuntimeError:
        # Interpreter shutdown: nothing will run the check.
        _finish_foreign(epoch)


def _finish_foreign(epoch: int) -> None:
    with _LOCK:
        if epoch == _EPOCH and _FOREIGN is not None:
            _FOREIGN.in_flight = False


def _check_foreign(job_id: int, epoch: int) -> None:
    try:
        runs = _foreign_workspace().jobs.list_runs(job_id=job_id, completed_only=True, limit=_FOREIGN_PAGE)
        page = list(itertools.islice(runs, _FOREIGN_PAGE))
        with _LOCK:
            if epoch != _EPOCH or _FOREIGN is None:
                return
            newer = [
                run
                for run in page
                if isinstance(getattr(run, "end_time", None), int)
                and run.end_time > _FOREIGN.baseline_end_ms
                and getattr(run, "run_id", None) not in _SUBMITTED
            ]
            if not newer:
                return
            newest = max(newer, key=lambda run: run.end_time)
            _FOREIGN.baseline_end_ms = newest.end_time
        bump_workflow_generation()
        emit(
            log,
            "lifecycle_sync_completed",
            mode="job_foreign",
            job_id=job_id,
            run_id=newest.run_id,
            result_state=describe_run(newest).result_state,
        )
    except Exception as exc:  # noqa: BLE001 -- the baseline stays; the next minute retries
        emit(log, "lifecycle_job_watch_error", level=logging.WARNING, exc_type=type(exc).__name__)
    finally:
        _finish_foreign(epoch)


def _observe(now: float | None, executor: Executor | None) -> None:
    global _CHECK_IN_FLIGHT
    expired: list[_PendingRun] = []
    due: list[_PendingRun] = []
    with _LOCK:
        if not _PENDING:
            return
        at = now if now is not None else _CLOCK()
        for run_id, run in list(_PENDING.items()):
            if at - run.submitted_at >= _PENDING_WINDOW_S:
                expired.append(_PENDING.pop(run_id))
        if not _CHECK_IN_FLIGHT:
            due = [run for run in _PENDING.values() if at - run.checked_at >= _CHECK_INTERVAL_S]
            for run in due:
                run.checked_at = at
            _CHECK_IN_FLIGHT = bool(due)
        epoch = _EPOCH
    for run in expired:
        emit(log, "lifecycle_job_watch_expired", level=logging.WARNING, job_id=run.job_id, run_id=run.run_id)
    if not due:
        return
    try:
        (executor or _get_executor()).submit(_check, due, epoch)
    except RuntimeError:
        # Interpreter shutdown: nothing will run the check.
        _finish_check(epoch)


def _finish_check(epoch: int) -> None:
    global _CHECK_IN_FLIGHT
    with _LOCK:
        if epoch == _EPOCH:
            _CHECK_IN_FLIGHT = False


def _check(runs: list[_PendingRun], epoch: int) -> None:
    try:
        for run in runs:
            try:
                state = describe_run(run.workspace.jobs.get_run(run_id=run.run_id))
            except Exception as exc:  # noqa: BLE001 -- the run stays pending
                emit(log, "lifecycle_job_watch_error", level=logging.WARNING, exc_type=type(exc).__name__)
                continue
            if (state.life_cycle_state or "") not in TERMINAL_LIFECYCLE_STATES:
                continue
            with _LOCK:
                finished = epoch == _EPOCH and _PENDING.pop(run.run_id, None) is not None
            if not finished:
                continue
            generation = bump_workflow_generation()
            emit(
                log,
                "lifecycle_sync_completed",
                mode="job",
                job_id=run.job_id,
                run_id=run.run_id,
                result_state=state.result_state,
                generation=generation,
            )
    finally:
        _finish_check(epoch)


def pending_run_ids() -> list[int]:
    """The run ids still being watched, oldest first (diagnostics and tests)."""
    with _LOCK:
        return list(_PENDING)


def _reset_for_tests(
    *,
    now: Callable[[], float] | None = None,
    executor: Executor | None = None,
    workspace_factory: Callable[[], Any] | None = None,
) -> None:
    """Forget every pending run and the foreign arm, unregister the observer, drop the executor.

    ``now`` / ``executor`` install a test clock and executor for the calls
    that arrive through ``gold_cache.workflow_key`` (which passes no overrides);
    ``workspace_factory`` builds the foreign arm's client.
    """
    global _CHECK_IN_FLIGHT, _EPOCH, _OBSERVING, _CLOCK, _EXECUTOR_OVERRIDE
    global _FOREIGN, _WORKSPACE_FACTORY, _FOREIGN_WORKSPACE
    with _LOCK:
        _PENDING.clear()
        _SUBMITTED.clear()
        _CHECK_IN_FLIGHT = False
        _EPOCH += 1
        _OBSERVING = False
        _CLOCK = now if now is not None else time.monotonic
        _EXECUTOR_OVERRIDE = executor
        _FOREIGN = None
        _WORKSPACE_FACTORY = workspace_factory
        _FOREIGN_WORKSPACE = None
    unregister_generation_observer(observe)
    _shutdown_executor()


__all__ = ["WATCH_THREAD_PREFIX", "enable_foreign_run_watch", "note_submitted", "observe", "pending_run_ids"]
