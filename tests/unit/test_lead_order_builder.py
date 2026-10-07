"""The ONE Lead Queue order and keyset builder (D-audit-reads-a, tables-02).

The generated statements are EXECUTED, not string-matched: an in-memory
SQLite database stands in for the warehouse (``mip.gold.`` is stripped; the
dialect needs nothing else here: unary minus, ``NULLS LAST``, ``CONCAT`` and
named ``:params`` all run as Databricks SQL runs them). Pinned:

* pages 0 + 1 (500 + 500, resumed from the keyset of page 0's last RAW row)
  equal the first 1,000 rows of ONE ``ORDER BY``, for every sort and
  direction, on the lead_population and the borrower_360 paths, with NULL
  equity and NULL rate runs straddling the page boundary and heavy ties;
* ``rank`` returns exactly today's row order on both paths;
* ``has_more`` is the +1 sentinel: an exact multiple of the page size is the
  end of the view;
* the ``lead_list`` cache key differs by sort, direction and keyset;
* ITEM 11 routing: ``list``, ``list_page`` and ``list_with_identity`` all
  project through the shared ``_LEAD_POPULATION_SELECT_FROM_LP/_B360``
  constants, so a projection added there reaches paged reads unchanged.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from backend.services.repositories.databricks_lead_order import (
    LEAD_POPULATION_SOURCE,
    MATCHED_SOURCE,
    RANK,
    LeadOrder,
    keyset_clause,
    keyset_of_row,
    order_by_sql,
)
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.repositories.databricks_shared import (
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)
from tests.fixtures.sqlite_lead_warehouse import SqliteLeadWarehouse, gold_lead_row

PAGE = 500
ROWS = 1_050
SORTS = [("rank", None)] + [(sort, d) for sort in ("score", "equity", "rate", "confidence") for d in ("asc", "desc")]


_SqliteWarehouse = SqliteLeadWarehouse


def _row(index: int) -> dict[str, Any]:
    return gold_lead_row(index)


@pytest.fixture
def warehouse() -> _SqliteWarehouse:
    return _SqliteWarehouse([_row(index) for index in range(ROWS)])


def _repo(warehouse: _SqliteWarehouse, *, cache_ttl_s: float = 0.0) -> DatabricksLeadRepository:
    return DatabricksLeadRepository(warehouse, cache_ttl_s=cache_ttl_s)  # type: ignore[arg-type]


PATHS = {
    # No filter: gold.lead_population, ranked by -rank_overall.
    "lead_population": {},
    # A geography: gold.borrower_360, ranked by opportunity_score.
    "borrower_360": {"state_codes": ["IL"]},
}


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize(("sort", "sort_dir"), SORTS)
def test_two_keyset_pages_equal_the_first_thousand_rows_of_one_order(
    warehouse: _SqliteWarehouse, path: str, sort: str, sort_dir: str | None
) -> None:
    repo = _repo(warehouse)
    filters = PATHS[path]

    whole = repo.list_page(None, None, limit=2 * PAGE, sort=sort, sort_dir=sort_dir, **filters)
    first = repo.list_page(None, None, limit=PAGE, sort=sort, sort_dir=sort_dir, **filters)
    second = repo.list_page(
        None, None, limit=PAGE, sort=sort, sort_dir=sort_dir, after=first.last_keyset, **filters
    )

    expected = [lead.borrower_id for lead in whole.leads]
    paged = [lead.borrower_id for lead in first.leads + second.leads]
    assert len(expected) == 2 * PAGE
    assert paged == expected
    assert first.has_more and second.has_more  # 1,050 rows: a third page exists
    assert len(set(paged)) == 2 * PAGE
    table = "borrower_360" if path == "borrower_360" else "lead_population"
    assert all(table in sql for sql in warehouse.statements)


def test_the_null_runs_straddle_the_boundary_so_both_null_branches_run(warehouse: _SqliteWarehouse) -> None:
    repo = _repo(warehouse)

    equity = repo.list_page(None, None, limit=PAGE, sort="equity", sort_dir="desc")
    rate = repo.list_page(None, None, limit=PAGE, sort="rate", sort_dir="asc")

    assert equity.last_keyset is not None and equity.last_keyset[0] == 1  # page 0 ends inside the NULLs
    assert rate.last_keyset is not None and rate.last_keyset[0] == 0  # page 0 ends before them
    rate_next = repo.list_page(None, None, limit=PAGE, sort="rate", sort_dir="asc", after=rate.last_keyset)
    assert rate_next.last_keyset is not None and rate_next.last_keyset[0] == 1  # page 1 reaches them
    after_null = repo.list_page(None, None, limit=PAGE, sort="equity", sort_dir="desc", after=equity.last_keyset)
    assert "lp.equity_estimate IS NULL AND" in warehouse.statements[-1]
    assert after_null.leads


@pytest.mark.parametrize("path", sorted(PATHS))
def test_rank_is_todays_row_order(warehouse: _SqliteWarehouse, path: str) -> None:
    repo = _repo(warehouse)
    filters = PATHS[path]
    page = repo.list_page(None, None, limit=ROWS, **filters)
    sql = warehouse.statements[-1]
    today = (
        "ORDER BY b.opportunity_score DESC, b.borrower_id ASC"
        if path == "borrower_360"
        else "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC"
    )
    rewritten = re.sub(r"ORDER BY .*? LIMIT", f"{today} LIMIT", sql)
    old_order = [row["borrower_id"] for row in warehouse.execute(rewritten, {"state_0": "IL"} if filters else {})]

    assert [lead.borrower_id for lead in page.leads] == old_order
    if path == "borrower_360":
        assert "ORDER BY b.opportunity_score DESC, b.borrower_id ASC LIMIT" in sql


def test_list_is_the_rank_page_and_returns_rows_only(warehouse: _SqliteWarehouse) -> None:
    repo = _repo(warehouse)

    assert [lead.borrower_id for lead in repo.list(None, None, limit=10)] == [
        lead.borrower_id for lead in repo.list_page(None, None, limit=10).leads
    ]


def test_an_exact_multiple_of_the_page_size_has_no_more(warehouse: _SqliteWarehouse) -> None:
    exact = _SqliteWarehouse([_row(index) for index in range(2 * PAGE)])
    repo = _repo(exact)

    first = repo.list_page(None, None, limit=PAGE)
    second = repo.list_page(None, None, limit=PAGE, after=first.last_keyset)

    assert first.has_more is True
    assert len(second.leads) == PAGE and second.has_more is False
    assert second.last_keyset is not None  # a full final page still names its last row


def test_the_keyset_is_read_from_the_raw_row_never_the_redacted_lead() -> None:
    row = {"borrower_id": "B-L00001QUEUEXX", "__rank_order": -3, "equity_estimate": None}

    assert keyset_of_row(LeadOrder.of("equity", "desc"), row) == (1, None, -3, "B-L00001QUEUEXX")
    assert keyset_of_row(RANK, row) == (-3, "B-L00001QUEUEXX")


def test_every_keyset_value_is_a_bound_parameter() -> None:
    params: dict[str, object] = {}
    clause = keyset_clause(LeadOrder.of("rate", "asc"), (0, "1 OR 1=1", -3, "B-1' --"), LEAD_POPULATION_SOURCE, params)

    assert "1 OR 1=1" not in clause and "B-1'" not in clause
    assert params == {"lead_after_value": "1 OR 1=1", "lead_after_rank": -3, "lead_after_id": "B-1' --"}


def test_an_unknown_sort_or_direction_is_refused() -> None:
    with pytest.raises(ValueError):
        LeadOrder.of("relationship", None)
    with pytest.raises(ValueError):
        LeadOrder.of("equity", "sideways")
    assert LeadOrder.of("rank", "asc") == RANK  # rank carries no direction


def test_a_mismatched_keyset_shape_is_refused() -> None:
    with pytest.raises(ValueError):
        keyset_clause(RANK, (0, None, -3, "B-1"), LEAD_POPULATION_SOURCE, {})
    with pytest.raises(ValueError):
        keyset_clause(LeadOrder.of("score", "desc"), (-3, "B-1"), LEAD_POPULATION_SOURCE, {})


def test_an_unknown_filter_keyword_is_refused(warehouse: _SqliteWarehouse) -> None:
    with pytest.raises(TypeError):
        _repo(warehouse).list_page(None, None, limit=10, not_a_filter=1)


def test_the_cache_key_differs_by_sort_direction_and_keyset(warehouse: _SqliteWarehouse) -> None:
    repo = _repo(warehouse, cache_ttl_s=300.0)

    first = repo.list_page(None, None, limit=PAGE, sort="equity", sort_dir="desc")
    calls = [
        dict(sort="equity", sort_dir="desc"),  # cached
        dict(sort="equity", sort_dir="asc"),
        dict(sort="score", sort_dir="desc"),
        dict(sort="equity", sort_dir="desc", after=first.last_keyset),
        dict(sort="equity", sort_dir="desc", after=first.last_keyset),  # cached
    ]
    counts = []
    for kwargs in calls:
        before = len(warehouse.statements)
        repo.list_page(None, None, limit=PAGE, **kwargs)  # type: ignore[arg-type]
        counts.append(len(warehouse.statements) - before)

    assert counts == [0, 1, 1, 1, 0]
    # The literal prefix the gold-version TTL scans key on is unchanged.
    keys = list(repo._cache._entries)
    assert len(keys) == 4 and all(key.startswith("lead_list:") for key in keys)


def test_rank_page_and_list_share_one_cache_entry(warehouse: _SqliteWarehouse) -> None:
    repo = _repo(warehouse, cache_ttl_s=300.0)

    repo.list(None, None, limit=PAGE)
    before = len(warehouse.statements)
    repo.list_page(None, None, limit=PAGE)

    assert len(warehouse.statements) == before


def test_matched_order_reads_the_projected_columns() -> None:
    assert order_by_sql(LeadOrder.of("equity", "asc"), MATCHED_SOURCE) == (
        "ORDER BY equity_estimate ASC NULLS LAST, __rank_order DESC, borrower_id ASC"
    )
    assert order_by_sql(RANK, MATCHED_SOURCE) == "ORDER BY __rank_order DESC, borrower_id ASC"


class _CapturingClient:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        _ = params
        self.statements.append(sql)
        return []

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        _ = (sql, params)
        return {"n": 0}


@pytest.mark.parametrize("filters", [{}, {"state_codes": ["IL"]}])
def test_item_11_every_ordered_read_projects_the_shared_constants(filters: dict[str, Any]) -> None:
    client = _CapturingClient()
    repo = DatabricksLeadRepository(client, cache_ttl_s=0.0)  # type: ignore[arg-type]
    projection = _LEAD_POPULATION_SELECT_FROM_B360 if filters else _LEAD_POPULATION_SELECT_FROM_LP

    repo.list(None, None, limit=5, **filters)
    repo.list_page(None, None, limit=5, sort="equity", sort_dir="asc", **filters)
    with pytest.raises(ValueError):  # no metadata row from the capturing client
        repo.list_with_identity(None, None, limit=5, sort="equity", sort_dir="asc", **filters)

    assert len(client.statements) == 3
    for sql in client.statements:
        assert projection in sql
    assert "ORDER BY equity_estimate ASC NULLS LAST, __rank_order DESC, borrower_id ASC" in client.statements[2]
