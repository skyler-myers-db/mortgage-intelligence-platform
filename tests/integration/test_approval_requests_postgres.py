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
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql
from psycopg.rows import dict_row

from backend.schemas.outreach_revoke import OutreachRevokeRequest
from backend.services.approval_request_create import create_approval_request
from backend.services.approval_requests import (
    ApprovalRequestConflict,
    ApprovalRequestLinkRefused,
    ApprovalRequestRefused,
    list_approval_requests,
    open_borrower_ids_for_queue,
    verify_decision_link,
    withdraw_approval_request,
)
from backend.services.outreach_revoke import RevokeRefused, revoke_approval
from backend.services.sales_state import SalesStateStore
from backend.services.workspace_queue_version import QUEUE_VERSION_SQL, queue_version_from_row
from jobs import lakebase_migrate, sync_lifecycle_state
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
        "VALUES ('APPROVE', %s, 'approval', 'x') RETURNING audit_id::text",
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


def test_the_app_role_postflight_passes_and_grants_no_delete(
    conn_kwargs: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The grant step's privilege, routine and trigger postflight with the new
    contract entries: the app role may read, insert and update both tables,
    never delete, and cannot call the two trigger functions."""

    _migrated(conn_kwargs)
    suffix = uuid4().hex[:12]
    app_role, verifier_role = f"mip_test_app_{suffix}", f"mip_test_verifier_{suffix}"
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
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
        with psycopg.connect(**conn_kwargs) as conn:
            for table in ("approval_request_batches", "approval_request_items"):
                granted = conn.execute(
                    "SELECT has_table_privilege(%(role)s, %(table)s, 'SELECT'), "
                    "has_table_privilege(%(role)s, %(table)s, 'INSERT'), "
                    "has_table_privilege(%(role)s, %(table)s, 'UPDATE'), "
                    "has_table_privilege(%(role)s, %(table)s, 'DELETE'), "
                    "has_table_privilege(%(role)s, %(table)s, 'TRUNCATE')",
                    {"role": app_role, "table": f"mip_app.{table}"},
                ).fetchone()
                assert granted == (True, True, True, False, False), table
            for function in ("enforce_approval_request_batch_finalize_only", "enforce_approval_request_item_transition"):
                callable_by_app = conn.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    (app_role, f"mip_app.{function}()"),
                ).fetchone()
                assert callable_by_app == (False,), function
    finally:
        with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
            for role in (app_role, verifier_role):
                conn.execute(psql.SQL("DROP OWNED BY {}").format(psql.Identifier(role)))
                conn.execute(psql.SQL("DROP ROLE {}").format(psql.Identifier(role)))


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


# -- the service over real SQL -------------------------------------------------------------
#
# The unit suites drive the services against an in-memory model of these
# statements; these prove the statements themselves.

APPROVER = "pat.approver@summit.example"
LEAD_A, LEAD_B, LEAD_C = "B-ARQTESTX00001", "B-ARQTESTX00002", "B-ARQTESTX00003"
NOTE = "Rate-sensitive refinance candidates in our footprint."


class _PgLakebase:
    """The LakebaseClient surface the services use, one real connection per call."""

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


class _Leads:
    def list(self, segment: str | None, portfolio_id: str | None, limit: int | None = None, **kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(
                borrower_id=bid, marketing_eligible=True, dnc=False, consent_status="opt_in",
                suppression_reason=None, approval_status="pending",
            )
            for bid in kwargs.get("borrower_ids") or []
        ]


def _service(conn_kwargs: dict[str, str]) -> _PgLakebase:
    _migrated(conn_kwargs)
    return _PgLakebase(conn_kwargs)


def _decide(
    conn_kwargs: dict[str, str], borrower_id: str, action: str, *, batch_id: str | None = None
) -> str:
    """A finalized decision row, its intent canonical like _canonical_intent writes it."""

    intent: dict[str, Any] = {"action": action, "actor": APPROVER, "borrower_id": borrower_id}
    if batch_id is not None:
        intent["approval_request_batch_id"] = batch_id
    text = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO mip_app.approvals (borrower_id, action, actor_email, channel, offer_code, "
            "decision_intent, decided_at) VALUES (%s, %s, %s, 'email', 'refi', %s, clock_timestamp()) "
            "RETURNING approval_id::text",
            (borrower_id, action, APPROVER, text),
        ).fetchone()
        assert row is not None
        approval_id = str(row[0])
        conn.execute(
            "UPDATE mip_app.approvals SET decision_response = '{}'::jsonb, audit_event_id = %s "
            "WHERE approval_id = %s",
            (_audit_id(conn), approval_id),
        )
    return approval_id


def test_create_list_link_and_withdraw_run_on_the_real_statements(conn_kwargs: dict[str, str]) -> None:
    lakebase = _service(conn_kwargs)
    created = create_approval_request(
        lakebase, _Leads(), actor=ALICE, borrower_ids=[LEAD_A, LEAD_B, LEAD_C], note=NOTE,  # type: ignore[arg-type]
        request_key=str(uuid4()),
    )
    batch_id = created.batch_id
    assert created.requested == [LEAD_A, LEAD_B, LEAD_C]
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        batch = conn.execute(
            "SELECT requested_by, response, audit_event_id::text AS audit_event_id "
            "FROM mip_app.approval_request_batches WHERE batch_id = %s",
            (batch_id,),
        ).fetchone()
        audit = conn.execute(
            "SELECT event_type, metadata FROM mip_app.action_audit WHERE audit_id = %s",
            (created.audit_event_id,),
        ).fetchone()
    assert batch is not None and audit is not None
    assert batch["requested_by"] == ALICE and batch["audit_event_id"] == created.audit_event_id
    assert batch["response"]["batch_id"] == batch_id
    assert audit["event_type"] == "APPROVAL_REQUESTED"
    assert audit["metadata"]["borrower_ids"] == [LEAD_A, LEAD_B, LEAD_C]

    # The requester may not decide their own request; another approver may.
    with pytest.raises(ApprovalRequestLinkRefused) as own:
        verify_decision_link(lakebase, batch_id=batch_id, borrower_id=LEAD_A, actor=ALICE.upper())  # type: ignore[arg-type]
    assert own.value.kind == "self"
    verify_decision_link(lakebase, batch_id=batch_id, borrower_id=LEAD_A, actor=APPROVER)  # type: ignore[arg-type]

    linked = _decide(conn_kwargs, LEAD_A, "approve", batch_id=batch_id)
    _decide(conn_kwargs, LEAD_B, "reject")  # without the link
    with pytest.raises(ApprovalRequestLinkRefused) as closed:
        verify_decision_link(lakebase, batch_id=batch_id, borrower_id=LEAD_A, actor=APPROVER)  # type: ignore[arg-type]
    assert closed.value.kind == "not_open"

    [view] = list_approval_requests(lakebase, actor=APPROVER, scope="open").batches  # type: ignore[arg-type]
    assert view.requested_by is None and view.requested_by_display == "Alice Analyst"
    assert {row.borrower_id: (row.state, row.approval_id) for row in view.rows} == {
        LEAD_A: ("approved", linked),
        LEAD_B: ("decided_outside", None),
        LEAD_C: ("open", None),
    }
    assert open_borrower_ids_for_queue(lakebase, batch_id=batch_id, actor=APPROVER, is_approver=True) == [LEAD_C]  # type: ignore[arg-type]

    withdrawn = withdraw_approval_request(lakebase, actor=ALICE, batch_id=batch_id)  # type: ignore[arg-type]
    assert (withdrawn.withdrawn_now, withdrawn.already_closed) == (1, 2)
    again = withdraw_approval_request(lakebase, actor=ALICE, batch_id=batch_id)  # type: ignore[arg-type]
    assert (again.withdrawn_now, again.audit_event_id) == (0, None)
    [mine] = list_approval_requests(lakebase, actor=ALICE, scope="mine").batches  # type: ignore[arg-type]
    assert mine.requested_by == ALICE
    assert {row.borrower_id: row.state for row in mine.rows}[LEAD_C] == "withdrawn"
    assert list_approval_requests(lakebase, actor=APPROVER, scope="open").batches == []  # type: ignore[arg-type]


def test_a_replay_returns_the_stored_body_and_a_changed_payload_conflicts(conn_kwargs: dict[str, str]) -> None:
    lakebase = _service(conn_kwargs)
    key = str(uuid4())
    first = create_approval_request(lakebase, _Leads(), actor=ALICE, borrower_ids=[LEAD_A], note=NOTE, request_key=key)  # type: ignore[arg-type]
    again = create_approval_request(lakebase, _Leads(), actor=ALICE, borrower_ids=[LEAD_A], note=NOTE, request_key=key)  # type: ignore[arg-type]
    assert again == first
    with pytest.raises(ApprovalRequestConflict):
        create_approval_request(lakebase, _Leads(), actor=ALICE, borrower_ids=[LEAD_B], note=NOTE, request_key=key)  # type: ignore[arg-type]


def test_two_concurrent_requests_for_one_borrower_hold_it_once(conn_kwargs: dict[str, str]) -> None:
    lakebase = _service(conn_kwargs)
    first_batch = str(uuid4())
    # The first request's transaction holds the borrower but has not committed.
    first = psycopg.connect(**conn_kwargs)
    try:
        first.execute(
            "INSERT INTO mip_app.approval_request_batches "
            "(batch_id, requested_by, request_key, request_intent_hash, note) VALUES (%s, %s, %s, %s, %s)",
            (first_batch, "bob.analyst@summit.example", str(uuid4()), HASH, NOTE),
        )
        first.execute(
            "INSERT INTO mip_app.approval_request_items (batch_id, borrower_id) VALUES (%s, %s)",
            (first_batch, LEAD_A),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            second = pool.submit(
                create_approval_request,
                lakebase,  # type: ignore[arg-type]
                _Leads(),  # type: ignore[arg-type]
                actor=ALICE,
                borrower_ids=[LEAD_A, LEAD_B],
                note=NOTE,
                request_key=str(uuid4()),
            )
            # The second insert waits on the one-open-item index.
            time.sleep(0.5)
            assert not second.done()
            first.execute(
                "UPDATE mip_app.approval_request_batches SET response = '{}'::jsonb, audit_event_id = %s "
                "WHERE batch_id = %s",
                (_audit_id(first), first_batch),
            )
            first.commit()
            result = second.result(timeout=30)
    finally:
        first.close()
    assert result.requested == [LEAD_B]
    assert [(skip.borrower_id, skip.reason) for skip in result.skipped] == [(LEAD_A, "already_requested")]
    with psycopg.connect(**conn_kwargs) as conn:
        open_rows = conn.execute(
            "SELECT batch_id::text FROM mip_app.approval_request_items WHERE borrower_id = %s AND status = 'open'",
            (LEAD_A,),
        ).fetchall()
    assert open_rows == [(first_batch,)]


def test_a_zero_eligible_request_writes_only_its_refusal_audit_row(conn_kwargs: dict[str, str]) -> None:
    lakebase = _service(conn_kwargs)
    _decide(conn_kwargs, LEAD_A, "approve")
    with pytest.raises(ApprovalRequestRefused) as refused:
        create_approval_request(lakebase, _Leads(), actor=ALICE, borrower_ids=[LEAD_A], note=NOTE, request_key=str(uuid4()))  # type: ignore[arg-type]
    assert refused.value.skipped_counts["already_decided"] == 1
    with psycopg.connect(**conn_kwargs) as conn:
        batches = conn.execute("SELECT count(*) FROM mip_app.approval_request_batches").fetchone()
        audits = conn.execute(
            "SELECT event_type FROM mip_app.action_audit WHERE event_type LIKE 'APPROVAL_REQUEST%%'"
        ).fetchall()
    assert batches == (0,)
    assert audits == [("APPROVAL_REQUEST_REFUSED",)]


def _revoke(lakebase: _PgLakebase, approval_id: str) -> Any:
    payload = OutreachRevokeRequest(
        borrower_id=LEAD_A, approval_id=approval_id, rationale="Offer code was mis-keyed; needs a second review.",
        request_id=str(uuid4()),
    )
    return revoke_approval(lakebase, actor=APPROVER, payload=payload, rationale=payload.rationale, subject_clip=None)  # type: ignore[arg-type]


def test_a_revoke_appends_and_finalizes_and_every_reader_sees_pending(conn_kwargs: dict[str, str]) -> None:
    lakebase = _service(conn_kwargs)
    approval_id = _decide(conn_kwargs, LEAD_A, "approve")
    with pytest.raises(ApprovalRequestRefused):  # approved: not requestable
        create_approval_request(lakebase, _Leads(), actor=ALICE, borrower_ids=[LEAD_A], note=NOTE, request_key=str(uuid4()))  # type: ignore[arg-type]
    response, created = _revoke(lakebase, approval_id)
    assert created and response.revoked_approval_id == approval_id
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT approval_id::text AS approval_id, action, audit_event_id::text AS audit_event_id "
            "FROM mip_app.approvals WHERE borrower_id = %s ORDER BY decided_at",
            (LEAD_A,),
        ).fetchall()
        synced = [row for row in conn.execute(sync_lifecycle_state._LAKEBASE_QUERY).fetchall() if row["borrower_id"] == LEAD_A]
    assert [(row["approval_id"], row["action"]) for row in rows] == [
        (approval_id, "approve"), (response.approval_id, "revoke"),
    ]
    assert rows[1]["audit_event_id"] == response.audit_event_id
    assert (synced[0]["approval_status"], synced[0]["outreach_status"]) == ("pending", "none")
    lifecycle = SalesStateStore(lakebase).lifecycle_for(LEAD_A)  # type: ignore[arg-type]
    assert (lifecycle["approval_status"], lifecycle["outreach_status"]) == ("pending", "none")
    # Pending again: the borrower can be requested again.
    again = create_approval_request(lakebase, _Leads(), actor=ALICE, borrower_ids=[LEAD_A], note=NOTE, request_key=str(uuid4()))  # type: ignore[arg-type]
    assert again.requested == [LEAD_A]


@pytest.mark.parametrize("ledger", ["delivered", "outcome"])
def test_delivered_activation_or_a_recorded_outcome_refuses_the_revoke(
    conn_kwargs: dict[str, str], ledger: str
) -> None:
    lakebase = _service(conn_kwargs)
    approval_id = _decide(conn_kwargs, LEAD_A, "approve")
    _record_outreach(conn_kwargs, ledger, approval_id)
    with pytest.raises(RevokeRefused) as refused:
        _revoke(lakebase, approval_id)
    assert refused.value.kind == {"delivered": "delivered", "outcome": "outcome_recorded"}[ledger]


def _record_outreach(conn_kwargs: dict[str, str], ledger: str, approval_id: str) -> None:
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        if ledger == "delivered":
            conn.execute(
                "INSERT INTO mip_app.activation_outbox (destination_key, entity_type, entity_id, borrower_id, "
                "approval_id, channel, status, request_id, created_by) "
                "VALUES ('salesforce_crm', 'borrower', %s, %s, %s, 'email', 'delivered', %s, %s)",
                (LEAD_A, LEAD_A, approval_id, str(uuid4()), APPROVER),
            )
        else:
            conn.execute(
                "INSERT INTO mip_app.lead_outcomes (borrower_id, outcome_type, source_system, request_id, created_by) "
                "VALUES (%s, 'application_submitted', 'manual_import', %s, %s)",
                (LEAD_A, str(uuid4()), APPROVER),
            )


@pytest.mark.parametrize("ledger", ["delivered", "outcome"])
def test_the_queue_version_moves_on_delivery_status_and_crm_outcomes(
    conn_kwargs: dict[str, str], ledger: str
) -> None:
    """states-09 on the real statement: the two ledgers the lifecycle sync
    carries into the queue now move the version by themselves."""

    lakebase = _service(conn_kwargs)
    approval_id = _decide(conn_kwargs, LEAD_A, "approve")
    before = queue_version_from_row(lakebase.fetchone(QUEUE_VERSION_SQL))
    assert queue_version_from_row(lakebase.fetchone(QUEUE_VERSION_SQL)) == before
    _record_outreach(conn_kwargs, ledger, approval_id)
    assert queue_version_from_row(lakebase.fetchone(QUEUE_VERSION_SQL)) != before
