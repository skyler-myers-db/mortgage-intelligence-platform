"""Home Delta Explainer contracts (``GET /api/home/summary/attribution``, audit wow-ai-3).

Pinned at the layer each property lives in:

* the SQL: gold.funnel_snapshot_daily, gold.rate_window_weekly and
  ref.offer_rules_config only (never silver, never borrower_360), named binds;
* the nearest-snapshot choice (at-or-before first), the per-state rows and
  the unattributed remainder, the rate weeks and the offer-rules flag;
* the route: 422 on a future or a too-old baseline, 503 on a dependency
  outage, and no audit row (the in-memory audit store pattern of
  test_boot_reads_audit_free.py);
* the cache key.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from backend.api import home as home_api
from backend.main import app
from backend.schemas.home_attribution import ATTRIBUTION_NOTE, HomeAttributionMeasure
from backend.services import audit_store as audit_store_module
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import DatabricksSqlColumnMissingError
from backend.services.home_attribution import (
    MEASURE_COLUMNS,
    HomeAttributionService,
    _snapshot_dates_sql,
    attribute_states,
    choose_snapshot_dates,
    get_home_attribution_service,
    rate_facts,
)
from backend.services.resilience import DependencyDownError
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

BASELINE = date(2026, 9, 1)


class _FakeSql:
    """Answers the service's four statement shapes from in-memory rows."""

    def __init__(
        self,
        *,
        dates: dict[str, Any] | None = None,
        values: list[dict[str, Any]] | None = None,
        weeks: list[dict[str, Any]] | None = None,
        rules: dict[str, Any] | None = None,
    ) -> None:
        self.dates = dates if dates is not None else {"at_or_before": "2026-09-01", "after": "2026-09-02", "latest": "2026-09-30"}
        self.values = values if values is not None else [
            {"snapshot_date": "2026-09-01", "state": "_ALL", "value": 1_000},
            {"snapshot_date": "2026-09-30", "state": "_ALL", "value": 1_150},
            {"snapshot_date": "2026-09-01", "state": "TX", "value": 400},
            {"snapshot_date": "2026-09-30", "state": "TX", "value": 520},
            {"snapshot_date": "2026-09-01", "state": "IL", "value": 300},
            {"snapshot_date": "2026-09-30", "state": "IL", "value": 280},
            {"snapshot_date": "2026-09-30", "state": "FL", "value": 25},
        ]
        self.weeks = weeks if weeks is not None else [
            {"observation_week": "2026-08-31", "market_rate_pct": 6.41, "is_latest": False},
            {"observation_week": "2026-09-28", "market_rate_pct": 6.22, "is_latest": True},
        ]
        self.rules = rules if rules is not None else {"last_updated": "2026-09-15 10:00:00"}
        self.statements: list[tuple[str, Any]] = []

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.statements.append((statement, parameters))
        if "rate_window_weekly" in statement:
            return list(self.weeks)
        if " AS value " in statement:
            return list(self.values)
        raise AssertionError(f"unexpected execute: {statement}")

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        self.statements.append((statement, parameters))
        if "at_or_before" in statement:
            return dict(self.dates)
        if "offer_rules_config" in statement:
            return dict(self.rules)
        raise AssertionError(f"unexpected execute_one: {statement}")


def _service(sql: _FakeSql) -> HomeAttributionService:
    return HomeAttributionService(sql, ttl_s=0)


# -- SQL boundary -------------------------------------------------------------


def test_reads_only_gold_and_ref_never_silver_or_borrower_360() -> None:
    sql = _FakeSql()
    _service(sql).attribution("refi_economics_screen", BASELINE)
    assert len(sql.statements) == 4
    text = " ".join(statement for statement, _ in sql.statements)
    relations = set(re.findall(r"\bmip\.(\w+)\.(\w+)", text))
    assert relations == {
        ("gold", "funnel_snapshot_daily"),
        ("gold", "rate_window_weekly"),
        ("ref", "offer_rules_config"),
    }
    assert "silver" not in text
    assert "borrower_360" not in text
    # Values ride named binds, never the statement text.
    assert "2026-09-01" not in text
    assert all(params is None or isinstance(params, dict) for _, params in sql.statements)


def test_each_measure_maps_to_a_closed_column() -> None:
    assert MEASURE_COLUMNS["refi_economics_screen"][:2] == ("_ALL", "in_the_money_borrowers")
    assert MEASURE_COLUMNS["high_opportunity"][:2] == ("_ALL", "high_opportunity_borrowers")
    assert MEASURE_COLUMNS["offers_recommended"][:2] == ("_ALL", "offer_recommended_borrowers")
    assert MEASURE_COLUMNS["listed_for_sale"][:2] == ("listed", "addressable_borrowers")
    sql = _FakeSql()
    _service(sql).attribution("listed_for_sale", BASELINE)
    values = next(statement for statement, _ in sql.statements if " AS value " in statement)
    assert "addressable_borrowers AS value" in values
    assert all(params.get("segment_code") == "listed" for statement, params in sql.statements if params and "segment_code" in params)


# -- nearest snapshot, states, remainder --------------------------------------


def test_the_nearest_snapshot_prefers_the_one_on_or_before_the_baseline() -> None:
    assert choose_snapshot_dates({"at_or_before": "2026-08-30", "after": "2026-09-02", "latest": "2026-09-30"}) == (
        date(2026, 8, 30),
        date(2026, 9, 30),
    )
    # Nothing on or before: the earliest after.
    assert choose_snapshot_dates({"at_or_before": None, "after": "2026-09-02", "latest": "2026-09-30"}) == (
        date(2026, 9, 2),
        date(2026, 9, 30),
    )
    assert choose_snapshot_dates({"at_or_before": None, "after": None, "latest": None}) == (None, None)
    assert choose_snapshot_dates(None) == (None, None)


def test_states_sort_by_change_and_the_remainder_is_unattributed() -> None:
    response = _service(_FakeSql()).attribution("refi_economics_screen", BASELINE)
    assert (response.baseline_total, response.current_total, response.total_change) == (1_000, 1_150, 150)
    assert [(row.state, row.baseline_count, row.current_count, row.change) for row in response.states] == [
        ("TX", 400, 520, 120),
        ("IL", 300, 280, -20),
        ("FL", None, 25, None),
    ]
    # 150 - (120 - 20): FL has no baseline row, so its 25 is not invented as a change.
    assert response.unattributed_change == 50
    assert response.population == "addressable"
    assert response.nearest_snapshot is False
    assert response.note == ATTRIBUTION_NOTE
    assert response.sources == ["mip.gold.funnel_snapshot_daily", "mip.gold.rate_window_weekly", "mip.ref.offer_rules_config"]


def test_a_baseline_without_its_own_snapshot_says_it_used_the_nearest() -> None:
    sql = _FakeSql(dates={"at_or_before": "2026-08-29", "after": "2026-09-02", "latest": "2026-09-30"})
    sql.values = [
        {"snapshot_date": "2026-08-29", "state": "_ALL", "value": 990},
        {"snapshot_date": "2026-09-30", "state": "_ALL", "value": 1_150},
    ]
    response = _service(sql).attribution("high_opportunity", BASELINE)
    assert response.baseline_snapshot_date == date(2026, 8, 29)
    assert response.nearest_snapshot is True
    assert response.total_change == 160
    assert response.unattributed_change == 160
    assert response.label == "high-opportunity"


def test_no_snapshot_rows_is_honest_never_fabricated() -> None:
    sql = _FakeSql(dates={"at_or_before": None, "after": None, "latest": None}, values=[])
    response = _service(sql).attribution("offers_recommended", BASELINE)
    assert response.baseline_total is None
    assert response.current_total is None
    assert response.total_change is None
    assert response.unattributed_change is None
    assert response.states == []
    # The per-state statement is never issued without two dates.
    assert not any(" AS value " in statement for statement, _ in sql.statements)


def test_attribute_states_ignores_rows_outside_the_two_dates() -> None:
    rows = [
        {"snapshot_date": "2026-09-01", "state": "_ALL", "value": 10},
        {"snapshot_date": "2026-09-30", "state": "_ALL", "value": 12},
        {"snapshot_date": "2026-09-15", "state": "TX", "value": 99},
    ]
    assert attribute_states(rows, date(2026, 9, 1), date(2026, 9, 30)) == (10, 12, [])


# -- coincident facts -----------------------------------------------------------


def test_rate_weeks_are_the_latest_and_the_latest_on_or_before_the_baseline() -> None:
    rate = rate_facts(
        [
            {"observation_week": "2026-08-31", "market_rate_pct": 6.41, "is_latest": False},
            {"observation_week": "2026-09-28", "market_rate_pct": 6.22, "is_latest": True},
        ],
        BASELINE,
    )
    assert (rate.series_id, rate.baseline_week, rate.baseline_pct) == ("MORTGAGE30US", date(2026, 8, 31), 6.41)
    assert (rate.latest_week, rate.latest_pct) == (date(2026, 9, 28), 6.22)
    # A baseline before the series: no baseline print, the latest still shown.
    early = rate_facts([{"observation_week": "2026-09-28", "market_rate_pct": 6.22, "is_latest": True}], date(2026, 1, 1))
    assert early.baseline_week is None and early.baseline_pct is None and early.latest_pct == 6.22


def test_the_offer_rules_flag_compares_the_last_change_with_the_baseline() -> None:
    changed = _service(_FakeSql(rules={"last_updated": "2026-09-15 10:00:00"})).attribution("listed_for_sale", BASELINE)
    assert changed.offer_rules_changed_since_baseline is True
    assert changed.offer_rules_last_updated == datetime(2026, 9, 15, 10, 0)
    unchanged = _service(_FakeSql(rules={"last_updated": "2026-08-01 00:00:00"})).attribution("listed_for_sale", BASELINE)
    assert unchanged.offer_rules_changed_since_baseline is False
    unknown = _service(_FakeSql(rules={"last_updated": None})).attribution("listed_for_sale", BASELINE)
    assert unknown.offer_rules_changed_since_baseline is None


def test_the_cache_key_is_measure_and_baseline() -> None:
    assert HomeAttributionService.cache_key("refi_economics_screen", BASELINE) == "home.attribution.refi_economics_screen.2026-09-01"

    class _Recorder:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def get_or_set(self, key: str, factory: Any, *, ttl_s: float, **_: Any) -> Any:
            self.keys.append(key)
            return factory()

    recorder = _Recorder()
    HomeAttributionService(_FakeSql(), cache=recorder, ttl_s=300).attribution("high_opportunity", BASELINE)  # type: ignore[arg-type]
    assert recorder.keys == ["home.attribution.high_opportunity.2026-09-01"]


# -- route ------------------------------------------------------------------------

IDENTITY = {
    "X-Forwarded-Email": "analyst@summit.example",
    "X-Forwarded-User": "analyst@summit.example",
    "X-Forwarded-Groups": "",
}


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    monkeypatch.setattr(audit_store_module, "_AUDIT_STORE", store)
    yield store


def _install(service: Any) -> None:
    app.dependency_overrides[get_home_attribution_service] = lambda: service


def _recent_baseline() -> str:
    return (datetime.now(UTC).date() - timedelta(days=30)).isoformat()


def test_the_route_returns_the_breakdown_and_writes_no_audit_row(audit: InMemoryAuditStore) -> None:
    sql = _FakeSql()
    _install(_service(sql))
    response = TestClient(app).get(
        f"/api/v1/home/summary/attribution?measure=refi_economics_screen&baseline={_recent_baseline()}",
        headers=IDENTITY,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["measure"] == "refi_economics_screen"
    assert body["population"] == "addressable"
    assert body["total_change"] == 150
    assert set(body) >= {"states", "unattributed_change", "rate", "offer_rules_changed_since_baseline", "sources", "note"}
    assert audit.list(limit=100) == []


@pytest.mark.parametrize("days", [-1, 401])
def test_a_future_or_too_old_baseline_is_a_422(days: int) -> None:
    _install(_service(_FakeSql()))
    baseline = (datetime.now(UTC).date() - timedelta(days=days)).isoformat()
    response = TestClient(app).get(f"/api/v1/home/summary/attribution?measure=high_opportunity&baseline={baseline}")
    assert response.status_code == 422
    assert response.json()["detail"] == home_api.ATTRIBUTION_BASELINE_DETAIL


def test_the_client_never_asks_outside_the_route_lookback() -> None:
    """The drawer's explainer skips a baseline the route would 422 (deltaExplainer.model.ts)."""
    model = Path(__file__).resolve().parents[2] / "frontend/src/components/mortgage/deltaExplainer.model.ts"
    match = re.search(r"export const ATTRIBUTION_MAX_LOOKBACK_DAYS = (\d+);", model.read_text())
    assert match, "deltaExplainer.model.ts declares ATTRIBUTION_MAX_LOOKBACK_DAYS"
    assert int(match.group(1)) == home_api.ATTRIBUTION_MAX_LOOKBACK_DAYS


@pytest.mark.parametrize("days", [0, 400])
def test_a_baseline_inside_the_lookback_is_answered(days: int) -> None:
    _install(_service(_FakeSql()))
    baseline = (datetime.now(UTC).date() - timedelta(days=days)).isoformat()
    response = TestClient(app).get(f"/api/v1/home/summary/attribution?measure=high_opportunity&baseline={baseline}")
    assert response.status_code == 200


def test_an_unknown_measure_is_a_422() -> None:
    _install(_service(_FakeSql()))
    # A genuinely unknown measure: competitor_lien became a real measure in
    # W5c (its Literal member lands with w5-evidence-drawer).
    response = TestClient(app).get(f"/api/v1/home/summary/attribution?measure=zyrplax_count&baseline={_recent_baseline()}")
    assert response.status_code == 422


def test_a_dependency_outage_is_a_sanitized_503() -> None:
    class _Down:
        def attribution(self, measure: str, baseline: date) -> Any:
            raise DependencyDownError("warehouse", reason="socket 10.0.0.7 refused")

    _install(_Down())
    response = TestClient(app).get(f"/api/v1/home/summary/attribution?measure=listed_for_sale&baseline={_recent_baseline()}")
    assert response.status_code == 503
    assert "10.0.0.7" not in response.text
    assert "warehouse" in response.json()["detail"].lower()


# -- competitor_lien (wow-ai-3, W5c) -----------------------------------------
#
# The Literal member is w5-evidence-drawer's, so the service-layer cases run
# the competitor_lien COLUMN through an existing measure's slot; the one case
# that needs the measure itself activates once the member lands.

_MISSING_COMPETITOR_COLUMN = (
    "[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter with "
    "name `competitor_lien_borrowers` cannot be resolved. SQLSTATE: 42703"
)


def test_competitor_lien_maps_to_its_nullable_funnel_column() -> None:
    assert MEASURE_COLUMNS["competitor_lien"] == ("_ALL", "competitor_lien_borrowers", "competitor liens")


@pytest.mark.parametrize("measure", sorted(MEASURE_COLUMNS))
def test_snapshot_dates_are_chosen_only_among_snapshots_that_recorded_the_measure(measure: str) -> None:
    _segment, column, _label = MEASURE_COLUMNS[measure]
    assert _snapshot_dates_sql(column).endswith(f"AND {column} IS NOT NULL")


def test_the_service_issues_the_is_not_null_filter() -> None:
    sql = _FakeSql()
    _service(sql).attribution("high_opportunity", BASELINE)
    dates = next(statement for statement, _ in sql.statements if "at_or_before" in statement)
    assert dates.endswith("AND high_opportunity_borrowers IS NOT NULL")


class _ColumnMissingSql(_FakeSql):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        if "at_or_before" in statement:
            self.statements.append((statement, parameters))
            raise self.error
        return super().execute_one(statement, parameters)


def _wrapped(error: BaseException) -> DependencyDownError:
    return DependencyDownError(
        "warehouse", reason="missing", last_error=error, kind=DependencyDownError.KIND_RETRIES_EXHAUSTED
    )


@pytest.mark.parametrize(
    "error",
    [
        _wrapped(DatabricksSqlColumnMissingError(_MISSING_COMPETITOR_COLUMN)),
        DatabricksSqlColumnMissingError(_MISSING_COMPETITOR_COLUMN),
    ],
    ids=["resilient-client", "bare-client"],
)
def test_a_read_ahead_of_the_ensure_step_is_the_no_snapshot_shape(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    monkeypatch.setitem(MEASURE_COLUMNS, "high_opportunity", MEASURE_COLUMNS["competitor_lien"])
    sql = _ColumnMissingSql(error)

    response = _service(sql).attribution("high_opportunity", BASELINE)

    assert response.baseline_snapshot_date is None and response.current_snapshot_date is None
    assert response.states == []
    assert response.baseline_total is None and response.current_total is None
    assert response.total_change is None and response.unattributed_change is None
    assert response.rate.latest_pct == 6.22, "the coinciding facts still answer"
    assert not any(" AS value " in statement for statement, _ in sql.statements)


def test_a_missing_not_null_column_is_still_an_outage() -> None:
    sql = _ColumnMissingSql(_wrapped(DatabricksSqlColumnMissingError(_MISSING_COMPETITOR_COLUMN)))
    with pytest.raises(DependencyDownError):
        _service(sql).attribution("refi_economics_screen", BASELINE)


def test_per_state_rows_and_the_remainder_reconcile_to_the_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(MEASURE_COLUMNS, "high_opportunity", MEASURE_COLUMNS["competitor_lien"])
    sql = _FakeSql()

    response = _service(sql).attribution("high_opportunity", BASELINE)

    values = next(statement for statement, _ in sql.statements if " AS value " in statement)
    assert "competitor_lien_borrowers AS value" in values
    assert response.total_change == 150
    attributed = sum(row.change for row in response.states if row.change is not None)
    assert attributed + (response.unattributed_change or 0) == response.total_change


def test_the_competitor_lien_measure_answers_no_snapshot_until_one_records_it() -> None:
    # Was skip-guarded until the W5c merge brought w5-evidence-drawer's
    # HomeAttributionMeasure member; it can no longer skip.
    assert "competitor_lien" in get_args(HomeAttributionMeasure)
    sql = _FakeSql(dates={"at_or_before": None, "after": None, "latest": None}, values=[])
    response = _service(sql).attribution("competitor_lien", BASELINE)
    assert response.measure == "competitor_lien"
    assert response.label == "competitor liens"
    assert response.states == [] and response.current_total is None


def test_the_competitor_lien_route_answers_the_no_snapshot_shape_until_a_snapshot_records_it(
    audit: InMemoryAuditStore,
) -> None:
    """W5c INT-9 (w5-evidence-drawer x w5-gold-slot-cache), through the real route.

    competitor_lien is a snapshotted measure now (MEASURE_COLUMNS carries it,
    unpatched), so the route reaches the service instead of answering
    ``snapshotted: false``. Until the first post-deploy snapshot records the
    nullable column, the answer is the honest no-snapshot shape: no dates, no
    totals, no states, no per-state read and no audit row. The drawer renders
    its generic empty state for it (DeltaExplainer.test.tsx), never the
    not-snapshotted note.
    """

    sql = _FakeSql(dates={"at_or_before": None, "after": None, "latest": None}, values=[])
    _install(_service(sql))
    response = TestClient(app).get(
        f"/api/v1/home/summary/attribution?measure=competitor_lien&baseline={_recent_baseline()}",
        headers=IDENTITY,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["measure"], body["label"], body["snapshotted"]) == ("competitor_lien", "competitor liens", True)
    assert (body["baseline_snapshot_date"], body["current_snapshot_date"]) == (None, None)
    assert (body["baseline_total"], body["current_total"], body["total_change"]) == (None, None, None)
    assert body["states"] == [] and body["unattributed_change"] is None
    assert any("at_or_before" in statement for statement, _ in sql.statements)
    assert not any(" AS value " in statement for statement, _ in sql.statements)
    assert audit.list(limit=100) == []
