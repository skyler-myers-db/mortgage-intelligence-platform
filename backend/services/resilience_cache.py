"""Process-local caches for hot dependency reads.

Single responsibility: keep a bounded, thread-safe copy of expensive
dependency results so a burst of callers does not stampede the
warehouse. Two shapes live here:

* ``TTLCache`` -- per-key expiry with optional single-flight refresh and
  ``stale_if_error`` last-good serving.
* ``StaleWhileRevalidateCache`` -- two-tier TTL used by the health probe,
  plus the bounded background executor it refreshes on.

``backend.services.resilience`` re-exports both, so existing import
sites keep working unchanged.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from threading import Event, Lock
from typing import Any

from backend.services.observability import emit

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTL cache -- per-key expiry, thread-safe.
# ---------------------------------------------------------------------------


class TTLCache:
    """Bounded per-key TTL cache with optional single-flight refresh.

    The cache is intentionally process-local. Databricks Apps runs this
    Module 0 deployment as a single app instance, so a local cache avoids
    warehouse stampedes without introducing another dependency. If a
    customer scales the app horizontally, each replica will keep its own
    cache and the load-test baseline must be re-captured under that shape.

    Legacy callers can keep using ``get`` + ``set``. New hot aggregate
    paths should prefer ``get_or_set`` so a burst of callers for the
    same expired key runs the expensive factory once, while followers
    wait for that result instead of stampeding the warehouse.

    Expired entries are retained until eviction so ``stale_if_error``
    can serve the last-good aggregate when a read-only refresh fails.
    Mutable workflow endpoints should not use that option.
    """

    def __init__(
        self,
        now: Callable[[], float] = time.monotonic,
        *,
        max_entries: int = 256,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._entries: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._inflight: dict[str, Event] = {}
        self._lock = Lock()
        self._now = now
        self._max_entries = max_entries

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._emit_cache_event("ttl_cache_miss", key, reason="empty")
                return None
            value, expires_at = entry
            self._entries.move_to_end(key)
            if self._now() >= expires_at:
                self._emit_cache_event("ttl_cache_miss", key, reason="expired")
                return None
            self._emit_cache_event("ttl_cache_hit", key)
            return value

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        if ttl_s <= 0:
            # Zero TTL disables caching for this entry entirely; this
            # is the "cache disabled" fast-path (MIP_CACHE_TTL_S=0).
            return
        with self._lock:
            self._entries[key] = (value, self._now() + ttl_s)
            self._entries.move_to_end(key)
            self._evict_locked()

    def get_or_set(
        self,
        key: str,
        factory: Callable[[], Any],
        *,
        ttl_s: float,
        stale_if_error: bool = False,
        wait_timeout_s: float = 30.0,
    ) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached

        leader = False
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                value, expires_at = entry
                self._entries.move_to_end(key)
                if self._now() < expires_at:
                    self._emit_cache_event("ttl_cache_hit", key, reason="double_check")
                    return value
            event = self._inflight.get(key)
            if event is None:
                event = Event()
                self._inflight[key] = event
                leader = True

        if not leader:
            self._emit_cache_event("ttl_cache_wait", key)
            if event.wait(timeout=wait_timeout_s):
                cached = self.get(key)
                if cached is not None:
                    return cached
                if stale_if_error:
                    stale = self.get_stale(key)
                    if stale is not None:
                        self._emit_cache_event("ttl_cache_stale_hit", key, reason="leader_failed")
                        return stale
            # The leader timed out or failed without a stale value.
            # Compute rather than returning a false empty state.
            self._emit_cache_event("ttl_cache_miss", key, reason="singleflight_fallback")

        try:
            value = factory()
        except Exception:
            if stale_if_error:
                stale = self.get_stale(key)
                if stale is not None:
                    self._emit_cache_event("ttl_cache_stale_hit", key, reason="factory_error")
                    return stale
            raise
        else:
            self.set(key, value, ttl_s)
            return value
        finally:
            if leader:
                with self._lock:
                    finished = self._inflight.pop(key, None)
                    if finished is not None:
                        finished.set()

    def get_stale(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
            event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            for event in self._inflight.values():
                event.set()
            self._inflight.clear()

    def _evict_locked(self) -> None:
        while len(self._entries) > self._max_entries:
            evicted_key, _ = self._entries.popitem(last=False)
            self._emit_cache_event("ttl_cache_eviction", evicted_key, reason="max_entries")

    @staticmethod
    def _emit_cache_event(event: str, key: str, **extra: Any) -> None:
        emit(log, event, level=logging.DEBUG, cache_key=key, **extra)


# ---------------------------------------------------------------------------
# Stale-while-revalidate cache -- used by /api/health to serve cached probe
# results for a hard TTL while refreshing out-of-band past a shorter soft
# TTL. Closes the p95 tail where a plain TTL cache expires mid-burst and the
# next requester eats a full probe round-trip.
# ---------------------------------------------------------------------------


# Shared executor: one slot per dependency (warehouse/lakebase/genie).
# A cache key retains one Future until the real probe returns, even after a
# caller stops waiting. That makes timeout handling truthful: a slow probe
# cannot fan out into replacement work, and its eventual result can still
# update the cache. Created lazily so processes that never touch SWR do not
# pay for the threads.
_SWR_EXECUTOR: ThreadPoolExecutor | None = None
_SWR_EXECUTOR_LOCK = Lock()


def _get_swr_executor() -> ThreadPoolExecutor:
    global _SWR_EXECUTOR
    with _SWR_EXECUTOR_LOCK:
        if _SWR_EXECUTOR is None:
            _SWR_EXECUTOR = ThreadPoolExecutor(
                max_workers=3, thread_name_prefix="mip-swr"
            )
        return _SWR_EXECUTOR


def _shutdown_swr_executor() -> None:
    """Atexit hook: reject queued work without creating orphan probes.

    ``cancel_futures=True`` drops queued but not-yet-started executor work.
    Python cannot cancel a running sync I/O call; those calls stay in the
    bounded executor (one Future per dependency) until the underlying client
    timeout retires them. There is no nested daemon-thread escape hatch.

    Safe to call multiple times; the second call is a no-op because
    ``_SWR_EXECUTOR`` is set back to None after shutdown.
    """
    global _SWR_EXECUTOR
    with _SWR_EXECUTOR_LOCK:
        pool = _SWR_EXECUTOR
        _SWR_EXECUTOR = None
    if pool is None:
        return
    with contextlib.suppress(Exception):
        pool.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_swr_executor)


class StaleWhileRevalidateCache:
    """Two-tier TTL cache: soft TTL triggers background refresh, hard TTL
    makes a caller wait for the shared in-flight probe.

    For each key we track ``(value, hard_expiry, soft_expiry,
    refresh_in_flight)`` under a single lock. ``get_or_refresh`` returns
    the cached value unless the hard TTL has elapsed; if only the soft
    TTL has elapsed it kicks a background refresh into a shared
    ``ThreadPoolExecutor`` and still returns the cached value. Concurrent
    callers share one completion Future. A cold caller waits only for its
    budget; timing out never creates a cached synthetic failure, and the
    eventual real completion still refreshes the cache.

    Not a drop-in replacement for :class:`TTLCache` -- the API takes a
    probe callable because the whole point is "serve cached while we
    refresh". Callers that just want a simple TTL should keep using
    TTLCache.
    """

    def __init__(
        self,
        *,
        soft_ttl_s: float,
        hard_ttl_s: float,
        executor: ThreadPoolExecutor | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if soft_ttl_s <= 0:
            raise ValueError("soft_ttl_s must be > 0")
        if hard_ttl_s <= soft_ttl_s:
            raise ValueError("hard_ttl_s must be > soft_ttl_s")
        self._soft_ttl_s = soft_ttl_s
        self._hard_ttl_s = hard_ttl_s
        self._executor_override = executor
        self._now = now
        self._lock = Lock()
        # key -> (value, hard_expiry, soft_expiry, refresh_in_flight)
        self._entries: dict[str, tuple[Any, float, float, bool]] = {}
        self._inflight: dict[str, Future[Any]] = {}

    def _executor(self) -> ThreadPoolExecutor:
        return self._executor_override or _get_swr_executor()

    def get_or_refresh(
        self,
        key: str,
        probe: Callable[[], Any],
        *,
        wait_timeout_s: float = 30.0,
    ) -> Any | None:
        """Resolve one key within a caller-level wait budget."""
        return self.get_or_refresh_many(
            {key: probe}, wait_timeout_s=wait_timeout_s
        )[key]

    def get_or_refresh_many(
        self,
        probes: dict[str, Callable[[], Any]],
        *,
        wait_timeout_s: float,
    ) -> dict[str, Any | None]:
        """Resolve a probe batch against one absolute caller deadline.

        Every cold dependency starts before this method waits, allowing the
        probes to overlap. Stale values return immediately while exactly one
        refresh remains active per key.
        """
        if wait_timeout_s < 0:
            raise ValueError("wait_timeout_s must be >= 0")
        deadline = time.monotonic() + wait_timeout_s

        immediate: dict[str, Any] = {}
        pending: dict[str, Future[Any]] = {}
        reservations: list[tuple[str, Callable[[], Any], Future[Any], bool]] = []
        now = self._now()
        with self._lock:
            for key, probe in probes.items():
                entry = self._entries.get(key)
                if entry is not None:
                    value, hard_expiry, soft_expiry, _refreshing = entry
                    if now < hard_expiry:
                        immediate[key] = value
                        if now >= soft_expiry and key not in self._inflight:
                            stale_completion: Future[Any] = Future()
                            self._inflight[key] = stale_completion
                            self._entries[key] = (
                                value,
                                hard_expiry,
                                soft_expiry,
                                True,
                            )
                            reservations.append((key, probe, stale_completion, True))
                        continue

                completion = self._inflight.get(key)
                if completion is None:
                    completion = Future()
                    self._inflight[key] = completion
                    if entry is not None:
                        value, hard_expiry, soft_expiry, _refreshing = entry
                        self._entries[key] = (
                            value,
                            hard_expiry,
                            soft_expiry,
                            True,
                        )
                    reservations.append((key, probe, completion, False))
                pending[key] = completion

        # Submit after releasing the cache lock. Test executors may execute
        # inline, and production workers call back into this cache on finish.
        for reservation in reservations:
            self._submit_reserved(*reservation)

        results: dict[str, Any | None] = dict(immediate)
        for key, completion in pending.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results[key] = completion.result(timeout=remaining)
            except FuturesTimeoutError:
                emit(
                    log,
                    "health_probe_caller_timeout",
                    level=logging.WARNING,
                    dependency=key,
                    timeout_s=wait_timeout_s,
                    outcome="timeout",
                )
                results[key] = None
            except BaseException as exc:  # noqa: BLE001 -- health fails closed
                emit(
                    log,
                    "health_probe_refresh_failed",
                    level=logging.WARNING,
                    dependency=key,
                    outcome="error",
                    exc_type=type(exc).__name__,
                )
                results[key] = None
        return results

    def _submit_reserved(
        self,
        key: str,
        probe: Callable[[], Any],
        completion: Future[Any],
        stale_refresh: bool,
    ) -> None:
        """Submit a reservation to the one shared bounded executor."""

        def _worker() -> None:
            start = time.monotonic()
            try:
                value = probe()
            except BaseException as exc:  # noqa: BLE001 -- copied to waiters
                self._finish_error(key, completion)
                completion.set_exception(exc)
                emit(
                    log,
                    "health_probe_background_refresh",
                    level=logging.WARNING,
                    dependency=key,
                    hit_soft_ttl=stale_refresh,
                    duration_ms=round((time.monotonic() - start) * 1000.0, 2),
                    outcome="error",
                    exc_type=type(exc).__name__,
                )
                return

            self._finish_success(key, completion, value)
            completion.set_result(value)
            emit(
                log,
                "health_probe_background_refresh",
                dependency=key,
                hit_soft_ttl=stale_refresh,
                duration_ms=round((time.monotonic() - start) * 1000.0, 2),
                outcome="ok",
            )

        try:
            self._executor().submit(_worker)
        except RuntimeError as exc:
            self._finish_error(key, completion)
            completion.set_exception(exc)

    def _finish_success(
        self, key: str, completion: Future[Any], value: Any
    ) -> None:
        now = self._now()
        with self._lock:
            if self._inflight.get(key) is not completion:
                return
            self._entries[key] = (
                value,
                now + self._hard_ttl_s,
                now + self._soft_ttl_s,
                False,
            )
            del self._inflight[key]

    def _finish_error(self, key: str, completion: Future[Any]) -> None:
        with self._lock:
            if self._inflight.get(key) is not completion:
                return
            del self._inflight[key]
            entry = self._entries.get(key)
            if entry is not None:
                value, hard_expiry, soft_expiry, _refreshing = entry
                self._entries[key] = (value, hard_expiry, soft_expiry, False)

    def clear(self) -> None:
        """Drop cached and single-flight state; late workers cannot repopulate it."""
        with self._lock:
            self._entries.clear()
            self._inflight.clear()
