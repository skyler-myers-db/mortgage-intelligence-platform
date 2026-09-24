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

Refreshes run on a dedicated two-worker ``mip-gold-swr`` executor, never the
three-worker ``mip-swr`` pool the health probes need, and are triggered only by
a request: there is no timer, so an idle warehouse still auto-stops.
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
# every approval-workflow write and every lifecycle-sync completion.
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


def workflow_key(family: str, *parts: str) -> str:
    """``{family}:{generation}[:{part}...]`` for a value that reads the mirror.

    A bump moves every such key forward AND drops the older generations of
    each family from every live gold cache: a dead generation is never read
    again, and left in place it would age through the LRU pushing out live
    preview keys (one orphan per approval write).
    """
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


class GoldAggregateCache:
    """Bounded LRU stale-while-revalidate cache; see the module docstring."""

    def __init__(
        self,
        max_entries: int = 256,
        now: Callable[[], float] = time.monotonic,
        executor: Executor | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, Event] = {}
        # key -> token of the one background refresh allowed per key.
        self._refreshing: dict[str, object] = {}
        self._lock = Lock()
        self._now = now
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
        leader = False
        schedule: object | None = None
        value: Any = None
        event: Event | None = None
        with self._lock:
            entry = self._entries.get(key)
            now = self._now()
            if entry is not None and now < entry.hard_expiry:
                self._entries.move_to_end(key)
                if now < entry.soft_expiry:
                    outcome = "hit"
                else:
                    outcome = "stale"
                    if key not in self._refreshing:
                        schedule = object()
                        self._refreshing[key] = schedule
                value = entry.value
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
            return value
        assert event is not None
        if not leader:
            return self._follow(key, event, factory, ttl_s, hard_s, stale_if_error, wait_timeout_s)
        return self._lead(key, event, factory, ttl_s, hard_s, stale_if_error)

    def _lead(
        self,
        key: str,
        event: Event,
        factory: Callable[[], Any],
        ttl_s: float,
        hard_s: float,
        stale_if_error: bool,
    ) -> Any:
        try:
            value = factory()
        except Exception:
            stale = self._stale_after_error(key, stale_if_error)
            if stale is not _NOTHING:
                return stale
            _record_cache("miss")
            raise
        else:
            with self._lock:
                if self._inflight.get(key) is event:
                    self._store_locked(key, value, ttl_s, hard_s)
            self._emit("gold_cache_miss", key)
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
    ) -> Any:
        if event.wait(timeout=wait_timeout_s):
            with self._lock:
                entry = self._entries.get(key)
                fresh = entry is not None and self._now() < entry.hard_expiry
                value = entry.value if entry is not None else None
            if fresh:
                self._emit("gold_cache_miss", key, reason="singleflight_follower")
                _record_cache("miss")
                return value
            # The leader failed: like TTLCache, a stale_if_error follower
            # serves the retained entry instead of re-running the failed read.
            stale = self._stale_after_error(key, stale_if_error)
            if stale is not _NOTHING:
                return stale
        # The leader timed out or failed: compute rather than return empty.
        try:
            value = factory()
        except Exception:
            stale = self._stale_after_error(key, stale_if_error)
            if stale is not _NOTHING:
                return stale
            _record_cache("miss")
            raise
        with self._lock:
            self._store_locked(key, value, ttl_s, hard_s)
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
            try:
                value = factory()
            except BaseException as exc:  # noqa: BLE001 -- a refresh never raises
                with self._lock:
                    if self._refreshing.get(key) is not token:
                        return
                    del self._refreshing[key]
                    if not stale_if_error:
                        self._entries.pop(key, None)
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
                self._store_locked(key, value, ttl_s, hard_s)

        try:
            self._executor().submit(_refresh)
        except RuntimeError:
            # Interpreter shutdown: keep serving the stale value.
            with self._lock:
                if self._refreshing.get(key) is token:
                    del self._refreshing[key]

    def _store_locked(self, key: str, value: Any, ttl_s: float, hard_s: float) -> None:
        now = self._now()
        self._entries[key] = _Entry(value, now + ttl_s, now + hard_s)
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
    "workflow_generation",
    "workflow_key",
]
