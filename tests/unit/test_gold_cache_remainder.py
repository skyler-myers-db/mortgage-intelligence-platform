"""The delivery-06 remainder: three more hard-expiry caches serve stale-while-revalidate.

Each factory reads only the SQL client and settings (never the actor), so a
refresh outside any request is safe, and no audit-writing read sits behind
them. Every test injects the clock and a deferred executor, so a refresh runs
only when the test says so.

* the rate window (``analytics.rate_window``): 60 s soft TTL, default hard
  cap, stale-if-error;
* the segment source-readiness gates: the list's soft TTL, stale-if-error,
  and a cold failure gates nothing and is NOT cached (the build used to
  swallow it, so a failure was stored as ``{}``);
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, Future
from typing import Any

import pytest

from backend.config.settings import settings
from backend.schemas.lead import SegmentSummary
from backend.services.gold_cache import GoldAggregateCache
from backend.services.repositories.databricks_geo import DatabricksSegmentRepository
from backend.services.repositories.databricks_rate_window import DatabricksRateWindowRepository
from backend.services.repositories.databricks_segment_gates import apply_source_gates
from backend.services.resilience import TTLCache


class _DeferredExecutor(Executor):
    def __init__(self) -> None:
        self.jobs: list[Callable[[], Any]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.jobs.append(lambda: fn(*args, **kwargs))
        return Future()

    def run_all(self) -> None:
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Warehouse:
    """Counts statements by marker and answers each from ``rows``."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.error: BaseException | None = None
        self.statements: list[str] = []

    def count(self, marker: str) -> int:
        return sum(marker in sql for sql in self.statements)

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        for marker, rows in self.rows.items():
            if marker in statement:
                return [dict(row) for row in rows]
        return []

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


def _swr(clock: _Clock, deferred: _DeferredExecutor) -> GoldAggregateCache:
    return GoldAggregateCache(now=clock, executor=deferred)


# ---------------------------------------------------------------------------
# The rate window.
# ---------------------------------------------------------------------------

_RATE_WINDOW = "gold.rate_window_weekly"


def _week(itm_count: int) -> list[dict[str, Any]]:
    return [
        {
            "series_id": "MORTGAGE30US",
            "observation_week": "2026-09-17",
            "market_rate_pct": "6.3",
            "is_latest": "true",
            "book_median_rate_pct": "7.1",
            "book_p25_rate_pct": "6.5",
            "book_p75_rate_pct": "7.6",
            "book_lien_count": "1000",
            "itm_count": str(itm_count),
            "min_spread_bps_applied": "75",
            "min_equity_pct_applied": "15",
            "book_as_of": "2026-09-20 06:00:00",
            "refreshed_at": "2026-09-20 06:00:00",
        }
    ]


@pytest.fixture
def rate_window() -> tuple[DatabricksRateWindowRepository, _Warehouse, _Clock, _DeferredExecutor]:
    warehouse, clock, deferred = _Warehouse(), _Clock(), _DeferredExecutor()
    warehouse.rows[_RATE_WINDOW] = _week(100)
    repo = DatabricksRateWindowRepository(warehouse, cache=_swr(clock, deferred))  # type: ignore[arg-type]
    return repo, warehouse, clock, deferred


def _itm(repo: DatabricksRateWindowRepository) -> int:
    return repo.rate_window().weeks[-1].itm_count


def test_rate_window_hits_before_its_soft_ttl(rate_window: Any) -> None:
    repo, warehouse, clock, deferred = rate_window
    assert _itm(repo) == 100
    clock.now += 59.0
    assert _itm(repo) == 100
    assert warehouse.count(_RATE_WINDOW) == 1 and deferred.jobs == []


def test_rate_window_serves_stale_with_exactly_one_refresh(rate_window: Any) -> None:
    repo, warehouse, clock, deferred = rate_window
    _itm(repo)
    warehouse.rows[_RATE_WINDOW] = _week(200)
    clock.now += 61.0

    assert _itm(repo) == 100, "served stale at once"
    assert _itm(repo) == 100
    assert len(deferred.jobs) == 1, "one refresh per key"
    assert warehouse.count(_RATE_WINDOW) == 1

    deferred.run_all()
    assert _itm(repo) == 200
    assert warehouse.count(_RATE_WINDOW) == 2


def test_rate_window_recomputes_inline_after_the_hard_cap(rate_window: Any) -> None:
    repo, warehouse, clock, deferred = rate_window
    _itm(repo)
    warehouse.rows[_RATE_WINDOW] = _week(300)
    clock.now += settings.mip_gold_cache_max_stale_s + 1.0

    assert _itm(repo) == 300
    assert deferred.jobs == []


def test_rate_window_keeps_last_good_when_a_refresh_fails(rate_window: Any) -> None:
    repo, warehouse, clock, deferred = rate_window
    _itm(repo)
    clock.now += 61.0
    assert _itm(repo) == 100
    warehouse.error = RuntimeError("warehouse down")
    deferred.run_all()

    assert _itm(repo) == 100, "last-good survives the failed refresh"
    assert len(deferred.jobs) == 1, "and the next read schedules another"


def test_rate_window_defaults_to_the_gold_cache_and_still_accepts_a_ttl_cache() -> None:
    warehouse = _Warehouse()
    assert isinstance(DatabricksRateWindowRepository(warehouse)._cache, GoldAggregateCache)  # type: ignore[arg-type]
    injected = TTLCache()
    assert DatabricksRateWindowRepository(warehouse, cache=injected)._cache is injected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The segment source-readiness gates.
# ---------------------------------------------------------------------------

_READINESS = "gold.source_readiness"


def _listed() -> list[SegmentSummary]:
    return [
        SegmentSummary(code="itm", name="itm", count=10, delta="+0%", avg_score=50, description="", color="#000000"),
        SegmentSummary(code="listed", name="listed", count=5, delta="+0%", avg_score=50, description="", color="#000000"),
    ]


def _gate(cache: Any, warehouse: _Warehouse) -> str:
    """The ``listed`` card's gate (it needs MLS Listings)."""
    gated = apply_source_gates(_listed(), client=warehouse, cache=cache, cache_ttl_s=30.0)  # type: ignore[arg-type]
    return {segment.code: segment.source_status for segment in gated}["listed"]


def _readiness(status: str) -> list[dict[str, Any]]:
    return [{"source_name": "MLS Listings", "status": status}]


def test_gates_hit_before_their_soft_ttl() -> None:
    warehouse, clock, deferred = _Warehouse(), _Clock(), _DeferredExecutor()
    warehouse.rows[_READINESS] = _readiness("roadmap")
    cache = _swr(clock, deferred)

    assert _gate(cache, warehouse) == "not_connected"
    clock.now += 29.0
    assert _gate(cache, warehouse) == "not_connected"
    assert warehouse.count(_READINESS) == 1 and deferred.jobs == []


def test_gates_serve_stale_with_exactly_one_refresh() -> None:
    warehouse, clock, deferred = _Warehouse(), _Clock(), _DeferredExecutor()
    warehouse.rows[_READINESS] = _readiness("roadmap")
    cache = _swr(clock, deferred)
    _gate(cache, warehouse)
    warehouse.rows[_READINESS] = _readiness("live")
    clock.now += 31.0

    assert _gate(cache, warehouse) == "not_connected"
    assert _gate(cache, warehouse) == "not_connected"
    assert len(deferred.jobs) == 1

    deferred.run_all()
    assert _gate(cache, warehouse) == "connected"


def test_gates_recompute_inline_after_the_hard_cap() -> None:
    warehouse, clock, deferred = _Warehouse(), _Clock(), _DeferredExecutor()
    warehouse.rows[_READINESS] = _readiness("roadmap")
    cache = _swr(clock, deferred)
    _gate(cache, warehouse)
    warehouse.rows[_READINESS] = _readiness("permission_denied")
    clock.now += settings.mip_gold_cache_max_stale_s + 1.0

    assert _gate(cache, warehouse) == "not_licensed"
    assert deferred.jobs == []


def test_gates_keep_last_good_when_a_refresh_fails() -> None:
    warehouse, clock, deferred = _Warehouse(), _Clock(), _DeferredExecutor()
    warehouse.rows[_READINESS] = _readiness("roadmap")
    cache = _swr(clock, deferred)
    _gate(cache, warehouse)
    clock.now += 31.0
    _gate(cache, warehouse)
    warehouse.error = RuntimeError("warehouse down")
    deferred.run_all()

    assert _gate(cache, warehouse) == "not_connected", "a failed refresh never un-gates a card"


def test_a_cold_gate_failure_gates_nothing_and_is_not_cached(caplog: pytest.LogCaptureFixture) -> None:
    warehouse, clock, deferred = _Warehouse(), _Clock(), _DeferredExecutor()
    warehouse.rows[_READINESS] = _readiness("roadmap")
    warehouse.error = RuntimeError("warehouse down")
    cache = _swr(clock, deferred)

    with caplog.at_level(logging.WARNING):
        gated = apply_source_gates(_listed(), client=warehouse, cache=cache, cache_ttl_s=30.0)  # type: ignore[arg-type]

    assert [segment.source_status for segment in gated] == ["connected", "connected"]
    assert [segment.source_name for segment in gated] == [None, None]
    assert [r for r in caplog.records if getattr(r, "mip_event", None) == "segment_source_readiness_unavailable"]

    warehouse.error = None
    assert _gate(cache, warehouse) == "not_connected", "the failure was never stored"
    assert warehouse.count(_READINESS) == 2


def test_the_segment_repository_gates_through_its_own_gold_cache() -> None:
    warehouse, clock, deferred = _Warehouse(), _Clock(), _DeferredExecutor()
    warehouse.rows[".gold.segment_population"] = [
        {"segment_code": "listed", "name": "listed", "count": 5, "delta_vs_prior": "+0%",
         "avg_score": 50, "description": "", "color": "#000000"},
    ]
    warehouse.rows[_READINESS] = _readiness("roadmap")
    gate_cache = _swr(clock, deferred)
    repo = DatabricksSegmentRepository(
        warehouse,  # type: ignore[arg-type]
        cache=TTLCache(now=clock),
        cache_ttl_s=10.0,
        gate_cache=gate_cache,
    )
    assert repo.list(None)[0].source_status == "not_connected"

    warehouse.rows[_READINESS] = _readiness("live")
    clock.now += 11.0  # the list's hard expiry; the gates are stale, not expired
    assert repo.list(None)[0].source_status == "not_connected"
    assert len(deferred.jobs) == 1
    deferred.run_all()
    clock.now += 11.0
    assert repo.list(None)[0].source_status == "connected"

    assert isinstance(DatabricksSegmentRepository(warehouse)._gate_cache, GoldAggregateCache)  # type: ignore[arg-type]
