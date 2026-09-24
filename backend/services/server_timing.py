"""Server-Timing for ``/api/*`` responses (audit ``delivery-v3``, server half).

Per real session the team could not tell whether a slow panel was a cache
miss, a warehouse resume or Lakebase hydration. Every ``/api/*`` response now
carries one header the browser exposes as ``PerformanceResourceTiming``
``serverTiming`` (w2-error-telemetry's ``rum.ts`` consumes it):

    Server-Timing: cache;desc=hit|miss|stale, warehouse;dur=<ms>,
                   lakebase;dur=<ms>, total;dur=<ms>

* ``cache`` is the worst outcome any aggregate cache saw for the request
  (miss > stale > hit); ``warehouse`` / ``lakebase`` are the summed statement
  durations; ``total`` is middleware entry to response start. An entry that
  was not observed is omitted; ``total`` is always present; ``dur`` has one
  decimal.
* Only these four names, and only enum or numeric values: never an id, a
  path, a statement hash or an error message.

One mutable collector per request, set in a ContextVar ONCE, by the
middleware (critic fix 22): sync handlers and sync dependencies run in the
threadpool with a copy of the request context, so they mutate the same
collector object; code must never ``ContextVar.set`` from a worker thread.
Executor threads (gold-cache refreshes, health probes, keep-warm pings) start
with an empty context, see no collector, and record nothing.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from threading import Lock

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "Server-Timing"
_CACHE_RANK = {"hit": 0, "stale": 1, "miss": 2}
_DEPENDENCIES = ("warehouse", "lakebase")


class TimingCollector:
    """What one request observed; lock-protected because threads share it."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cache: str | None = None
        self._durations: dict[str, float] = {}

    def record_cache(self, outcome: str) -> None:
        rank = _CACHE_RANK.get(outcome)
        if rank is None:
            return
        with self._lock:
            if self._cache is None or rank > _CACHE_RANK[self._cache]:
                self._cache = outcome

    def record_dependency(self, name: str, duration_ms: float) -> None:
        if name not in _DEPENDENCIES:
            return
        with self._lock:
            self._durations[name] = self._durations.get(name, 0.0) + max(0.0, float(duration_ms))

    def header_value(self, total_ms: float) -> str:
        with self._lock:
            parts = [f"cache;desc={self._cache}"] if self._cache is not None else []
            parts.extend(
                f"{name};dur={self._durations[name]:.1f}"
                for name in _DEPENDENCIES
                if name in self._durations
            )
        parts.append(f"total;dur={max(0.0, total_ms):.1f}")
        return ", ".join(parts)


_COLLECTOR: ContextVar[TimingCollector | None] = ContextVar("mip_server_timing", default=None)


def record_cache(outcome: str) -> None:
    """Fold one aggregate-cache outcome into the current request, if any."""
    collector = _COLLECTOR.get()
    if collector is not None:
        collector.record_cache(outcome)


def record_dependency(name: str, duration_ms: float) -> None:
    """Add one warehouse / Lakebase statement duration to the current request."""
    collector = _COLLECTOR.get()
    if collector is not None:
        collector.record_dependency(name, duration_ms)


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class ServerTimingMiddleware:
    """Pure-ASGI middleware: one collector per ``/api/*`` HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_api_path(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return
        collector = TimingCollector()
        token = _COLLECTOR.set(collector)
        start = time.perf_counter()

        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start":
                total_ms = (time.perf_counter() - start) * 1000.0
                MutableHeaders(scope=message).append(HEADER, collector.header_value(total_ms))
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            _COLLECTOR.reset(token)


__all__ = [
    "HEADER",
    "ServerTimingMiddleware",
    "TimingCollector",
    "record_cache",
    "record_dependency",
]
