"""S7 economics-scatter queries and response assembly."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from backend.schemas.analytics import (
    AnalyticsFilters,
    EquitySpreadBin,
    EquitySpreadOverview,
    EquitySpreadPoint,
    EquitySpreadPointsResponse,
    EquitySpreadViewport,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.economics_scatter import (
    EQUITY_BIN_PCT,
    EQUITY_DOMAIN_MAX,
    EQUITY_DOMAIN_MIN,
    EQUITY_SPREAD_SOURCE_TABLE,
    MAX_SCATTER_POINT_ROWS,
    SPREAD_BIN_BPS,
    SPREAD_DOMAIN_MAX,
    SPREAD_DOMAIN_MIN,
    segment_display_label,
)


class _ScatterRepository(Protocol):
    def _cached(self, key: str, builder: Callable[[], Any]) -> Any: ...

    def _execute_template(
        self,
        template: str,
        filters: AnalyticsFilters,
        *,
        alias: str = "b",
        extra: list[str] | None = None,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]: ...


_EQUITY_SPREAD_BINS_SQL = (
    "SELECT "
    "  p.equity_bin_pct AS equity_bin_pct, "
    "  p.spread_bin_bps AS spread_bin_bps, "
    "  CAST(COUNT(*) AS INT) AS borrower_count, "
    "  CAST(ROUND(AVG(p.opportunity_score)) AS INT) AS mean_opportunity_score, "
    "  CAST(SUM(CASE WHEN p.in_the_money THEN 1 ELSE 0 END) AS INT) AS in_the_money_borrowers, "
    "  MAX(p.refreshed_at) AS refreshed_at "
    f"FROM {qualify('gold', 'equity_spread_points')} AS p "
    "{where} "
    "GROUP BY p.equity_bin_pct, p.spread_bin_bps "
    "ORDER BY p.equity_bin_pct, p.spread_bin_bps"
)

_EQUITY_SPREAD_POINTS_SQL = (
    "SELECT "
    "  p.borrower_id AS borrower_id, "
    "  p.display_name AS display_name, "
    "  p.primary_segment_code AS primary_segment_code, "
    "  p.state AS state, "
    "  p.equity_pct AS equity_pct, "
    "  p.rate_spread_bps AS rate_spread_bps, "
    "  p.opportunity_score AS opportunity_score, "
    "  p.score_band AS score_band, "
    "  p.in_the_money AS in_the_money "
    f"FROM {qualify('gold', 'equity_spread_points')} AS p "
    "{where} "
    "ORDER BY p.opportunity_score DESC, p.borrower_id "
    f"LIMIT {MAX_SCATTER_POINT_ROWS}"
)

_EQUITY_SPREAD_POINTS_COUNT_SQL = (
    "SELECT "
    "  CAST(COUNT(*) AS BIGINT) AS total_matching, "
    "  MAX(p.refreshed_at) AS refreshed_at "
    f"FROM {qualify('gold', 'equity_spread_points')} AS p "
    "{where}"
)


def _int(value: Any, default: int = 0) -> int:
    return default if value is None else int(value)


def _filter_key(filters: AnalyticsFilters) -> str:
    return (
        f"states={','.join(filters.states) or '*'}|"
        f"segments={','.join(filters.segment_codes) or '*'}|"
        f"mode={filters.segment_mode}|relationship={filters.lender_relationship or '*'}|"
        f"target_lender={filters.target_lender_ref or '*'}|"
        f"signals={','.join(filters.signal_types) or '*'}|days={filters.days}"
    )


def equity_spread_overview(
    repository: _ScatterRepository,
    filters: AnalyticsFilters,
) -> EquitySpreadOverview:
    """Build the density overview from one grouped gold-table scan."""
    rows = repository._execute_template(_EQUITY_SPREAD_BINS_SQL, filters, alias="p")
    bins = [
        EquitySpreadBin(
            equity_bin_pct=_int(row.get("equity_bin_pct")),
            spread_bin_bps=_int(row.get("spread_bin_bps")),
            borrower_count=_int(row.get("borrower_count")),
            mean_opportunity_score=_int(row.get("mean_opportunity_score")),
            in_the_money_borrowers=_int(row.get("in_the_money_borrowers")),
        )
        for row in rows
    ]
    refreshed = [str(row["refreshed_at"]) for row in rows if row.get("refreshed_at") is not None]
    return EquitySpreadOverview(
        bins=bins,
        total_borrowers=sum(item.borrower_count for item in bins),
        equity_bin_pct=EQUITY_BIN_PCT,
        spread_bin_bps=SPREAD_BIN_BPS,
        equity_domain_min=EQUITY_DOMAIN_MIN,
        equity_domain_max=EQUITY_DOMAIN_MAX,
        spread_domain_min=SPREAD_DOMAIN_MIN,
        spread_domain_max=SPREAD_DOMAIN_MAX,
        source_table=EQUITY_SPREAD_SOURCE_TABLE,
        refreshed_at=max(refreshed) if refreshed else None,
    )


def economics_points(
    repository: _ScatterRepository,
    filters: AnalyticsFilters | None = None,
    viewport: EquitySpreadViewport | None = None,
) -> EquitySpreadPointsResponse:
    analytics_filters = filters or AnalyticsFilters()
    window = viewport or EquitySpreadViewport()

    def build() -> EquitySpreadPointsResponse:
        extra = [
            "p.equity_pct BETWEEN :vp_equity_min AND :vp_equity_max",
            "p.rate_spread_bps BETWEEN :vp_spread_min AND :vp_spread_max",
        ]
        params: dict[str, object] = {
            "vp_equity_min": window.equity_min,
            "vp_equity_max": window.equity_max,
            "vp_spread_min": window.spread_min,
            "vp_spread_max": window.spread_max,
        }
        point_rows = repository._execute_template(
            _EQUITY_SPREAD_POINTS_SQL,
            analytics_filters,
            alias="p",
            extra=extra,
            params=params,
        )
        count_row = (
            repository._execute_template(
                _EQUITY_SPREAD_POINTS_COUNT_SQL,
                analytics_filters,
                alias="p",
                extra=extra,
                params=params,
            )
            or [{}]
        )[0]
        points = [
            EquitySpreadPoint(
                borrower_id=str(row.get("borrower_id") or ""),
                display_name=str(row.get("display_name") or "Borrower"),
                segment=segment_display_label(row.get("primary_segment_code")),
                state=str(row.get("state") or ""),
                equity_pct=_int(row.get("equity_pct")),
                rate_spread_bps=_int(row.get("rate_spread_bps")),
                opportunity_score=_int(row.get("opportunity_score")),
                score_band=row.get("score_band"),
                in_the_money=bool(row.get("in_the_money")),
            )
            for row in point_rows[:MAX_SCATTER_POINT_ROWS]
        ]
        total_matching = max(_int(count_row.get("total_matching")), len(points))
        refreshed_at = count_row.get("refreshed_at")
        return EquitySpreadPointsResponse(
            points=points,
            total_matching=total_matching,
            showing=len(points),
            point_cap=MAX_SCATTER_POINT_ROWS,
            truncated=total_matching > len(points),
            viewport=window,
            source_table=EQUITY_SPREAD_SOURCE_TABLE,
            refreshed_at=None if refreshed_at is None else str(refreshed_at),
        )

    key = (
        f"analytics.economics_points:{_filter_key(analytics_filters)}|"
        f"vp={window.equity_min},{window.equity_max},{window.spread_min},{window.spread_max}"
    )
    return repository._cached(key, build)
