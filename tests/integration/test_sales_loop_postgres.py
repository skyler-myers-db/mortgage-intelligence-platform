"""Real-PostgreSQL contract of the W5d sales loop (states-07 item 1, wow-power-5 step 2).

No Lakebase block ships with this lane, so there is no twice-applied
migration test: the schema and the seed are applied once on a fresh
``mip_app`` (the harness of test_approval_requests_postgres.py), and the
EXACT store statements then run against PostgreSQL:

- a release finds the replaced officer through ``released_at = assigned_at``
  (assign releases and inserts in one transaction, so ``now()`` is equal) and
  hands the lead back;
- two concurrent releases of the same assignment serialise on FOR UPDATE:
  one releases, the other is ``assignment_not_active``;
- a distribution followed by its batch release writes one LEAD_UNASSIGN per
  borrower, each found by the dossier's history SQL for its borrower only;
- the Board's commit re-reads open counts through the transaction cursor.

Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services import borrower_decision_history as history
from backend.services.sales_distribution_plan import Candidate
from backend.services.sales_state import SalesStateStore
from backend.services.sales_state_release import ReleaseConflict
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
ADMIN = "skyler@entrada.ai"
LO1 = "lo01@summit.example"
LO2 = "lo02@summit.example"
BORROWERS = [f"B-0000000000SL{n}" for n in range(1, 6)]


@pytest.fixture
def conn_kwargs() -> Iterator[dict[str, str]]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    kwargs = {"conninfo": dsn}
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)
    with contract_as_of(_SCHEMA):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )
    try:
        yield kwargs
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


class _PgLakebase:
    """The LakebaseClient surface the store uses, one real connection per call."""

    _supports_atomic_transactions = True

    def __init__(self, conn_kwargs: dict[str, str]) -> None:
        self._kwargs = conn_kwargs

    def fetchone(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return conn.execute(statement, params).fetchone()

    def fetchall(
        self, statement: str, params: dict[str, Any] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return conn.execute(statement, params).fetchall()[:limit]

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            yield conn


def _store(conn_kwargs: dict[str, str]) -> SalesStateStore:
    return SalesStateStore(_PgLakebase(conn_kwargs))  # type: ignore[arg-type]


def _assign(store: SalesStateStore, borrower_id: str, lo: str) -> str:
    assignment, _ = store.assign_lead(
        borrower_id=borrower_id,
        assigned_to_email=lo,
        assigned_by=ADMIN,
        expires_in_hours=24,
        strategy="manual",
        request_id=str(uuid4()),
    )
    return assignment.assignment_id


def _no_gate(_borrower_id: str) -> None:
    return None


def _rows(conn_kwargs: dict[str, str], sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        return conn.execute(sql, params).fetchall()


def test_a_release_hands_the_lead_back_through_the_released_at_match(conn_kwargs: dict[str, str]) -> None:
    store = _store(conn_kwargs)
    borrower_id = BORROWERS[0]
    first = _assign(store, borrower_id, LO1)
    second = _assign(store, borrower_id, LO2)

    result = store.release_assignment(
        borrower_id=borrower_id, assignment_id=second, actor=ADMIN, request_id=str(uuid4()), restore_gate=_no_gate
    )

    assert result.released.assignment_id == second
    assert result.restored is not None
    assert result.restored.assigned_to_email == LO1
    rows = _rows(
        conn_kwargs,
        "SELECT assignment_id::text AS id, released_at, assigned_to_email FROM mip_app.lead_assignments "
        "WHERE borrower_id = %(bid)s ORDER BY assigned_at",
        {"bid": borrower_id},
    )
    assert [row["id"] for row in rows[:2]] == [first, second]
    assert len(rows) == 3  # nothing deleted; the restore is a new row
    assert [row["released_at"] is None for row in rows] == [False, False, True]
    [audit] = _rows(
        conn_kwargs,
        "SELECT metadata FROM mip_app.action_audit WHERE event_type = 'LEAD_UNASSIGN'",
        {},
    )
    assert audit["metadata"]["restored_assignment_id"] == result.restored.assignment_id


def test_two_concurrent_releases_serialise_on_the_row_lock(conn_kwargs: dict[str, str]) -> None:
    store = _store(conn_kwargs)
    borrower_id = BORROWERS[1]
    assignment_id = _assign(store, borrower_id, LO1)

    def release(_: int) -> str:
        try:
            _store(conn_kwargs).release_assignment(
                borrower_id=borrower_id,
                assignment_id=assignment_id,
                actor=ADMIN,
                request_id=str(uuid4()),
                restore_gate=_no_gate,
            )
            return "released"
        except ReleaseConflict as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(release, range(2)))

    assert outcomes == ["assignment_not_active", "released"]
    audits = _rows(conn_kwargs, "SELECT 1 FROM mip_app.action_audit WHERE event_type = 'LEAD_UNASSIGN'", {})
    assert len(audits) == 1


def test_a_distribution_release_is_found_in_each_borrower_s_history_only(conn_kwargs: dict[str, str]) -> None:
    store = _store(conn_kwargs)
    run = BORROWERS[2:5]
    run_id = str(uuid4())
    store.distribute(
        borrower_ids=run,
        lo_emails=[LO1, LO2],
        assigned_by=ADMIN,
        expires_in_hours=24,
        strategy="round_robin",
        request_id=run_id,
    )
    request_id = str(uuid4())

    result = store.release_distribution(
        distribution_request_id=run_id, actor=ADMIN, request_id=request_id, restore_gate=_no_gate
    )
    replay = store.release_distribution(
        distribution_request_id=run_id, actor=ADMIN, request_id=request_id, restore_gate=_no_gate
    )

    assert result.released_count == 3
    assert replay == result
    for borrower_id in run:
        found = _rows(
            conn_kwargs,
            history.DECISION_HISTORY_SQL,
            {"bid": borrower_id, "entity_ids": [borrower_id, *run]},
        )
        unassigns = [row for row in found if row["event_type"] == "LEAD_UNASSIGN"]
        assert [row["entity_id"] for row in unassigns] == [borrower_id]


def test_the_board_commit_re_plans_on_the_transaction_cursor(conn_kwargs: dict[str, str]) -> None:
    store = _store(conn_kwargs)
    candidates = [Candidate(BORROWERS[0], 90, "IL"), Candidate(BORROWERS[1], 80, "CA")]
    preview = store.preview_distribution(
        candidates=candidates, lo_emails=[LO1, LO2], actor=ADMIN, expires_in_hours=24, skipped_not_assignable=0
    )

    assignments, audit_id, committed = store.distribute_score_balanced(
        candidates=candidates,
        lo_emails=[LO1, LO2],
        assigned_by=ADMIN,
        expires_in_hours=24,
        request_id=str(uuid4()),
        expected_plan_fingerprint=preview.plan_fingerprint,
        skipped_not_assignable=0,
    )

    assert audit_id
    assert committed is not None and committed.plan_fingerprint == preview.plan_fingerprint
    assert {(a.borrower_id, a.assigned_to_email) for a in assignments} == {(BORROWERS[0], LO1), (BORROWERS[1], LO2)}
    assert store.open_assignment_counts([LO1, LO2]) == {LO1: 1, LO2: 1}
    assert store.active_borrower_ids(BORROWERS) == {BORROWERS[0], BORROWERS[1]}


def test_the_workload_counts_open_stale_assigned_and_worked_in_both_windows(conn_kwargs: dict[str, str]) -> None:
    # W5e w5-analytics item 10 (critic-10 6c): the EXACT workload statement on
    # PostgreSQL. Rows are placed relative to now() so each falls in a known
    # window: the period is the last 7 days, the previous one the 7 before.
    store = _store(conn_kwargs)
    [today] = _rows(conn_kwargs, "SELECT current_date AS d", {})
    to_date = today["d"]
    window = {
        "from_date": (to_date - timedelta(days=6)).isoformat(),
        "to_date": to_date.isoformat(),
        "previous_from": (to_date - timedelta(days=13)).isoformat(),
        "previous_to": (to_date - timedelta(days=7)).isoformat(),
        "stale_days": 7,
    }
    ids = [f"B-0000000000WL{n}" for n in range(1, 5)]
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        for borrower_id, days_ago, released in [
            (ids[0], 2, False),  # open, assigned this period, worked this period
            (ids[1], 10, False),  # open and STALE: older than 7 days, no disposition since
            (ids[2], 9, False),  # open, a disposition after it was assigned: not stale; worked previous
            (ids[3], 3, True),  # released: not open, still assigned this period
        ]:
            conn.execute(
                "INSERT INTO mip_app.lead_assignments (borrower_id, assigned_to_email, assigned_by, assigned_at, released_at) "
                "VALUES (%(bid)s, %(lo)s, %(by)s, now() - make_interval(days => %(days)s), "
                "CASE WHEN %(released)s THEN now() - interval '1 day' END)",
                {"bid": borrower_id, "lo": LO1, "by": ADMIN, "days": days_ago, "released": released},
            )
        for borrower_id, days_ago in [(ids[0], 1), (ids[0], 1), (ids[2], 8)]:
            conn.execute(
                "INSERT INTO mip_app.call_dispositions (borrower_id, lo_email, outcome, occurred_at) "
                "VALUES (%(bid)s, %(lo)s, 'connected', now() - make_interval(days => %(days)s))",
                {"bid": borrower_id, "lo": LO1, "days": days_ago},
            )

    [before] = _rows(conn_kwargs, "SELECT COUNT(*) AS n FROM mip_app.action_audit", {})
    manager = store.visible_lo_emails(actor="sam.manager@summit.example")
    rows = {row["email"]: row for row in store.workload(**window, visible_lo_emails=manager)}

    # Every active loan officer in the manager's scope is listed, worked or not.
    assert set(rows) == {f"lo0{n}@summit.example" for n in range(1, 7)}
    lo1 = rows[LO1]
    assert (lo1["open_count"], lo1["stale_count"]) == (3, 1)
    assert (lo1["assigned_count"], lo1["previous_assigned_count"]) == (2, 2)
    # Worked counts distinct borrowers: two calls to one borrower is one.
    assert (lo1["worked_count"], lo1["previous_worked_count"]) == (1, 1)
    assert lo1["display_label"] == "Summit LO 01" and lo1["capacity_per_day"] == 35
    lo2 = rows[LO2]
    assert [lo2[key] for key in ("open_count", "stale_count", "assigned_count", "worked_count")] == [0, 0, 0, 0]

    # Scope: only the visible officers; an admin (None) sees every loan officer.
    assert [row["email"] for row in store.workload(**window, visible_lo_emails={LO2})] == [LO2]
    assert len(store.workload(**window, visible_lo_emails=None)) == 6
    # stale_days is the bound: at 11 days the 10-day-old assignment is not stale yet.
    assert store.workload(**{**window, "stale_days": 11}, visible_lo_emails={LO1})[0]["stale_count"] == 0
    # The reads wrote nothing: no audit row.
    [after] = _rows(conn_kwargs, "SELECT COUNT(*) AS n FROM mip_app.action_audit", {})
    assert after["n"] == before["n"]
