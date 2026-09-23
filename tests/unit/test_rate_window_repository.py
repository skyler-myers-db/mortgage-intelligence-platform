"""DatabricksRateWindowRepository: projection + cache posture (dataviz-08).

Driven through a fake SQL client so no warehouse is touched. Pins:

* the SQL reads ONLY the precomputed gold table -- never borrower_360 and
  never a percentile per request (that work belongs to the refresh job);
* row coercion (string-typed statement-API values, NULL book columns);
* the cache posture the brief asks for: short-TTL, single-flight (a burst
  shares one round-trip), stale-if-error (an expired entry survives a
  warehouse flap) and cold-cache failures propagating to the 503 path.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from backend.services.repositories.databricks_rate_window import (
    RATE_WINDOW_SQL,
    DatabricksRateWindowRepository,
)
from backend.services.resilience import TTLCache


class _FakeSqlClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.gate: threading.Event | None = None

    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self.gate is not None:
            self.gate.wait(timeout=5.0)
        if self.error is not None:
            raise self.error
        self.calls.append((statement, dict(parameters or {})))
        return list(self.rows)

    def execute_one(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


def _rows() -> list[dict[str, Any]]:
    # Statement Execution API JSON rows arrive as strings; the projection must
    # coerce them, and NULL book columns must stay None.
    common = {
        "series_id": "MORTGAGE30US",
        "book_median_rate_pct": "7.1",
        "book_p25_rate_pct": "6.55",
        "book_p75_rate_pct": "7.62",
        "book_lien_count": "48210",
        "min_spread_bps_applied": "75",
        "min_equity_pct_applied": "15",
        "book_as_of": "2026-04-16 06:00:00",
        "refreshed_at": "2026-04-16 06:00:00",
    }
    return [
        {**common, "observation_week": "2026-04-06", "market_rate_pct": "6.37", "is_latest": "false", "itm_count": "1180"},
        {**common, "observation_week": "2026-04-13", "market_rate_pct": "6.3", "is_latest": "true", "itm_count": "1204"},
    ]


def test_sql_reads_only_the_precomputed_gold_table() -> None:
    lowered = RATE_WINDOW_SQL.lower()
    assert "mip.gold.rate_window_weekly" in lowered
    assert "order by observation_week" in lowered
    assert ":series_id" in lowered
    # The book is never touched per request, and no percentile is computed.
    assert "borrower_360" not in lowered
    assert "lien_current" not in lowered
    assert "percentile" not in lowered
    assert "is_latest = true" not in lowered  # the whole history, not one row


def test_projection_coerces_rows_and_carries_book_provenance() -> None:
    client = _FakeSqlClient()
    client.rows = _rows()
    repo = DatabricksRateWindowRepository(client, cache_ttl_s=60.0)

    result = repo.rate_window()

    assert [(w.week, w.market_rate_pct, w.itm_count, w.is_latest) for w in result.weeks] == [
        ("2026-04-06", 6.37, 1180, False),
        ("2026-04-13", 6.3, 1204, True),
    ]
    assert result.weeks[0].book_median_pct == 7.1
    assert result.weeks[0].book_p25_pct == 6.55
    assert result.weeks[0].book_p75_pct == 7.62
    assert result.book_lien_count == 48210
    assert result.book_as_of == "2026-04-16 06:00:00"
    assert result.thresholds.min_spread_bps == 75
    assert result.thresholds.min_equity_pct == 15
    assert result.provenance.market_rate_source == "mip.silver.market_rates_weekly"
    assert result.provenance.gold_source == "mip.gold.rate_window_weekly"
    assert result.provenance.book_as_of == result.book_as_of
    assert "book_as_of" in result.provenance.note
    assert client.calls[0][1] == {"series_id": "MORTGAGE30US"}


def test_projection_keeps_empty_book_columns_null_and_counts_non_negative() -> None:
    client = _FakeSqlClient()
    client.rows = [
        {
            "series_id": "MORTGAGE30US",
            "observation_week": "2026-04-13",
            "market_rate_pct": "6.3",
            "is_latest": True,
            "book_median_rate_pct": None,
            "book_p25_rate_pct": None,
            "book_p75_rate_pct": None,
            "book_lien_count": "0",
            "itm_count": "0",
            "min_spread_bps_applied": None,
            "min_equity_pct_applied": None,
            "book_as_of": "2026-04-16 06:00:00",
            "refreshed_at": "2026-04-16 06:00:00",
        },
    ]
    repo = DatabricksRateWindowRepository(client)

    result = repo.rate_window()

    assert result.weeks[0].book_median_pct is None
    assert result.weeks[0].itm_count == 0
    assert result.book_lien_count == 0
    assert result.thresholds.min_spread_bps is None
    assert result.thresholds.min_equity_pct is None


def test_a_week_without_a_print_is_skipped_not_drawn_at_zero() -> None:
    client = _FakeSqlClient()
    rows = _rows()
    rows.insert(1, {**rows[0], "observation_week": "2026-04-09", "market_rate_pct": None, "itm_count": "0"})
    client.rows = rows
    repo = DatabricksRateWindowRepository(client)

    result = repo.rate_window()

    assert [w.week for w in result.weeks] == ["2026-04-06", "2026-04-13"]
    assert all(w.market_rate_pct > 0 for w in result.weeks)


def test_empty_series_is_an_empty_response_not_an_error() -> None:
    client = _FakeSqlClient()
    repo = DatabricksRateWindowRepository(client)

    result = repo.rate_window()

    assert result.weeks == []
    assert result.book_as_of is None
    assert result.provenance.gold_source == "mip.gold.rate_window_weekly"


def test_repeat_reads_inside_ttl_share_one_round_trip() -> None:
    clock = [0.0]
    client = _FakeSqlClient()
    client.rows = _rows()
    repo = DatabricksRateWindowRepository(client, cache=TTLCache(now=lambda: clock[0]), cache_ttl_s=10.0)

    first = repo.rate_window()
    clock[0] = 5.0
    second = repo.rate_window()

    assert first is second
    assert len(client.calls) == 1

    clock[0] = 11.0  # past TTL -> refresh
    repo.rate_window()
    assert len(client.calls) == 2


def test_concurrent_cold_reads_are_single_flight() -> None:
    client = _FakeSqlClient()
    client.rows = _rows()
    client.gate = threading.Event()
    repo = DatabricksRateWindowRepository(client, cache_ttl_s=60.0)
    results: list[Any] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(repo.rate_window())
        except BaseException as exc:  # pragma: no cover - surfaced through the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    client.gate.set()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not errors
    assert len(results) == 4
    assert all(r.weeks[-1].itm_count == 1204 for r in results)
    assert len(client.calls) == 1, [c[0][:60] for c in client.calls]


def test_expired_entry_is_served_stale_when_the_refresh_fails() -> None:
    clock = [0.0]
    client = _FakeSqlClient()
    client.rows = _rows()
    repo = DatabricksRateWindowRepository(client, cache=TTLCache(now=lambda: clock[0]), cache_ttl_s=10.0)

    first = repo.rate_window()
    assert first.weeks[-1].itm_count == 1204

    clock[0] = 11.0  # past TTL
    client.error = RuntimeError("warehouse down")
    second = repo.rate_window()

    assert second.weeks[-1].itm_count == 1204
    assert second.book_as_of == first.book_as_of


def test_cold_cache_failure_propagates_to_the_503_path() -> None:
    client = _FakeSqlClient()
    client.error = RuntimeError("warehouse down")
    repo = DatabricksRateWindowRepository(client, cache_ttl_s=60.0)

    with pytest.raises(RuntimeError, match="warehouse down"):
        repo.rate_window()
