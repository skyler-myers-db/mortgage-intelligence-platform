"""Real-PostgreSQL proof of 'Exclude my ledger reads' (W5d, tables-10).

The schema is applied through ``lakebase_migrate._run_transaction`` (there is
no new Lakebase block, so there is no twice-applied migration here). Ledger
rows are inserted by raw SQL; the EXACT page statement
(``LakebaseAuditStore.list``) and count statement (``read_audit_count``) then
run against PostgreSQL: with the reader's identity bound, only that reader's
VIEW_AUDIT_LEDGER rows drop, and the count equals the page total. The shared
clause is also evaluated over NULL event types and actors, which the table
itself refuses (NOT NULL), to pin its NULL-safety. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services.audit_filter_sql import audit_filter_clauses, read_audit_count
from backend.services.audit_lakebase_store import LakebaseAuditStore
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")

ADMIN = "ops@summit.example"
AUDITOR = "auditor.one@summit.example"
APPROVER = "approver@summit.example"
# The ledger is append-only (no DELETE), so the proof scopes every read to
# its own entity id, apart from anything the seed wrote.
ENTITY = "exclusion-proof"


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


class _PostgresClient:
    """The two LakebaseClient reads the explorer uses, over psycopg."""

    def __init__(self, conn_kwargs: dict[str, str]) -> None:
        self._kwargs = conn_kwargs

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return list(conn.execute(sql, params or {}).fetchall())[:limit]

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return conn.execute(sql, params or {}).fetchone()


def _migrated(conn_kwargs: dict[str, str]) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)
    with contract_as_of(_SCHEMA):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            conn_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


def _seed(conn_kwargs: dict[str, str]) -> None:
    rows = [
        ("VIEW_AUDIT_LEDGER", ADMIN),
        ("VIEW_AUDIT_LEDGER", ADMIN),
        ("VIEW_AUDIT_LEDGER", AUDITOR),
        ("VIEW_LEADS", ADMIN),
        ("APPROVE", APPROVER),
        ("VIEW_AUDIT_LEDGER", ""),
    ]
    with psycopg.connect(**conn_kwargs) as conn:
        for event_type, actor in rows:
            conn.execute(
                "INSERT INTO mip_app.action_audit (event_type, actor_email, entity_type, entity_id, metadata) "
                "VALUES (%s, %s, 'audit_ledger', %s, '{}'::jsonb)",
                (event_type, actor, ENTITY),
            )


def test_the_page_and_the_count_drop_only_the_readers_ledger_reads(conn_kwargs: dict[str, str]) -> None:
    _migrated(conn_kwargs)
    _seed(conn_kwargs)
    client = _PostgresClient(conn_kwargs)
    store = LakebaseAuditStore(client=client)  # type: ignore[arg-type]

    page = store.list(limit=100, entity_id=ENTITY, exclude_ledger_reads_by=ADMIN)
    counted = read_audit_count(client, entity_id=ENTITY, exclude_ledger_reads_by=ADMIN)  # type: ignore[arg-type]
    everything = store.list(limit=100, entity_id=ENTITY)
    counted_all = read_audit_count(client, entity_id=ENTITY)  # type: ignore[arg-type]

    assert sorted((row.event_type, row.actor) for row in page) == sorted(
        [
            ("VIEW_AUDIT_LEDGER", AUDITOR),
            ("VIEW_LEADS", ADMIN),
            ("APPROVE", APPROVER),
            ("VIEW_AUDIT_LEDGER", ""),
        ]
    )
    assert counted.count == len(page) == 4
    assert counted_all.count == len(everything) == 6


def test_the_clause_is_null_safe_in_postgres(conn_kwargs: dict[str, str]) -> None:
    clauses, params = audit_filter_clauses(exclude_ledger_reads_by=ADMIN)
    [clause] = clauses
    sql = (
        "SELECT event_type, actor_email FROM (VALUES "
        "(NULL::text, %(admin)s::text), "
        "('VIEW_AUDIT_LEDGER', NULL::text), "
        "(NULL::text, NULL::text), "
        "('VIEW_AUDIT_LEDGER', %(admin)s::text), "
        "('VIEW_LEADS', %(admin)s::text)"
        f") AS t(event_type, actor_email) WHERE {clause}"
    )
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        kept = conn.execute(sql, {**params, "admin": ADMIN}).fetchall()

    assert [(row["event_type"], row["actor_email"]) for row in kept] == [
        (None, ADMIN),
        ("VIEW_AUDIT_LEDGER", None),
        (None, None),
        ("VIEW_LEADS", ADMIN),
    ]
