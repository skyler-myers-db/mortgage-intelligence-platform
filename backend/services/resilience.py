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

The breaker (and its typed refusal error) live in ``resilience_breaker``;
the caches live in ``resilience_cache``. Both are re-exported here, so
``backend.services.resilience`` stays the single import surface for
every caller.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Generic, TypeVar

from backend.services.observability import timed_dependency
from backend.services.resilience_breaker import (
    CircuitBreaker,
    DependencyDownError,
    all_breakers,
    get_breaker,
)
from backend.services.resilience_breaker import (
    _reset_breakers_for_tests as _reset_breakers_for_tests,
)
from backend.services.resilience_cache import (
    StaleWhileRevalidateCache,
    TTLCache,
)
from backend.services.resilience_cache import (
    _get_swr_executor as _get_swr_executor,
)
from backend.services.resilience_cache import (
    _shutdown_swr_executor as _shutdown_swr_executor,
)

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
