"""KPI delta service contracts: 0/1/2+ snapshot edges + golden delta math.

The Lakebase and warehouse clients are pure in-memory fakes that answer the
service's three SQL shapes (previous visit, snapshot at-or-before, snapshot
after) so no test touches a network. Delta arithmetic is pinned by golden
fixtures -- if the math or the measure vocabulary drifts, these fail loudly.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from backend.schemas.kpi_deltas import HEADLINE_COUNT_MEASURES, HeadlineKpis
from backend.services.kpi_deltas import KpiDeltaService, compute_deltas

_NOW = datetime(2026, 7, 10, 18, 0, 0, tzinfo=UTC)

# Golden current/baseline readings. Deltas asserted digit-for-digit below.
_CURRENT_ROW: dict[str, Any] = {
    "marketable_population": 5_240_100,
    "refi_economics_screen": 261_400,
    "high_opportunity": 88_210,
    "offers_available": 402_330,
    "offers_recommended": 310_450,
    "avg_opportunity_score": 61.5,
}
_BASELINE_MEASURES: dict[str, Any] = {
    "marketable_population": 5_212_000,
    "refi_economics_screen": 259_150,
    "high_opportunity": 86_900,
    "offers_available": 398_210,
    "offers_recommended": 305_925,
    "avg_opportunity_score": 60.75,
}
_GOLDEN_DELTAS: dict[str, Any] = {
    "marketable_population": 28_100,
    "refi_economics_screen": 2_250,
    "high_opportunity": 1_310,
    "offers_available": 4_120,
    "offers_recommended": 4_525,
    "avg_opportunity_score": 0.75,
}


def _snapshot_row(snapshot_at: datetime, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "snapshot_date": snapshot_at.date(),
        "snapshot_at": snapshot_at,
        "source_view": "portfolio_headline_metric_view",
        **_BASELINE_MEASURES,
    }
    row.update(overrides)
    return row


class _FakeLakebase:
    """Answers the service's three fetchone shapes from in-memory rows."""

    def __init__(
        self,
        visits: list[datetime] | None = None,
        snapshots: list[dict[str, Any]] | None = None,
    ) -> None:
        self.visits = visits or []
        self.snapshots = snapshots or []
        self.fetchones: list[tuple[str, dict[str, Any]]] = []

    def fetchone(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        p = params or {}
        self.fetchones.append((sql, p))
        if "mip_app.user_visits" in sql:
            older = [v for v in self.visits if v < p["before"]]
            return {"visited_at": max(older)} if older else None
        if "snapshot_at <=" in sql:
            side = [s for s in self.snapshots if s["snapshot_at"] <= p["ts"]]
            return max(side, key=lambda s: s["snapshot_at"]) if side else None
        if "snapshot_at >" in sql:
            side = [s for s in self.snapshots if s["snapshot_at"] > p["ts"]]
            return min(side, key=lambda s: s["snapshot_at"]) if side else None
        raise AssertionError(f"unexpected fetchone: {sql}")


class _FakeSql:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = dict(_CURRENT_ROW) if row is None else row
        self.statements: list[str] = []

    def execute_one(
        self, statement: str, parameters: Any = None
    ) -> dict[str, Any] | None:
        self.statements.append(statement)
        return dict(self.row)


def _service(
    lakebase: _FakeLakebase,
    sql: _FakeSql | None = None,
    *,
    grace_s: float = 900.0,
) -> KpiDeltaService:
    return KpiDeltaService(
        lakebase_client=lakebase,
        sql_client=sql if sql is not None else _FakeSql(),
        current_session_grace_s=grace_s,
        now=lambda: _NOW,
    )


# ---------------------------------------------------------------------------
# Golden delta math (pure).
# ---------------------------------------------------------------------------


def test_compute_deltas_golden_fixture() -> None:
    current = HeadlineKpis.model_validate(_CURRENT_ROW)
    baseline = HeadlineKpis.model_validate(_BASELINE_MEASURES)

    deltas = compute_deltas(current, baseline)

    for measure, expected in _GOLDEN_DELTAS.items():
        assert getattr(deltas, measure) == expected, measure


def test_compute_deltas_are_signed_and_avg_none_propagates() -> None:
    current = HeadlineKpis.model_validate(
        {**_BASELINE_MEASURES, "high_opportunity": 86_000, "avg_opportunity_score": None}
    )
    baseline = HeadlineKpis.model_validate(_BASELINE_MEASURES)

    deltas = compute_deltas(current, baseline)

    assert deltas.high_opportunity == -900  # declines are signed, not clamped
    assert deltas.avg_opportunity_score is None


# ---------------------------------------------------------------------------
# Edge case: zero snapshots (pre-backfill install).
# ---------------------------------------------------------------------------


def test_zero_snapshots_yields_current_only_no_deltas() -> None:
    lakebase = _FakeLakebase(visits=[_NOW - timedelta(days=1)], snapshots=[])
    result = _service(lakebase).deltas_for_actor("lo@summit.example")

    assert result.previous_visit_at == _NOW - timedelta(days=1)
    assert result.baseline is None
    assert result.baseline_snapshot_at is None
    assert result.deltas is None
    assert result.current.marketable_population == _CURRENT_ROW["marketable_population"]


# ---------------------------------------------------------------------------
# Edge case: no previous visit (first login).
# ---------------------------------------------------------------------------


def test_no_previous_visit_yields_no_baseline_even_with_snapshots() -> None:
    lakebase = _FakeLakebase(
        visits=[], snapshots=[_snapshot_row(_NOW - timedelta(days=1))]
    )
    result = _service(lakebase).deltas_for_actor("lo@summit.example")

    assert result.previous_visit_at is None
    assert result.baseline is None
    assert result.deltas is None
    assert result.actor_email == "lo@summit.example"


def test_previous_visit_excludes_current_session_window() -> None:
    """The row the middleware wrote for the CURRENT session (inside the
    dedupe window) must not masquerade as the last login."""
    current_session_visit = _NOW - timedelta(minutes=5)
    last_login = _NOW - timedelta(days=2)
    lakebase = _FakeLakebase(
        visits=[last_login, current_session_visit],
        snapshots=[_snapshot_row(_NOW - timedelta(days=2, hours=1))],
    )

    result = _service(lakebase, grace_s=900.0).deltas_for_actor("lo@summit.example")

    assert result.previous_visit_at == last_login
    visit_sql, visit_params = lakebase.fetchones[0]
    assert "mip_app.user_visits" in visit_sql
    assert visit_params["before"] == _NOW - timedelta(seconds=900)


# ---------------------------------------------------------------------------
# Edge case: exactly one snapshot -- nearest from either side.
# ---------------------------------------------------------------------------


def test_single_snapshot_after_visit_is_still_nearest() -> None:
    visit = _NOW - timedelta(days=3)
    only_snapshot = _snapshot_row(_NOW - timedelta(days=1))  # after the visit
    lakebase = _FakeLakebase(visits=[visit], snapshots=[only_snapshot])

    result = _service(lakebase).deltas_for_actor("lo@summit.example")

    assert result.baseline_snapshot_at == only_snapshot["snapshot_at"]
    assert result.deltas is not None


def test_single_snapshot_before_visit_is_still_nearest() -> None:
    visit = _NOW - timedelta(days=1)
    only_snapshot = _snapshot_row(_NOW - timedelta(days=4))  # before the visit
    lakebase = _FakeLakebase(visits=[visit], snapshots=[only_snapshot])

    result = _service(lakebase).deltas_for_actor("lo@summit.example")

    assert result.baseline_snapshot_at == only_snapshot["snapshot_at"]


# ---------------------------------------------------------------------------
# Normal case: two or more snapshots.
# ---------------------------------------------------------------------------


def test_two_plus_snapshots_pick_the_closer_side() -> None:
    visit = _NOW - timedelta(days=1)
    far_before = _snapshot_row(visit - timedelta(hours=20))
    near_after = _snapshot_row(visit + timedelta(hours=2))
    lakebase = _FakeLakebase(visits=[visit], snapshots=[far_before, near_after])

    result = _service(lakebase).deltas_for_actor("lo@summit.example")

    assert result.baseline_snapshot_at == near_after["snapshot_at"]


def test_snapshot_tie_resolves_to_at_or_before_row() -> None:
    visit = _NOW - timedelta(days=1)
    before = _snapshot_row(visit - timedelta(hours=6))
    after = _snapshot_row(visit + timedelta(hours=6))
    lakebase = _FakeLakebase(visits=[visit], snapshots=[before, after])

    result = _service(lakebase).deltas_for_actor("lo@summit.example")

    assert result.baseline_snapshot_at == before["snapshot_at"]


def test_deltas_for_actor_end_to_end_golden() -> None:
    visit = _NOW - timedelta(days=1)
    baseline_at = visit - timedelta(hours=1)
    lakebase = _FakeLakebase(
        visits=[visit],
        snapshots=[
            _snapshot_row(baseline_at),
            _snapshot_row(visit - timedelta(days=6)),
        ],
    )
    sql = _FakeSql()

    result = _service(lakebase, sql).deltas_for_actor("lo@summit.example")

    assert result.previous_visit_at == visit
    assert result.baseline_snapshot_at == baseline_at
    assert result.deltas is not None
    for measure, expected in _GOLDEN_DELTAS.items():
        assert getattr(result.deltas, measure) == expected, measure
    # The live read went to the S1 headline metric view, catalog-qualified.
    assert any(
        "semantics.portfolio_headline_metric_view" in stmt for stmt in sql.statements
    )


def test_headline_measure_vocabulary_is_the_s1_set() -> None:
    """The count-measure list is the S4 wire vocabulary; renames here are
    breaking changes for snapshots already persisted in Lakebase."""
    assert HEADLINE_COUNT_MEASURES == (
        "marketable_population",
        "refi_economics_screen",
        "high_opportunity",
        "offers_available",
        "offers_recommended",
    )
