"""Signal stack endpoint + repository (audit wow-stage-5).

``GET /api/v1/segments/combinations`` (alias ``/api/segments/combinations``)
serves the exact core-segment combinations from
``mip.gold.segment_combination_rollup`` plus a live contactable subset. Pins:

* the route shape and its evidence manifest; the read writes NO audit row;
* a cold warehouse is the resilience layer's honest 503 ``warming_up``;
* codes come back in core order, rows largest first then by key;
* ``contactable`` is clamped to ``0..addressable``, and an unreported one stays
  None, never 0;
* a row whose key is empty or names a non-core code is dropped with an event;
* only THIS table missing is ``built=False``; another missing table, or a
  missing schema, keeps its 503;
* rows that ALL fail the contract are a ``SegmentCombinationContractError``
  (counts only, an ERROR event) and the route's non-retryable 503
  ``contract_failure`` with no dependency; stale-if-error still serves the
  last good projection through it;
* one statement, single-flight and stale-if-error; a cold failure propagates.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.segment_combinations import CORE_SEGMENT_CODES, SegmentCombinationResponse
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import DatabricksSqlObjectMissingError
from backend.services.gold_cache import GoldAggregateCache
from backend.services.repositories import get_segment_combination_repository
from backend.services.repositories.databricks_segment_combinations import (
    SEGMENT_COMBINATIONS_SQL,
    DatabricksSegmentCombinationRepository,
    SegmentCombinationContractError,
    project_combinations,
)
from backend.services.resilience import DependencyDownError, TTLCache
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

PATH = "/api/v1/segments/combinations"
LEGACY_PATH = "/api/segments/combinations"


def _row(key: str, addressable: object, contactable: object = 0) -> dict[str, Any]:
    # Statement Execution API rows arrive as strings.
    return {
        "combination_key": key,
        "addressable_borrowers": None if addressable is None else str(addressable),
        "refreshed_at": "2026-07-14 12:00:00",
        "contactable": None if contactable is None else str(contactable),
    }


ROWS = [
    _row("itm", 9000, 700),
    _row("equity+itm", 2400, 210),
    _row("equity+itm+listed", 310, 25),
    _row("equity+investor+itm+retention", 42, 4),
]


class _FakeSqlClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.rows: list[dict[str, Any]] = list(ROWS)
        self.error: BaseException | None = None
        self.gate: threading.Event | None = None

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.calls.append(statement)
        if self.gate is not None:
            self.gate.wait(timeout=5.0)
        if self.error is not None:
            raise self.error
        return list(self.rows)

    def execute_one(self, statement: str, parameters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


class _StubRepo:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    def combinations(self) -> SegmentCombinationResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return project_combinations(ROWS)


@pytest.fixture
def stub_repo() -> Iterator[_StubRepo]:
    repo = _StubRepo()
    prior = app.dependency_overrides.get(get_segment_combination_repository)
    app.dependency_overrides[get_segment_combination_repository] = lambda: repo
    try:
        yield repo
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_combination_repository, None)
        else:
            app.dependency_overrides[get_segment_combination_repository] = prior


def _missing(message: str) -> DependencyDownError:
    return DependencyDownError(
        "warehouse",
        reason="DatabricksSqlObjectMissingError: ...",
        last_error=DatabricksSqlObjectMissingError(message),
        kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
    )


def test_route_returns_the_exact_combinations_and_their_evidence(stub_repo: _StubRepo) -> None:
    response = TestClient(app).get(PATH)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"built", "core_codes", "combinations", "provenance"}
    assert body["built"] is True
    assert body["core_codes"] == list(CORE_SEGMENT_CODES)
    assert body["combinations"][1] == {
        "segment_codes": ["itm", "equity"],
        "signal_count": 2,
        "addressable": 2400,
        "contactable": 210,
    }
    provenance = body["provenance"]
    assert provenance["source"] == "mip.gold.segment_combination_rollup"
    assert "live" in provenance["contactable_source"]
    assert provenance["refreshed_at"] == "2026-07-14 12:00:00"
    assert "Whole book" in provenance["note"]
    assert stub_repo.calls == 1


def test_both_paths_answer(stub_repo: _StubRepo) -> None:
    for path in (PATH, LEGACY_PATH):
        assert TestClient(app).get(path).json()["built"] is True


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
    prior = app.dependency_overrides.get(get_segment_combination_repository)
    app.dependency_overrides[get_segment_combination_repository] = lambda: repo
    try:
        response = TestClient(app).get(PATH)
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_combination_repository, None)
        else:
            app.dependency_overrides[get_segment_combination_repository] = prior
    assert response.status_code == 503
    body = response.json()
    assert body["reason"] == "warming_up"
    assert body["retryable"] is True
    assert "combinations" not in body


def test_codes_follow_the_core_order_and_rows_sort_by_size_then_key() -> None:
    rows = [_row("retention", 50), _row("equity+itm", 400), _row("itm", 400), _row("investor+listed", 50)]
    result = project_combinations(rows)
    assert [combo.segment_codes for combo in result.combinations] == [
        ["itm", "equity"],  # 400, key 'equity+itm' sorts before 'itm'
        ["itm"],
        ["listed", "investor"],  # 50, key 'investor+listed' before 'retention'
        ["retention"],
    ]
    assert [combo.signal_count for combo in result.combinations] == [2, 1, 2, 1]


def test_contactable_is_clamped_and_unreported_stays_none() -> None:
    result = project_combinations([
        _row("itm", 100, 140),  # a drifted live subset: clamped to addressable
        _row("equity", 80, -3),
        _row("listed", 60, None),
        _row("permit", 40, 0),
    ])
    by_code = {combo.segment_codes[0]: combo.contactable for combo in result.combinations}
    assert by_code == {"itm": 100, "equity": 0, "listed": None, "permit": 0}


def test_a_row_that_is_not_a_core_set_is_dropped_with_an_event(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = project_combinations([_row("itm", 10), _row("", 5), _row("itm+payoff_loss_leads", 3), _row("itm+itm", 2)])
    assert [combo.segment_codes for combo in result.combinations] == [["itm"]]
    events = [record for record in caplog.records if getattr(record, "mip_event", None) == "segment_combination_row_dropped"]
    assert [event.mip_extras["reason"] for event in events] == ["empty_key", "unknown_code", "unknown_code"]  # type: ignore[attr-defined]
    assert all(event.mip_outcome == "dropped" for event in events)  # type: ignore[attr-defined]


ALL_DROPPED = [_row("", 5), _row("itm+payoff_loss_leads", 3), _row("itm+itm", 2)]


def test_rows_that_all_fail_the_contract_raise_with_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(SegmentCombinationContractError) as raised:
        project_combinations(ALL_DROPPED)
    assert (raised.value.row_count, raised.value.dropped) == (3, {"empty_key": 1, "unknown_code": 2})
    failures = [
        record for record in caplog.records if getattr(record, "mip_event", None) == "segment_combination_contract_failure"
    ]
    assert len(failures) == 1
    failure = failures[0]
    assert failure.levelno == logging.ERROR
    assert failure.mip_outcome == "contract_failure"  # type: ignore[attr-defined]
    assert failure.mip_extras == {  # type: ignore[attr-defined]
        "row_count": 3,
        "dropped_empty_key": 1,
        "dropped_unknown_code": 2,
    }
    # Counts only: no combination key reaches the event or the error.
    assert "payoff_loss_leads" not in repr(failure.__dict__)
    assert "payoff_loss_leads" not in str(raised.value)


def test_the_route_answers_a_non_retryable_contract_failure_503_without_a_dependency() -> None:
    repo = _StubRepo(error=SegmentCombinationContractError(3, {"unknown_code": 3}))
    prior = app.dependency_overrides.get(get_segment_combination_repository)
    app.dependency_overrides[get_segment_combination_repository] = lambda: repo
    try:
        response = TestClient(app).get(PATH)
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_combination_repository, None)
        else:
            app.dependency_overrides[get_segment_combination_repository] = prior
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"detail", "retryable", "reason", "correlation_id"}
    assert body["detail"] == "The signal stack failed its data contract."
    assert body["retryable"] is False
    assert body["reason"] == "contract_failure"
    assert body["correlation_id"]
    assert "dependency" not in body and "built" not in body


def test_a_contract_failure_without_a_last_good_projection_propagates() -> None:
    client = _FakeSqlClient()
    client.rows = list(ALL_DROPPED)
    with pytest.raises(SegmentCombinationContractError):
        DatabricksSegmentCombinationRepository(client, cache=GoldAggregateCache()).combinations()


def test_stale_if_error_keeps_the_last_good_projection_through_a_contract_failure() -> None:
    now = [0.0]
    client = _FakeSqlClient()
    cache = GoldAggregateCache(now=lambda: now[0], executor=_InlineExecutor())
    repo = DatabricksSegmentCombinationRepository(client, cache=cache, cache_ttl_s=60.0)
    first = repo.combinations()
    assert first.built is True
    client.rows = list(ALL_DROPPED)
    now[0] = 120.0
    assert repo.combinations().combinations == first.combinations
    now[0] = 125.0
    assert repo.combinations().combinations == first.combinations


def test_an_empty_table_is_not_built() -> None:
    result = project_combinations([])
    assert result.built is False
    assert result.combinations == []
    assert result.core_codes == list(CORE_SEGMENT_CODES)
    assert result.provenance.refreshed_at is None


def test_this_table_missing_is_not_built_not_warming() -> None:
    client = _FakeSqlClient()
    client.error = _missing(
        "Databricks SQL statement did not succeed (state='FAILED' statement_id='x'): "
        "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`segment_combination_rollup` "
        "cannot be found. SQLSTATE: 42P01"
    )
    result = DatabricksSegmentCombinationRepository(client, cache=TTLCache()).combinations()
    assert result.built is False
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "message",
    [
        "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`borrower_360` cannot be found.",
        # A missing schema (classified object-missing by later wave-5 work) is not "not built".
        "[SCHEMA_NOT_FOUND] The schema `mip`.`gold` cannot be found. SQLSTATE: 42704",
    ],
)
def test_another_missing_object_keeps_its_503(message: str) -> None:
    client = _FakeSqlClient()
    client.error = _missing(message)
    repo = DatabricksSegmentCombinationRepository(client, cache=TTLCache())
    with pytest.raises(DependencyDownError):
        repo.combinations()


def test_one_statement_single_flight() -> None:
    client = _FakeSqlClient()
    client.gate = threading.Event()
    repo = DatabricksSegmentCombinationRepository(client, cache=GoldAggregateCache())
    results: list[SegmentCombinationResponse] = []
    threads = [threading.Thread(target=lambda: results.append(repo.combinations())) for _ in range(4)]
    for thread in threads:
        thread.start()
    client.gate.set()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 4
    assert client.calls == [SEGMENT_COMBINATIONS_SQL]


class _InlineExecutor(Executor):
    """Runs the cache's background refresh at once, so its outcome is deterministic."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 -- recorded on the future, as a pool would
            future.set_exception(exc)
        return future


def test_stale_if_error_keeps_the_last_good_rows_through_a_failed_refresh() -> None:
    now = [0.0]
    client = _FakeSqlClient()
    cache = GoldAggregateCache(now=lambda: now[0], executor=_InlineExecutor())
    repo = DatabricksSegmentCombinationRepository(client, cache=cache, cache_ttl_s=60.0)
    first = repo.combinations()
    assert first.built is True
    client.error = DependencyDownError("warehouse", reason="boom", kind=DependencyDownError.KIND_RETRIES_EXHAUSTED)
    # Past the soft TTL: served stale while one refresh runs, and fails.
    now[0] = 120.0
    assert repo.combinations().combinations == first.combinations
    # The failed refresh kept last-good (without stale-if-error it evicts the
    # entry and this read recomputes inline and raises).
    now[0] = 125.0
    assert repo.combinations().combinations == first.combinations


def test_a_cold_failure_propagates() -> None:
    client = _FakeSqlClient()
    client.error = DependencyDownError("warehouse", reason="cold", kind=DependencyDownError.KIND_WARMING_UP)
    with pytest.raises(DependencyDownError):
        DatabricksSegmentCombinationRepository(client, cache=GoldAggregateCache()).combinations()
