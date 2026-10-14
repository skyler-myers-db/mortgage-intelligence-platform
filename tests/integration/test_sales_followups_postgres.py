"""Real-PostgreSQL contract of the Follow-ups tray's shared open-item CTE.

D-shell-deviations-a (audit 2026-09-21 shell-09, wow-power-3 phase 1). The
schema before the ``followups_index`` block is applied, then the full schema
TWICE through ``lakebase_migrate._run_transaction`` (the second run executes
the executable-hook preflight over the new partial index), each wrapped in
``contract_as_of``. Then the REAL ``SUMMARY_SQL`` / ``LIST_SQL`` /
``BORROWER_ITEM_SQL`` run over seeded rows: every kind, precedence, the
release / reassignment / later-disposition / outcome / supersede closures,
the requested follow-up's owner states, the 7-day new-assignment window, the
14-day summary horizon, the per-kind cap and the snooze join.

Approvals, dispositions and outcomes refuse removal, so each test seeds its
own actor and borrower ids instead of truncating. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services.sales_followups_sql import BORROWER_ITEM_SQL, LIST_SQL, SUMMARY_SQL
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

#: Dated at the W5d merge: the first of the W5d blocks, after 2026_10_07_rum_daily.
FOLLOWUPS_INDEX_VERSION = "2026_10_14_followups_index"
_MARKER = "-- Follow-ups due index ---"
_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_IDS = itertools.count(1)
NOW = datetime.now(UTC)


def _apply(dsn: str, schema_sql: str) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema_sql)
    with contract_as_of(schema_sql):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            {"conninfo": dsn},
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    value = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    with psycopg.connect(value, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    _apply(value, _SCHEMA[: _SCHEMA.index(_MARKER)])
    _apply(value, _SCHEMA)
    # The re-run: the executable-hook preflight now inventories the new index.
    _apply(value, _SCHEMA)
    try:
        yield value
    finally:
        with psycopg.connect(value, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _bid() -> str:
    return f"B-{next(_IDS):013d}"


class World:
    """One test's actors and seeded rows; every write commits on its own."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        n = next(_IDS)
        self.actor = f"lo{n}a@summit.example"
        self.other = f"lo{n}b@summit.example"
        self.manager = f"mgr{n}@summit.example"
        self.other_label = f"Summit LO {n}B"
        for email, label in ((self.actor, f"Summit LO {n}A"), (self.other, self.other_label),
                             (self.manager, f"Summit Manager {n}")):
            self._run(
                "INSERT INTO mip_app.sales_team (email, display_label, role) VALUES (%s, %s, 'loan_officer')",
                (email, label),
            )

    def _run(self, sql: str, params: tuple[Any, ...]) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(sql, params)

    def assign(self, borrower: str, email: str, *, days_ago: float = 1, status: str = "assigned",
               status_updated_at: datetime | None = None) -> None:
        assigned_at = NOW - timedelta(days=days_ago)
        self._run(
            "INSERT INTO mip_app.lead_assignments (borrower_id, assigned_to_email, assigned_by, assigned_at, "
            "status, status_updated_at) VALUES (%s, %s, 'mgr@summit.example', %s, %s, %s)",
            (borrower, email, assigned_at, status, status_updated_at or assigned_at),
        )

    def release(self, borrower: str) -> None:
        self._run(
            "UPDATE mip_app.lead_assignments SET released_at = now() WHERE borrower_id = %s AND released_at IS NULL",
            (borrower,),
        )

    def dispose(self, borrower: str, lo: str, outcome: str, *, at: datetime,
                callback_at: datetime | None = None, attempt: int = 1) -> None:
        self._run(
            "INSERT INTO mip_app.call_dispositions (borrower_id, lo_email, outcome, attempt_number, occurred_at, "
            "callback_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (borrower, lo, outcome, attempt, at, callback_at),
        )

    def decide(self, borrower: str, actor: str, action: str, *, at: datetime,
               follow_up_at: datetime | None = None, assigned_to_email: str | None = None) -> None:
        self._run(
            "INSERT INTO mip_app.approvals (borrower_id, action, actor_email, decided_at, follow_up_at, "
            "assigned_to_email) VALUES (%s, %s, %s, %s, %s, %s)",
            (borrower, action, actor, at, follow_up_at, assigned_to_email),
        )

    def outcome(self, borrower: str, *, at: datetime) -> None:
        self._run(
            "INSERT INTO mip_app.lead_outcomes (borrower_id, outcome_type, source_system, occurred_at, "
            "request_id, created_by) VALUES (%s, 'application_submitted', 'manual_import', %s, %s, %s)",
            (borrower, at, f"req-{next(_IDS)}", self.manager),
        )

    def snooze(self, borrower: str, *, snoozed_at: datetime, wake_at: datetime,
               woken_at: datetime | None = None, wake_reason: str | None = None,
               actor: str | None = None) -> None:
        self._run(
            "INSERT INTO mip_app.lead_snoozes (actor_email, borrower_id, snoozed_at, wake_at, woken_at, "
            "wake_reason) VALUES (%s, %s, %s, %s, %s, %s)",
            (actor or self.actor, borrower, snoozed_at, wake_at, woken_at, wake_reason),
        )

    def items(self, actor: str | None = None, window_days: int = 7) -> dict[str, dict[str, Any]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            rows = conn.execute(LIST_SQL, {"actor": actor or self.actor, "window_days": window_days}).fetchall()
        return {row["borrower_id"]: row for row in rows}

    def kinds(self, actor: str | None = None) -> dict[str, str]:
        return {borrower: row["kind"] for borrower, row in self.items(actor).items()}

    def next_due(self, actor: str | None = None) -> datetime | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute(SUMMARY_SQL, {"actor": actor or self.actor}).fetchone()
        return None if row is None else row["next_due_at"]

    def item(self, borrower: str, actor: str | None = None) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            return conn.execute(BORROWER_ITEM_SQL, {"actor": actor or self.actor, "borrower_id": borrower}).fetchone()


@pytest.fixture
def world(dsn: str) -> World:
    return World(dsn)


def test_the_index_migration_is_recorded_once_with_its_partial_predicate(dsn: str) -> None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        versions = conn.execute(
            "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = %s", (FOLLOWUPS_INDEX_VERSION,)
        ).fetchone()
        index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'mip_app' AND indexname = 'idx_approvals_follow_up_open'"
        ).fetchone()
    assert versions == {"n": 1}
    assert index is not None
    assert index["indexdef"].endswith(
        "USING btree (follow_up_at) WHERE ((action = 'approve'::text) AND (follow_up_at IS NOT NULL))"
    )


def test_one_item_per_kind(world: World) -> None:
    callback, follow_up, requested, assigned = _bid(), _bid(), _bid(), _bid()
    world.assign(callback, world.actor, days_ago=10)
    world.dispose(callback, world.actor, "callback_scheduled", at=NOW - timedelta(hours=5),
                  callback_at=NOW + timedelta(hours=1), attempt=2)
    world.assign(follow_up, world.actor, days_ago=10)
    world.decide(follow_up, world.manager, "approve", at=NOW - timedelta(days=3), follow_up_at=NOW + timedelta(days=2))
    world.assign(requested, world.other, days_ago=10)
    world.decide(requested, f"  {world.actor.upper()} ", "approve", at=NOW - timedelta(days=4),
                 follow_up_at=NOW - timedelta(hours=1))
    world.assign(assigned, world.actor, days_ago=1)

    items = world.items()

    assert {b: row["kind"] for b, row in items.items()} == {
        callback: "callback", follow_up: "approval_follow_up", requested: "requested_follow_up",
        assigned: "new_assignment",
    }
    assert items[callback]["attempt_number"] == 2
    assert items[callback]["due_at"] == NOW + timedelta(hours=1)
    assert items[follow_up]["since_at"] == NOW - timedelta(days=3)
    assert (items[requested]["owner_state"], items[requested]["owner_label"]) == ("assigned", world.other_label)
    assert items[assigned]["due_at"] is None
    assert world.next_due() == NOW - timedelta(hours=1)


def test_a_routed_unassigned_follow_up_reaches_only_the_requester(world: World) -> None:
    borrower = _bid()
    world.decide(borrower, world.actor, "approve", at=NOW - timedelta(days=1),
                 follow_up_at=NOW + timedelta(days=1), assigned_to_email=world.other)

    assert world.items()[borrower]["owner_state"] == "unassigned"
    assert world.items()[borrower]["owner_label"] is None
    assert borrower not in world.items(world.other), "approvals.assigned_to_email never surfaces it"


def test_a_lapse_window_and_the_owner_never_duplicate_a_requested_item(world: World) -> None:
    owned, not_lapsed = _bid(), _bid()
    world.assign(owned, world.actor, days_ago=10)
    world.decide(owned, world.actor, "approve", at=NOW - timedelta(days=2), follow_up_at=NOW - timedelta(hours=2))
    world.assign(not_lapsed, world.other, days_ago=10)
    world.decide(not_lapsed, world.actor, "approve", at=NOW - timedelta(days=2), follow_up_at=NOW + timedelta(days=1))

    kinds = world.kinds()

    assert kinds[owned] == "approval_follow_up", "the requester who owns the lead sees it once, as the owner"
    assert not_lapsed not in kinds, "an assigned follow-up reaches its requester only once it lapsed"
    assert world.kinds(world.other)[not_lapsed] == "approval_follow_up"


def test_release_closes_a_callback_and_reassignment_moves_it(world: World) -> None:
    borrower = _bid()
    world.assign(borrower, world.actor, days_ago=10)
    world.dispose(borrower, world.actor, "callback_scheduled", at=NOW - timedelta(hours=3),
                  callback_at=NOW + timedelta(hours=4))
    assert world.kinds()[borrower] == "callback"

    world.release(borrower)
    assert borrower not in world.kinds()

    world.assign(borrower, world.other, days_ago=10)
    assert borrower not in world.kinds(), "never the disposition's logger"
    assert world.kinds(world.other)[borrower] == "callback", "the callback follows the active assignee"


def test_a_later_disposition_resolves_an_approval_follow_up(world: World) -> None:
    borrower = _bid()
    world.assign(borrower, world.actor, days_ago=10)
    world.decide(borrower, world.manager, "approve", at=NOW - timedelta(days=2), follow_up_at=NOW + timedelta(days=1))
    assert world.kinds()[borrower] == "approval_follow_up"

    world.dispose(borrower, world.actor, "called_no_answer", at=NOW - timedelta(days=1))

    assert borrower not in world.kinds()


@pytest.mark.parametrize("closer", ["lead_outcome", "outcome_recorded"])
def test_an_outcome_after_the_scheduling_time_closes_both_kinds(dsn: str, closer: str) -> None:
    world = World(dsn)
    callback, follow_up = _bid(), _bid()
    status = "outcome_recorded" if closer == "outcome_recorded" else "assigned"
    for borrower in (callback, follow_up):
        world.assign(borrower, world.actor, days_ago=10, status=status, status_updated_at=NOW - timedelta(minutes=5))
    world.dispose(callback, world.actor, "callback_scheduled", at=NOW - timedelta(hours=2),
                  callback_at=NOW + timedelta(hours=2))
    world.decide(follow_up, world.manager, "approve", at=NOW - timedelta(hours=2), follow_up_at=NOW + timedelta(days=1))
    if closer == "lead_outcome":
        assert set(world.kinds()) == {callback, follow_up}, "non-vacuity: both open before the outcome"
        world.outcome(callback, at=NOW - timedelta(minutes=5))
        world.outcome(follow_up, at=NOW - timedelta(minutes=5))

    assert world.kinds() == {}


def test_an_outcome_before_the_scheduling_time_does_not_close_it(world: World) -> None:
    borrower = _bid()
    world.assign(borrower, world.actor, days_ago=10)
    world.outcome(borrower, at=NOW - timedelta(days=5))
    world.dispose(borrower, world.actor, "callback_scheduled", at=NOW - timedelta(hours=2),
                  callback_at=NOW + timedelta(hours=2))

    assert world.kinds()[borrower] == "callback"


@pytest.mark.parametrize("action", ["hold", "reject", "revoke"])
def test_a_later_decision_supersedes_a_follow_up(dsn: str, action: str) -> None:
    world = World(dsn)
    borrower = _bid()
    world.assign(borrower, world.actor, days_ago=10)
    world.decide(borrower, world.manager, "approve", at=NOW - timedelta(days=2), follow_up_at=NOW + timedelta(days=1))
    world.decide(borrower, world.manager, action, at=NOW - timedelta(days=1))

    assert borrower not in world.kinds()


def test_the_new_assignment_window_is_seven_days_and_status_assigned_only(world: World) -> None:
    fresh, stale, drafted = _bid(), _bid(), _bid()
    world.assign(fresh, world.actor, days_ago=6)
    world.assign(stale, world.actor, days_ago=8)
    world.assign(drafted, world.actor, days_ago=1, status="contact_drafted")

    assert world.kinds() == {fresh: "new_assignment"}


def test_precedence_is_callback_then_follow_up_then_new_assignment(world: World) -> None:
    borrower = _bid()
    world.assign(borrower, world.actor, days_ago=1)
    world.decide(borrower, world.manager, "approve", at=NOW - timedelta(days=2), follow_up_at=NOW + timedelta(days=1))
    world.dispose(borrower, world.actor, "callback_scheduled", at=NOW - timedelta(hours=1),
                  callback_at=NOW + timedelta(hours=1))

    assert list(world.items().values())[0]["kind"] == "callback"
    assert len(world.items()) == 1, "one row per actor and borrower"


def test_a_thirty_day_old_callback_is_listed_but_never_lights_the_dot(world: World) -> None:
    old, recent = _bid(), _bid()
    world.assign(old, world.actor, days_ago=40)
    world.dispose(old, world.actor, "callback_scheduled", at=NOW - timedelta(days=31),
                  callback_at=NOW - timedelta(days=30))
    assert world.kinds() == {old: "callback"}
    assert world.next_due() is None

    world.assign(recent, world.actor, days_ago=40)
    world.dispose(recent, world.actor, "callback_scheduled", at=NOW - timedelta(days=14),
                  callback_at=NOW - timedelta(days=13))
    assert world.next_due() == NOW - timedelta(days=13)


def test_the_window_bounds_future_items_but_never_new_assignments(world: World) -> None:
    near, far, assigned = _bid(), _bid(), _bid()
    for borrower in (near, far):
        world.assign(borrower, world.actor, days_ago=10)
    world.dispose(near, world.actor, "callback_scheduled", at=NOW - timedelta(hours=1),
                  callback_at=NOW + timedelta(days=2))
    world.dispose(far, world.actor, "callback_scheduled", at=NOW - timedelta(hours=1),
                  callback_at=NOW + timedelta(days=9))
    world.assign(assigned, world.actor, days_ago=2)

    assert set(world.items(window_days=7)) == {near, assigned}
    assert set(world.items(window_days=14)) == {near, far, assigned}


def test_the_list_returns_at_most_51_rows_per_kind(world: World) -> None:
    for _ in range(53):
        world.assign(_bid(), world.actor, days_ago=1)

    rows = list(world.items().values())

    assert len(rows) == 51
    assert max(row["kind_rank"] for row in rows) == 51


def test_a_snooze_hides_an_item_until_a_newer_one_or_its_wake(world: World) -> None:
    borrower, signal = _bid(), _bid()
    world.assign(borrower, world.actor, days_ago=10)
    world.dispose(borrower, world.actor, "callback_scheduled", at=NOW - timedelta(hours=5),
                  callback_at=NOW + timedelta(hours=1))
    world.snooze(borrower, snoozed_at=NOW - timedelta(hours=1), wake_at=NOW + timedelta(days=3))

    assert world.items()[borrower]["snoozed"] is True
    assert world.next_due() is None, "a snoozed item never lights the dot"
    assert world.item(borrower) is not None, "the ownership check still sees it"

    world.dispose(borrower, world.actor, "callback_scheduled", at=NOW - timedelta(minutes=10),
                  callback_at=NOW + timedelta(hours=2))
    assert world.items()[borrower]["snoozed"] is False, "a newer callback always shows"

    world.assign(signal, world.actor, days_ago=10)
    world.dispose(signal, world.actor, "callback_scheduled", at=NOW - timedelta(days=2),
                  callback_at=NOW + timedelta(hours=3))
    world.snooze(signal, snoozed_at=NOW - timedelta(days=1), wake_at=NOW + timedelta(days=2),
                 woken_at=NOW - timedelta(hours=2), wake_reason="signal")
    row = world.items()[signal]
    assert (row["snoozed"], row["woke_on_signal"]) == (False, True)


def test_another_actors_snooze_or_an_expired_one_hides_nothing(world: World) -> None:
    borrower = _bid()
    world.assign(borrower, world.actor, days_ago=10)
    world.dispose(borrower, world.actor, "callback_scheduled", at=NOW - timedelta(hours=5),
                  callback_at=NOW + timedelta(hours=1))
    world.snooze(borrower, snoozed_at=NOW - timedelta(hours=1), wake_at=NOW + timedelta(days=3), actor=world.other)
    world.snooze(borrower, snoozed_at=NOW - timedelta(hours=4), wake_at=NOW - timedelta(minutes=1))

    assert world.items()[borrower]["snoozed"] is False
    assert world.next_due() == NOW + timedelta(hours=1)


def test_the_ownership_check_is_scoped_to_the_actor(world: World) -> None:
    borrower = _bid()
    world.assign(borrower, world.actor, days_ago=2)

    assert world.item(borrower) == {"kind": "new_assignment", "since_at": NOW - timedelta(days=2)}
    assert world.item(borrower, world.other) is None
    assert world.item(_bid()) is None
