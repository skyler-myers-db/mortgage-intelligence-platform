"""S7 economics scatter: bin-math goldens, honest N-of-M, server-side cap.

Three layers under test:

1. Pure bin math in ``backend/services/economics_scatter.py`` pinned by
   ``tests/fixtures/equity_spread_bins_golden.json`` and asserted to be the
   SAME formula the gold CTAS materializes (string-level parity against
   ``sql/transformations/gold_equity_spread_points.sql``, the pattern the
   score-band guard uses for its pinned literals).
2. ``DatabricksAnalyticsRepository.economics_points`` -- the ≤5k cap is
   enforced server-side even when the SQL layer mis-serves more rows, and
   the "showing N of M" payload reports the pre-cap total for the SAME
   predicate.
3. The FastAPI viewport parsing (min<=max, plot-domain bounds).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.schemas.analytics import AnalyticsFilters, EquitySpreadViewport
from backend.services import economics_scatter
from backend.services.repositories.databricks_analytics import (
    DatabricksAnalyticsRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "equity_spread_bins_golden.json").read_text(encoding="utf-8")
)
TRANSFORMATION = (
    REPO_ROOT / "sql" / "transformations" / "gold_equity_spread_points.sql"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Bin math goldens + SQL parity
# ---------------------------------------------------------------------------


def test_golden_constants_match_pinned_module() -> None:
    assert GOLDEN["equity_bin_pct"] == economics_scatter.EQUITY_BIN_PCT
    assert GOLDEN["spread_bin_bps"] == economics_scatter.SPREAD_BIN_BPS
    assert GOLDEN["equity_domain"] == [
        economics_scatter.EQUITY_DOMAIN_MIN,
        economics_scatter.EQUITY_DOMAIN_MAX,
    ]
    assert GOLDEN["spread_domain"] == [
        economics_scatter.SPREAD_DOMAIN_MIN,
        economics_scatter.SPREAD_DOMAIN_MAX,
    ]
    assert GOLDEN["max_scatter_point_rows"] == economics_scatter.MAX_SCATTER_POINT_ROWS


@pytest.mark.parametrize("case", GOLDEN["equity_cases"], ids=lambda c: f"eq{c['equity_pct']}")
def test_equity_bin_goldens(case: dict[str, int]) -> None:
    assert economics_scatter.equity_bin_pct(case["equity_pct"]) == case["bin"]


@pytest.mark.parametrize("case", GOLDEN["spread_cases"], ids=lambda c: f"bps{c['rate_spread_bps']}")
def test_spread_bin_goldens(case: dict[str, int]) -> None:
    assert economics_scatter.spread_bin_bps(case["rate_spread_bps"]) == case["bin"]


def test_transformation_sql_uses_the_same_bin_formulas_and_domain() -> None:
    """The CTAS and the Python mirror must floor by the same widths over the
    same plot domain -- drift here would make the overview bins disagree
    with the zoomed real points."""
    eq = economics_scatter.EQUITY_BIN_PCT
    sp = economics_scatter.SPREAD_BIN_BPS
    assert f"FLOOR(b.equity_pct / {eq}) * {eq}" in TRANSFORMATION
    assert f"FLOOR(b.rate_spread_bps / {sp}) * {sp}" in TRANSFORMATION
    assert (
        f"b.equity_pct BETWEEN {economics_scatter.EQUITY_DOMAIN_MIN} "
        f"AND {economics_scatter.EQUITY_DOMAIN_MAX}"
    ) in TRANSFORMATION
    assert (
        f"b.rate_spread_bps BETWEEN {economics_scatter.SPREAD_DOMAIN_MIN} "
        f"AND {economics_scatter.SPREAD_DOMAIN_MAX}"
    ) in TRANSFORMATION
    # Band vocabulary comes from the canonical UC function, never a literal.
    assert "fn_score_band(b.opportunity_score)" in TRANSFORMATION
    assert not re.search(r"opportunity_score\s*>=\s*\d", TRANSFORMATION)


def test_viewport_schema_bounds_match_pinned_domain() -> None:
    """schemas/analytics.py cannot import services, so its literal bounds
    mirror the pinned domain constants; this is the parity pin."""
    fields = EquitySpreadViewport.model_fields
    assert fields["equity_min"].metadata[1].le == economics_scatter.EQUITY_DOMAIN_MAX
    assert fields["equity_min"].metadata[0].ge == economics_scatter.EQUITY_DOMAIN_MIN
    assert fields["spread_min"].metadata[0].ge == economics_scatter.SPREAD_DOMAIN_MIN
    assert fields["spread_max"].metadata[1].le == economics_scatter.SPREAD_DOMAIN_MAX
    assert fields["equity_min"].default == economics_scatter.EQUITY_DOMAIN_MIN
    assert fields["equity_max"].default == economics_scatter.EQUITY_DOMAIN_MAX
    assert fields["spread_min"].default == economics_scatter.SPREAD_DOMAIN_MIN
    assert fields["spread_max"].default == economics_scatter.SPREAD_DOMAIN_MAX


def test_viewport_rejects_inverted_bounds() -> None:
    with pytest.raises(ValidationError):
        EquitySpreadViewport(equity_min=80, equity_max=20)
    with pytest.raises(ValidationError):
        EquitySpreadViewport(spread_min=300, spread_max=0)


# ---------------------------------------------------------------------------
# 2. Repository: cap + showing-N-of-M
# ---------------------------------------------------------------------------


def _point_row(
    i: int,
    *,
    total_matching: int = 1,
    coordinate_total: int | None = None,
) -> dict[str, Any]:
    return {
        "borrower_id": f"B-{i:013d}",
        "display_name": f"Owner {i}",
        "primary_segment_code": "itm",
        "state": "IL",
        "equity_pct": 40,
        "rate_spread_bps": 88,
        "opportunity_score": 80,
        "score_band": "med",
        "in_the_money": True,
        "total_matching": total_matching,
        "coordinate_total": coordinate_total or total_matching,
        "refreshed_at": "2026-07-10 06:00:00",
    }


class _ScatterSqlClient:
    """Serves one snapshot-consistent points statement with window metadata."""

    def __init__(self, *, point_rows: int, total_matching: int) -> None:
        self._point_rows = point_rows
        self._total = total_matching
        self.statements: list[str] = []
        self.parameters: list[Any] = []

    def execute(self, statement: str, parameters: Any | None = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        self.parameters.append(parameters)
        if "ORDER BY p.opportunity_score DESC, p.borrower_id" in statement:
            return [_point_row(i, total_matching=self._total) for i in range(self._point_rows)]
        raise AssertionError(statement)


def test_points_showing_n_of_m_is_honest() -> None:
    client = _ScatterSqlClient(point_rows=3, total_matching=970)
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]

    payload = repo.economics_points(
        AnalyticsFilters(),
        EquitySpreadViewport(equity_min=40, equity_max=60, spread_min=50, spread_max=200),
    )
    assert payload.showing == 3
    assert payload.total_matching == 970
    assert payload.truncated is True
    assert payload.point_cap == economics_scatter.MAX_SCATTER_POINT_ROWS
    assert payload.source_table == economics_scatter.EQUITY_SPREAD_SOURCE_TABLE
    # Count, refresh time, and points must come from one filtered statement so
    # a concurrent refresh cannot make the chart say "N of M" across snapshots.
    assert len(client.statements) == 1
    points_sql = client.statements[0]
    assert "CAST(COUNT(*) OVER () AS BIGINT) AS total_matching" in points_sql
    assert (
        "COUNT(*) OVER (PARTITION BY p.equity_pct, p.rate_spread_bps)" in points_sql
    )
    assert "MAX(p.refreshed_at) OVER () AS refreshed_at" in points_sql
    for predicate in (
        "p.equity_pct BETWEEN :vp_equity_min AND :vp_equity_max",
        "p.rate_spread_bps BETWEEN :vp_spread_min AND :vp_spread_max",
    ):
        assert predicate in points_sql
    assert client.parameters[0]["vp_equity_min"] == 40
    assert client.parameters[0]["vp_spread_max"] == 200
    assert {point.coordinate_total for point in payload.points} == {970}


def test_points_not_truncated_when_everything_fits() -> None:
    client = _ScatterSqlClient(point_rows=2, total_matching=2)
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]
    payload = repo.economics_points(AnalyticsFilters(), EquitySpreadViewport())
    assert payload.showing == 2
    assert payload.total_matching == 2
    assert payload.truncated is False


def test_points_reject_internally_inconsistent_snapshot_metadata() -> None:
    client = _ScatterSqlClient(point_rows=3, total_matching=2)
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="snapshot count"):
        repo.economics_points(AnalyticsFilters(), EquitySpreadViewport())


def test_points_cap_enforced_server_side_even_against_overserving_sql() -> None:
    """Belt-and-braces: if the SQL layer ever mis-serves more than the cap,
    the repository still never pushes >cap rows to the wire."""
    over = economics_scatter.MAX_SCATTER_POINT_ROWS + 37
    client = _ScatterSqlClient(point_rows=over, total_matching=over)
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]
    payload = repo.economics_points(AnalyticsFilters(), EquitySpreadViewport())
    assert payload.showing == economics_scatter.MAX_SCATTER_POINT_ROWS
    assert len(payload.points) == economics_scatter.MAX_SCATTER_POINT_ROWS
    assert payload.total_matching == over
    assert payload.truncated is True
    # And the SQL itself carries the cap so a healthy warehouse never ships
    # more than the cap in the first place.
    points_sql = next(s for s in client.statements if "ORDER BY p.opportunity_score" in s)
    assert points_sql.rstrip().endswith(f"LIMIT {economics_scatter.MAX_SCATTER_POINT_ROWS}")


def test_points_cache_keys_include_viewport() -> None:
    client = _ScatterSqlClient(point_rows=1, total_matching=1)
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]
    repo.economics_points(AnalyticsFilters(), EquitySpreadViewport(equity_min=10, equity_max=20))
    calls_after_first = len(client.statements)
    # Same viewport -> served from cache, no new SQL.
    repo.economics_points(AnalyticsFilters(), EquitySpreadViewport(equity_min=10, equity_max=20))
    assert len(client.statements) == calls_after_first
    # Different viewport -> distinct cache key -> fresh SQL.
    repo.economics_points(AnalyticsFilters(), EquitySpreadViewport(equity_min=10, equity_max=30))
    assert len(client.statements) > calls_after_first


def test_segment_display_labels_cover_every_product_segment() -> None:
    assert economics_scatter.segment_display_label("itm") == "Prime Refi Candidates"
    assert economics_scatter.segment_display_label("equity") == "Home Equity Candidate"
    assert economics_scatter.segment_display_label(None) == economics_scatter.UNSEGMENTED_LABEL
    assert economics_scatter.segment_display_label("unknown_code") == economics_scatter.UNSEGMENTED_LABEL
