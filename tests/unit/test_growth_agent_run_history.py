"""``GET /api/growth-agent/runs`` is a read-only, actor-scoped list (genie-09 part 2).

It reads the caller's own rows of ``mip_app.growth_agent_runs`` newest first,
writes no audit row, and never returns the actor, the stored criteria or the
stored route (a stored route can hold an actor-bound handoff proof).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.growth_agent_ledger_sql import RUN_LIST_SQL
from backend.services.lakebase import LakebaseError, get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

ACTOR = "loan.officer@example.com"
OTHER_ACTOR = "someone.else@example.com"
RUNS_PATH = "/api/growth-agent/runs"
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _row(actor: str, minutes: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": uuid4(),
        "actor_email": actor,
        "workflow_id": "daily_refi_brief",
        "workflow_title": "Daily Refi Opportunity Brief",
        "status": "completed",
        "broad_total": 1200,
        "actionable_total": 140,
        "actionable_avg_score": 78.5,
        "source_assets": ["mip.gold.borrower_360"],
        "audit_event_id": uuid4(),
        "created_at": T0 + timedelta(minutes=minutes),
        "criteria": {"states": ["IL"]},
        "route": "/lead-queue?segment=itm&growth_handoff=secret-proof",
    }
    row.update(overrides)
    return row


class _LedgerLakebase:
    """Evaluates RUN_LIST_SQL's actor filter, order and limit over rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetchalls: list[tuple[str, dict[str, Any], int]] = []
        self.transactions = 0
        self.fail = False

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, dict(params or {}), limit))
        if self.fail:
            raise LakebaseError("lakebase down")
        rows = list(self.rows)
        if re.search(r"WHERE\s+actor_email\s*=\s*%\(actor_email\)s", sql):
            rows = [row for row in rows if row["actor_email"] == (params or {})["actor_email"]]
        if "ORDER BY created_at DESC" in sql:
            rows.sort(key=lambda row: (row["created_at"], str(row["run_id"])), reverse=True)
        selected = re.search(r"SELECT(.*?)FROM", sql, re.DOTALL)
        columns = [name.strip() for name in selected.group(1).split(",")] if selected else list(rows[0])
        return [{column: row.get(column) for column in columns} for row in rows[: int((params or {})["limit"])]]

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        self.transactions += 1
        yield None


@pytest.fixture
def ledger() -> Iterator[tuple[_LedgerLakebase, InMemoryAuditStore, TestClient]]:
    rows = [_row(ACTOR, minutes) for minutes in (0, 30, 10)] + [_row(OTHER_ACTOR, 60)]
    lakebase = _LedgerLakebase(rows)
    audit_store = InMemoryAuditStore()
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_audit_store] = lambda: audit_store
    try:
        yield lakebase, audit_store, TestClient(app)
    finally:
        app.dependency_overrides.pop(get_lakebase_client, None)
        app.dependency_overrides.pop(get_audit_store, None)


def _get(client: TestClient, query: str = "", actor: str = ACTOR) -> Any:
    return client.get(f"{RUNS_PATH}{query}", headers={"X-Forwarded-Email": actor})


def test_the_list_is_scoped_to_the_caller(ledger: tuple[_LedgerLakebase, InMemoryAuditStore, TestClient]) -> None:
    lakebase, _audit, client = ledger
    response = _get(client)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    sql, params, _limit = lakebase.fetchalls[-1]
    assert params["actor_email"] == ACTOR
    assert re.search(r"WHERE\s+actor_email\s*=\s*%\(actor_email\)s", sql)
    other = _get(client, actor=OTHER_ACTOR).json()
    assert len(other) == 1


def test_the_sql_filters_on_the_actor_and_selects_no_private_column() -> None:
    assert re.search(r"WHERE\s+actor_email\s*=\s*%\(actor_email\)s", RUN_LIST_SQL)
    select = RUN_LIST_SQL.split("FROM", 1)[0]
    for column in ("actor_email", "criteria", "route", "request_id", "tool_steps"):
        assert column not in select, column
    assert "mip_app.growth_agent_runs" in RUN_LIST_SQL
    assert "ORDER BY created_at DESC, run_id DESC" in RUN_LIST_SQL


def test_newest_first(ledger: tuple[_LedgerLakebase, InMemoryAuditStore, TestClient]) -> None:
    _lakebase, _audit, client = ledger
    created = [row["created_at"] for row in _get(client).json()]
    assert created == sorted(created, reverse=True)
    assert created[0].startswith("2026-09-20T12:30")


@pytest.mark.parametrize(("query", "expected"), [("", 20), ("?limit=1", 1), ("?limit=50", 50)])
def test_limit_is_bounded(
    ledger: tuple[_LedgerLakebase, InMemoryAuditStore, TestClient], query: str, expected: int
) -> None:
    lakebase, _audit, client = ledger
    assert _get(client, query).status_code == 200
    _sql, params, limit = lakebase.fetchalls[-1]
    assert params["limit"] == expected and limit == expected


@pytest.mark.parametrize("query", ["?limit=0", "?limit=51", "?limit=-1", "?limit=many"])
def test_out_of_range_limits_are_rejected(
    ledger: tuple[_LedgerLakebase, InMemoryAuditStore, TestClient], query: str
) -> None:
    lakebase, _audit, client = ledger
    assert _get(client, query).status_code == 422
    assert lakebase.fetchalls == []


def test_the_body_never_carries_actor_criteria_or_route(
    ledger: tuple[_LedgerLakebase, InMemoryAuditStore, TestClient],
) -> None:
    _lakebase, _audit, client = ledger
    response = _get(client)
    for summary in response.json():
        assert {"actor_email", "criteria", "route"}.isdisjoint(summary)
    assert "growth_handoff" not in response.text
    assert ACTOR not in response.text


def test_the_read_writes_no_audit_row(ledger: tuple[_LedgerLakebase, InMemoryAuditStore, TestClient]) -> None:
    lakebase, audit_store, client = ledger
    for _ in range(3):
        assert _get(client).status_code == 200
    assert list(audit_store.list()) == []
    assert lakebase.transactions == 0


def test_a_lakebase_failure_is_a_safe_503(ledger: tuple[_LedgerLakebase, InMemoryAuditStore, TestClient]) -> None:
    lakebase, _audit, client = ledger
    lakebase.fail = True
    response = _get(client)
    assert response.status_code == 503
    assert "lakebase down" not in response.text
