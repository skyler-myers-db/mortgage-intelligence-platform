"""``GET /api/growth-agent/monitors/summary`` briefs saved watchlists, audit-free (wow-ai-4).

Each briefing is a watchlist's latest completed run, the run before it, the
change between them and up to eight recent eligible counts, with the cached
scheduler state on the envelope. It writes no audit row, never touches the
SQL warehouse, never starts a run and never returns a stored route, criteria
or actor. The SQL's window maths runs on real PostgreSQL in
``tests/integration/test_growth_agent_runs_postgres.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import backend.api.growth_agent_compose_routes as compose_routes
from backend.main import app
from backend.schemas.growth_agent_watchlist import (
    GrowthAgentSchedulerStatus,
    GrowthAgentWatchlistBriefing,
)
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.growth_agent_ledger_sql import WATCHLIST_SUMMARY_SQL
from backend.services.growth_agent_watchlist_summary import briefings_from_rows
from backend.services.lakebase import LakebaseError, get_lakebase_client

ACTOR = "loan.officer@example.com"
SUMMARY_PATH = "/api/growth-agent/monitors/summary"
T0 = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
PAUSED = GrowthAgentSchedulerStatus(state="paused", reason="job_schedule")


def _series_row(monitor_id: str, recency: int | None, **run: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "monitor_id": monitor_id,
        "workflow_id": "daily_refi_brief",
        "name": "IL Refi Watch",
        "cadence": "daily",
        "status": "active",
        "recency": recency,
        "run_count": None,
        "run_at": None,
        "actionable_total": None,
        "actionable_avg_score": None,
        "previous_run_at": None,
        "previous_actionable_total": None,
        "previous_actionable_avg_score": None,
        # A column the SQL never selects; the briefing must not echo it.
        "route": "/lead-queue?segment=itm&growth_handoff=secret-proof",
    }
    base.update(run)
    return base


def _three_run_series(monitor_id: str = "m-1") -> list[dict[str, Any]]:
    """Newest first, as WATCHLIST_SUMMARY_SQL orders them (recency 1 = latest)."""

    return [
        _series_row(
            monitor_id, 1, run_count=3, run_at=T0 + timedelta(days=2), actionable_total=52,
            actionable_avg_score=74.26, previous_run_at=T0 + timedelta(days=1),
            previous_actionable_total=44, previous_actionable_avg_score=72.0,
        ),
        _series_row(
            monitor_id, 2, run_count=3, run_at=T0 + timedelta(days=1), actionable_total=44,
            actionable_avg_score=72.0, previous_run_at=T0, previous_actionable_total=40,
            previous_actionable_avg_score=71.0,
        ),
        _series_row(monitor_id, 3, run_count=3, run_at=T0, actionable_total=40, actionable_avg_score=71.0),
    ]


class _SummaryLakebase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetchalls: list[tuple[str, dict[str, Any], int]] = []
        self.transactions = 0
        self.fail = False

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, dict(params or {}), limit))
        if self.fail:
            raise LakebaseError("lakebase down at host db-1")
        return [dict(row) for row in self.rows][:limit]

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        self.transactions += 1
        yield None


@pytest.fixture
def summary_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_SummaryLakebase, MagicMock, MagicMock, TestClient]]:
    lakebase = _SummaryLakebase(_three_run_series() + [_series_row("m-2", None, name="TX Refi Watch")])
    sql = MagicMock(name="sql_client")
    audit_store = MagicMock(name="audit_store")
    monkeypatch.setattr(compose_routes, "growth_agent_scheduler_status", lambda: PAUSED)
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_audit_store] = lambda: audit_store
    try:
        yield lakebase, sql, audit_store, TestClient(app)
    finally:
        for dependency in (get_lakebase_client, get_sql_client, get_audit_store):
            app.dependency_overrides.pop(dependency, None)


def _get(client: TestClient, query: str = "") -> Any:
    return client.get(f"{SUMMARY_PATH}{query}", headers={"X-Forwarded-Email": ACTOR})


def test_briefings_carry_the_run_over_run_change(
    summary_client: tuple[_SummaryLakebase, MagicMock, MagicMock, TestClient],
) -> None:
    _lakebase, _sql, _audit, client = summary_client
    response = _get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scheduler"] == {"state": "paused", "reason": "job_schedule"}
    first, second = body["watchlists"]
    assert first["monitor_id"] == "m-1" and first["run_count"] == 3
    assert (first["actionable_total"], first["previous_actionable_total"], first["actionable_delta"]) == (52, 44, 8)
    assert (first["actionable_avg_score"], first["previous_actionable_avg_score"]) == (74.26, 72.0)
    assert first["avg_score_delta"] == 2.3
    assert first["recent_actionable_totals"] == [40, 44, 52]
    assert first["last_run_at"].startswith("2026-09-22T09:00")
    assert first["previous_run_at"].startswith("2026-09-21T09:00")
    assert second["monitor_id"] == "m-2" and second["name"] == "TX Refi Watch"
    assert second["run_count"] == 0 and second["recent_actionable_totals"] == []
    assert second["actionable_delta"] is None and second["avg_score_delta"] is None


def test_a_single_run_has_no_delta() -> None:
    [briefing] = briefings_from_rows(
        [_series_row("m-1", 1, run_count=1, run_at=T0, actionable_total=12, actionable_avg_score=70.0)]
    )
    assert briefing.actionable_delta is None and briefing.avg_score_delta is None
    assert briefing.recent_actionable_totals == [12]


def test_a_drop_is_a_negative_delta() -> None:
    [briefing] = briefings_from_rows(
        [
            _series_row(
                "m-1", 1, run_count=2, run_at=T0, actionable_total=30, actionable_avg_score=68.04,
                previous_actionable_total=41, previous_actionable_avg_score=70.0,
            )
        ]
    )
    assert briefing.actionable_delta == -11
    assert briefing.avg_score_delta == -2.0


def test_the_read_is_audit_free_and_never_touches_the_warehouse(
    summary_client: tuple[_SummaryLakebase, MagicMock, MagicMock, TestClient],
) -> None:
    lakebase, sql, audit_store, client = summary_client
    for _ in range(3):
        assert _get(client).status_code == 200
    assert audit_store.mock_calls == []
    assert sql.mock_calls == []
    assert lakebase.transactions == 0


def test_the_body_never_carries_route_criteria_or_actor(
    summary_client: tuple[_SummaryLakebase, MagicMock, MagicMock, TestClient],
) -> None:
    _lakebase, _sql, _audit, client = summary_client
    response = _get(client)
    for briefing in response.json()["watchlists"]:
        assert set(briefing) == set(GrowthAgentWatchlistBriefing.model_fields)
        assert {"route", "criteria", "actor_email", "last_run_id", "seed_run_id"}.isdisjoint(briefing)
    assert "growth_handoff" not in response.text
    assert ACTOR not in response.text


def test_the_sql_is_actor_scoped_and_selects_no_private_column() -> None:
    # The watchlists CTE plus each of the series' three arms (monitor, seed, last run).
    assert WATCHLIST_SUMMARY_SQL.count("actor_email = %(actor_email)s") == 4
    final_select = WATCHLIST_SUMMARY_SQL.rsplit("SELECT", 1)[1].split("FROM", 1)[0]
    for column in ("route", "criteria", "actor_email", "seed_run_id", "last_run_id"):
        assert not re.search(rf"\b{column}\b", final_select), column
    assert "r.status = 'completed'" in WATCHLIST_SUMMARY_SQL
    assert "LAG(r.actionable_total)" in WATCHLIST_SUMMARY_SQL


@pytest.mark.parametrize(("query", "expected"), [("", 20), ("?limit=1", 1), ("?limit=50", 50)])
def test_limit_is_bounded(
    summary_client: tuple[_SummaryLakebase, MagicMock, MagicMock, TestClient], query: str, expected: int
) -> None:
    lakebase, _sql, _audit, client = summary_client
    assert _get(client, query).status_code == 200
    _query_sql, params, row_cap = lakebase.fetchalls[-1]
    assert params == {"actor_email": ACTOR, "limit": expected, "points": 8}
    assert row_cap == expected * 8


@pytest.mark.parametrize("query", ["?limit=0", "?limit=51", "?limit=many"])
def test_out_of_range_limits_are_rejected(
    summary_client: tuple[_SummaryLakebase, MagicMock, MagicMock, TestClient], query: str
) -> None:
    lakebase, _sql, _audit, client = summary_client
    assert _get(client, query).status_code == 422
    assert lakebase.fetchalls == []


def test_a_lakebase_failure_is_a_safe_503(
    summary_client: tuple[_SummaryLakebase, MagicMock, MagicMock, TestClient],
) -> None:
    lakebase, _sql, _audit, client = summary_client
    lakebase.fail = True
    response = _get(client)
    assert response.status_code == 503
    assert "db-1" not in response.text
