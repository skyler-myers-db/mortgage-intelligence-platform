"""Staleness scope for cached gold reads (audit ``delivery-06``, record e1).

A cached aggregate can be served after its refresh FAILED: the gold cache
keeps last-good (``stale_if_error=True``) until its hard cap. That is the
right availability posture, but a user must be able to tell the counts are
older than they look. This module carries that fact from the cache that
served the retained value up to the response header.

* ``run_in_staleness_scope(factory)`` runs ONE factory call with a fresh
  scope set in this thread's context (reset in ``finally``). It returns the
  value and the OLDEST last-good wall time any cache reported while the
  factory ran, or ``None``. It is legal on any thread, including the gold
  cache's refresh workers, because it only ever sets its own context.
* ``report_stale(wall)`` records ``wall`` (epoch seconds of the served value's
  last successful Unity Catalog read) into the current scope, if there is
  one, AND into the request's Server-Timing collector
  (``server_timing.record_stale_error``), which is a no-op off a request.

A cache stores the scope's result with the entry (``degraded``), so a value
BUILT from a retained read is marked too, and it reports again whenever it
serves such an entry, so nested staleness propagates to the outer scope.
The scope var is distinct from ``server_timing``'s collector var.
"""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from threading import Lock
from typing import TypeVar

from backend.services.server_timing import record_stale_error

_T = TypeVar("_T")


class _Scope:
    """The oldest last-good wall time reported inside one factory call."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.oldest: float | None = None

    def record(self, wall: float) -> None:
        with self._lock:
            if self.oldest is None or wall < self.oldest:
                self.oldest = wall


_STALENESS_SCOPE: ContextVar[_Scope | None] = ContextVar("mip_cache_staleness_scope", default=None)


def run_in_staleness_scope(factory: Callable[[], _T]) -> tuple[_T, float | None]:
    """Run ``factory`` once in a fresh scope; return (value, degraded_since)."""
    scope = _Scope()
    token = _STALENESS_SCOPE.set(scope)
    try:
        value = factory()
    finally:
        _STALENESS_SCOPE.reset(token)
    return value, scope.oldest


def report_stale(wall: float) -> None:
    """Record that a served value was last read successfully at ``wall``."""
    scope = _STALENESS_SCOPE.get()
    if scope is not None:
        scope.record(wall)
    record_stale_error(wall)


__all__ = ["report_stale", "run_in_staleness_scope"]
