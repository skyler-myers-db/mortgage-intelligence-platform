"""Stale-while-revalidate cache for hot gold aggregates (audit ``delivery-06``).

Gold tables refresh about daily, yet a hard-expiry ``TTLCache`` made one
user pay the full warehouse round trip (1.4-5.6 s cold) every soft TTL on the
hero KPIs while single-flight followers blocked behind them. This cache keeps
serving the last value past its soft TTL and refreshes it out of band:

* ``ttl_s <= 0``: the factory runs directly (the ``MIP_CACHE_TTL_S=0`` path).
* before the soft TTL (``ttl_s``): a hit.
* after the soft TTL, before the hard cap (``hard_ttl_s``, else
  ``settings.mip_gold_cache_max_stale_s``): the cached value returns at once
  and exactly ONE background refresh per key is scheduled. Outcome ``stale``.
* absent or hard-expired: computed INLINE in the caller's thread with
  single-flight; followers wait up to ``wait_timeout_s`` and then compute
  rather than return an empty state. Outcome ``miss``.
* an inline factory error serves the retained entry when ``stale_if_error``
  and one exists; otherwise the ORIGINAL exception propagates, so routers keep
  their 503 contract.

A background refresh that succeeds replaces the value and restarts both TTLs
from its completion. One that fails keeps last-good (``stale_if_error=True``,
until the hard cap) or evicts the entry (``stale_if_error=False``), so the next
caller recomputes inline and sees the failure, exactly as before.

Staleness is visible (decision record e1): every factory call runs inside
``cache_staleness.run_in_staleness_scope``. An entry records the wall time of
its last successful read (``last_good_wall``), whether it was built from a
retained read (``degraded``) and whether its latest background refresh failed
(``refresh_failed``; only ``_store_locked`` clears it). Whenever this cache
RETURNS such an entry, or serves last-good after an inline failure, it calls
``report_stale(last_good_wall)``, which sets the ``X-Data-Last-Good-At``
response header and propagates to any outer factory's scope. A plain stale
serve in the soft window with no failure carries no marker.

Refreshes run on a dedicated two-worker ``mip-gold-swr`` executor, never the
three-worker ``mip-swr`` pool the health probes need, and are triggered only by
a request: there is no timer, so an idle warehouse still auto-stops.

Snapshot generation (delivery-06 remainder): every entry carries the
``gold_snapshot`` generation current when its value was read, and a lookup
treats an OLDER-generation entry as a miss: computed inline with single-flight,
never served as a hit or a plain stale serve (DEBUG ``gold_cache_miss``,
``reason='snapshot_advanced'``). The entry stays in place, so a failed
recompute with ``stale_if_error`` still serves it with the
``X-Data-Last-Good-At`` marker. Identity is (key, generation), not a per-key
suffix: a suffix would lengthen every key, break ``workflow_key``'s
``family:generation`` parsing in ``drop_workflow_generations`` and orphan the
last-good value ``stale_if_error`` needs across a refresh. The snapshot probe
rides only reads that already go to the warehouse (an inline miss schedules it
on this executor; a background refresh runs it first).
Factories must read only process-level state (the SQL client and settings),
never the request actor or headers, because a refresh runs outside any request.

Values are real cached Unity Catalog reads: a served-stale payload keeps its own
``data_refreshed_at`` / ``snapshot_date``. Nothing that writes an audit row
(VIEW_*, DRAFT_OUTREACH, RECOMMEND_OFFER) may sit behind this cache.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any, Protocol

from backend.config.settings import settings
from backend.services.cache_staleness import report_stale, run_in_staleness_scope
from backend.services.gold_snapshot import get_gold_snapshot_watch, snapshot_generation
from backend.services.observability import emit
from backend.services.server_timing import record_cache as _record_cache

log = logging.getLogger(__name__)

GOLD_SWR_WORKERS = 2
GOLD_SWR_THREAD_PREFIX = "mip-gold-swr"


class AggregateCache(Protocol):
    """What a repository needs from its aggregate cache.

    ``TTLCache`` satisfies it structurally, so tests that inject a clock-driven
    ``TTLCache`` keep exercising plain hard-expiry semantics.
    """

    def get_or_set(
        self,
        key: str,
        factory: Callable[[], Any],
        *,
        ttl_s: float,
        stale_if_error: bool = False,
    ) -> Any: ...

    def clear(self) -> None: ...


# ---------------------------------------------------------------------------
# Workflow generation: the lifecycle-mirror counts ride a key that changes on
# every approval-workflow write and every lifecycle-sync completion this
# process performs or observes (see ``lifecycle_run_watch``).
# ---------------------------------------------------------------------------

_WORKFLOW_GENERATION = 0
_WORKFLOW_GENERATION_LOCK = Lock()
# Key families built by ``workflow_key`` (guarded by the generation lock).
_WORKFLOW_FAMILIES: set[str] = set()
# Every live GoldAggregateCache, so a bump can sweep the dead generations.
_LIVE_CACHES: weakref.WeakSet[GoldAggregateCache] = weakref.WeakSet()
_LIVE_CACHES_LOCK = Lock()


def workflow_generation() -> int:
    with _WORKFLOW_GENERATION_LOCK:
        return _WORKFLOW_GENERATION


_GENERATION_OBSERVERS: list[Callable[[], None]] = []
_GENERATION_OBSERVERS_LOCK = Lock()


def register_generation_observer(fn: Callable[[], None]) -> None:
    """Call ``fn`` each time a workflow key is built (idempotent).

    An observer runs on the reading request's thread, before the generation
    is read, so it must never block: ``lifecycle_run_watch.observe`` only
    schedules a check of the job runs it is waiting on.
    """
    with _GENERATION_OBSERVERS_LOCK:
        if fn not in _GENERATION_OBSERVERS:
            _GENERATION_OBSERVERS.append(fn)


def unregister_generation_observer(fn: Callable[[], None]) -> None:
    with _GENERATION_OBSERVERS_LOCK:
        if fn in _GENERATION_OBSERVERS:
            _GENERATION_OBSERVERS.remove(fn)


def _notify_generation_observers() -> None:
    with _GENERATION_OBSERVERS_LOCK:
        observers = tuple(_GENERATION_OBSERVERS)
    for observer in observers:
        try:
            observer()
        except Exception as exc:  # noqa: BLE001 -- a read never fails on an observer
            emit(
                log,
                "workflow_generation_observer_failed",
                level=logging.WARNING,
                exc_type=type(exc).__name__,
            )


def workflow_key(family: str, *parts: str) -> str:
    """``{family}:{generation}[:{part}...]`` for a value that reads the mirror.

    A bump moves every such key forward AND drops the older generations of
    each family from every live gold cache: a dead generation is never read
    again, and left in place it would age through the LRU pushing out live
    preview keys (one orphan per approval write).

    The generation observers run first, outside the generation lock: a
    reader of workflow counts is what lets the lifecycle run watch notice a
    job-mode sync finished (it bumps from its own worker thread).
    """
    _notify_generation_observers()
    with _WORKFLOW_GENERATION_LOCK:
        _WORKFLOW_FAMILIES.add(family)
        generation = _WORKFLOW_GENERATION
    return ":".join((family, str(generation), *parts))


def bump_workflow_generation() -> int:
    """Move every workflow-count key forward; returns the new generation."""
    global _WORKFLOW_GENERATION
    with _WORKFLOW_GENERATION_LOCK:
        _WORKFLOW_GENERATION += 1
        generation = _WORKFLOW_GENERATION
        families = tuple(_WORKFLOW_FAMILIES)
    if families:
        with _LIVE_CACHES_LOCK:
            caches = list(_LIVE_CACHES)
        for cache in caches:
            cache.drop_workflow_generations(families, keep=generation)
    return generation


# ---------------------------------------------------------------------------
# Dedicated refresh executor.
# ---------------------------------------------------------------------------

_GOLD_EXECUTOR: ThreadPoolExecutor | None = None
_GOLD_EXECUTOR_LOCK = Lock()


def _get_gold_swr_executor() -> ThreadPoolExecutor:
    global _GOLD_EXECUTOR
    with _GOLD_EXECUTOR_LOCK:
        if _GOLD_EXECUTOR is None:
            _GOLD_EXECUTOR = ThreadPoolExecutor(
                max_workers=GOLD_SWR_WORKERS, thread_name_prefix=GOLD_SWR_THREAD_PREFIX
            )
        return _GOLD_EXECUTOR


def _shutdown_gold_swr_executor() -> None:
    """Atexit hook mirroring ``_shutdown_swr_executor``: drop queued refreshes."""
    global _GOLD_EXECUTOR
    with _GOLD_EXECUTOR_LOCK:
        pool = _GOLD_EXECUTOR
        _GOLD_EXECUTOR = None
    if pool is None:
        return
    with contextlib.suppress(Exception):
        pool.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_gold_swr_executor)


@dataclass
class _Entry:
    value: Any
    soft_expiry: float
    hard_expiry: float
    # Epoch seconds of the value's last successful read (or of the oldest
    # retained read it was built from), for X-Data-Last-Good-At.
    last_good_wall: float
    # Built from a value served after a failed refresh (nested staleness).
    degraded: bool = False
    # The latest background refresh failed; reset only by _store_locked.
    refresh_failed: bool = False
    # The gold snapshot generation its value was read under (gold_snapshot).
    generation: int = 0

    @property
    def marked(self) -> bool:
        return self.degraded or self.refresh_failed


class GoldAggregateCache:
    """Bounded LRU stale-while-revalidate cache; see the module docstring."""

    def __init__(
        self,
        max_entries: int = 256,
        now: Callable[[], float] = time.monotonic,
        executor: Executor | None = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, Event] = {}
        # key -> token of the one background refresh allowed per key.
        self._refreshing: dict[str, object] = {}
        self._lock = Lock()
        self._now = now
        self._wall = wall
        self._max_entries = max_entries
        self._executor_override = executor
        with _LIVE_CACHES_LOCK:
            _LIVE_CACHES.add(self)

    def _executor(self) -> Executor:
        return self._executor_override or _get_gold_swr_executor()

    def get_or_set(
        self,
        key: str,
        factory: Callable[[], Any],
        *,
        ttl_s: float,
        stale_if_error: bool = False,
        hard_ttl_s: float | None = None,
        wait_timeout_s: float = 30.0,
    ) -> Any:
        if ttl_s <= 0:
            return factory()
        hard_s = max(
            ttl_s,
            float(hard_ttl_s if hard_ttl_s is not None else settings.mip_gold_cache_max_stale_s),
        )
        generation = snapshot_generation()
        leader = False
        schedule: object | None = None
        value: Any = None
        event: Event | None = None
        served: _Entry | None = None
        advanced = False
        with self._lock:
            entry = self._entries.get(key)
            now = self._now()
            advanced = entry is not None and entry.generation < generation
            if entry is not None and now < entry.hard_expiry and not advanced:
                self._entries.move_to_end(key)
                if now < entry.soft_expiry:
                    outcome = "hit"
                else:
                    outcome = "stale"
                    if key not in self._refreshing:
                        schedule = object()
                        self._refreshing[key] = schedule
                value = entry.value
                served = entry
            else:
                outcome = "miss"
                event = self._inflight.get(key)
                if event is None:
                    event = Event()
                    self._inflight[key] = event
                    leader = True
        if outcome != "miss":
            self._emit(f"gold_cache_{outcome}", key)
            _record_cache(outcome)
            if schedule is not None:
                self._submit_refresh(key, factory, schedule, ttl_s, hard_s, stale_if_error)
            # Read after the submit: a synchronous executor has already
            # recorded a failed refresh on this entry.
            if served is not None and served.marked:
                report_stale(served.last_good_wall)
            return value
        assert event is not None
        if not leader:
            return self._follow(
                key, event, factory, ttl_s, hard_s, stale_if_error, wait_timeout_s, generation
            )
        # An inline miss already goes to the warehouse: the snapshot probe
        # rides it, on the gold-swr executor, at most once per soft TTL.
        watch = get_gold_snapshot_watch()
        if watch is not None:
            watch.schedule_probe(self._executor())
        return self._lead(
            key, event, factory, ttl_s, hard_s, stale_if_error,
            miss_reason="snapshot_advanced" if advanced else None,
        )

    def _lead(
        self,
        key: str,
        event: Event,
        factory: Callable[[], Any],
        ttl_s: float,
        hard_s: float,
        stale_if_error: bool,
        *,
        miss_reason: str | None = None,
    ) -> Any:
        # Read just before the factory, after any probe the miss scheduled.
        generation = snapshot_generation()
        try:
            value, degraded_since = run_in_staleness_scope(factory)
        except Exception:
            stale = self._stale_after_error(key, stale_if_error)
            if stale is not _NOTHING:
                return stale
            _record_cache("miss")
            raise
        else:
            with self._lock:
                if self._inflight.get(key) is event:
                    self._store_locked(
                        key, value, ttl_s, hard_s, degraded_since, generation=generation
                    )
            if degraded_since is not None:
                report_stale(min(self._wall(), degraded_since))
            if miss_reason is None:
                self._emit("gold_cache_miss", key)
            else:
                self._emit("gold_cache_miss", key, reason=miss_reason)
            _record_cache("miss")
            return value
        finally:
            with self._lock:
                if self._inflight.get(key) is event:
                    del self._inflight[key]
            event.set()

    def _follow(
        self,
        key: str,
        event: Event,
        factory: Callable[[], Any],
        ttl_s: float,
        hard_s: float,
        stale_if_error: bool,
        wait_timeout_s: float,
        generation: int,
    ) -> Any:
        if event.wait(timeout=wait_timeout_s):
            with self._lock:
                entry = self._entries.get(key)
                # An older-generation entry the leader failed to replace is
                # not fresh: it goes through _stale_after_error and its marker.
                fresh = (
                    entry is not None
                    and self._now() < entry.hard_expiry
                    and entry.generation >= generation
                )
                value = entry.value if entry is not None else None
            if fresh:
                if entry is not None and entry.marked:
                    report_stale(entry.last_good_wall)
                self._emit("gold_cache_miss", key, reason="singleflight_follower")
                _record_cache("miss")
                return value
            # The leader failed: like TTLCache, a stale_if_error follower
            # serves the retained entry instead of re-running the failed read.
            stale = self._stale_after_error(key, stale_if_error)
            if stale is not _NOTHING:
                return stale
        # The leader timed out or failed: compute rather than return empty.
        generation = snapshot_generation()
        try:
            value, degraded_since = run_in_staleness_scope(factory)
        except Exception:
            stale = self._stale_after_error(key, stale_if_error)
            if stale is not _NOTHING:
                return stale
            _record_cache("miss")
            raise
        with self._lock:
            self._store_locked(key, value, ttl_s, hard_s, degraded_since, generation=generation)
        if degraded_since is not None:
            report_stale(min(self._wall(), degraded_since))
        self._emit("gold_cache_miss", key, reason="singleflight_fallback")
        _record_cache("miss")
        return value

    def _stale_after_error(self, key: str, stale_if_error: bool) -> Any:
        if not stale_if_error:
            return _NOTHING
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _NOTHING
            self._entries.move_to_end(key)
            value = entry.value
            last_good_wall = entry.last_good_wall
        # Served after a failed read, even from a hard-expired entry.
        report_stale(last_good_wall)
        self._emit("gold_cache_stale", key, reason="factory_error")
        _record_cache("stale")
        return value

    def _submit_refresh(
        self,
        key: str,
        factory: Callable[[], Any],
        token: object,
        ttl_s: float,
        hard_s: float,
        stale_if_error: bool,
    ) -> None:
        def _refresh() -> None:
            # A background refresh already goes to the warehouse: probe the
            # gold snapshot first, so the value stores under the generation
            # it was read in.
            watch = get_gold_snapshot_watch()
            if watch is not None:
                watch.probe_if_due()
            generation = snapshot_generation()
            try:
                value, degraded_since = run_in_staleness_scope(factory)
            except BaseException as exc:  # noqa: BLE001 -- a refresh never raises
                with self._lock:
                    if self._refreshing.get(key) is not token:
                        return
                    del self._refreshing[key]
                    retained = self._entries.get(key)
                    if not stale_if_error:
                        self._entries.pop(key, None)
                    elif retained is not None:
                        retained.refresh_failed = True
                emit(
                    log,
                    "gold_cache_refresh_failed",
                    level=logging.WARNING,
                    cache_key=key,
                    exc_type=type(exc).__name__,
                )
                return
            with self._lock:
                if self._refreshing.get(key) is not token:
                    return
                del self._refreshing[key]
                self._store_locked(
                    key, value, ttl_s, hard_s, degraded_since, generation=generation
                )

        try:
            self._executor().submit(_refresh)
        except RuntimeError:
            # Interpreter shutdown: keep serving the stale value.
            with self._lock:
                if self._refreshing.get(key) is token:
                    del self._refreshing[key]

    def _store_locked(
        self,
        key: str,
        value: Any,
        ttl_s: float,
        hard_s: float,
        degraded_since: float | None = None,
        *,
        generation: int = 0,
    ) -> None:
        """Store a fresh entry: the ONLY place ``refresh_failed`` resets."""
        now = self._now()
        wall = self._wall()
        last_good = wall if degraded_since is None else min(wall, degraded_since)
        self._entries[key] = _Entry(
            value,
            now + ttl_s,
            now + hard_s,
            last_good,
            degraded=degraded_since is not None,
            refresh_failed=False,
            generation=generation,
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def drop_workflow_generations(self, families: tuple[str, ...], *, keep: int) -> int:
        """Drop every ``workflow_key`` entry of ``families`` not at ``keep``.

        A background refresh of a dropped key is disowned (it stores nothing).
        An inline leader still in flight may store one old-generation entry;
        the next bump sweeps it, since every generation but ``keep`` goes.
        """
        prefixes = tuple(f"{family}:" for family in families)
        current = str(keep)

        def dead(key: str) -> bool:
            prefix = next((p for p in prefixes if key.startswith(p)), None)
            return prefix is not None and key[len(prefix):].partition(":")[0] != current

        with self._lock:
            doomed = [key for key in self._entries if dead(key)]
            for key in doomed:
                del self._entries[key]
            for key in [key for key in self._refreshing if dead(key)]:
                del self._refreshing[key]
        return len(doomed)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._refreshing.pop(key, None)
            event = self._inflight.pop(key, None)
        if event is not None:
            event.set()

    def clear(self) -> None:
        """Drop every entry; late refreshes and leaders cannot repopulate it."""
        with self._lock:
            self._entries.clear()
            self._refreshing.clear()
            events = list(self._inflight.values())
            self._inflight.clear()
        for event in events:
            event.set()

    @staticmethod
    def _emit(event: str, key: str, **extra: Any) -> None:
        emit(log, event, level=logging.DEBUG, cache_key=key, **extra)


_NOTHING: Any = object()


def get_or_set_capped(
    cache: AggregateCache,
    key: str,
    factory: Callable[[], Any],
    *,
    ttl_s: float,
    stale_if_error: bool = False,
    hard_ttl_s: float | None = None,
) -> Any:
    """``get_or_set`` that passes a hard cap only to a cache that has one.

    A governed build step passes ``hard_ttl_s=ttl_s`` (no stale window); an
    injected ``TTLCache`` is already hard-expiry, so it gets the plain call.
    """
    if hard_ttl_s is not None and isinstance(cache, GoldAggregateCache):
        return cache.get_or_set(
            key, factory, ttl_s=ttl_s, stale_if_error=stale_if_error, hard_ttl_s=hard_ttl_s
        )
    return cache.get_or_set(key, factory, ttl_s=ttl_s, stale_if_error=stale_if_error)


__all__ = [
    "GOLD_SWR_THREAD_PREFIX",
    "GOLD_SWR_WORKERS",
    "AggregateCache",
    "GoldAggregateCache",
    "bump_workflow_generation",
    "get_or_set_capped",
    "register_generation_observer",
    "unregister_generation_observer",
    "workflow_generation",
    "workflow_key",
]
