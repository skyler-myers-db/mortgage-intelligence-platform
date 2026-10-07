"""Whether the saved-watchlist scheduler is running (audit 2026-09-21 flow-08 / wow-ai-4).

The bundle job ``mip_growth_agent_monitor_scheduler`` ships with its schedule
PAUSED (cost control), and the UI used to hard-code "scheduler paused". This
module reads the real state, read-only, through the Jobs API:

- ``active`` / ``job_schedule``: the job's schedule (or trigger) is UNPAUSED;
- ``paused`` / ``job_schedule``: it is PAUSED;
- ``paused`` / ``no_schedule``: the job has no schedule or trigger at all;
- ``unavailable`` / ``not_configured``: the App has no job-id binding
  (``MIP_GROWTH_AGENT_SCHEDULER_JOB_ID``), so no WorkspaceClient is built
  (a local operator with a configured workspace may resolve it by name);
- ``unavailable`` / ``lookup_failed``: anything went wrong, including a
  binding that names a different job. Logged with the exception type only.

The job is never listed or run from here (it is not a managed job; the App
binds it CAN_VIEW). The answer is cached per process, 300 s for a resolved
state and 60 s for a failed lookup, and served stale-while-refresh so a slow
Jobs API never holds a page (W5d):

- fresh cache: returned as is;
- stale cache: the stale value is returned at once and at most ONE background
  refresh starts (an in-flight flag under ``_lock``);
- cold cache (the first read after process start): one bounded synchronous
  lookup, single-flight: concurrent cold callers wait on that lookup and
  never issue a second ``jobs.get``.

``_lock`` only guards the cache and the in-flight state; it is never held
during a Jobs API call.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from backend.config.settings import is_placeholder_databricks_config, settings
from backend.schemas.growth_agent_watchlist import GrowthAgentSchedulerStatus
from backend.services.databricks_jobs import (
    GROWTH_AGENT_SCHEDULER_JOB,
    job_name_lookup_allowed,
    read_only_job_bound,
    read_schedule_pause_status,
    resolve_read_only_job_id,
)
from backend.services.observability import emit

log = logging.getLogger(__name__)

RESOLVED_TTL_S = 300.0
LOOKUP_FAILED_TTL_S = 60.0
# Bounded SDK client: a slow Jobs API must not hold a page render.
HTTP_TIMEOUT_S = 3
RETRY_TIMEOUT_S = 5

# A cold caller that follows another cold caller's lookup waits at most this
# long (the lookup itself is bounded by the client timeouts above).
COLD_WAIT_S = 15.0

WorkspaceFactory = Callable[[], Any]
# Starts a background refresh; injectable so tests can hold or inline it.
RefreshRunner = Callable[[Callable[[], None]], None]

_lock = threading.Lock()
_cached: tuple[float, GrowthAgentSchedulerStatus] | None = None
_refreshing = False
_cold_lookup: threading.Event | None = None
# Bumped by reset: a lookup started before a reset never stores its answer.
_generation = 0
_LOOKUP_FAILED = GrowthAgentSchedulerStatus(state="unavailable", reason="lookup_failed")


def _bounded_workspace_client() -> Any:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    return WorkspaceClient(
        config=Config(http_timeout_seconds=HTTP_TIMEOUT_S, retry_timeout_seconds=RETRY_TIMEOUT_S)
    )


def _needs_client() -> bool:
    """A client is built only to read a bound job, or for the local name lookup.

    The name lookup (``APP_ENV=local`` only, like the managed jobs) also needs
    a real configured workspace host, so a hermetic test or a bare checkout
    never builds a client or reaches the network.
    """

    if read_only_job_bound(GROWTH_AGENT_SCHEDULER_JOB):
        return True
    host = (settings.databricks_host or "").strip()
    return job_name_lookup_allowed() and bool(host) and not is_placeholder_databricks_config(host=host)


def _lookup(workspace_factory: WorkspaceFactory) -> GrowthAgentSchedulerStatus:
    if not _needs_client():
        return GrowthAgentSchedulerStatus(state="unavailable", reason="not_configured")
    try:
        workspace = workspace_factory()
        job_id = resolve_read_only_job_id(workspace, GROWTH_AGENT_SCHEDULER_JOB)
        if job_id is None:
            return GrowthAgentSchedulerStatus(state="unavailable", reason="not_configured")
        pause_status = read_schedule_pause_status(workspace, job_id, GROWTH_AGENT_SCHEDULER_JOB)
    except Exception as exc:  # noqa: BLE001 - any failure is an honest "unavailable"
        emit(
            log,
            "growth_agent_scheduler_state_unavailable",
            level=logging.WARNING,
            exc_type=type(exc).__name__,
        )
        return GrowthAgentSchedulerStatus(state="unavailable", reason="lookup_failed")
    if pause_status == "UNPAUSED":
        return GrowthAgentSchedulerStatus(state="active", reason="job_schedule")
    if pause_status == "PAUSED":
        return GrowthAgentSchedulerStatus(state="paused", reason="job_schedule")
    return GrowthAgentSchedulerStatus(state="paused", reason="no_schedule")


def _thread_runner(refresh: Callable[[], None]) -> None:
    threading.Thread(target=refresh, name="growth-agent-scheduler-refresh", daemon=True).start()


def _store(status: GrowthAgentSchedulerStatus) -> None:
    """Cache ``status`` with its TTL; the caller holds ``_lock``."""

    global _cached
    ttl = LOOKUP_FAILED_TTL_S if status.reason == "lookup_failed" else RESOLVED_TTL_S
    _cached = (time.monotonic() + ttl, status)


def _refresh(workspace_factory: WorkspaceFactory, generation: int) -> None:
    """The one background refresh of a stale answer."""

    global _refreshing
    status = _LOOKUP_FAILED
    try:
        status = _lookup(workspace_factory)
    finally:
        with _lock:
            if generation == _generation:
                _store(status)
                _refreshing = False


def growth_agent_scheduler_status(
    *,
    workspace_factory: WorkspaceFactory | None = None,
    refresh_runner: RefreshRunner | None = None,
) -> GrowthAgentSchedulerStatus:
    """The scheduler's state: fresh from cache, stale while one refresh runs, or one cold lookup."""

    global _refreshing, _cold_lookup
    factory = workspace_factory or _bounded_workspace_client
    start_refresh = False
    follow: threading.Event | None = None
    with _lock:
        generation = _generation
        cached = _cached
        if cached is not None:
            if time.monotonic() < cached[0]:
                return cached[1]
            start_refresh = not _refreshing
            _refreshing = True
        else:
            follow = _cold_lookup
            if follow is None:
                _cold_lookup = lead = threading.Event()
    if cached is not None:
        if start_refresh:
            (refresh_runner or _thread_runner)(lambda: _refresh(factory, generation))
        return cached[1]
    if follow is not None:
        follow.wait(COLD_WAIT_S)
        with _lock:
            return _cached[1] if _cached is not None else _LOOKUP_FAILED
    status = _LOOKUP_FAILED
    try:
        status = _lookup(factory)
    finally:
        with _lock:
            if generation == _generation:
                _store(status)
                _cold_lookup = None
        lead.set()
    return status


def reset_growth_agent_scheduler_cache() -> None:
    """Forget the cached state and any lookup in flight (tests)."""

    global _cached, _refreshing, _cold_lookup, _generation
    with _lock:
        _cached = None
        _refreshing = False
        if _cold_lookup is not None:
            _cold_lookup.set()
        _cold_lookup = None
        _generation += 1


__all__ = [
    "COLD_WAIT_S",
    "HTTP_TIMEOUT_S",
    "LOOKUP_FAILED_TTL_S",
    "RESOLVED_TTL_S",
    "RETRY_TIMEOUT_S",
    "growth_agent_scheduler_status",
    "reset_growth_agent_scheduler_cache",
]
