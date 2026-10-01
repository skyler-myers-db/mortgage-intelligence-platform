"""Real-Postgres contract of the 2026_10_01 saved Lead Queue views migration.

tables-09 phase 2 / flow-08 slice 1. The executable-hook preflight
inventories the live catalog, so only a RE-RUN can fail on a new expression
(the PR #143 fossil class): this suite starts from the schema as it stood
before the block, applies the governed migration TWICE through
``lakebase_migrate._run_transaction``, then drives LakebaseSavedViewStore
against the real table: CHECK refusals, owner isolation, soft delete and
re-create, the per-actor limit, and the audit row written by the same
statement. Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable
database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services.saved_view_params import canonical_saved_view_params
from backend.services.saved_view_store import (
    SAVED_VIEW_LIMIT,
    LakebaseSavedViewStore,
    SavedViewConflict,
)
from jobs import lakebase_migrate

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_MARKER = "-- Saved Lead Queue views ---"
ALICE = "alice.lo@summit.example"
BOB = "bob.lo@summit.example"
PARAMS, FINGERPRINT = canonical_saved_view_params("state=IL&segment=itm&sort=score&dir=desc")


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
    lakebase_migrate._run_transaction(
        (pre_seed, _SEED, post_seed),
        conn_kwargs,
        app_role="lakebase-schema-upgrade-test-role",
        verify_outreach_integrity=True,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )


class _Client:
    """The two LakebaseClient calls the store makes, over one real connection each."""

    def __init__(self, conn_kwargs: dict[str, str]) -> None:
        self._kwargs = conn_kwargs

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return conn.execute(sql, params or {}).fetchone()

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            rows = conn.execute(sql, params or {}).fetchall()
        return rows[:limit] if limit is not None else rows


def _migrated(conn_kwargs: dict[str, str]) -> LakebaseSavedViewStore:
    _apply(conn_kwargs, _SCHEMA[: _SCHEMA.index(_MARKER)])
    _apply(conn_kwargs, _SCHEMA)
    # The executable-hook preflight inventories the new CHECKs on this run.
    _apply(conn_kwargs, _SCHEMA)
    return LakebaseSavedViewStore(_Client(conn_kwargs))  # type: ignore[arg-type]


def _audit_rows(conn_kwargs: dict[str, str], view_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT event_type, actor_email, entity_type, metadata FROM mip_app.action_audit "
            "WHERE entity_id = %s ORDER BY audit_sequence",
            (view_id,),
        ).fetchall()


def test_the_migration_applies_twice_and_is_recorded_once(conn_kwargs: dict[str, str]) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        versions = conn.execute(
            "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = '2026_10_01_saved_views'"
        ).fetchone()
        indexes = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'saved_views' ORDER BY indexname"
        ).fetchall()
    assert versions == {"n": 1}
    assert [row["indexname"] for row in indexes] == [
        "idx_saved_views_actor_surface_updated",
        "saved_views_pkey",
        "uq_saved_views_actor_surface_name_live",
    ]


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("surface", "genie"),
        ("name", " padded"),
        ("name", "x" * 61),
        ("name_key", ""),
        ("params", ""),
        ("params", "a" * 2049),
    ],
)
def test_the_checks_refuse_malformed_rows(conn_kwargs: dict[str, str], column: str, value: str) -> None:
    _migrated(conn_kwargs)
    row = {"actor_email": ALICE, "surface": "lead_queue", "name": "TX refi", "name_key": "tx refi", "params": PARAMS}
    row[column] = value
    with psycopg.connect(**conn_kwargs) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO mip_app.saved_views (actor_email, surface, name, name_key, params) "
            "VALUES (%(actor_email)s, %(surface)s, %(name)s, %(name_key)s, %(params)s)",
            row,
        )


def test_crud_is_owner_scoped_and_audited_in_the_same_statement(conn_kwargs: dict[str, str]) -> None:
    store = _migrated(conn_kwargs)
    saved = store.create(actor=ALICE, name="TX refi", params=PARAMS, filter_fingerprint=FINGERPRINT)
    assert saved.ok and saved.audit_event_id
    [view] = store.list(actor=ALICE)
    assert (view.view_id, view.name, view.params) == (saved.view_id, "TX refi", PARAMS)
    assert store.list(actor=BOB) == []
    # Bob can neither see nor delete Alice's view.
    assert store.delete(actor=BOB, view_id=saved.view_id) is None
    assert len(store.list(actor=ALICE)) == 1
    deleted = store.delete(actor=ALICE, view_id=saved.view_id)
    assert deleted is not None and deleted.audit_event_id
    assert store.list(actor=ALICE) == []
    audits = _audit_rows(conn_kwargs, saved.view_id)
    assert [(row["event_type"], row["actor_email"], row["entity_type"]) for row in audits] == [
        ("SAVE_QUEUE_VIEW", ALICE, "saved_view"),
        ("DELETE_QUEUE_VIEW", ALICE, "saved_view"),
    ]
    assert audits[0]["metadata"]["saved_view_id"] == saved.view_id
    assert audits[0]["metadata"]["filter_fingerprint"] == FINGERPRINT
    assert "TX refi" not in str(audits) and "segment" not in str(audits[0]["metadata"])
    # The deleted name is free again; the old row stays, soft-deleted.
    again = store.create(actor=ALICE, name="tx REFI", params=PARAMS, filter_fingerprint=FINGERPRINT)
    assert again.view_id != saved.view_id
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT count(*) AS n FROM mip_app.saved_views").fetchone()
    assert rows == {"n": 2}


def test_a_live_duplicate_and_the_limit_are_refused(conn_kwargs: dict[str, str]) -> None:
    store = _migrated(conn_kwargs)
    store.create(actor=ALICE, name="TX refi", params=PARAMS, filter_fingerprint=FINGERPRINT)
    with pytest.raises(SavedViewConflict) as duplicate:
        store.create(actor=ALICE, name="TX REFI", params=PARAMS, filter_fingerprint=FINGERPRINT)
    assert duplicate.value.kind == "duplicate"
    # Another actor may use the same name.
    store.create(actor=BOB, name="TX refi", params=PARAMS, filter_fingerprint=FINGERPRINT)
    for index in range(1, SAVED_VIEW_LIMIT):
        store.create(actor=ALICE, name=f"View {index}", params=PARAMS, filter_fingerprint=FINGERPRINT)
    with pytest.raises(SavedViewConflict) as limit:
        store.create(actor=ALICE, name="One more", params=PARAMS, filter_fingerprint=FINGERPRINT)
    assert limit.value.kind == "limit"
    assert len(store.list(actor=ALICE)) == SAVED_VIEW_LIMIT
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        audits = conn.execute(
            "SELECT count(*) AS n FROM mip_app.action_audit WHERE event_type = 'SAVE_QUEUE_VIEW'"
        ).fetchone()
    # One audit row per stored view: the refused saves wrote none.
    assert audits == {"n": SAVED_VIEW_LIMIT + 1}
