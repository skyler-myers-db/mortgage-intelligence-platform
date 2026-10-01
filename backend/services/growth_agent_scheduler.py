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

The job is never listed or run from here (it is not a managed job), and the
answer is cached per process: 300 s for a resolved state, 60 s for a failed
lookup, with one lookup in flight at a time.
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

WorkspaceFactory = Callable[[], Any]

_lock = threading.Lock()
_cached: tuple[float, GrowthAgentSchedulerStatus] | None = None


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


def growth_agent_scheduler_status(
    *, workspace_factory: WorkspaceFactory | None = None
) -> GrowthAgentSchedulerStatus:
    """The scheduler's state, from the process cache when it is still fresh."""

    global _cached
    with _lock:
        now = time.monotonic()
        if _cached is not None and now < _cached[0]:
            return _cached[1]
        status = _lookup(workspace_factory or _bounded_workspace_client)
        ttl = LOOKUP_FAILED_TTL_S if status.reason == "lookup_failed" else RESOLVED_TTL_S
        _cached = (time.monotonic() + ttl, status)
        return status


def reset_growth_agent_scheduler_cache() -> None:
    """Forget the cached state (tests)."""

    global _cached
    with _lock:
        _cached = None


__all__ = [
    "HTTP_TIMEOUT_S",
    "LOOKUP_FAILED_TTL_S",
    "RESOLVED_TTL_S",
    "RETRY_TIMEOUT_S",
    "growth_agent_scheduler_status",
    "reset_growth_agent_scheduler_cache",
]
