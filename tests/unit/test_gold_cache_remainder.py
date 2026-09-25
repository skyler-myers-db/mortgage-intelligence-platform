"""The delivery-06 remainder: three more hard-expiry caches serve stale-while-revalidate.

Each factory reads only the SQL client and settings (never the actor), so a
refresh outside any request is safe, and no audit-writing read sits behind
them. Every test injects the clock and a deferred executor, so a refresh runs
only when the test says so.

* the rate window (``analytics.rate_window``): 60 s soft TTL, default hard
  cap, stale-if-error;
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, Future
from typing import Any

import pytest

from backend.config.settings import settings
from backend.services.gold_cache import GoldAggregateCache
from backend.services.repositories.databricks_rate_window import DatabricksRateWindowRepository
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
