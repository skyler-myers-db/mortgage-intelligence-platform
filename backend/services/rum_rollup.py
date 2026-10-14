"""In-process RUM day aggregates, flushed once a minute to Lakebase (D-platform-process-d2).

Browser RUM is operational telemetry, never an audit record. Nothing per event
is kept: ``record_rum`` hands each validated event to :func:`add`, which folds
it into a pending aggregate keyed by ``(day_utc, metric, route, facet,
rating)`` and holding a sample count, a sum, a min, a max, 24 histogram
buckets (``rum_buckets``) and the contributing builds. ``flush`` writes every
pending aggregate in ONE multi-row upsert into ``mip_app.rum_daily``, a
90-slot day ring keyed ``(slot, metric, route, facet, rating)``:

* a same-day conflict increments the stored row;
* a same-slot conflict for a NEWER day resets every field, builds included;
* a straggler for an OLDER day is refused by the ``WHERE``, never written
  backwards.

Retention is zero-in-place: the first writing flush of each UTC day in each
process first zeroes every row older than 90 days, so the App role needs no
removal grant (the migration postflight forbids one).

Every key part is already validated against a closed vocabulary
(``backend/schemas/telemetry.py``): the route is a registry template, the
facet is built only from closed values, and the rating is derived here from
the value, never taken from the browser. No identifier of any kind is stored.

Failure posture: an open Lakebase breaker skips the flush and keeps the
bounded aggregates for the next minute (the ``state`` is read; ``allow`` is
never called). A failed statement drops that batch, logs ONE
``rum_rollup_flush_failed`` warning with counts only, and never raises. At
most 1000 keys wait at once; an event for a new key past that cap is
dropped and counted.

Threads: importing this module starts nothing. The process singleton starts
one daemon flusher (an ``Event.wait(60)`` loop) on its first :func:`add`;
the FastAPI lifespan calls :func:`flush_on_shutdown`, and ``atexit`` is the
fallback. Tests use :func:`_reset_for_tests` with an injected client and
clock and no flusher.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from threading import Event, Lock, Thread
from typing import Any, Final

from backend.config.settings import settings
from backend.schemas.telemetry import RumDetailKey, RumEvent
from backend.services.observability import emit
from backend.services.rum_buckets import bucket_index, rating, zero_buckets

log = logging.getLogger(__name__)

FLUSH_INTERVAL_S: Final = 60.0
MAX_PENDING_KEYS: Final = 1000
MAX_BUILDS: Final = 8
RING_DAYS: Final = 90
RING_EPOCH: Final = date(2026, 1, 1)
FLUSHER_THREAD_NAME: Final = "mip-rum-rollup"

#: The INP phase details, each feeding its own derived metric row.
_INP_PHASES: Final[tuple[tuple[RumDetailKey, str], ...]] = (
    ("input_delay_ms", "inp_input_delay"),
    ("processing_ms", "inp_processing"),
    ("presentation_ms", "inp_presentation"),
)

_UPSERT_HEAD: Final = (
    "INSERT INTO mip_app.rum_daily AS r (slot, day, metric, route, facet, rating, builds, "
    "sample_count, value_sum, value_min, value_max, buckets) VALUES "
)
_UPSERT_TAIL: Final = (
    " ON CONFLICT (slot, metric, route, facet, rating) DO UPDATE SET "
    "sample_count = CASE WHEN r.day = EXCLUDED.day THEN r.sample_count + EXCLUDED.sample_count "
    "ELSE EXCLUDED.sample_count END, "
    "value_sum = CASE WHEN r.day = EXCLUDED.day THEN r.value_sum + EXCLUDED.value_sum "
    "ELSE EXCLUDED.value_sum END, "
    "value_min = CASE WHEN r.day = EXCLUDED.day THEN LEAST(r.value_min, EXCLUDED.value_min) "
    "ELSE EXCLUDED.value_min END, "
    "value_max = CASE WHEN r.day = EXCLUDED.day THEN GREATEST(r.value_max, EXCLUDED.value_max) "
    "ELSE EXCLUDED.value_max END, "
    "buckets = CASE WHEN r.day = EXCLUDED.day THEN "
    "ARRAY(SELECT a + b FROM unnest(r.buckets, EXCLUDED.buckets) AS t(a, b)) "
    "ELSE EXCLUDED.buckets END, "
    "builds = CASE WHEN r.day = EXCLUDED.day THEN "
    "(ARRAY(SELECT DISTINCT x FROM unnest(r.builds || EXCLUDED.builds) AS u(x) ORDER BY x))[1:8] "
    "ELSE EXCLUDED.builds END, "
    "day = EXCLUDED.day "
    "WHERE EXCLUDED.day >= r.day"
)
_ROW_COLUMNS: Final = (
    "slot", "day", "metric", "route", "facet", "rating", "builds",
    "sample_count", "value_sum", "value_min", "value_max", "buckets",
)
_ROW_CASTS: Final = {"day": "::date", "builds": "::text[]", "buckets": "::int[]"}
_ZERO_SQL: Final = (
    "UPDATE mip_app.rum_daily SET sample_count = 0, value_sum = 0, value_min = NULL, "
    "value_max = NULL, buckets = %(zeros)s::int[], builds = '{}'::text[] "
    "WHERE day < %(cutoff)s::date AND sample_count > 0"
)

Key = tuple[date, str, str, str, str]


@dataclass
class _Aggregate:
    sample_count: int = 0
    value_sum: float = 0.0
    value_min: float | None = None
    value_max: float | None = None
    buckets: list[int] = field(default_factory=zero_buckets)
    builds: set[str] = field(default_factory=set)

    def add(self, metric: str, value: float, build: str) -> None:
        self.sample_count += 1
        self.value_sum += value
        self.value_min = value if self.value_min is None else min(self.value_min, value)
        self.value_max = value if self.value_max is None else max(self.value_max, value)
        self.buckets[bucket_index(metric, value)] += 1
        self.builds.add(build)


def ring_slot(day: date) -> int:
    """The day's slot in the 90-slot ring."""
    return (day - RING_EPOCH).days % RING_DAYS


def facet_for(event: RumEvent) -> str:
    """The facet of an event, built only from its closed-vocabulary details."""
    details = event.details
    if event.metric == "api_call":
        return f"{details.get('api_route') or '-'}|{details.get('cache') or '-'}"
    if event.metric == "client_error":
        return (
            f"{details.get('error_name')}|{details.get('error_kind')}|"
            f"{details.get('boundary') or '-'}"
        )
    if event.metric == "inp":
        return str(details.get("interaction_target") or "other")
    if event.metric == "lcp":
        return str(details.get("lcp_element") or "other")
    return ""


def _samples(event: RumEvent) -> Iterator[tuple[str, float]]:
    """(metric, value) rows one event feeds: an INP event feeds up to four."""
    if event.metric == "long_task":
        return
    yield event.metric, float(event.value)
    if event.metric != "inp":
        return
    for key, metric in _INP_PHASES:
        value = event.details.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            yield metric, float(value)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _current_build() -> str:
    sha = (settings.mip_git_sha or "").strip()
    return sha[:12] if sha else "unversioned"


def _default_client() -> Any:
    from backend.services.lakebase import get_lakebase_client

    return get_lakebase_client()


def _breaker_open() -> bool:
    from backend.services.resilience import CircuitBreaker, get_breaker

    return get_breaker("lakebase").state == CircuitBreaker.OPEN


class RumRollup:
    """The thread-safe accumulator and its flusher."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] = _default_client,
        clock: Callable[[], datetime] = _utc_now,
        autostart: bool = True,
    ) -> None:
        self._client_factory = client_factory
        self._clock = clock
        self._autostart = autostart
        self._lock = Lock()
        self._flush_lock = Lock()
        self._pending: dict[Key, _Aggregate] = {}
        self._events = 0
        self._dropped = 0
        self._zeroed_day: date | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    # -- accumulate ------------------------------------------------------
    def add(self, event: RumEvent) -> None:
        day = self._clock().astimezone(UTC).date()
        build = _current_build()
        facet = facet_for(event)
        added = False
        with self._lock:
            for metric, value in _samples(event):
                key = (day, metric, event.route, facet, rating(metric, value))
                aggregate = self._pending.get(key)
                if aggregate is None:
                    if len(self._pending) >= MAX_PENDING_KEYS:
                        self._dropped += 1
                        continue
                    aggregate = self._pending[key] = _Aggregate()
                aggregate.add(metric, value, build)
                added = True
            if added:
                self._events += 1
            start = self._autostart and added and self._thread is None
            if start:
                self._stop.clear()
                self._thread = Thread(target=self._run, name=FLUSHER_THREAD_NAME, daemon=True)
        if start and self._thread is not None:
            self._thread.start()

    def pending_keys(self) -> list[Key]:
        with self._lock:
            return sorted(self._pending)

    # -- flush ---------------------------------------------------------------
    def flush(self) -> None:
        """Write the pending aggregates once. Never raises."""
        with self._flush_lock:
            try:
                self._flush()
            except Exception:  # noqa: BLE001 -- telemetry never fails a caller
                emit(log, "rum_rollup_flush_failed", level=logging.WARNING, rows=0, events=0, dropped=0)

    def _flush(self) -> None:
        if _breaker_open():
            return
        with self._lock:
            if not self._pending and not self._dropped:
                return
            batch, self._pending = self._pending, {}
            events, self._events = self._events, 0
            dropped, self._dropped = self._dropped, 0
        started = time.perf_counter()
        today = self._clock().astimezone(UTC).date()
        rows = _rows(batch)
        try:
            if rows:
                self._write(rows, today)
        except Exception:  # noqa: BLE001 -- the batch is dropped, never retried
            emit(log, "rum_rollup_flush_failed", level=logging.WARNING, rows=len(rows), events=events,
                 dropped=dropped)
            return
        emit(
            log,
            "rum_rollup_flushed",
            rows=len(rows),
            events=events,
            dropped=dropped,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    def _write(self, rows: list[dict[str, Any]], today: date) -> None:
        sql, params = upsert_statement(rows)
        zero = self._zeroed_day != today
        client = self._client_factory()
        with client.transaction() as conn, conn.cursor() as cur:
            if zero:
                cur.execute(_ZERO_SQL, {"zeros": zero_buckets(), "cutoff": today - timedelta(days=RING_DAYS - 1)})
            cur.execute(sql, params)
        if zero:
            self._zeroed_day = today

    # -- lifecycle -------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.wait(FLUSH_INTERVAL_S):
            self.flush()

    def stop(self) -> None:
        """Stop the flusher thread (if any) and wait briefly for it."""
        with self._lock:
            thread, self._thread = self._thread, None
        self._stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    def shutdown(self) -> None:
        """Stop the flusher, then flush what is pending once. Idempotent."""
        self.stop()
        self.flush()


def _rows(batch: dict[Key, _Aggregate]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (day, metric, route, facet, row_rating), aggregate in sorted(batch.items()):
        rows.append(
            {
                "slot": ring_slot(day),
                "day": day,
                "metric": metric,
                "route": route,
                "facet": facet,
                "rating": row_rating,
                "builds": sorted(aggregate.builds)[:MAX_BUILDS],
                "sample_count": aggregate.sample_count,
                "value_sum": aggregate.value_sum,
                "value_min": aggregate.value_min,
                "value_max": aggregate.value_max,
                "buckets": list(aggregate.buckets),
            }
        )
    return rows


def upsert_statement(rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """ONE multi-row upsert; every value is a bound, named parameter."""
    params: dict[str, Any] = {}
    tuples: list[str] = []
    for index, row in enumerate(rows):
        slots: list[str] = []
        for column in _ROW_COLUMNS:
            name = f"{column}_{index}"
            params[name] = row[column]
            slots.append("%(" + name + ")s" + _ROW_CASTS.get(column, ""))
        tuples.append("(" + ", ".join(slots) + ")")
    return _UPSERT_HEAD + ", ".join(tuples) + _UPSERT_TAIL, params


# ---------------------------------------------------------------------------
# The process singleton.
# ---------------------------------------------------------------------------

_SINGLETON: RumRollup | None = None
_SINGLETON_LOCK = Lock()


def _rollup() -> RumRollup:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = RumRollup()
        return _SINGLETON


def add(event: RumEvent) -> None:
    """Fold one validated event into the process's pending day aggregates."""
    _rollup().add(event)


def flush_on_shutdown() -> None:
    """Stop the flusher and write what is pending. Idempotent; never raises."""
    with _SINGLETON_LOCK:
        rollup = _SINGLETON
    if rollup is None:
        return
    with contextlib.suppress(Exception):
        rollup.shutdown()


atexit.register(flush_on_shutdown)


def _reset_for_tests(
    *,
    client_factory: Callable[[], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
    autostart: bool = False,
) -> RumRollup:
    """Replace the singleton (its flusher stopped) with a fresh one; returns it."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        previous = _SINGLETON
        _SINGLETON = RumRollup(
            client_factory=client_factory or _default_client,
            clock=clock or _utc_now,
            autostart=autostart,
        )
        fresh = _SINGLETON
    if previous is not None:
        previous.stop()
    return fresh


__all__ = [
    "FLUSHER_THREAD_NAME",
    "MAX_BUILDS",
    "MAX_PENDING_KEYS",
    "RumRollup",
    "add",
    "facet_for",
    "flush_on_shutdown",
    "ring_slot",
    "upsert_statement",
]
