"""Workflow counts never ride the long-lived gold preview (audit ``delivery-06``).

The preview economics are served stale-while-revalidate for up to a day, so
the lifecycle-mirror counts (approved, in outreach) are overlaid per request
from their own entry keyed by the workflow generation. Every approval write
(``clear_sales_state_cache``) and every lifecycle-sync completion moves the
generation, so the counts re-read at once while the economics stay cached.
"""
from __future__ import annotations

from concurrent.futures import Executor, Future
from typing import Any

import pytest

from backend.schemas.portfolio import PortfolioCriteria, PortfolioPreviewRequest
from backend.services import job_trigger
from backend.services.gold_cache import GoldAggregateCache
from backend.services.lifecycle_sync import LifecycleSyncResult
from backend.services.repositories.databricks_analytics import DatabricksAnalyticsRepository
from backend.services.repositories.databricks_portfolio import DatabricksPortfolioRepository
from backend.services.sales_state import clear_sales_state_cache


class _NoRefreshExecutor(Executor):
    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        raise AssertionError("no background refresh expected inside the soft TTL")


class _ParkedRefreshExecutor(Executor):
    """Accepts background refreshes and never runs them (a slow warehouse)."""

    def __init__(self) -> None:
        self.parked: list[Any] = []

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.parked.append(fn)
        return Future()


class _Warehouse:
    """Answers the preview, trend, day-zero and lifecycle-mirror statements."""

    def __init__(self) -> None:
        self.approved = 7
        self.in_outreach = 2
        self.fail_counts = False
        self.segment_approval_rate = 1.0
        self.statements: list[str] = []

    def count(self, marker: str) -> int:
        return sum(marker in sql for sql in self.statements)

    def execute(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        self.statements.append(sql)
        if "funnel_snapshot_daily" in sql:
            return [
                {
                    "snapshot_date": "2026-09-20",
                    "snapshot_at": "2026-09-20T06:00:00",
                    "marketable_population": 100,
                    "high_intent_leads": 40,
                    "top_tier_opportunities": 12,
                    "offers_recommended": 30,
                    "avg_score": 61,
                    "approved_count": 3,
                    "in_outreach_count": 1,
                }
            ]
        if SEGMENT_OVERVIEW_SQL in sql:
            return [
                {
                    "segment_code": "itm",
                    "name": "Prime Refi Candidates",
                    "borrower_count": 100,
                    "mean_opportunity_score": 61,
                    "approval_rate": self.segment_approval_rate,
                    "outreach_rate": 0.5,
                }
            ]
        return []

    def execute_one(self, sql: str, params: Any = None) -> dict[str, Any]:
        self.statements.append(sql)
        if "borrower_lifecycle_state" in sql:
            if self.fail_counts:
                raise RuntimeError("mirror read failed")
            return {"approved_count": self.approved, "in_outreach_count": self.in_outreach}
        if "lead_population" in sql and "day_zero" in sql:
            return {"day_zero": False}
        return {"marketable_population": 100, "high_intent_leads": 40}


PREVIEW_SQL = "preview_population"
COUNTS_SQL = "borrower_lifecycle_state"
SEGMENT_OVERVIEW_SQL = "segment_dim"


@pytest.fixture
def warehouse() -> _Warehouse:
    return _Warehouse()


@pytest.fixture
def repo(warehouse: _Warehouse) -> DatabricksPortfolioRepository:
    cache = GoldAggregateCache(now=lambda: 0.0, executor=_NoRefreshExecutor())
    return DatabricksPortfolioRepository(warehouse, cache=cache, cache_ttl_s=120.0)  # type: ignore[arg-type]


def test_economics_are_cached_and_live_counts_overlay_them(
    repo: DatabricksPortfolioRepository, warehouse: _Warehouse
) -> None:
    first = repo.preview(None)
    second = repo.preview(None)

    assert (first.approved_count, first.in_outreach_count) == (7, 2)
    assert second == first
    assert warehouse.count(PREVIEW_SQL) == 1, "the preview economics ran once"
    assert warehouse.count(COUNTS_SQL) == 1, "the counts are cached within a generation"


def test_an_approval_write_rereads_the_counts_but_not_the_economics(
    repo: DatabricksPortfolioRepository, warehouse: _Warehouse
) -> None:
    assert repo.preview(None).approved_count == 7

    warehouse.approved = 8
    clear_sales_state_cache()  # every approve / reject / assign / outcome write
    after = repo.preview(None)

    assert after.approved_count == 8
    assert warehouse.count(COUNTS_SQL) == 2
    assert warehouse.count(PREVIEW_SQL) == 1


def test_a_completed_lifecycle_sync_rereads_the_counts(
    repo: DatabricksPortfolioRepository,
    warehouse: _Warehouse,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_MODE", "warehouse")
    monkeypatch.setattr(
        "backend.services.lifecycle_sync.sync_lifecycle_state_via_warehouse",
        lambda **_kwargs: LifecycleSyncResult(lakebase_rows=1, mirrored_rows=1, funnel_snapshot_rows=None),
    )
    assert repo.preview(None).in_outreach_count == 2

    warehouse.in_outreach = 5
    job_trigger.trigger_lifecycle_sync(reason="test")

    assert repo.preview(None).in_outreach_count == 5
    assert warehouse.count(COUNTS_SQL) == 2
    assert warehouse.count(PREVIEW_SQL) == 1


def test_approval_writes_leave_no_dead_count_keys_to_evict_other_previews(warehouse: _Warehouse) -> None:
    """Each bump sweeps the older counts generation out of the gold cache.

    Left in place, one orphaned ``portfolio.workflow_counts:N`` per approval
    write would age through the bounded LRU and push out previews nobody has
    re-read since (the reviewer's 256-entry concern, at a bound of 4 here).
    """
    cache = GoldAggregateCache(max_entries=4, now=lambda: 0.0, executor=_NoRefreshExecutor())
    repo = DatabricksPortfolioRepository(warehouse, cache=cache, cache_ttl_s=120.0)  # type: ignore[arg-type]
    filtered = PortfolioPreviewRequest(criteria=PortfolioCriteria(min_equity_pct=25))
    repo.preview(None)
    repo.preview(filtered)  # a preview nobody re-reads while the writes land
    assert warehouse.count(PREVIEW_SQL) == 2

    for approved in (8, 9, 10):
        warehouse.approved = approved
        clear_sales_state_cache()  # an approval write
        assert repo.preview(None).approved_count == approved

    repo.preview(filtered)
    assert warehouse.count(PREVIEW_SQL) == 2, "the filtered preview is still served from cache"


def test_a_failed_counts_read_keeps_the_snapshot_counts_and_is_not_cached(
    repo: DatabricksPortfolioRepository, warehouse: _Warehouse
) -> None:
    warehouse.fail_counts = True
    degraded = repo.preview(None)

    assert (degraded.approved_count, degraded.in_outreach_count) == (3, 1)

    warehouse.fail_counts = False
    assert repo.preview(None).approved_count == 7, "the failure was never cached"


def test_filtered_previews_read_no_workflow_counts(
    repo: DatabricksPortfolioRepository, warehouse: _Warehouse
) -> None:
    filtered = repo.preview(
        PortfolioPreviewRequest(criteria=PortfolioCriteria(min_equity_pct=25))
    )

    assert filtered.approved_count is None
    assert filtered.in_outreach_count is None
    assert warehouse.count(COUNTS_SQL) == 0


def test_the_executive_funnel_key_moves_with_the_workflow_generation(warehouse: _Warehouse) -> None:
    analytics = DatabricksAnalyticsRepository(
        warehouse,  # type: ignore[arg-type]
        cache=GoldAggregateCache(now=lambda: 0.0, executor=_NoRefreshExecutor()),
        cache_ttl_s=300.0,
    )
    analytics.executive()
    analytics.executive()
    runs_before = len(warehouse.statements)

    clear_sales_state_cache()
    analytics.executive()

    assert len(warehouse.statements) > runs_before, "Approved / Actioned re-read the mirror"


@pytest.mark.parametrize(
    "elapsed_s",
    [0.0, 301.0],
    ids=["inside-soft-ttl", "soft-expired-stale-window"],
)
def test_the_segment_overview_key_moves_with_the_workflow_generation(
    warehouse: _Warehouse, elapsed_s: float
) -> None:
    """Segment approval_rate / outreach_rate read the lifecycle mirror.

    Past the 300 s soft TTL a generation-less key would serve the old rate
    stale for up to the 24 h hard cap after an approval write; the plain
    ``TTLCache`` it replaced recomputed there.
    """
    clock = [0.0]
    analytics = DatabricksAnalyticsRepository(
        warehouse,  # type: ignore[arg-type]
        cache=GoldAggregateCache(now=lambda: clock[0], executor=_ParkedRefreshExecutor()),
        cache_ttl_s=300.0,
    )
    analytics.segments()
    primed = analytics.segments()
    assert primed.overview[0].approval_rate == 1.0
    assert warehouse.count(SEGMENT_OVERVIEW_SQL) == 1

    clock[0] = elapsed_s
    warehouse.segment_approval_rate = 2.0
    clear_sales_state_cache()  # an approval write
    after = analytics.segments()

    assert warehouse.count(SEGMENT_OVERVIEW_SQL) == 2, "the overview re-read the mirror"
    assert after.overview[0].approval_rate == 2.0
