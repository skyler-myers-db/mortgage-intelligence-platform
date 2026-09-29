"""The refi screen a refresh applied, for the economics charts (dataviz-06).

``fn_in_the_money`` receives its spread and equity thresholds from the
application layer, and ``gold_borrower_360.sql`` carries the pair it applied
on every row (``min_spread_bps_applied`` / ``min_equity_pct_applied``). The
spread histogram's rule and the scatter guides read that pair from here, so a
lender-tuned screen can never drift from the chart and the frontend holds no
threshold constant.

The pair is per refresh, not per filter, so the statement is unfiltered and
cached under one filter-independent key: two filter combinations cost one
statement per TTL. A query failure propagates through the repository's
resilience path and fails the economics response visibly; there is no
default and no substitution.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from backend.schemas.analytics import AnalyticsThresholds
from backend.services.databricks_sql_helpers import qualify

THRESHOLDS_CACHE_KEY = "analytics.thresholds"

ECONOMICS_THRESHOLDS_SQL = (
    "SELECT "
    "  CAST(COUNT(*) AS BIGINT) AS row_count, "
    "  CAST(COUNT(min_spread_bps_applied) AS BIGINT) AS spread_rows, "
    "  MIN(min_spread_bps_applied) AS min_spread_lo, "
    "  MAX(min_spread_bps_applied) AS min_spread_hi, "
    "  CAST(COUNT(min_equity_pct_applied) AS BIGINT) AS equity_rows, "
    "  MIN(min_equity_pct_applied) AS min_equity_lo, "
    "  MAX(min_equity_pct_applied) AS min_equity_hi "
    f"FROM {qualify('gold', 'borrower_360')}"
)


class _SqlClient(Protocol):
    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]: ...


class _ThresholdsRepository(Protocol):
    @property
    def _client(self) -> _SqlClient: ...

    def _cached(self, key: str, builder: Callable[[], Any]) -> Any: ...


_Why = Literal["not_built", "not_uniform"]


def _governed_value(row: dict[str, Any], rows: str, lo: str, hi: str) -> tuple[int | None, _Why | None]:
    """(value, why-null) for one ``*_applied`` column."""
    total = int(row.get("row_count") or 0)
    carrying = int(row.get(rows) or 0)
    low, high = row.get(lo), row.get(hi)
    if carrying == 0 or low is None or high is None:
        return None, "not_built"
    if carrying != total or int(low) != int(high):
        return None, "not_uniform"
    return int(low), None


def thresholds_from_row(row: dict[str, Any]) -> AnalyticsThresholds:
    spread, spread_why = _governed_value(row, "spread_rows", "min_spread_lo", "min_spread_hi")
    equity, equity_why = _governed_value(row, "equity_rows", "min_equity_lo", "min_equity_hi")
    whys = {spread_why, equity_why}
    reason: _Why | None = None
    if "not_uniform" in whys:
        reason = "not_uniform"
    elif "not_built" in whys:
        reason = "not_built"
    return AnalyticsThresholds(min_spread_bps=spread, min_equity_pct=equity, reason=reason)


def economics_thresholds(repo: _ThresholdsRepository) -> AnalyticsThresholds:
    def build() -> AnalyticsThresholds:
        rows = repo._client.execute(ECONOMICS_THRESHOLDS_SQL)
        return thresholds_from_row(rows[0] if rows else {})

    result: AnalyticsThresholds = repo._cached(THRESHOLDS_CACHE_KEY, build)
    return result
