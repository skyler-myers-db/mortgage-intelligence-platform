"""Last-login KPI delta primitives over ``mip_app.kpi_snapshots``.

S3: the small typed service S4's personalized home summary consumes.
Given an actor, it anchors on the actor's PREVIOUS visit (from the
throttled ``mip_app.user_visits`` ledger), finds the persisted headline
snapshot NEAREST that timestamp, reads the now-ish headline metrics live
from ``mip.semantics.portfolio_headline_metric_view`` (real UC read,
short-TTL cached like the portfolio preview), and returns the signed
per-measure deltas.

Edge contract (pinned by tests/unit/test_kpi_deltas.py):

* no previous visit  -> ``previous_visit_at``/``baseline``/``deltas`` are
  ``None``; ``current`` still carries the live numbers (first login).
* zero snapshots     -> ``baseline``/``deltas`` are ``None`` even when a
  previous visit exists (pre-backfill install; the deploy script's
  backfill step makes this state transient).
* one snapshot       -> it is the nearest snapshot regardless of which
  side of the visit it falls on.
* two or more        -> the closer of (latest at-or-before, earliest
  after); exact ties resolve to the at-or-before row.

The "previous" visit is the most recent ``user_visits`` row older than
``now - visit dedupe window``: the middleware writes at most one row per
actor per window, so anything inside the window is the CURRENT session's
row, not the last login.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from backend.schemas.kpi_deltas import (
    HEADLINE_COUNT_MEASURES,
    HeadlineKpis,
    KpiDeltaResult,
    KpiDeltas,
    KpiSnapshot,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.resilience import TTLCache
from backend.services.visit_tracking import DEFAULT_VISIT_DEDUPE_WINDOW_S

log = logging.getLogger(__name__)

# Live headline aggregates. Aliases match mip_app.kpi_snapshots columns and
# jobs/kpi_snapshot.py 1:1 (parity pinned by
# tests/unit/test_kpi_snapshot_contract.py).
_CURRENT_METRICS_SQL_TEMPLATE = (
    "SELECT "
    "  COUNT(*)                                                    AS marketable_population, "
    "  COALESCE(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END), 0)  AS refi_economics_screen, "
    "  COALESCE(SUM(CASE WHEN is_high_opportunity THEN 1 ELSE 0 END), 0) AS high_opportunity, "
    "  COALESCE(SUM(CASE WHEN offer_available THEN 1 ELSE 0 END), 0)     AS offers_available, "
    "  COALESCE(SUM(CASE WHEN offer_recommended THEN 1 ELSE 0 END), 0)   AS offers_recommended, "
    "  AVG(opportunity_score)                                      AS avg_opportunity_score "
    "FROM {metric_view}"
)

_SNAPSHOT_COLUMNS = (
    "snapshot_date, snapshot_at, source_view, "
    "marketable_population, refi_economics_screen, high_opportunity, "
    "offers_available, offers_recommended, avg_opportunity_score"
)

_PREVIOUS_VISIT_SQL = """
SELECT visited_at
FROM mip_app.user_visits
WHERE actor_email = %(actor_email)s
  AND visited_at < %(before)s
ORDER BY visited_at DESC
LIMIT 1
"""

# Both sides of the nearest-snapshot lookup ride the snapshot_at index as
# ORDER BY ... LIMIT 1 scans.
_SNAPSHOT_AT_OR_BEFORE_SQL = f"""
SELECT {_SNAPSHOT_COLUMNS}
FROM mip_app.kpi_snapshots
WHERE snapshot_at <= %(ts)s
ORDER BY snapshot_at DESC
LIMIT 1
"""

_SNAPSHOT_AFTER_SQL = f"""
SELECT {_SNAPSHOT_COLUMNS}
FROM mip_app.kpi_snapshots
WHERE snapshot_at > %(ts)s
ORDER BY snapshot_at ASC
LIMIT 1
"""

_CURRENT_METRICS_CACHE_KEY = "kpi_deltas.current_metrics"


def _snapshot_from_row(row: dict[str, Any]) -> KpiSnapshot:
    return KpiSnapshot.model_validate(row)


def _headline_from_row(row: dict[str, Any]) -> HeadlineKpis:
    values: dict[str, Any] = {
        measure: int(row.get(measure) or 0) for measure in HEADLINE_COUNT_MEASURES
    }
    avg_score = row.get("avg_opportunity_score")
    values["avg_opportunity_score"] = float(avg_score) if avg_score is not None else None
    return HeadlineKpis.model_validate(values)


def compute_deltas(current: HeadlineKpis, baseline: HeadlineKpis) -> KpiDeltas:
    """Signed current-minus-baseline differences (pure; golden-fixture pinned)."""
    counts = {
        measure: getattr(current, measure) - getattr(baseline, measure)
        for measure in HEADLINE_COUNT_MEASURES
    }
    avg_delta: float | None = None
    if current.avg_opportunity_score is not None and baseline.avg_opportunity_score is not None:
        avg_delta = round(current.avg_opportunity_score - baseline.avg_opportunity_score, 2)
    return KpiDeltas(avg_opportunity_score=avg_delta, **counts)


class KpiDeltaService:
    """Typed read-side over ``user_visits`` + ``kpi_snapshots`` + the live
    headline metric view. All Lakebase reads go through the injected
    client (resilient singleton in production, fakes in unit tests)."""

    def __init__(
        self,
        lakebase_client: Any | None = None,
        sql_client: Any | None = None,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 30.0,
        current_session_grace_s: float = DEFAULT_VISIT_DEDUPE_WINDOW_S,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._lakebase_client = lakebase_client
        self._sql_client = sql_client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s
        self._current_session_grace_s = current_session_grace_s
        self._now = now

    # -- dependency accessors (lazy so unit tests never touch factories) --

    def _lakebase(self) -> Any:
        if self._lakebase_client is None:
            from backend.services.lakebase import get_lakebase_client

            self._lakebase_client = get_lakebase_client()
        return self._lakebase_client

    def _sql(self) -> Any:
        if self._sql_client is None:
            from backend.services.databricks_sql import get_sql_client

            self._sql_client = get_sql_client()
        return self._sql_client

    # -- primitives ------------------------------------------------------

    def current_metrics(self) -> HeadlineKpis:
        """Now-ish headline aggregates from the live S1 metric view."""

        def _load() -> HeadlineKpis:
            statement = _CURRENT_METRICS_SQL_TEMPLATE.format(
                metric_view=qualify("semantics", "portfolio_headline_metric_view")
            )
            row = self._sql().execute_one(statement) or {}
            return _headline_from_row(row)

        return self._cache.get_or_set(
            _CURRENT_METRICS_CACHE_KEY, _load, ttl_s=self._cache_ttl_s
        )

    def previous_visit(self, actor_email: str, *, before: datetime) -> datetime | None:
        """Most recent recorded visit strictly earlier than ``before``."""
        row = self._lakebase().fetchone(
            _PREVIOUS_VISIT_SQL,
            {"actor_email": actor_email, "before": before},
        )
        return row["visited_at"] if row else None

    def nearest_snapshot(self, ts: datetime) -> KpiSnapshot | None:
        """Snapshot with the smallest |snapshot_at - ts|; ties prefer the
        at-or-before row. Returns None only when the table is empty."""
        client = self._lakebase()
        before_row = client.fetchone(_SNAPSHOT_AT_OR_BEFORE_SQL, {"ts": ts})
        after_row = client.fetchone(_SNAPSHOT_AFTER_SQL, {"ts": ts})
        if before_row is None and after_row is None:
            return None
        if before_row is None:
            assert after_row is not None
            return _snapshot_from_row(after_row)
        if after_row is None:
            return _snapshot_from_row(before_row)
        before_gap = abs((ts - before_row["snapshot_at"]).total_seconds())
        after_gap = abs((after_row["snapshot_at"] - ts).total_seconds())
        return _snapshot_from_row(before_row if before_gap <= after_gap else after_row)

    # -- the S4 surface ----------------------------------------------------

    def deltas_for_actor(self, actor_email: str) -> KpiDeltaResult:
        """Current metrics vs the snapshot nearest the actor's previous visit."""
        now = self._now()
        before = now - timedelta(seconds=self._current_session_grace_s)
        previous_visit_at = self.previous_visit(actor_email, before=before)
        current = self.current_metrics()

        baseline_snapshot: KpiSnapshot | None = None
        if previous_visit_at is not None:
            baseline_snapshot = self.nearest_snapshot(previous_visit_at)

        if baseline_snapshot is None:
            return KpiDeltaResult(
                actor_email=actor_email,
                previous_visit_at=previous_visit_at,
                current=current,
            )
        baseline = HeadlineKpis.model_validate(
            baseline_snapshot.model_dump(
                include=set(HEADLINE_COUNT_MEASURES) | {"avg_opportunity_score"}
            )
        )
        return KpiDeltaResult(
            actor_email=actor_email,
            previous_visit_at=previous_visit_at,
            baseline_snapshot_at=baseline_snapshot.snapshot_at,
            current=current,
            baseline=baseline,
            deltas=compute_deltas(current, baseline),
        )


_SERVICE: KpiDeltaService | None = None
_SERVICE_LOCK = Lock()


def get_kpi_delta_service() -> KpiDeltaService:
    """Process-singleton accessor (mirrors the other service factories)."""
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = KpiDeltaService()
    return _SERVICE


def _reset_service_for_tests() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None
