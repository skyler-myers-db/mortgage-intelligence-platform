"""A gold-versioned TTLCache entry carries the generation its read BEGAN under.

W5c integration follow-up of w5-gold-slot-cache (fix1, nonBlocking 2), across
the w5-lead-queue-paging repositories: the callers that read outside
``get_or_set`` (the Lead Queue page through LeadPageCache, the lead count and
the lead facets) capture ``TTLCache.generation_for`` before their read and
pass it to ``set`` / ``put``. A gold snapshot that advances while the read
runs then leaves the entry at the older generation, so the next lookup
misses and re-reads instead of serving the pre-advance rows as current until
the TTL ends. Each repository case advances the snapshot from inside the
fake warehouse's first statement.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from backend.services.gold_snapshot import (
    GoldSnapshotWatch,
    install_gold_snapshot_watch_for_tests,
    snapshot_generation,
)
from backend.services.repositories.databricks_lead_facets import DatabricksLeadFacetRepository
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.resilience_cache import TTLCache
from tests.fixtures.sqlite_lead_warehouse import SqliteLeadWarehouse, gold_lead_row


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def advance() -> Iterator[Callable[[], None]]:
    """Install a snapshot watch at generation 0; the returned call moves it on by one."""

    clock = _Clock()
    ids = iter(f"2026-10-0{day} 02:00:00" for day in range(1, 10))
    watch = GoldSnapshotWatch(lambda: next(ids), ttl_s=300, now=clock)
    install_gold_snapshot_watch_for_tests(watch)
    watch.probe_if_due()
    assert snapshot_generation() == 0

    def _advance() -> None:
        before = snapshot_generation()
        clock.now += 300
        watch.probe_if_due()
        assert snapshot_generation() == before + 1

    try:
        yield _advance
    finally:
        install_gold_snapshot_watch_for_tests(None)


def test_a_set_stamped_with_the_pre_read_generation_misses_after_a_mid_read_advance(
    advance: Callable[[], None],
) -> None:
    ttl = TTLCache()
    generation = ttl.generation_for("lead_count:k")
    advance()  # the read runs across the refresh
    ttl.set("lead_count:k", 7, 900, generation=generation)

    assert ttl.get("lead_count:k") is None, "rows read before the advance are not current"
    assert ttl.get_stale("lead_count:k") == 7, "get_stale still serves them"
    # Control: the un-captured stamp (the pre-fix call shape) calls them current.
    ttl.set("lead_count:k", 7, 900)
    assert ttl.get("lead_count:k") == 7
    # Every unversioned key is unaffected.
    assert ttl.generation_for("geo:state") == 0


class _AdvancingWarehouse(SqliteLeadWarehouse):
    """The first statement runs while the gold snapshot advances."""

    def __init__(self, rows: list[dict[str, Any]], advance: Callable[[], None]) -> None:
        super().__init__(rows)
        self._advance: Callable[[], None] | None = advance
        self.reads = 0

    def _tick(self) -> None:
        self.reads += 1
        if self._advance is not None:
            advance, self._advance = self._advance, None
            advance()

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        self._tick()
        return super().execute(sql, params)

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        self._tick()
        return super().execute_one(sql, params)


def test_a_lead_page_read_across_an_advance_is_read_again(advance: Callable[[], None]) -> None:
    warehouse = _AdvancingWarehouse([gold_lead_row(index) for index in range(40)], advance)
    repo = DatabricksLeadRepository(warehouse, cache_ttl_s=300.0)  # type: ignore[arg-type]

    first = repo.list_page(None, None, limit=500)
    reads = warehouse.reads
    second = repo.list_page(None, None, limit=500)
    assert warehouse.reads > reads, "the page read across the advance is a miss"
    third_reads = warehouse.reads
    repo.list_page(None, None, limit=500)
    assert warehouse.reads == third_reads, "the re-read is cached at the new generation"
    assert [lead.borrower_id for lead in second.leads] == [lead.borrower_id for lead in first.leads]


def test_a_lead_count_read_across_an_advance_is_read_again(advance: Callable[[], None]) -> None:
    warehouse = _AdvancingWarehouse([gold_lead_row(index) for index in range(40)], advance)
    repo = DatabricksLeadRepository(warehouse, cache_ttl_s=300.0)  # type: ignore[arg-type]

    assert repo.count(None, None) == 40
    warehouse.count = 41  # what the refreshed gold holds
    assert repo.count(None, None) == 41, "the count read across the advance is a miss"
    reads = warehouse.reads
    assert repo.count(None, None) == 41
    assert warehouse.reads == reads, "the re-read is cached at the new generation"


class _FacetClient:
    def __init__(self, advance: Callable[[], None]) -> None:
        self._advance: Callable[[], None] | None = advance
        self.value = 3
        self.reads = 0

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        _ = (sql, params)
        self.reads += 1
        if self._advance is not None:
            advance, self._advance = self._advance, None
            advance()
        return [{"facet_value": "IL", "facet_count": self.value}]

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object]:
        raise AssertionError(f"unexpected execute_one: {sql}")


def test_a_facet_read_across_an_advance_is_read_again(advance: Callable[[], None]) -> None:
    client = _FacetClient(advance)
    repo = DatabricksLeadFacetRepository(client, cache_ttl_s=300.0)  # type: ignore[arg-type]

    assert repo.facets("state", segment=None, portfolio_id=None).buckets == [("IL", 3)]
    client.value = 4
    assert repo.facets("state", segment=None, portfolio_id=None).buckets == [("IL", 4)]
    assert repo.facets("state", segment=None, portfolio_id=None).buckets == [("IL", 4)]
    assert client.reads == 2, "one re-read after the advance, then a hit"
