"""Resilience primitives for Module 0 Slice 6.

The app runs on live Unity Catalog + Lakebase in every environment.
Real-world flakiness (warehouse cold-start,
transient 5xx from the Statement Execution API, brief Lakebase TCP
hiccups) must be masked by retry / circuit-break / short-TTL cache, NOT
by silent mock fallback. When the breaker opens, the router returns
HTTP 503 with ``retryable: true`` and the UI shows a visible degraded
banner -- never fake data.

This module is deliberately stdlib-only so it can sit under the
Databricks Apps serverless runtime with zero additional wheels.

Public surface:

* ``CircuitBreaker`` -- OPEN / HALF_OPEN / CLOSED state machine with
  configurable failure threshold, cool-down window, and half-open
  probe count. Thread-safe via ``threading.Lock``.
* ``with_retry`` -- exponential backoff with decorrelated jitter.
* ``TTLCache`` -- per-key TTL cache with thread-safe get/set/invalidate.
* ``Resilient`` -- compose breaker + retry (+ optional cache) around a
  callable, surfacing ``DependencyDownError`` when the breaker refuses
  the call.
* ``DependencyDownError`` -- typed exception the routers catch and
  translate to HTTP 503 with a ``retryable: true`` body.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from threading import Event, Lock
from typing import Any, Generic, TypeVar

from backend.services.observability import (
    emit,
    record_breaker_state_change,
    timed_dependency,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exception -- routers catch this one class and translate to 503.
# ---------------------------------------------------------------------------


class DependencyDownError(RuntimeError):
    """Raised when a dependency circuit breaker refuses a call.

    ``dependency`` is the short name the UI shows ("warehouse",
    "lakebase"); ``reason`` is the operator-facing string (underlying
    exception or "breaker open"); ``kind`` is the machine-readable
    classification the frontend keys on to pick a retry cadence.

    R6-05: ``kind`` distinguishes "warming_up" (first-request cold-start
    against a suspended warehouse, fast retry OK) from "breaker_open"
    (breaker already tripped by prior flap, give it the cooldown window
    before retrying) from "retries_exhausted" (retry budget blown by a
    harder outage). The legacy ``retryable: true`` field stays on the
    wire so existing UI code keeps working; the additive ``kind`` lets
    the frontend (in a parallel cycle) pick a smarter backoff.
    """

    KIND_WARMING_UP = "warming_up"
    KIND_BREAKER_OPEN = "breaker_open"
    KIND_RETRIES_EXHAUSTED = "retries_exhausted"
    _ALLOWED_KINDS = frozenset({KIND_WARMING_UP, KIND_BREAKER_OPEN, KIND_RETRIES_EXHAUSTED})

    def __init__(
        self,
        dependency: str,
        *,
        reason: str,
        last_error: BaseException | None = None,
        kind: str = KIND_WARMING_UP,
    ) -> None:
        super().__init__(f"{dependency} dependency is down: {reason}")
        self.dependency = dependency
        self.reason = reason
        self.last_error = last_error
        # Defensively clamp to the allowed set so a typo in a future
        # call site can't ship a freeform string to the frontend.
        self.kind = kind if kind in self._ALLOWED_KINDS else self.KIND_WARMING_UP


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Three-state breaker (CLOSED, OPEN, HALF_OPEN).

    - CLOSED: calls go through. Each failure increments a counter;
      when the counter reaches ``failure_threshold`` the breaker
      transitions to OPEN.
    - OPEN: calls are refused for ``cooldown_s`` seconds. After the
      cool-down elapses, the next ``allow()`` returns True and the
      breaker transitions to HALF_OPEN.
    - HALF_OPEN: a small number of probes (``half_open_probes``) are
      permitted. One success closes the breaker; one failure re-opens
      and restarts the cool-down.

    Thread-safety is at the method level via ``threading.Lock``; every
    state transition happens under the lock.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        cooldown_s: float = 30.0,
        half_open_probes: int = 1,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_s <= 0:
            raise ValueError("cooldown_s must be > 0")
        if half_open_probes < 1:
            raise ValueError("half_open_probes must be >= 1")
        self._name = name
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._half_open_probes = half_open_probes
        self._now = now
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._probes_in_flight = 0
        self._lock = Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> str:
        # Trigger a passive transition from OPEN to HALF_OPEN if the
        # cool-down elapsed between calls to ``allow``.
        with self._lock:
            self._maybe_half_open_locked()
            return self._state

    def _maybe_half_open_locked(self) -> None:
        """Promote OPEN -> HALF_OPEN when the cool-down elapsed.

        Caller must hold ``self._lock``.
        """
        if self._state != self.OPEN or self._opened_at is None:
            return
        if (self._now() - self._opened_at) >= self._cooldown_s:
            self._state = self.HALF_OPEN
            self._probes_in_flight = 0
            # Slice-13: structured event for ops dashboards / grep.
            emit(
                log,
                "circuit_breaker_state_change",
                dependency=self._name,
                from_state=self.OPEN,
                to_state=self.HALF_OPEN,
                name=self._name,
                failure_count=self._failure_count,
                cooldown_s=self._cooldown_s,
            )
            record_breaker_state_change(
                name=self._name, from_state=self.OPEN, to_state=self.HALF_OPEN
            )

    def allow(self) -> bool:
        """Return True when a call is permitted.

        In HALF_OPEN the caller must take the slot by actually making
        the call; ``record_success`` / ``record_failure`` retire the
        probe. We cap concurrent probes at ``half_open_probes`` so a
        thundering herd can't pile onto a still-broken dependency.
        """
        with self._lock:
            self._maybe_half_open_locked()
            if self._state == self.CLOSED:
                return True
            if self._state == self.HALF_OPEN:
                if self._probes_in_flight < self._half_open_probes:
                    self._probes_in_flight += 1
                    return True
                return False
            # OPEN
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                emit(
                    log,
                    "circuit_breaker_state_change",
                    dependency=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.CLOSED,
                    name=self._name,
                    failure_count=self._failure_count,
                    cooldown_s=self._cooldown_s,
                )
                record_breaker_state_change(
                    name=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.CLOSED,
                )
                self._state = self.CLOSED
                self._failure_count = 0
                self._opened_at = None
                self._probes_in_flight = 0
                return
            # CLOSED success path -- reset counter.
            self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                # One failed probe re-opens and restarts the cool-down.
                emit(
                    log,
                    "circuit_breaker_state_change",
                    level=logging.WARNING,
                    dependency=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.OPEN,
                    name=self._name,
                    failure_count=self._failure_count,
                    cooldown_s=self._cooldown_s,
                )
                record_breaker_state_change(
                    name=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.OPEN,
                )
                self._state = self.OPEN
                self._opened_at = self._now()
                self._probes_in_flight = 0
                return
            if self._state == self.OPEN:
                # Already open; leave ``opened_at`` alone.
                return
            # CLOSED
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                emit(
                    log,
                    "circuit_breaker_state_change",
                    level=logging.WARNING,
                    dependency=self._name,
                    from_state=self.CLOSED,
                    to_state=self.OPEN,
                    name=self._name,
                    failure_count=self._failure_count,
                    cooldown_s=self._cooldown_s,
                )
                record_breaker_state_change(
                    name=self._name,
                    from_state=self.CLOSED,
                    to_state=self.OPEN,
                )
                self._state = self.OPEN
                self._opened_at = self._now()

    def reset(self) -> None:
        """Force back to CLOSED. Test-only."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._opened_at = None
            self._probes_in_flight = 0

    def force_open_for_placeholder_config(self) -> None:
        """Jam the breaker OPEN because the dependency was configured with a
        placeholder value that will guaranteed-fail. Used at boot for the
        Genie client when GENIE_SPACE_ID is still a bundle-default placeholder
        (``00000000PLACEHOLDER``). Holds the state until reset/cooldown is
        consumed; the cooldown will re-probe and the placeholder check will
        trip the next boot.

        Round-3 hole-finder #18, 2026-04-23.
        """
        with self._lock:
            self._state = self.OPEN
            self._failure_count = self._failure_threshold
            self._opened_at = self._now()
            self._probes_in_flight = 0

    def force_close_if_config_changed(self, predicate: Callable[[], bool]) -> bool:
        """Force CLOSED when ``predicate()`` returns True.

        R6-18 escape hatch. ``force_open_for_placeholder_config`` jams the
        breaker OPEN at boot when a placeholder config value (e.g.
        ``GENIE_SPACE_ID=00000000PLACEHOLDER``) would guaranteed-fail. The
        cooldown is irrelevant: every half-open probe will still fail on
        the same placeholder, so the breaker flip-flops until the process
        restarts.

        But Databricks Apps supports env-var rotation WITHOUT a restart.
        When the operator fixes the config at runtime, the breaker has no
        way to notice unless we probe it. This method lets the Genie
        client (or any other caller) evaluate a cheap predicate before
        ``allow()`` and, if the predicate now returns True, close the
        breaker and let the next real probe through.

        ``predicate`` should be a pure read of the current config (e.g.
        ``lambda: not is_placeholder_space_id(settings.genie_space_id)``).
        Returns True when the breaker was actually closed -- callers can
        use that to emit a structured recovery log.
        """
        if not predicate():
            return False
        with self._lock:
            if self._state == self.CLOSED:
                return False
            emit(
                log,
                "circuit_breaker_state_change",
                dependency=self._name,
                from_state=self._state,
                to_state=self.CLOSED,
                name=self._name,
                failure_count=self._failure_count,
                cooldown_s=self._cooldown_s,
                reason="config_changed",
            )
            record_breaker_state_change(
                name=self._name,
                from_state=self._state,
                to_state=self.CLOSED,
            )
            self._state = self.CLOSED
            self._failure_count = 0
            self._opened_at = None
            self._probes_in_flight = 0
            return True


# ---------------------------------------------------------------------------
# Retry with exponential backoff + decorrelated jitter.
# ---------------------------------------------------------------------------


T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    backoff_base: float = 0.2,
    backoff_max: float = 2.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    """Invoke ``fn`` with exponential-backoff retries.

    On each failure matching ``retry_on`` we wait
    ``min(backoff_max, backoff_base * 2**attempt) * jitter`` seconds,
    where ``jitter`` is uniformly distributed in ``[0.5, 1.5]``. The
    jitter is decorrelated (per-call random draw, not a shared state)
    so parallel callers don't synchronize their retries into a wave.

    Raises the *last* exception when all attempts are exhausted. A
    ``retry_on`` miss (e.g. ``DependencyDownError``) short-circuits to
    the original exception immediately.

    R6-15: ``DependencyDownError`` is ALWAYS excluded from retry,
    regardless of ``retry_on``. It's the canonical "a nested Resilient
    already did its retries, stop" signal -- retrying it in an outer
    call would compound 3x3=9 real attempts per user request against a
    dependency that already gave up. Explicit subclass check (not just
    tuple membership) so callers that pass a broader ``retry_on`` like
    ``(Exception,)`` still benefit.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except DependencyDownError:
            # R6-15: never retry a DependencyDownError -- it means a
            # nested Resilient has already exhausted its own retry
            # budget (or the breaker is OPEN). Propagate immediately.
            raise
        except BaseException as exc:  # noqa: BLE001 -- re-raised below
            if not isinstance(exc, retry_on):
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            delay = min(backoff_max, backoff_base * (2 ** attempt))
            # Decorrelated jitter in [0.5, 1.5] * delay.
            jittered = delay * (0.5 + rand())
            sleep(jittered)
    # Exhausted. ``last_exc`` must be set by definition.
    assert last_exc is not None  # pragma: no cover -- invariant
    raise last_exc


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


# ---------------------------------------------------------------------------
# Composed wrapper -- breaker + retry (+ optional cache).
# ---------------------------------------------------------------------------


class Resilient(Generic[T]):
    """Compose a ``CircuitBreaker`` + ``with_retry`` around a callable.

    Usage::

        resilient = Resilient(
            breaker=warehouse_breaker,
            dependency_name="warehouse",
            attempts=3,
        )
        rows = resilient.call(lambda: client.execute(sql))

    The breaker decides whether the call is even attempted; if OPEN,
    we raise ``DependencyDownError`` without invoking ``fn``. If the
    breaker permits the call, we drive it through ``with_retry``. On
    success: ``record_success``. On terminal failure (all retries
    exhausted): ``record_failure`` and re-raise as
    ``DependencyDownError`` so the router's one-line ``except`` clause
    works.
    """

    def __init__(
        self,
        *,
        breaker: CircuitBreaker,
        dependency_name: str,
        attempts: int = 3,
        backoff_base: float = 0.2,
        backoff_max: float = 2.0,
        retry_on: tuple[type[BaseException], ...] = (Exception,),
    ) -> None:
        self._breaker = breaker
        self._name = dependency_name
        self._attempts = attempts
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._retry_on = retry_on

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def call(self, fn: Callable[[], T]) -> T:
        if not self._breaker.allow():
            # R6-05: the breaker is already OPEN (or HALF_OPEN with no
            # probe slot). Tag ``kind=breaker_open`` so the frontend can
            # back off longer than the warming-up default; hammering a
            # known-open breaker just burns the client retry budget.
            raise DependencyDownError(
                self._name,
                reason="circuit breaker is open",
                kind=DependencyDownError.KIND_BREAKER_OPEN,
            )
        # Slice-13: wrap every dependency call in a structured span so
        # operators can correlate a request with every downstream SQL /
        # Genie / Lakebase call it fanned out into. timed_dependency is
        # a cheap no-op when the root logger is at WARN and no handler
        # is attached, so this costs nothing in unit tests.
        try:
            with timed_dependency(self._name, "call"):
                result = with_retry(
                    fn,
                    attempts=self._attempts,
                    backoff_base=self._backoff_base,
                    backoff_max=self._backoff_max,
                    retry_on=self._retry_on,
                )
        except BaseException as exc:
            self._breaker.record_failure()
            if isinstance(exc, DependencyDownError):
                raise
            # R6-05: the call went through the breaker but the
            # retry budget is exhausted -- tag as retries_exhausted so
            # the frontend knows a plain retry likely won't help and
            # the UI can show an operator-oriented message instead of
            # the "warming up" copy.
            raise DependencyDownError(
                self._name,
                reason=f"{type(exc).__name__}: {exc}",
                last_error=exc,
                kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
            ) from exc
        self._breaker.record_success()
        return result


# ---------------------------------------------------------------------------
# Named-singleton registry -- routers + health endpoint share one breaker
# instance per dependency so the "circuit_breakers" status in /api/health is
# coherent with the one the repositories actually use.
# ---------------------------------------------------------------------------


_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = Lock()


def get_breaker(
    name: str,
    *,
    failure_threshold: int = 5,
    cooldown_s: float = 30.0,
    half_open_probes: int = 1,
) -> CircuitBreaker:
    """Return (and lazily construct) the process-wide breaker for ``name``."""
    with _BREAKERS_LOCK:
        existing = _BREAKERS.get(name)
        if existing is not None:
            return existing
        created = CircuitBreaker(
            name,
            failure_threshold=failure_threshold,
            cooldown_s=cooldown_s,
            half_open_probes=half_open_probes,
        )
        _BREAKERS[name] = created
        return created


def all_breakers() -> dict[str, CircuitBreaker]:
    with _BREAKERS_LOCK:
        return dict(_BREAKERS)


def _reset_breakers_for_tests() -> None:
    """Test helper -- drop every cached breaker."""
    with _BREAKERS_LOCK:
        _BREAKERS.clear()


__all__ = [
    "CircuitBreaker",
    "DependencyDownError",
    "Resilient",
    "StaleWhileRevalidateCache",
    "TTLCache",
    "all_breakers",
    "get_breaker",
    "with_retry",
]
