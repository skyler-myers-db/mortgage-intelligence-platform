"""The refi screen the economics charts draw (audit 2026-09-21 dataviz-06).

The spread histogram's rule and the scatter guides take min_spread_bps /
min_equity_pct from the economics payload, read from the ``*_applied``
columns ``gold_borrower_360.sql`` carries per refresh. A field is only ever
the value every scored row agrees on; otherwise it is None with a reason the
UI can state honestly. Never a default.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from backend.schemas.analytics import AnalyticsFilters
from backend.services.repositories.analytics_thresholds import (
    ECONOMICS_THRESHOLDS_SQL,
    THRESHOLDS_CACHE_KEY,
    thresholds_from_row,
)
from backend.services.repositories.databricks_analytics import DatabricksAnalyticsRepository


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "row_count": 10,
        "spread_rows": 10,
        "min_spread_lo": 75,
        "min_spread_hi": 75,
        "equity_rows": 10,
        "min_equity_lo": 15,
        "min_equity_hi": 15,
    }
    row.update(overrides)
    return row


class _EconomicsClient:
    """Answers the economics statements; counts the thresholds statement."""

    def __init__(self, thresholds_row: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.thresholds_row = thresholds_row or _row()
        self.fail = fail
        self.thresholds_calls = 0

    def execute(self, statement: str, _parameters: object | None = None) -> list[dict[str, Any]]:
        if statement == ECONOMICS_THRESHOLDS_SQL:
            self.thresholds_calls += 1
            if self.fail:
                raise RuntimeError("warehouse unavailable")
            return [self.thresholds_row]
        return []


def test_a_uniform_refresh_gives_the_governed_values() -> None:
    thresholds = thresholds_from_row(_row())
    assert thresholds.model_dump() == {"min_spread_bps": 75, "min_equity_pct": 15, "reason": None}


def test_every_row_null_means_the_screen_was_not_built() -> None:
    thresholds = thresholds_from_row(
        _row(spread_rows=0, min_spread_lo=None, min_spread_hi=None, equity_rows=0, min_equity_lo=None, min_equity_hi=None),
    )
    assert thresholds.model_dump() == {"min_spread_bps": None, "min_equity_pct": None, "reason": "not_built"}
    # An empty table has no screen either.
    assert thresholds_from_row({}).reason == "not_built"


def test_min_not_equal_to_max_has_no_single_governed_value() -> None:
    thresholds = thresholds_from_row(_row(min_spread_lo=50, min_spread_hi=75))
    assert thresholds.min_spread_bps is None
    assert thresholds.min_equity_pct == 15
    assert thresholds.reason == "not_uniform"


def test_a_screen_on_some_rows_only_is_not_uniform_either() -> None:
    thresholds = thresholds_from_row(_row(equity_rows=7))
    assert thresholds.min_spread_bps == 75
    assert thresholds.min_equity_pct is None
    assert thresholds.reason == "not_uniform"


def test_not_uniform_outranks_not_built_when_both_columns_are_null() -> None:
    thresholds = thresholds_from_row(
        _row(min_spread_lo=50, min_spread_hi=75, equity_rows=0, min_equity_lo=None, min_equity_hi=None),
    )
    assert (thresholds.min_spread_bps, thresholds.min_equity_pct, thresholds.reason) == (None, None, "not_uniform")


def test_the_statement_reads_gold_borrower_360_only_and_is_unfiltered() -> None:
    sql = ECONOMICS_THRESHOLDS_SQL
    assert "mip.gold.borrower_360" in sql
    assert "silver" not in sql and "mip.raw." not in sql
    assert "WHERE" not in sql.upper()
    assert "min_spread_bps_applied" in sql and "min_equity_pct_applied" in sql


def test_two_filter_combinations_issue_one_statement_under_one_key() -> None:
    client = _EconomicsClient()
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]
    first = repo.economics(AnalyticsFilters(states=["IL"]))
    second = repo.economics(AnalyticsFilters(states=["CA"], segment_codes=["itm"]))
    assert client.thresholds_calls == 1
    assert first.thresholds == second.thresholds
    assert first.thresholds.min_spread_bps == 75
    assert THRESHOLDS_CACHE_KEY == "analytics.thresholds"


def test_a_query_failure_fails_the_economics_response_with_no_default() -> None:
    repo = DatabricksAnalyticsRepository(_EconomicsClient(fail=True))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="warehouse unavailable"):
        repo.economics()


_CHART_MODEL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "routes" / "analytics.chart-model.ts"
_ANALYTICS_REPO = Path(__file__).resolve().parents[2] / "backend" / "services" / "repositories" / "databricks_analytics.py"


def _ts_constant(name: str) -> int:
    match = re.search(rf"export const {name} = (-?\d+);", _CHART_MODEL.read_text())
    assert match, f"{name} not found in analytics.chart-model.ts"
    return int(match.group(1))


def test_the_chart_bucket_widths_match_the_governed_sql() -> None:
    """A histogram bin is [start, start + width): the width must be the SQL's FLOOR step."""
    score = re.search(
        r"FLOOR\(opportunity_score / (\d+)\) \* (\d+)", DatabricksAnalyticsRepository._SCORE_DISTRIBUTION_SQL
    )
    spread = re.search(r"FLOOR\(rate_spread_bps / (\d+)\) \* (\d+)", DatabricksAnalyticsRepository._RATE_SPREAD_HIST_SQL)
    assert score and spread
    assert int(score.group(1)) == int(score.group(2)) == _ts_constant("SCORE_BUCKET_WIDTH")
    assert int(spread.group(1)) == int(spread.group(2)) == _ts_constant("SPREAD_BUCKET_BPS")
    window = re.search(r"b\.rate_spread_bps BETWEEN (-?\d+) AND (-?\d+)", _ANALYTICS_REPO.read_text())
    assert window
    assert int(window.group(1)) == _ts_constant("SPREAD_HISTOGRAM_MIN_BPS")
    assert int(window.group(2)) == _ts_constant("SPREAD_HISTOGRAM_MAX_BPS")
