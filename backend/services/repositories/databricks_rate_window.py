"""Databricks-backed rate-window repository.

Reads ``mip.gold.rate_window_weekly`` -- the "why now" series the gold
refresh job precomputes (weekly FRED MORTGAGE30US against the current
fixed-rate book's note-rate band, plus the in-the-money count per week via
the canonical ``fn_rate_spread`` / ``fn_in_the_money`` primitives).

The repository is deliberately a thin projection: ONE statement over a
sub-300-row gold table, ordered by week. It never touches
``gold.borrower_360`` and never runs a percentile per request (the SQL
contract test pins both), because the multi-million-row book is exactly
the thing the gold table exists to keep off the request path.

Cache posture matches the geography rollups: short-TTL, single-flight (a
burst of Executive-tab loads shares one warehouse round-trip) and
stale-if-error (an expired entry is served while the warehouse flaps). A
COLD cache with a failing warehouse propagates the failure so the router's
resilience layer returns an honest 503 ``warming_up`` -- never an empty
fabricated series.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from backend.schemas.analytics_rate_window import (
    RateWindowProvenance,
    RateWindowResponse,
    RateWindowThresholds,
    RateWindowWeek,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.resilience import TTLCache

SERIES_ID = "MORTGAGE30US"

_GOLD_SOURCE = qualify("gold", "rate_window_weekly")
_MARKET_RATE_SOURCE = qualify("silver", "market_rates_weekly")
_BOOK_SOURCE = f"{qualify('gold', 'borrower_360')} + {qualify('silver', 'lien_current')}"
_RULE_SOURCE = f"{qualify('gold', 'fn_rate_spread')} + {qualify('gold', 'fn_in_the_money')}"

_PROVENANCE_NOTE = (
    "Today's book against the historical rate: the note-rate band and the "
    "in-the-money count are measured over the current fixed-rate book (as of "
    "book_as_of) and applied to every week's market rate. This is not the book "
    "as it stood in that week. Fixed-rate first liens only; ARM and unknown rate "
    "types are excluded because an adjustable coupon is not comparable to the "
    "30-year fixed print."
)

# The whole series, oldest first. ``is_latest`` is carried from silver so the
# current print is governed, not inferred from ordering.
RATE_WINDOW_SQL = (
    "SELECT "
    "  series_id, "
    "  observation_week, "
    "  CAST(market_rate_pct AS DOUBLE) AS market_rate_pct, "
    "  is_latest, "
    "  CAST(book_median_rate_pct AS DOUBLE) AS book_median_rate_pct, "
    "  CAST(book_p25_rate_pct AS DOUBLE) AS book_p25_rate_pct, "
    "  CAST(book_p75_rate_pct AS DOUBLE) AS book_p75_rate_pct, "
    "  CAST(book_lien_count AS BIGINT) AS book_lien_count, "
    "  CAST(itm_count AS BIGINT) AS itm_count, "
    "  min_spread_bps_applied, "
    "  min_equity_pct_applied, "
    "  book_as_of, "
    "  refreshed_at "
    f"FROM {_GOLD_SOURCE} "
    "WHERE series_id = :series_id "
    "ORDER BY observation_week"
)


def _date_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _timestamp_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return float(str(value))


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(float(str(value)))


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "t"}


class DatabricksRateWindowRepository:
    """Typed read model over ``mip.gold.rate_window_weekly``."""

    _SQL = RATE_WINDOW_SQL
    _CACHE_KEY = "analytics.rate_window"

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 60.0,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s

    def rate_window(self) -> RateWindowResponse:
        def build() -> RateWindowResponse:
            rows = self._client.execute(self._SQL, {"series_id": SERIES_ID}) or []
            return self._project(rows)

        result: RateWindowResponse = self._cache.get_or_set(
            self._CACHE_KEY,
            build,
            ttl_s=self._cache_ttl_s,
            stale_if_error=True,
        )
        return result

    @staticmethod
    def _project(rows: list[dict[str, Any]]) -> RateWindowResponse:
        weeks: list[RateWindowWeek] = []
        for row in rows:
            week = _date_text(row.get("observation_week"))
            if not week:
                continue
            weeks.append(
                RateWindowWeek(
                    week=week,
                    market_rate_pct=_float_or_none(row.get("market_rate_pct")) or 0.0,
                    book_median_pct=_float_or_none(row.get("book_median_rate_pct")),
                    book_p25_pct=_float_or_none(row.get("book_p25_rate_pct")),
                    book_p75_pct=_float_or_none(row.get("book_p75_rate_pct")),
                    itm_count=max(0, _int_or_none(row.get("itm_count")) or 0),
                    is_latest=_bool(row.get("is_latest")),
                )
            )
        # Every row carries the same book columns (one book, many weeks); the
        # first row is as good as any and an empty series has none.
        head = rows[0] if rows else {}
        book_as_of = _timestamp_text(head.get("book_as_of"))
        return RateWindowResponse(
            series_id=SERIES_ID,
            weeks=weeks,
            book_lien_count=max(0, _int_or_none(head.get("book_lien_count")) or 0),
            book_as_of=book_as_of,
            thresholds=RateWindowThresholds(
                min_spread_bps=_int_or_none(head.get("min_spread_bps_applied")),
                min_equity_pct=_int_or_none(head.get("min_equity_pct_applied")),
            ),
            provenance=RateWindowProvenance(
                market_rate_source=_MARKET_RATE_SOURCE,
                book_source=_BOOK_SOURCE,
                gold_source=_GOLD_SOURCE,
                rule_source=_RULE_SOURCE,
                book_as_of=book_as_of,
                refreshed_at=_timestamp_text(head.get("refreshed_at")),
                note=_PROVENANCE_NOTE,
            ),
        )
