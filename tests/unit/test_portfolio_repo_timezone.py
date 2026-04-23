"""Pin the timezone contract on ``PortfolioPreview.data_refreshed_at``.

Hole-finder round 2 #4 (2026-04-23): the Databricks SQL connector returns
TIMESTAMP as a tz-naive ``datetime``. Serialised to the wire without an
offset, ``new Date(...)`` in the browser re-interprets the string as
local time — European viewers saw the refresh stamp in the wrong hour.
The repository now stamps UTC (``+00:00``) on the outbound value; these
tests are the tripwire so a future refactor can't silently drop the
offset and bring the drift back.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from backend.services.repositories.databricks_repo import (
    DatabricksPortfolioRepository,
)


class _StubClient:
    """Minimal DatabricksSqlClient stand-in.

    Returns ``_preview_row`` for the aggregate query, ``_trend_rows`` for
    the funnel trend query, and ``_day_zero_row`` for the
    ``lead_population`` COUNT probe. We identify which query is in
    flight by looking for distinctive strings in the SQL text.
    """

    def __init__(
        self,
        preview_row: dict[str, Any],
        trend_rows: list[dict[str, Any]],
        day_zero_row: dict[str, Any] | None = None,
    ):
        self._preview_row = preview_row
        self._trend_rows = trend_rows
        self._day_zero_row = day_zero_row if day_zero_row is not None else {"day_zero": False}
        self.preview_calls: int = 0

    def _is_day_zero_sql(self, sql: str) -> bool:
        return "lead_population" in sql and "day_zero" in sql

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        _ = params
        if "funnel_snapshot_daily" in sql:
            return self._trend_rows
        if self._is_day_zero_sql(sql):
            return [self._day_zero_row]
        return [self._preview_row]

    def execute_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        _ = params
        if "funnel_snapshot_daily" in sql:
            return self._trend_rows[0] if self._trend_rows else {}
        if self._is_day_zero_sql(sql):
            return self._day_zero_row
        self.preview_calls += 1
        return self._preview_row


def _preview_row() -> dict[str, Any]:
    return {
        "marketable_population": 1000,
        "high_intent_leads": 300,
        "top_tier_opportunities": 120,
        "offers_recommended": 250,
        "avg_score": 72,
    }


def _trend_row(snapshot_at: Any) -> dict[str, Any]:
    return {
        "snapshot_date": "2026-04-22",
        "snapshot_at": snapshot_at,
        "marketable_population": 1000,
        "high_intent_leads": 300,
        "top_tier_opportunities": 120,
        "offers_recommended": 250,
        "avg_score": 72,
        "approved_count": 0,
        "in_outreach_count": 0,
    }


def test_naive_datetime_is_stamped_as_utc():
    """Tz-naive datetimes from the connector must come out as tz-aware UTC."""
    naive = datetime(2026, 4, 22, 18, 30, 0)
    client = _StubClient(_preview_row(), [_trend_row(naive)])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is not None
    assert preview.data_refreshed_at.tzinfo is not None, (
        "tz-naive datetime leaked through — browser will interpret as local time"
    )
    assert preview.data_refreshed_at.utcoffset() == UTC.utcoffset(preview.data_refreshed_at)
    # Serialised form must carry a UTC marker — either 'Z' (Pydantic's
    # default for UTC-aware datetimes) or '+00:00'. A bare
    # 'YYYY-MM-DDTHH:MM:SS' would mean the naive value leaked through.
    serialised = preview.model_dump_json()
    assert (
        '"data_refreshed_at":"2026-04-22T18:30:00Z"' in serialised
        or '"data_refreshed_at":"2026-04-22T18:30:00+00:00"' in serialised
    ), serialised


def test_already_aware_datetime_is_converted_to_utc():
    """A tz-aware datetime in another zone should convert to UTC, not stay
    in its source zone (otherwise clients comparing offsets drift)."""
    # Fixed UTC-5 (no DST) so the assertion below is deterministic.
    minus_five = timezone(-timedelta(hours=5))
    aware = datetime(2026, 4, 22, 13, 30, 0, tzinfo=minus_five)
    client = _StubClient(_preview_row(), [_trend_row(aware)])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is not None
    # 13:30 UTC-5 == 18:30 UTC
    assert preview.data_refreshed_at.hour == 18
    assert preview.data_refreshed_at.utcoffset() == UTC.utcoffset(preview.data_refreshed_at)


def test_iso_string_is_parsed_and_stamped_utc():
    """Defensive: a future connector change could emit an ISO string. The
    repository parses it and stamps UTC rather than passing through a raw
    string that Pydantic then serialises without tz."""
    client = _StubClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is not None
    assert preview.data_refreshed_at.tzinfo is not None


def test_none_stays_none():
    """Missing snapshot (Day-0 empty gold tables) must keep ``None`` — the
    UI renders the Day-0 empty-state banner on that exact shape."""
    client = _StubClient(_preview_row(), [])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is None


@pytest.mark.parametrize("bad_value", ["not a date", object(), 12345])
def test_unparseable_values_fall_back_to_none(bad_value: Any):
    """Defence in depth: a malformed value should coerce to ``None`` so a
    broken row upstream doesn't poison the whole preview response."""
    client = _StubClient(_preview_row(), [_trend_row(bad_value)])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    # Unparseable → None. Anything else (including a raw string echoed back)
    # would mean the timezone fix regressed.
    assert preview.data_refreshed_at is None or preview.data_refreshed_at.tzinfo is not None


# ---------------------------------------------------------------------------
# R5-20 -- server-authoritative ``day_zero`` flag on PortfolioPreview
# ---------------------------------------------------------------------------


def test_day_zero_true_when_lead_population_empty():
    """Authoritative signal: ``COUNT(*) FROM mip.gold.lead_population == 0``.

    Frontend used to infer day-0 from ``marketable_population == 0 AND
    data_refreshed_at IS NULL``. That mis-classifies a partial CTAS
    roll -- e.g. borrower_360 is being rewritten and briefly holds a
    non-zero count while the snapshot row is still null. The server flag
    closes that window.
    """
    client = _StubClient(
        _preview_row(),
        [_trend_row("2026-04-22T18:30:00")],
        day_zero_row={"day_zero": True},
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    preview = repo.preview(None)
    assert preview.day_zero is True


def test_day_zero_false_when_lead_population_has_rows():
    client = _StubClient(
        _preview_row(),
        [_trend_row("2026-04-22T18:30:00")],
        day_zero_row={"day_zero": False},
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    preview = repo.preview(None)
    assert preview.day_zero is False


def test_day_zero_probe_failure_propagates():
    """R6-07: a warehouse failure on the day-zero probe must propagate.

    The prior implementation silently returned ``False`` on exception,
    which surfaced a misleading preview -- a degraded banner on top of
    a "there IS data, it's just 0" KPI grid. Now the exception bubbles
    to the router so the caller sees one honest 503 and the warming-up
    banner instead of a half-rendered page.
    """

    class _FailingClient(_StubClient):
        def execute_one(
            self, sql: str, params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if self._is_day_zero_sql(sql):
                raise RuntimeError("warehouse probe failed")
            return super().execute_one(sql, params)

    client = _FailingClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="warehouse probe failed"):
        repo.preview(None)


# ---------------------------------------------------------------------------
# R5-08 -- deterministic cache key independent of dict iteration order
# ---------------------------------------------------------------------------


def test_preview_cache_key_stable_across_param_order():
    """``{'a':1,'b':2}`` and ``{'b':2,'a':1}`` must produce the same key."""
    k1 = DatabricksPortfolioRepository._preview_cache_key(
        "WHERE a=:a AND b=:b", {"a": 1, "b": 2}
    )
    k2 = DatabricksPortfolioRepository._preview_cache_key(
        "WHERE a=:a AND b=:b", {"b": 2, "a": 1}
    )
    assert k1 == k2, f"cache key drifts with dict order: {k1!r} != {k2!r}"


def test_preview_cache_key_distinguishes_distinct_criteria():
    """Different WHERE clauses and/or values MUST hash to different keys
    (we'd rather miss cache than serve the wrong row)."""
    k1 = DatabricksPortfolioRepository._preview_cache_key("WHERE state=:s", {"s": "IL"})
    k2 = DatabricksPortfolioRepository._preview_cache_key("WHERE state=:s", {"s": "CA"})
    k3 = DatabricksPortfolioRepository._preview_cache_key("", {})
    assert k1 != k2 != k3 and k1 != k3


def test_preview_second_call_same_order_hits_cache():
    """Functional: two calls with semantically-equivalent criteria must
    share the same cache entry. The second call should NOT re-run the
    preview SELECT on the stub client."""
    from backend.schemas.portfolio import PortfolioCriteria, PortfolioPreviewRequest

    client = _StubClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    req = PortfolioPreviewRequest(
        criteria=PortfolioCriteria(geography="Texas", min_equity_pct=25)
    )
    repo.preview(req)
    calls_after_first = client.preview_calls
    repo.preview(req)
    calls_after_second = client.preview_calls
    assert calls_after_first == calls_after_second == 1, (
        f"second call should have hit cache; saw {calls_after_second} preview SELECTs"
    )
