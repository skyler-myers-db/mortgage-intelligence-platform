"""Real-Postgres contract of the 2026_10_01_approval_requests migration.

W5b approval ledger (flow-02 / shell-06, D-approval-flow-c; flow-v2;
states-09). The executable-hook preflight inventories the live catalog, so
only a RE-RUN can fail on a new expression (the PR #143 fossil class): every
test starts from the schema as it stood before the block, then applies the
governed migration TWICE through ``lakebase_migrate._run_transaction`` (the
second run passes ``_preflight_executable_schema_hooks`` over the new CHECKs
and index predicates, and the postflight over the new triggers and routines).
Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_MARKER = "-- Approval requests (maker-checker), revoke and decided_at ---"
ALICE = "alice.analyst@summit.example"
BORROWER = "B-0000000000AR1"
HASH = "a" * 64


@pytest.fixture
def conn_kwargs() -> Iterator[dict[str, str]]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    kwargs = {"conninfo": dsn}
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    try:
        yield kwargs
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _apply(conn_kwargs: dict[str, str], schema_sql: str) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema_sql)
    with contract_as_of(schema_sql):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            conn_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


def _migrated(conn_kwargs: dict[str, str]) -> None:
    _apply(conn_kwargs, _SCHEMA[: _SCHEMA.index(_MARKER)])
    _apply(conn_kwargs, _SCHEMA)
    # The executable-hook preflight inventories the new CHECKs on this run.
    _apply(conn_kwargs, _SCHEMA)


def _audit_id(conn: psycopg.Connection[Any]) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.action_audit (event_type, actor_email, entity_type, entity_id) "
        "VALUES ('APPROVAL_REQUESTED', %s, 'approval_request_batch', 'x') RETURNING audit_id::text",
        (ALICE,),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _insert_batch(conn: psycopg.Connection[Any], **overrides: object) -> str:
    row: dict[str, object] = {
        "batch_id": str(uuid4()),
        "requested_by": ALICE,
        "request_key": str(uuid4()),
        "request_intent_hash": HASH,
        "note": "Rate-sensitive refinance candidates in our footprint.",
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO mip_app.approval_request_batches "
        "(batch_id, requested_by, request_key, request_intent_hash, note) "
        "VALUES (%(batch_id)s, %(requested_by)s, %(request_key)s, %(request_intent_hash)s, %(note)s)",
        row,
    )
    return str(row["batch_id"])


def test_the_migration_applies_twice_and_is_recorded_once(conn_kwargs: dict[str, str]) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        versions = conn.execute(
            "SELECT count(*) AS n FROM mip_app.schema_migrations "
            "WHERE version = '2026_10_01_approval_requests'"
        ).fetchone()
        indexes = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename IN ('approval_request_batches', 'approval_request_items') "
            "OR indexname = 'idx_approvals_decided_at' ORDER BY indexname"
        ).fetchall()
        triggers = conn.execute(
            "SELECT tgname, tgtype FROM pg_trigger WHERE NOT tgisinternal "
            "AND tgrelid IN ('mip_app.approval_request_batches'::regclass, "
            "'mip_app.approval_request_items'::regclass) ORDER BY tgname"
        ).fetchall()
    assert versions == {"n": 1}
    by_name = {row["indexname"]: row["indexdef"] for row in indexes}
    assert set(by_name) == {
        "approval_request_batches_pkey",
        "approval_request_items_pkey",
        "idx_approval_request_batches_created",
        "idx_approval_request_batches_requester",
        "idx_approval_request_items_borrower",
        "idx_approvals_decided_at",
        "uq_approval_request_batches_key",
        "uq_approval_request_items_open_borrower",
    }
    assert "WHERE (status = 'open'::text)" in by_name["uq_approval_request_items_open_borrower"]
    assert "(decided_at DESC)" in by_name["idx_approvals_decided_at"]
    assert [(row["tgname"], row["tgtype"]) for row in triggers] == [
        ("trg_approval_request_batches_finalize_only", 19),
        ("trg_approval_request_batches_no_remove", 42),
        ("trg_approval_request_items_no_remove", 42),
        ("trg_approval_request_items_transition", 19),
    ]


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("request_key", ""),
        ("request_key", "k" * 65),
        ("request_intent_hash", "A" * 64),
        ("request_intent_hash", "a" * 63),
        ("note", "   "),
        ("note", "n" * 501),
    ],
)
def test_the_batch_checks_refuse_malformed_rows(
    conn_kwargs: dict[str, str], column: str, value: str
) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn, pytest.raises(psycopg.errors.CheckViolation):
        _insert_batch(conn, **{column: value})


def test_a_batch_is_finalized_once_and_never_removed(conn_kwargs: dict[str, str]) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        batch_id = _insert_batch(conn)
        audit_id = _audit_id(conn)
        # A half finalization: the BEFORE trigger refuses it ahead of the CHECK.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE mip_app.approval_request_batches SET response = '{}'::jsonb WHERE batch_id = %s",
                (batch_id,),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE mip_app.approval_request_batches SET note = 'changed' WHERE batch_id = %s",
                (batch_id,),
            )
        conn.execute(
            "UPDATE mip_app.approval_request_batches "
            "SET response = %s::jsonb, audit_event_id = %s WHERE batch_id = %s",
            (json.dumps({"batch_id": batch_id}), audit_id, batch_id),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE mip_app.approval_request_batches SET response = '{\"x\": 1}'::jsonb "
                "WHERE batch_id = %s",
                (batch_id,),
            )
        for statement in (
            "DELETE FROM mip_app.approval_request_batches",
            "TRUNCATE mip_app.approval_request_batches CASCADE",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_batch(conn, request_key=_key_of(conn, batch_id))


def _key_of(conn: psycopg.Connection[Any], batch_id: str) -> str:
    row = conn.execute(
        "SELECT request_key FROM mip_app.approval_request_batches WHERE batch_id = %s", (batch_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_an_item_only_moves_from_open_to_withdrawn_or_expired(conn_kwargs: dict[str, str]) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        batch_id = _insert_batch(conn)
        for borrower_id in ("not-a-borrower", "B-123"):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO mip_app.approval_request_items (batch_id, borrower_id) VALUES (%s, %s)",
                    (batch_id, borrower_id),
                )
        conn.execute(
            "INSERT INTO mip_app.approval_request_items (batch_id, borrower_id) VALUES (%s, %s)",
            (batch_id, BORROWER),
        )
        where = "WHERE batch_id = %s AND borrower_id = %s"
        refused = (
            f"UPDATE mip_app.approval_request_items SET status = 'closed', closed_at = now() {where}",
            f"UPDATE mip_app.approval_request_items SET status = 'withdrawn' {where}",
            f"UPDATE mip_app.approval_request_items SET borrower_id = 'B-0000000000AR2', "
            f"status = 'withdrawn', closed_at = now() {where}",
        )
        for statement in refused:
            with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege)):
                conn.execute(statement, (batch_id, BORROWER))
        conn.execute(
            f"UPDATE mip_app.approval_request_items SET status = 'withdrawn', closed_at = now() {where}",
            (batch_id, BORROWER),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                f"UPDATE mip_app.approval_request_items SET status = 'expired' {where}",
                (batch_id, BORROWER),
            )
        for statement in (
            "DELETE FROM mip_app.approval_request_items",
            "TRUNCATE mip_app.approval_request_items",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_one_borrower_holds_at_most_one_open_item(conn_kwargs: dict[str, str]) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        first, second = _insert_batch(conn), _insert_batch(conn)
        insert = (
            "INSERT INTO mip_app.approval_request_items (batch_id, borrower_id) VALUES (%s, %s) "
            "ON CONFLICT (borrower_id) WHERE status = 'open' DO NOTHING RETURNING borrower_id"
        )
        assert conn.execute(insert, (first, BORROWER)).fetchone() == (BORROWER,)
        assert conn.execute(insert, (second, BORROWER)).fetchone() is None
        conn.execute(
            "UPDATE mip_app.approval_request_items SET status = 'expired', closed_at = now() "
            "WHERE batch_id = %s",
            (first,),
        )
        # Once the open item closes, the borrower can be requested again.
        assert conn.execute(insert, (second, BORROWER)).fetchone() == (BORROWER,)


def test_the_approvals_action_check_admits_revoke_and_refuses_anything_else(
    conn_kwargs: dict[str, str],
) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        checks = conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE conrelid = 'mip_app.approvals'::regclass AND conname = 'approvals_action_check'"
        ).fetchall()
    assert len(checks) == 1
    assert "'revoke'::text" in checks[0]["definition"]
    insert = (
        "INSERT INTO mip_app.approvals (borrower_id, action, actor_email, channel, offer_code) "
        "VALUES (%s, %s, %s, 'email', 'refi') RETURNING approval_id::text"
    )
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, (BORROWER, "bogus", ALICE))
        row = conn.execute(insert, (BORROWER, "revoke", ALICE)).fetchone()
        assert row is not None
        approval_id = str(row[0])
        audit_id = _audit_id(conn)
        # The revoke row finalizes under the unchanged approvals triggers ...
        conn.execute(
            "UPDATE mip_app.approvals SET decision_response = '{}'::jsonb, audit_event_id = %s "
            "WHERE approval_id = %s",
            (audit_id, approval_id),
        )
        # ... and is then immutable like every other decision row.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE mip_app.approvals SET action = 'approve' WHERE approval_id = %s",
                (approval_id,),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM mip_app.approvals WHERE approval_id = %s", (approval_id,))
