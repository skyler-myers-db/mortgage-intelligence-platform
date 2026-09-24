"""Rate Lever endpoint + repository (audit wow-stage-1).

``GET /api/v1/geo/rate-sensitivity`` (alias ``/api/geo/rate-sensitivity``)
serves the precomputed per-state scenario grid plus the live contactable
subset. Pins:

* the route shape and its evidence manifest; the read writes NO audit row;
* a cold warehouse is the resilience layer's honest 503 ``warming_up``, with
  no fabricated grid on the body;
* the repository pivots rows to per-state lists aligned to the grid and
  clamps ``contactable <= in_the_money <= addressable`` and
  ``rate_movable <= addressable``;
* an incomplete state grid is dropped with an observability event, never
  zero-filled; no rows (or no usable state) is ``built=False``;
* a missing table is ``built=False``, not a 503 (the deploy that introduces
  it promotes the App before the refresh builds it); any other missing
  object still fails;
* the cache is single-flight and stale-if-error, and a cold failure
  propagates to the 503 path.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.geo_rate_sensitivity import RateSensitivityResponse
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.rate_scenario import RATE_SCENARIO_STEPS_BPS
from backend.services.repositories import get_rate_sensitivity_repository
from backend.services.repositories.databricks_rate_sensitivity import (
    DatabricksRateSensitivityRepository,
    project_rate_sensitivity,
)
from backend.services.resilience import DependencyDownError, TTLCache
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

PATH = "/api/v1/geo/rate-sensitivity"
LEGACY_PATH = "/api/geo/rate-sensitivity"
STEPS = list(RATE_SCENARIO_STEPS_BPS)


def _rows_for(
    state: str,
    *,
    addressable: int,
    itm: list[int],
    contactable: list[int] | None = None,
    movable: int | None = None,
    skip_steps: tuple[int, ...] = (),
) -> list[dict[str, Any]]:
    # Statement Execution API rows arrive as strings.
    rows = []
    for index, step in enumerate(STEPS):
        if step in skip_steps:
            continue
        rows.append(
            {
                "state": state,
                "step_bps": str(step),
                "scenario_market_rate_pct": str(round(6.3 + step / 100, 6)),
                "addressable_borrowers": str(addressable),
                "rate_movable_borrowers": str(movable if movable is not None else addressable // 2),
                "in_the_money_borrowers": str(itm[index]),
                "min_spread_bps_applied": "75",
                "min_equity_pct_applied": "15",
                "book_as_of": "2026-07-14 12:00:00",
                "refreshed_at": "2026-07-14 12:00:00",
                "contactable_in_the_money": None if contactable is None else str(contactable[index]),
            }
        )
    return rows


IL_ITM = [900, 800, 700, 600, 500, 400, 300, 200, 100]
TX_ITM = [450, 400, 350, 300, 250, 200, 150, 100, 50]


class _FakeSqlClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.rows: list[dict[str, Any]] = []
        self.error: BaseException | None = None
        self.gate: threading.Event | None = None

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if self.gate is not None:
            self.gate.wait(timeout=5.0)
        if self.error is not None:
            raise self.error
        self.calls.append(statement)
        return list(self.rows)

    def execute_one(self, statement: str, parameters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


def _built_response() -> RateSensitivityResponse:
    return project_rate_sensitivity(
        _rows_for("IL", addressable=1000, itm=IL_ITM, contactable=[90, 80, 70, 60, 50, 40, 30, 20, 10])
        + _rows_for("TX", addressable=500, itm=TX_ITM, contactable=[45, 40, 35, 30, 25, 20, 15, 10, 5])
    )


class _StubRepo:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    def rate_sensitivity(self) -> RateSensitivityResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _built_response()


@pytest.fixture
def stub_repo() -> Iterator[_StubRepo]:
    repo = _StubRepo()
    prior = app.dependency_overrides.get(get_rate_sensitivity_repository)
    app.dependency_overrides[get_rate_sensitivity_repository] = lambda: repo
    try:
        yield repo
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_rate_sensitivity_repository, None)
        else:
            app.dependency_overrides[get_rate_sensitivity_repository] = prior


def test_route_returns_the_aligned_grid_and_its_evidence(stub_repo: _StubRepo) -> None:
    response = TestClient(app).get(PATH)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "built",
        "steps_bps",
        "scenario_market_rate_pct",
        "base_market_rate_pct",
        "thresholds",
        "states",
        "provenance",
    }
    assert body["built"] is True
    assert body["steps_bps"] == STEPS
    assert len(body["scenario_market_rate_pct"]) == len(STEPS)
    assert body["base_market_rate_pct"] == body["scenario_market_rate_pct"][STEPS.index(0)] == 6.3
    assert body["thresholds"] == {"min_spread_bps": 75, "min_equity_pct": 15}
    illinois = body["states"][0]
    assert set(illinois) == {"state", "addressable", "rate_movable", "in_the_money", "contactable_in_the_money"}
    assert illinois["state"] == "IL"
    assert illinois["in_the_money"] == IL_ITM
    provenance = body["provenance"]
    assert provenance["gold_source"] == "mip.gold.rate_sensitivity_rollup"
    assert "mip.gold.borrower_360" in provenance["book_source"]
    assert "mip.silver.lien_current" in provenance["book_source"]
    assert "fn_in_the_money" in provenance["rule_source"]
    assert "live" in provenance["contactable_source"]
    assert "not a forecast" in provenance["note"]
    assert stub_repo.calls == 1


def test_route_writes_no_audit_row(stub_repo: _StubRepo) -> None:
    audit = InMemoryAuditStore()
    prior = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        for path in (PATH, LEGACY_PATH):
            assert TestClient(app).get(path).status_code == 200
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior
    assert audit.list(limit=100) == []


def test_cold_warehouse_is_an_honest_warming_up_503() -> None:
    repo = _StubRepo(
        error=DependencyDownError(
            "warehouse",
            reason="statement timed out while the warehouse started",
            kind=DependencyDownError.KIND_WARMING_UP,
        )
    )
    prior = app.dependency_overrides.get(get_rate_sensitivity_repository)
    app.dependency_overrides[get_rate_sensitivity_repository] = lambda: repo
    try:
        response = TestClient(app).get(PATH)
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_rate_sensitivity_repository, None)
        else:
            app.dependency_overrides[get_rate_sensitivity_repository] = prior
    assert response.status_code == 503
    body = response.json()
    assert body["retryable"] is True
    assert body["reason"] == "warming_up"
    assert body["dependency"] == "warehouse"
    assert "states" not in body and "in_the_money" not in body
    assert "timed out" not in body["detail"]


def test_legacy_alias_answers(stub_repo: _StubRepo) -> None:
    assert TestClient(app).get(LEGACY_PATH).json()["built"] is True


def test_projection_pivots_aligned_lists_and_clamps_the_subsets() -> None:
    rows = _rows_for(
        "IL",
        addressable=1000,
        movable=1500,  # a drifted superset: clamped to addressable
        itm=[1200, 800, 700, 600, 500, 400, 300, 200, 100],  # step -100 above addressable
        contactable=[90, 900, 70, 60, 50, 40, 30, 20, 150],  # step -75 / +100 above in_the_money
    )
    result = project_rate_sensitivity(rows)

    assert result.built is True
    [illinois] = result.states
    assert illinois.addressable == 1000
    assert illinois.rate_movable == 1000
    assert illinois.in_the_money == [1000, 800, 700, 600, 500, 400, 300, 200, 100]
    assert illinois.contactable_in_the_money == [90, 800, 70, 60, 50, 40, 30, 20, 100]
    for index in range(len(STEPS)):
        assert illinois.contactable_in_the_money[index] <= illinois.in_the_money[index] <= illinois.addressable
    assert result.scenario_market_rate_pct[0] == 5.3
    assert result.base_market_rate_pct == 6.3
    assert result.provenance.book_as_of == "2026-07-14 12:00:00"


def test_unreported_contactable_stays_none_never_zero() -> None:
    result = project_rate_sensitivity(_rows_for("IL", addressable=1000, itm=IL_ITM, contactable=None))
    assert result.states[0].contactable_in_the_money is None


def test_incomplete_state_grid_is_dropped_with_an_event(caplog: pytest.LogCaptureFixture) -> None:
    rows = _rows_for("IL", addressable=1000, itm=IL_ITM) + _rows_for(
        "TX", addressable=500, itm=TX_ITM, skip_steps=(25,)
    )
    with caplog.at_level(logging.WARNING):
        result = project_rate_sensitivity(rows)

    assert [state.state for state in result.states] == ["IL"]
    events = [
        record for record in caplog.records
        if getattr(record, "mip_event", None) == "rate_sensitivity_state_grid_incomplete"
    ]
    assert len(events) == 1
    assert events[0].mip_extras == {"state": "TX", "missing_steps": 1}  # type: ignore[attr-defined]
    assert events[0].mip_outcome == "dropped"  # type: ignore[attr-defined]


def test_empty_table_is_not_built() -> None:
    result = project_rate_sensitivity([])
    assert result.built is False
    assert result.states == [] and result.steps_bps == [] and result.scenario_market_rate_pct == []
    assert result.base_market_rate_pct is None
    assert result.provenance.gold_source == "mip.gold.rate_sensitivity_rollup"


def test_no_usable_state_is_not_built() -> None:
    rows = _rows_for("TX", addressable=500, itm=TX_ITM, skip_steps=(0,))
    assert project_rate_sensitivity(rows).built is False


def test_missing_table_is_not_built_not_warming() -> None:
    client = _FakeSqlClient()
    client.error = DependencyDownError(
        "warehouse",
        reason="DatabricksSqlError: ...",
        last_error=DatabricksSqlError(
            "Databricks SQL statement did not succeed (state='FAILED' statement_id='x'): "
            "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`rate_sensitivity_rollup` "
            "cannot be found."
        ),
        kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
    )
    repo = DatabricksRateSensitivityRepository(client, cache=TTLCache())

    result = repo.rate_sensitivity()

    assert result.built is False
    assert result.states == []


def test_another_missing_object_still_fails() -> None:
    client = _FakeSqlClient()
    client.error = DatabricksSqlError("[TABLE_OR_VIEW_NOT_FOUND] `mip`.`gold`.`borrower_360` cannot be found.")
    repo = DatabricksRateSensitivityRepository(client, cache=TTLCache())

    with pytest.raises(DatabricksSqlError):
        repo.rate_sensitivity()


def test_repository_reads_one_statement_and_projects() -> None:
    client = _FakeSqlClient()
    client.rows = _rows_for("IL", addressable=1000, itm=IL_ITM, contactable=[9] * len(STEPS))
    repo = DatabricksRateSensitivityRepository(client, cache=TTLCache())

    result = repo.rate_sensitivity()

    assert len(client.calls) == 1
    assert "mip.gold.rate_sensitivity_rollup" in client.calls[0]
    assert result.states[0].contactable_in_the_money == [9] * len(STEPS)


def test_concurrent_cold_reads_are_single_flight() -> None:
    client = _FakeSqlClient()
    client.rows = _rows_for("IL", addressable=1000, itm=IL_ITM)
    client.gate = threading.Event()
    repo = DatabricksRateSensitivityRepository(client, cache=TTLCache())
    results: list[RateSensitivityResponse] = []

    threads = [threading.Thread(target=lambda: results.append(repo.rate_sensitivity())) for _ in range(4)]
    for thread in threads:
        thread.start()
    client.gate.set()
    for thread in threads:
        thread.join(timeout=10.0)

    assert len(results) == 4
    assert len(client.calls) == 1


def test_expired_entry_is_served_stale_when_the_refresh_fails() -> None:
    clock = [0.0]
    client = _FakeSqlClient()
    client.rows = _rows_for("IL", addressable=1000, itm=IL_ITM)
    repo = DatabricksRateSensitivityRepository(client, cache=TTLCache(now=lambda: clock[0]), cache_ttl_s=10.0)

    first = repo.rate_sensitivity()
    clock[0] = 11.0
    client.error = RuntimeError("warehouse down")
    second = repo.rate_sensitivity()

    assert second.states[0].in_the_money == first.states[0].in_the_money == IL_ITM


def test_cold_cache_failure_propagates_to_the_503_path() -> None:
    client = _FakeSqlClient()
    client.error = RuntimeError("warehouse down")
    repo = DatabricksRateSensitivityRepository(client, cache=TTLCache())

    with pytest.raises(RuntimeError, match="warehouse down"):
        repo.rate_sensitivity()
