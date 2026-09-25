"""The typical completion time of a Genie turn (audit 2026-09-21 ``genie-01``).

The status poll carries ``typical_seconds``: the median time from job
creation to the governed record (``recorded_at``) over the last 14 days'
recorded jobs of the same class, deep sweep or single turn, newest 200. Only
a job that delivered its answer (``succeeded``) is a sample: one that failed
or expired after its commit point never reached the user. It
is a hint, not a promise, so it is published only with at least
``MIN_SAMPLES`` samples, clamped to 1..3600 s, and cached per client and
class. The aggregate spans all actors and returns one number: no identity
leaves it, and reading it writes no audit row. A failure is never raised: it
is a throttled WARNING and no hint.
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from typing import Any

from backend.services.genie_completion_jobs import GenieCompletionJob
from backend.services.lakebase import LakebaseClient
from backend.services.observability import emit

log = logging.getLogger("mip-genie-jobs")

MIN_SAMPLES = 20
MAX_SAMPLES = 200
CACHE_TTL_S = 600.0
MISS_TTL_S = 60.0
_MIN_SECONDS = 1
_MAX_SECONDS = 3600
_WARNING_INTERVAL_S = 60.0

_DURATIONS_SQL = f"""
SELECT count(*) AS samples,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) AS median_s
  FROM (
        SELECT EXTRACT(EPOCH FROM recorded_at - created_at) AS seconds
          FROM mip_app.genie_completion_jobs
         WHERE recorded_at IS NOT NULL
           AND status = 'succeeded'
           AND deep = %(deep)s
           AND created_at > now() - interval '14 days'
         ORDER BY created_at DESC
         LIMIT {MAX_SAMPLES}
       ) AS recent
"""

_CACHE: weakref.WeakKeyDictionary[Any, dict[bool, tuple[float, int | None]]] = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()
_warned_at: float | None = None


def _cached(lakebase: LakebaseClient, deep: bool, now: float) -> tuple[bool, int | None]:
    try:
        with _LOCK:
            entry = _CACHE.get(lakebase, {}).get(deep)
    except TypeError:  # a client that cannot be weakly referenced: no cache
        return False, None
    if entry is not None and entry[0] > now:
        return True, entry[1]
    return False, None


def _store(lakebase: LakebaseClient, deep: bool, until: float, value: int | None) -> None:
    try:
        with _LOCK:
            _CACHE.setdefault(lakebase, {})[deep] = (until, value)
    except TypeError:
        return


def _warn(exc: Exception) -> None:
    global _warned_at
    now = time.monotonic()
    with _LOCK:
        if _warned_at is not None and now - _warned_at < _WARNING_INTERVAL_S:
            return
        _warned_at = now
    emit(
        log,
        "genie_job_durations_failed",
        level=logging.WARNING,
        dependency="lakebase",
        outcome="skipped",
        error_type=type(exc).__name__,
    )


def _median(row: dict[str, Any] | None) -> int | None:
    if row is None or int(row.get("samples") or 0) < MIN_SAMPLES or row.get("median_s") is None:
        return None
    return max(_MIN_SECONDS, min(_MAX_SECONDS, round(float(row["median_s"]))))


def typical_completion_seconds(lakebase: LakebaseClient, *, deep: bool) -> int | None:
    """The recent median completion seconds of one class, or None."""

    now = time.monotonic()
    hit, value = _cached(lakebase, deep, now)
    if hit:
        return value
    try:
        value = _median(lakebase.fetchone(_DURATIONS_SQL, {"deep": deep}))
    except Exception as exc:  # noqa: BLE001 - a hint never fails a status poll
        _warn(exc)
        _store(lakebase, deep, now + MISS_TTL_S, None)
        return None
    _store(lakebase, deep, now + (CACHE_TTL_S if value is not None else MISS_TTL_S), value)
    return value


def typical_seconds_for(lakebase: LakebaseClient, job: GenieCompletionJob) -> int | None:
    """The hint for ``job``: only while it runs, and only when its class is
    known (rows from before 2026_09_25 have no ``deep``)."""

    if job.terminal or job.deep is None:
        return None
    return typical_completion_seconds(lakebase, deep=job.deep)


def _reset_for_tests() -> None:
    global _warned_at
    with _LOCK:
        _CACHE.clear()
        _warned_at = None


__all__ = ["MIN_SAMPLES", "typical_completion_seconds", "typical_seconds_for"]
