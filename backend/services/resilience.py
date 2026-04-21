"""Resilience primitives for Module 0 Slice 6.

The app runs on live Unity Catalog + Lakebase in every environment,
including the DAIS booth. Real-world flakiness (warehouse cold-start,
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

import logging
import random
import time
from collections.abc import Callable
from threading import Lock
from typing import Any, Generic, TypeVar

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exception -- routers catch this one class and translate to 503.
# ---------------------------------------------------------------------------


class DependencyDownError(RuntimeError):
    """Raised when a dependency circuit breaker refuses a call.

    ``dependency`` is the short name the UI shows ("warehouse",
    "lakebase"); ``reason`` is the operator-facing string (underlying
    exception or "breaker open").
    """

    def __init__(
        self,
        dependency: str,
        *,
        reason: str,
        last_error: BaseException | None = None,
    ) -> None:
        super().__init__(f"{dependency} dependency is down: {reason}")
        self.dependency = dependency
        self.reason = reason
        self.last_error = last_error


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
                log.info("circuit_breaker[%s]: HALF_OPEN -> CLOSED on success", self._name)
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
                log.warning(
                    "circuit_breaker[%s]: HALF_OPEN probe failed, "
                    "re-opening for %.1fs",
                    self._name,
                    self._cooldown_s,
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
                log.warning(
                    "circuit_breaker[%s]: CLOSED -> OPEN after %d failures",
                    self._name,
                    self._failure_count,
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
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
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
    """Simple per-key TTL cache. No size bound (booth-scale traffic).

    Each entry stores (value, expiry_monotonic). A missed or expired
    lookup returns None; the caller must then compute + ``set`` the
    fresh value. We log at DEBUG on expiry so cache behaviour is
    inspectable during the booth.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._entries: dict[str, tuple[Any, float]] = {}
        self._lock = Lock()
        self._now = now

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if self._now() >= expires_at:
                # Drop expired entry so repeat misses don't grow the map.
                del self._entries[key]
                log.debug("ttl_cache miss (expired): %s", key)
                return None
            return value

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        if ttl_s <= 0:
            # Zero TTL disables caching for this entry entirely; this
            # is the "cache disabled" fast-path (MIP_CACHE_TTL_S=0).
            return
        with self._lock:
            self._entries[key] = (value, self._now() + ttl_s)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


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
            raise DependencyDownError(
                self._name,
                reason="circuit breaker is open",
            )
        try:
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
            raise DependencyDownError(
                self._name,
                reason=f"{type(exc).__name__}: {exc}",
                last_error=exc,
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
    "TTLCache",
    "all_breakers",
    "get_breaker",
    "with_retry",
]
