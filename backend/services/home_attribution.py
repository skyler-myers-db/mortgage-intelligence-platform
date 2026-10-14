"""Home Delta Explainer reads (audit 2026-09-21 ``wow-ai-3``).

``GET /api/home/summary/attribution`` explains one "since your last login"
movement from gold only:

* ``mip.gold.funnel_snapshot_daily`` -- the nearest snapshot to the requested
  baseline (the latest one ON OR BEFORE it, else the earliest after it) and
  the latest snapshot, per state and the ``_ALL`` national row;
* ``mip.gold.rate_window_weekly`` -- the FRED MORTGAGE30US print for the
  baseline week and the latest week;
* ``mip.ref.offer_rules_config`` -- the last time a rule changed
  (``MAX(last_updated)`` only).

App SQL is gold/ref only (never silver, never ``borrower_360``); every table
name goes through ``qualify``; values are named binds; the measure maps to
its column through a closed table, never through user text. The read writes
no audit row. With no snapshot rows the response says so with None totals
and no states: nothing is fabricated.

Snapshot dates are chosen only among snapshots that RECORDED the measure
(``{column} IS NOT NULL``). That matters for ``competitor_lien``: its column
(wow-ai-3, W5c) is nullable, rows recorded before it existed stay NULL, so
until the first post-deploy snapshot the response is the no-snapshot shape.
An install whose table has not gained the column yet (the App ahead of
``jobs/sync_lifecycle_state._ensure_funnel_snapshot_schema``) answers that
same shape, never a 5xx.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from threading import Lock
from typing import Any

from backend.schemas.home_attribution import (
    HomeAttributionMeasure,
    HomeAttributionRate,
    HomeAttributionState,
    HomeSummaryAttributionResponse,
)
from backend.services.databricks_sql import DatabricksSqlColumnMissingError
from backend.services.databricks_sql_helpers import qualify
from backend.services.gold_cache import AggregateCache, GoldAggregateCache
from backend.services.resilience_breaker import DependencyDownError

#: measure -> (segment_code row of funnel_snapshot_daily, column, display label).
MEASURE_COLUMNS: dict[str, tuple[str, str, str]] = {
    "refi_economics_screen": ("_ALL", "in_the_money_borrowers", "refi candidates"),
    "high_opportunity": ("_ALL", "high_opportunity_borrowers", "high-opportunity"),
    "offers_recommended": ("_ALL", "offer_recommended_borrowers", "primary offer paths"),
    "listed_for_sale": ("listed", "addressable_borrowers", "listed for sale"),
    "competitor_lien": ("_ALL", "competitor_lien_borrowers", "competitor liens"),
}

#: Funnel columns an existing table gains only from the ensure step, so a
#: read may run ahead of them; every other column is NOT NULL in 003.
_ENSURED_COLUMNS = frozenset({"competitor_lien_borrowers"})

RATE_SERIES_ID = "MORTGAGE30US"
NATIONAL = "_ALL"


def _snapshot_dates_sql(column: str) -> str:
    # ``column`` comes from the closed MEASURE_COLUMNS table, never user text.
    return (
        "SELECT "
        "CAST(MAX(CASE WHEN snapshot_date <= CAST(:baseline AS DATE) THEN snapshot_date END) AS STRING) AS at_or_before, "
        "CAST(MIN(CASE WHEN snapshot_date > CAST(:baseline AS DATE) THEN snapshot_date END) AS STRING) AS after, "
        "CAST(MAX(snapshot_date) AS STRING) AS latest "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} "
        "WHERE state = '_ALL' AND segment_code = :segment_code "
        f"AND {column} IS NOT NULL"
    )


def _is_ensured_column_missing(exc: BaseException, column: str) -> bool:
    """The App read the funnel table before the ensure step added ``column``."""
    if column not in _ENSURED_COLUMNS:
        return False
    error = exc.last_error if isinstance(exc, DependencyDownError) else exc
    return isinstance(error, DatabricksSqlColumnMissingError)


def _snapshot_values_sql(column: str) -> str:
    return (
        f"SELECT CAST(snapshot_date AS STRING) AS snapshot_date, state, {column} AS value "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} "
        "WHERE segment_code = :segment_code "
        "AND snapshot_date IN (CAST(:baseline_date AS DATE), CAST(:current_date AS DATE))"
    )


def _rate_weeks_sql() -> str:
    rate = qualify("gold", "rate_window_weekly")
    return (
        "SELECT CAST(observation_week AS STRING) AS observation_week, market_rate_pct, is_latest "
        f"FROM {rate} "
        "WHERE series_id = :series_id AND (is_latest = TRUE OR observation_week = ("
        f"SELECT MAX(observation_week) FROM {rate} "
        "WHERE series_id = :series_id AND observation_week <= CAST(:baseline AS DATE)))"
    )


def _offer_rules_sql() -> str:
    return f"SELECT CAST(MAX(last_updated) AS STRING) AS last_updated FROM {qualify('ref', 'offer_rules_config')}"


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    return date.fromisoformat(str(value)[:10])


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _as_int(value: Any) -> int | None:
    return None if value is None else int(value)


def choose_snapshot_dates(row: dict[str, Any] | None) -> tuple[date | None, date | None]:
    """(baseline, current): the latest snapshot ON OR BEFORE the baseline, else the earliest after it."""
    row = row or {}
    baseline = _as_date(row.get("at_or_before")) or _as_date(row.get("after"))
    return baseline, _as_date(row.get("latest"))


def rate_facts(rows: list[dict[str, Any]], baseline: date) -> HomeAttributionRate:
    """The latest week and the latest week on or before the baseline."""
    latest = next((row for row in rows if row.get("is_latest") in (True, "true", "TRUE")), None)
    before = [row for row in rows if (_as_date(row.get("observation_week")) or date.max) <= baseline]
    at_baseline = max(before, key=lambda row: str(row.get("observation_week")), default=None)
    return HomeAttributionRate(
        series_id=RATE_SERIES_ID,
        baseline_week=_as_date(at_baseline.get("observation_week")) if at_baseline else None,
        baseline_pct=float(at_baseline["market_rate_pct"]) if at_baseline else None,
        latest_week=_as_date(latest.get("observation_week")) if latest else None,
        latest_pct=float(latest["market_rate_pct"]) if latest else None,
    )


def attribute_states(
    rows: list[dict[str, Any]], baseline_date: date, current_date: date
) -> tuple[int | None, int | None, list[HomeAttributionState]]:
    """(baseline total, current total, per-state rows) from the two snapshots' values."""
    by_date: dict[date, dict[str, int]] = {baseline_date: {}, current_date: {}}
    for row in rows:
        when = _as_date(row.get("snapshot_date"))
        value = _as_int(row.get("value"))
        if when in by_date and value is not None:
            by_date[when][str(row.get("state"))] = value
    before, after = by_date[baseline_date], by_date[current_date]
    names = sorted((set(before) | set(after)) - {NATIONAL})
    states = []
    for state in names:
        then, now = before.get(state), after.get(state)
        change = None if then is None or now is None else now - then
        states.append(HomeAttributionState(state=state, baseline_count=then, current_count=now, change=change))
    states.sort(key=lambda row: (row.change is None, -abs(row.change or 0), row.state))
    return before.get(NATIONAL), after.get(NATIONAL), states


class HomeAttributionService:
    """Audit-free gold/ref reads behind the Delta Explainer, cached per (measure, baseline)."""

    def __init__(
        self,
        sql_client: Any | None = None,
        *,
        cache: AggregateCache | None = None,
        ttl_s: float | None = None,
        sql_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._sql_client = sql_client
        self._sql_factory = sql_factory
        self._cache: AggregateCache = cache if cache is not None else GoldAggregateCache()
        self._ttl_s = ttl_s

    def _sql(self) -> Any:
        if self._sql_client is None:
            if self._sql_factory is not None:
                self._sql_client = self._sql_factory()
            else:
                from backend.services.databricks_sql import get_sql_client

                self._sql_client = get_sql_client()
        return self._sql_client

    def _ttl(self) -> float:
        if self._ttl_s is not None:
            return self._ttl_s
        from backend.config.settings import settings

        return float(settings.mip_cache_ttl_s)

    @staticmethod
    def cache_key(measure: str, baseline: date) -> str:
        return f"home.attribution.{measure}.{baseline.isoformat()}"

    def attribution(self, measure: HomeAttributionMeasure, baseline: date) -> HomeSummaryAttributionResponse:
        return self._cache.get_or_set(
            self.cache_key(measure, baseline), lambda: self._load(measure, baseline), ttl_s=self._ttl()
        )

    def _load(self, measure: HomeAttributionMeasure, baseline: date) -> HomeSummaryAttributionResponse:
        segment_code, column, label = MEASURE_COLUMNS[measure]
        sql = self._sql()
        baseline_iso = baseline.isoformat()
        try:
            dates = sql.execute_one(
                _snapshot_dates_sql(column), {"baseline": baseline_iso, "segment_code": segment_code}
            )
        except (DependencyDownError, DatabricksSqlColumnMissingError) as exc:
            if not _is_ensured_column_missing(exc, column):
                raise
            dates = None  # no snapshot has recorded the measure yet
        baseline_date, current_date = choose_snapshot_dates(dates)
        rate = rate_facts(
            sql.execute(_rate_weeks_sql(), {"series_id": RATE_SERIES_ID, "baseline": baseline_iso}) or [],
            baseline,
        )
        rules_row = sql.execute_one(_offer_rules_sql()) or {}
        rules_updated = _as_datetime(rules_row.get("last_updated"))
        response = HomeSummaryAttributionResponse(
            measure=measure,
            label=label,
            requested_baseline_date=baseline,
            baseline_snapshot_date=baseline_date,
            current_snapshot_date=current_date,
            nearest_snapshot=baseline_date is not None and baseline_date != baseline,
            rate=rate,
            offer_rules_last_updated=rules_updated,
            offer_rules_changed_since_baseline=(
                None if rules_updated is None else rules_updated.date() > baseline
            ),
            sources=[
                qualify("gold", "funnel_snapshot_daily"),
                qualify("gold", "rate_window_weekly"),
                qualify("ref", "offer_rules_config"),
            ],
        )
        if baseline_date is None or current_date is None:
            return response
        rows = sql.execute(
            _snapshot_values_sql(column),
            {
                "segment_code": segment_code,
                "baseline_date": baseline_date.isoformat(),
                "current_date": current_date.isoformat(),
            },
        ) or []
        baseline_total, current_total, states = attribute_states(rows, baseline_date, current_date)
        total_change = (
            None if baseline_total is None or current_total is None else current_total - baseline_total
        )
        attributed = sum(row.change for row in states if row.change is not None)
        return response.model_copy(
            update={
                "baseline_total": baseline_total,
                "current_total": current_total,
                "total_change": total_change,
                "states": states,
                "unattributed_change": None if total_change is None else total_change - attributed,
            }
        )


_SERVICE: HomeAttributionService | None = None
_SERVICE_LOCK = Lock()


def get_home_attribution_service() -> HomeAttributionService:
    """Process-singleton accessor (mirrors the other service factories)."""
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = HomeAttributionService()
    return _SERVICE
