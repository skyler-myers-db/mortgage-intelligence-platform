"""Real-PostgreSQL contract for the approve path's draft-age read (audit flow-03).

``_GENERATED_OUTREACH_DRAFT_LOOKUP`` computes ``draft_age_seconds`` in SQL
(``GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (now() - created_at))))::bigint``)
from the existing ``mip_app.generated_outreach_drafts.created_at`` column;
there is no migration. The unit suites run the lookup against a fake client
that never evaluates it, so this suite runs the ACTUAL statement against the
ACTUAL ``lakebase/schema.sql`` DDL: a draft generated 42 s ago reads as a
whole number of seconds in [42, 60), a clock-skewed future ``created_at``
reads as 0 (never negative), and the read is repeatable (it writes nothing).
Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable database (the
convention of ``test_genie_completion_jobs_postgres.py``).
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

from backend.services.outreach_drafts import _GENERATED_OUTREACH_DRAFT_LOOKUP

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_ACTOR = "approver.one@summit.example"
_BORROWER = "B-000000000A001"


def _action_audit_ddl() -> str:
    start = _SCHEMA.index("CREATE TABLE IF NOT EXISTS mip_app.action_audit (")
    end = _SCHEMA.index("-- Audit archival run ledger", start)
    return _SCHEMA[start:end]


def _generated_drafts_ddl() -> str:
    start = _SCHEMA.index("CREATE TABLE IF NOT EXISTS mip_app.generated_outreach_drafts (")
    end = _SCHEMA.index("\n);\n", start) + len("\n);\n")
    return _SCHEMA[start:end]


@pytest.fixture
def dsn() -> Iterator[str]:
    value = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
        cur.execute("CREATE SCHEMA mip_app")
        cur.execute(_action_audit_ddl())  # type: ignore[arg-type]
        cur.execute(_generated_drafts_ddl())  # type: ignore[arg-type]
    try:
        yield value
    finally:
        with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _insert_draft(dsn: str, *, created_offset: str) -> str:
    generation_id = str(uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mip_app.action_audit (event_type, actor_email) "
            "VALUES ('DRAFT_OUTREACH', %s) RETURNING audit_id",
            (_ACTOR,),
        )
        row = cur.fetchone()
        assert row is not None
        cur.execute(
            "INSERT INTO mip_app.generated_outreach_drafts ("
            " generation_id, audit_event_id, actor_email, borrower_id, channel, offer_code,"
            " generation_mode, response_hash, response_json, created_at"
            ") VALUES (%s, %s, %s, %s, 'email', 'refi', 'governed_fallback', %s, %s::jsonb,"
            " now() + %s::interval)",
            (
                generation_id,
                row[0],
                _ACTOR,
                _BORROWER,
                "c" * 64,
                json.dumps({"generation_id": generation_id}),
                created_offset,
            ),
        )
    return generation_id


def _lookup(dsn: str, generation_id: str) -> dict[str, Any]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            _GENERATED_OUTREACH_DRAFT_LOOKUP,  # type: ignore[arg-type]
            {
                "generation_id": generation_id,
                "actor_email": _ACTOR,
                "borrower_id": _BORROWER,
                "campaign_id": None,
                "variant_name": None,
            },
        )
        found = cur.fetchone()
    assert found is not None
    return found


def test_a_draft_generated_42_seconds_ago_reads_as_whole_seconds(dsn: str) -> None:
    generation_id = _insert_draft(dsn, created_offset="-42 seconds")

    first = _lookup(dsn, generation_id)
    second = _lookup(dsn, generation_id)

    for row in (first, second):
        age = row["draft_age_seconds"]
        assert isinstance(age, int) and not isinstance(age, bool)
        assert 42 <= age < 60
    assert second["draft_age_seconds"] >= first["draft_age_seconds"]
    assert first["generation_id"] == second["generation_id"]


def test_a_future_created_at_reads_as_zero_never_negative(dsn: str) -> None:
    generation_id = _insert_draft(dsn, created_offset="1 hour")

    row = _lookup(dsn, generation_id)

    assert row["draft_age_seconds"] == 0


def test_the_lookup_writes_nothing(dsn: str) -> None:
    generation_id = _insert_draft(dsn, created_offset="-5 seconds")

    _lookup(dsn, generation_id)
    _lookup(dsn, generation_id)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mip_app.action_audit")
        audit_rows = cur.fetchone()
        cur.execute("SELECT count(*) FROM mip_app.generated_outreach_drafts")
        draft_rows = cur.fetchone()
    assert audit_rows == (1,)
    assert draft_rows == (1,)
