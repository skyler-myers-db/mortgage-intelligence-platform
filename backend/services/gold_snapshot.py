"""The learned gold snapshot: a cache generation that moves on a gold refresh.

Audit delivery-06 remainder. Hot aggregates are served stale-while-revalidate
for up to the 24 h hard cap, so after a gold refresh a process could keep
answering from the previous snapshot until each key's own soft TTL expired.
This module learns which snapshot is current and moves a GENERATION when it
advances; ``GoldAggregateCache`` and the gold-versioned ``TTLCache`` keys
compare an entry's generation at lookup, so an entry from an older snapshot is
a miss (single-flight, inline) rather than a hit or a plain stale serve.

* Snapshot id: ``SELECT CAST(MAX(checked_at) AS STRING) FROM
  gold.source_readiness``. ``checked_at`` is the refresh run's anchor stamped
  by ``ctas_source_readiness``, which depends on lead_scores, lead_population,
  segment_population and borrower_dossier, so the id moves only after the hot
  lead tables are rebuilt. ``ref.refresh_run_state.refresh_at`` is written at
  the TOP of the DAG and would advance mid-refresh. source_readiness is a
  ~20-row gold table, so App SQL stays gold-only.
* The probe runs ONLY on the ``mip-gold-swr`` executor and ONLY when a
  ``GoldAggregateCache`` read is already going to the warehouse (a scheduled
  background refresh, or an inline miss), at most once per
  ``settings.mip_cache_ttl_s``. There is no timer, so an idle warehouse still
  auto-stops.
* The first learn sets the id without a bump. A later DIFFERENT id bumps the
  generation and logs INFO ``gold_snapshot_advanced`` (the generation and the
  previous and current snapshot timestamps only). A failed probe keeps the
  generation, logs WARNING ``gold_snapshot_probe_failed`` with the exception
  type only, and retries after the next soft TTL.
* The watch is off when ``mip_cache_ttl_s <= 0`` and off under pytest unless a
  test installs one (the ``backpressure`` pattern), so unit tests that never
  install a watch see generation 0 forever.

Learning is per process: each App worker learns on its own next warehouse
read, so two workers can disagree for up to one soft TTL.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Executor
from threading import Lock

from backend.config.settings import _running_under_pytest, settings
from backend.services.databricks_sql_helpers import qualify
from backend.services.observability import emit

log = logging.getLogger(__name__)


def snapshot_id_sql() -> str:
    return (
        "SELECT CAST(MAX(checked_at) AS STRING) AS snapshot_id "
        f"FROM {qualify('gold', 'source_readiness')}"
    )


def _warehouse_probe() -> str | None:
    from backend.services.databricks_sql import get_sql_client

    row = get_sql_client().execute_one(snapshot_id_sql()) or {}
    value = row.get("snapshot_id")
    return str(value) if value else None


class GoldSnapshotWatch:
    """A process-wide record of the current gold snapshot and its generation."""

    def __init__(
        self,
        probe: Callable[[], str | None] = _warehouse_probe,
        *,
        ttl_s: float | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe = probe
        self._ttl_s = ttl_s
        self._now = now
        self._lock = Lock()
        self._generation = 0
        self._snapshot_id: str | None = None
        self._next_probe_at = float("-inf")
        self._probing = False

    def _ttl(self) -> float:
        return float(settings.mip_cache_ttl_s if self._ttl_s is None else self._ttl_s)

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def snapshot_id(self) -> str | None:
        with self._lock:
            return self._snapshot_id

    def due(self) -> bool:
        """A probe may run now (cheap: no claim, no I/O)."""
        if self._ttl() <= 0:
            return False
        with self._lock:
            return not self._probing and self._now() >= self._next_probe_at

    def schedule_probe(self, executor: Executor) -> None:
        """Submit one probe to ``executor`` when due (the inline-miss path)."""
        if not self.due():
            return
        try:
            executor.submit(self.probe_if_due)
        except RuntimeError:
            return  # interpreter shutdown: keep the generation

    def probe_if_due(self) -> None:
        """Probe at most once per soft TTL; call it on the gold-swr executor only."""
        ttl = self._ttl()
        if ttl <= 0:
            return
        with self._lock:
            now = self._now()
            if self._probing or now < self._next_probe_at:
                return
            self._probing = True
            self._next_probe_at = now + ttl
        try:
            snapshot = self._probe()
        except Exception as exc:  # noqa: BLE001 -- a probe never fails a read
            emit(
                log,
                "gold_snapshot_probe_failed",
                level=logging.WARNING,
                exc_type=type(exc).__name__,
            )
            return
        finally:
            with self._lock:
                self._probing = False
        self._learn(snapshot)

    def _learn(self, snapshot: str | None) -> None:
        if not snapshot:
            return
        with self._lock:
            previous = self._snapshot_id
            if previous == snapshot:
                return
            self._snapshot_id = snapshot
            if previous is None:
                return  # the first learn sets the id without a bump
            self._generation += 1
            generation = self._generation
        emit(
            log,
            "gold_snapshot_advanced",
            generation=generation,
            previous=previous,
            current=snapshot,
        )


_WATCH: GoldSnapshotWatch | None = None
_WATCH_LOCK = Lock()


def get_gold_snapshot_watch() -> GoldSnapshotWatch | None:
    """The process watch, or None when it is off (TTL <= 0, or pytest without one)."""
    global _WATCH
    if _WATCH is not None:
        return _WATCH
    if settings.mip_cache_ttl_s <= 0 or _running_under_pytest():
        return None
    with _WATCH_LOCK:
        if _WATCH is None:
            _WATCH = GoldSnapshotWatch()
        return _WATCH


def snapshot_generation() -> int:
    """The current gold snapshot generation (0 while no watch runs)."""
    watch = get_gold_snapshot_watch()
    return watch.generation() if watch is not None else 0


def install_gold_snapshot_watch_for_tests(watch: GoldSnapshotWatch | None) -> None:
    global _WATCH
    with _WATCH_LOCK:
        _WATCH = watch


__all__ = [
    "GoldSnapshotWatch",
    "get_gold_snapshot_watch",
    "install_gold_snapshot_watch_for_tests",
    "snapshot_generation",
    "snapshot_id_sql",
]
