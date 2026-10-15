"""Real-PostgreSQL contract of mip_app.lead_snoozes (wow-power-3 phase 2).

The schema before the ``lead_snoozes`` block is applied, then the full schema
TWICE through ``lakebase_migrate._run_transaction`` (the second run executes
the executable-hook preflight over the new CHECKs and partial indexes), each
wrapped in ``contract_as_of``. Then: the CHECKs refuse a bad id length, a
woken row without a reason, a reason without a wake and a wake_at not after
snoozed_at; the open-pair unique index refuses a second open row; the REAL
snooze service transaction supersedes, closes on the timer and replays a
request_id; the REAL wake UPDATEs of the lifecycle job wake only open rows
(an already-woken row never changes); and the grant postflight gives the App
role SELECT, INSERT and UPDATE only. Skipped unless ``MIP_TEST_POSTGRES_DSN``
names a disposable database.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql
from psycopg.rows import dict_row

from backend.services.lead_snooze import LeadSnoozeService
from backend.services.repositories.databricks_borrower_contactability import (
    BorrowerContactabilityRow,
)
from jobs import lakebase_migrate
from jobs import sync_lifecycle_state as job
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

#: Dated at the W5d merge: the second W5d block, after 2026_10_14_followups_index.
LEAD_SNOOZES_VERSION = "2026_10_14_lead_snoozes"
_MARKER = "-- Lead snoozes ---"
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
    # The re-run: the executable-hook preflight now inventories the new CHECKs.
    _apply(value, _SCHEMA)
    try:
        yield value
    finally:
        with psycopg.connect(value, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _bid() -> str:
    return f"B-{next(_IDS):013d}"


def _actor() -> str:
    return f"lo{next(_IDS)}@summit.example"


def _insert(dsn: str, **row: Any) -> None:
    columns = ", ".join(row)
    values = ", ".join(f"%({name})s" for name in row)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"INSERT INTO mip_app.lead_snoozes ({columns}) VALUES ({values})", row)


def _rows(dsn: str, actor: str) -> list[dict[str, Any]]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM mip_app.lead_snoozes WHERE actor_email = %s ORDER BY snoozed_at", (actor,)
        ).fetchall()


def test_the_migration_is_recorded_once_with_its_checks_and_indexes(dsn: str) -> None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        versions = conn.execute(
            "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = %s", (LEAD_SNOOZES_VERSION,)
        ).fetchone()
        checks = conn.execute(
            "SELECT count(*) AS n FROM pg_constraint WHERE conrelid = 'mip_app.lead_snoozes'::regclass "
            "AND contype = 'c'"
        ).fetchone()
        indexes = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'mip_app' AND tablename = 'lead_snoozes' "
            "ORDER BY indexname"
        ).fetchall()
    assert versions == {"n": 1}
    assert checks == {"n": 6}, "applied twice, still one CHECK each"
    assert [row["indexname"] for row in indexes] == [
        "idx_lead_snoozes_open_wake", "lead_snoozes_pkey", "uq_lead_snoozes_open_pair", "uq_lead_snoozes_request_id",
    ]


@pytest.mark.parametrize(
    "row",
    [
        {"borrower_id": "B-123"},
        {"borrower_id": "X-0000000000001"},
        {"wake_at": NOW - timedelta(days=1)},
        {"woken_at": NOW},
        {"wake_reason": "timer"},
        {"woken_at": NOW, "wake_reason": "manual"},
        {"request_id": "r" * 65},
        {"actor_email": "a" * 321},
    ],
)
def test_the_checks_refuse_a_malformed_row(dsn: str, row: dict[str, Any]) -> None:
    base = {"actor_email": _actor(), "borrower_id": _bid(), "snoozed_at": NOW, "wake_at": NOW + timedelta(days=1)}
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(dsn, **{**base, **row})


def test_one_open_row_per_pair_and_one_row_per_request_id(dsn: str) -> None:
    actor, borrower, request_id = _actor(), _bid(), str(uuid4())
    open_row = {"actor_email": actor, "borrower_id": borrower, "snoozed_at": NOW, "wake_at": NOW + timedelta(days=1)}
    _insert(dsn, **open_row, request_id=request_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(dsn, **open_row)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(dsn, **{**open_row, "borrower_id": _bid()}, request_id=request_id)
    # A closed row never blocks a new open one.
    _insert(dsn, **open_row, woken_at=NOW, wake_reason="superseded")
    assert len(_rows(dsn, actor)) == 2


class _Client:
    """The two methods the snooze service uses, on real connections with dict rows."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            return conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            yield conn


class _Gold:
    def contactability(self, borrower_ids: Sequence[str]) -> dict[str, BorrowerContactabilityRow]:
        return {
            borrower_id: BorrowerContactabilityRow.from_gold({
                "marketing_eligible": True, "consent_status": "opt_in", "suppression_reason": None, "dnc": False,
                "eligibility_source": "synthetic_seed", "last_touch_at": None, "eligible_recontact_at": None,
                "contactable_sql": True,
            })
            for borrower_id in borrower_ids
        }


def _assigned(dsn: str, actor: str, borrower: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO mip_app.sales_team (email, display_label, role) VALUES (%s, 'Summit LO', 'loan_officer') "
            "ON CONFLICT (email) DO NOTHING",
            (actor,),
        )
        conn.execute(
            "INSERT INTO mip_app.lead_assignments (borrower_id, assigned_to_email, assigned_by) "
            "VALUES (%s, %s, 'mgr@summit.example')",
            (borrower, actor),
        )


def test_the_real_snooze_transaction_supersedes_closes_and_replays(dsn: str) -> None:
    actor, borrower = _actor(), _bid()
    _assigned(dsn, actor, borrower)
    _insert(dsn, actor_email=actor, borrower_id=borrower, snoozed_at=NOW - timedelta(days=5),
            wake_at=NOW - timedelta(days=4))
    service = LeadSnoozeService(lambda: _Client(dsn))

    first = service.snooze(actor=actor, borrower_id=borrower, days=1, request_id=str(uuid4()), repo=_Gold())
    request_id = str(uuid4())
    second = service.snooze(actor=actor, borrower_id=borrower, days=7, request_id=request_id, repo=_Gold())
    replay = service.snooze(actor=actor, borrower_id=borrower, days=7, request_id=request_id, repo=_Gold())

    assert replay == second
    rows = _rows(dsn, actor)
    assert [row["wake_reason"] for row in rows] == ["timer", "superseded", None]
    assert rows[0]["woken_at"] == rows[0]["wake_at"], "a timer close stamps the wake time"
    assert rows[2]["wake_at"] == second.wake_at and rows[1]["wake_at"] == first.wake_at
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        audits = conn.execute(
            "SELECT event_type, entity_id, metadata FROM mip_app.action_audit WHERE audit_id = ANY(%s::uuid[])",
            ([str(row["audit_event_id"]) for row in rows[1:]],),
        ).fetchall()
    assert [audit["event_type"] for audit in audits] == ["LEAD_SNOOZE", "LEAD_SNOOZE"]
    assert {audit["metadata"]["snooze_days"] for audit in audits} == {1, 7}
    assert all(audit["entity_id"] == borrower for audit in audits)


def test_the_real_wake_updates_wake_only_open_rows(dsn: str) -> None:
    actor = _actor()
    signal_open, already_woken, expired, still_open = _bid(), _bid(), _bid(), _bid()
    for borrower, extra in (
        (signal_open, {}),
        (already_woken, {"woken_at": NOW - timedelta(hours=1), "wake_reason": "superseded"}),
        (expired, {"snoozed_at": NOW - timedelta(days=3), "wake_at": NOW - timedelta(days=1)}),
        (still_open, {}),
    ):
        _insert(dsn, **{"actor_email": actor, "borrower_id": borrower, "snoozed_at": NOW - timedelta(days=1),
                        "wake_at": NOW + timedelta(days=2), **extra})
    by_borrower = {row["borrower_id"]: row for row in _rows(dsn, actor)}
    before_woken = by_borrower[already_woken]
    ids = [str(by_borrower[signal_open]["snooze_id"]), str(before_woken["snooze_id"])]

    open_rows = job._fetch_open_snoozes({"conninfo": dsn})
    signal, timer = job._apply_snooze_wakes({"conninfo": dsn}, ids)

    assert {row["borrower_id"] for row in open_rows} >= {signal_open, still_open}
    assert expired not in {row["borrower_id"] for row in open_rows}, "an expired row is not open for a wake"
    assert signal == 1, "the already-woken row is not counted"
    assert timer >= 1
    after = {row["borrower_id"]: row for row in _rows(dsn, actor)}
    assert after[signal_open]["wake_reason"] == "signal" and after[signal_open]["woken_at"] is not None
    assert after[already_woken] == before_woken, "an already-woken row never changes"
    assert (after[expired]["wake_reason"], after[expired]["woken_at"]) == ("timer", after[expired]["wake_at"])
    assert after[still_open]["woken_at"] is None


def test_the_app_role_postflight_grants_select_insert_update_only(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn_kwargs = {"conninfo": dsn}
    suffix = uuid4().hex[:12]
    app_role, verifier_role = f"mip_test_app_{suffix}", f"mip_test_verifier_{suffix}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        for role in (app_role, verifier_role):
            conn.execute(psql.SQL("CREATE ROLE {} LOGIN NOREPLICATION INHERIT").format(psql.Identifier(role)))
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: app_role)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", verifier_role)
    try:
        lakebase_migrate._apply_app_role_grants(
            conn_kwargs,
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )
        with psycopg.connect(dsn) as conn:
            granted = conn.execute(
                "SELECT has_table_privilege(%(role)s, 'mip_app.lead_snoozes', 'SELECT'), "
                "has_table_privilege(%(role)s, 'mip_app.lead_snoozes', 'INSERT'), "
                "has_table_privilege(%(role)s, 'mip_app.lead_snoozes', 'UPDATE'), "
                "has_table_privilege(%(role)s, 'mip_app.lead_snoozes', 'DELETE'), "
                "has_table_privilege(%(role)s, 'mip_app.lead_snoozes', 'TRUNCATE')",
                {"role": app_role},
            ).fetchone()
        assert granted == (True, True, True, False, False)
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            for role in (app_role, verifier_role):
                conn.execute(psql.SQL("DROP OWNED BY {}").format(psql.Identifier(role)))
                conn.execute(psql.SQL("DROP ROLE {}").format(psql.Identifier(role)))
