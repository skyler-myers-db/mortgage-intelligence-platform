"""Real-PostgreSQL contract of the 2026_10_01_genie_job_sections verified-sections migration.

Audit 2026-09-21 ``genie-01`` phase 1b with ruling R1. The governed
migration is applied TWICE through ``lakebase_migrate._run_transaction`` (the
second run executes the executable-hook preflight over the new
``pg_column_size`` CHECK, the PR #143 fossil class); then the real SQL of the
sections writer and every terminal statement runs against it: each of the
five terminal transitions leaves ``sections_json`` NULL, the writer's UPDATE
is refused after a cancel or a record (its audit rows roll back with it),
and the named CHECK is in ``pg_constraint``. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services import genie_completion_jobs as jobs
from backend.services import genie_completion_record as record
from backend.services import genie_completion_sections as sections
from backend.services.genie_completion_stages import GenieJobFailureKind
from jobs import lakebase_migrate

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_HASH = "d" * 64


class _Pg:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]
            return cur.fetchone() if cur.description else None

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]
            return cur.fetchmany(limit) if cur.description else []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            yield conn

    def sql(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(statement, params)  # type: ignore[arg-type]

    def sections_of(self, job_id: str) -> Any:
        row = self.fetchone(
            "SELECT sections_json FROM mip_app.genie_completion_jobs WHERE job_id = %(id)s::uuid", {"id": job_id}
        )
        assert row is not None
        return row["sections_json"]

    def revealed_rows(self) -> list[dict[str, Any]]:
        return self.fetchall(
            "SELECT event_type, metadata FROM mip_app.action_audit WHERE event_type = 'GENIE_SECTION_REVEALED'"
        )


def _apply(dsn: str) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)
    lakebase_migrate._run_transaction(
        (pre_seed, _SEED, post_seed),
        {"conninfo": dsn},
        app_role="lakebase-schema-upgrade-test-role",
        verify_outreach_integrity=True,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )


@pytest.fixture(scope="module")
def pg() -> Iterator[_Pg]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    _apply(dsn)
    # The re-run: the executable-hook preflight now inventories the new CHECK.
    _apply(dsn)
    try:
        yield _Pg(dsn)
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _running(pg: _Pg, message_id: str) -> str:
    enrollment = jobs.create_or_join(
        pg,  # type: ignore[arg-type]
        actor="lo@example.com",
        conversation_id="conv-1",
        message_id=message_id,
        question_hash=_HASH,
        expires_at_epoch=int(time.time()) + 900,
        deep=True,
    )
    assert jobs.claim(pg, enrollment.job.job_id) is True  # type: ignore[arg-type]
    return enrollment.job.job_id


def _item(index: int) -> dict[str, Any]:
    return {
        "index": index, "title": f"Part {index}", "question": f"Part {index}?", "answer": "Illinois leads.",
        "trusted_assets": ["mip.gold.borrower_360"], "sql_query": "SELECT 1 FROM mip.gold.borrower_360",
        "row_count": 1, "table_rows": [{"state": "IL"}], "visualization": None, "narrative_withheld": False,
    }


def _reveal(pg: _Pg, job_id: str, count: int = 3) -> None:
    context = sections.RevealContext(
        actor="lo@example.com", conversation_id="conv-1", message_id=job_id[:8], question_label=_HASH[:16],
        correlation_id="corr-pg",
    )
    sections.write_sections(pg, job_id, context, [_item(n) for n in range(count)], sections._JobReveal())  # type: ignore[arg-type]


def test_the_migration_is_idempotent_and_its_check_is_named_in_the_catalog(pg: _Pg) -> None:
    checks = pg.fetchall(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'mip_app.genie_completion_jobs'::regclass AND contype = 'c'"
    )
    by_name = {row["conname"]: row["def"] for row in checks}
    assert "pg_column_size(sections_json) <= 8388608" in by_name["genie_completion_jobs_sections_size_chk"]
    assert sum(1 for name in by_name if "sections" in name) == 1, "applied twice, one CHECK"
    versions = pg.fetchone(
        "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = '2026_10_01_genie_job_sections'"
    )
    assert versions == {"n": 1}
    assert pg.fetchone(jobs._PROBE_SQL) == {"present": True}


def test_the_writer_audits_then_stores_and_the_poll_reads_by_revision(pg: _Pg) -> None:
    job_id = _running(pg, "reveal-1")
    before = len(pg.revealed_rows())

    _reveal(pg, job_id)

    stored = pg.sections_of(job_id)
    assert (stored["rev"], stored["count"], len(stored["sections"])) == (1, 3, 3)
    assert len(pg.revealed_rows()) == before + 3
    job = jobs.read_for_actor(pg, job_id=job_id, actor="lo@example.com", conversation_id="conv-1",  # type: ignore[arg-type]
                              message_id="reveal-1", with_sections=True, known_sections_rev=None)
    assert job is not None and (job.sections_rev, job.sections_count) == (1, 3)
    assert job.revealed is not None and len(job.revealed) == 3
    same = jobs.read_for_actor(pg, job_id=job_id, actor="lo@example.com", conversation_id="conv-1",  # type: ignore[arg-type]
                               message_id="reveal-1", with_sections=True, known_sections_rev=1)
    assert same is not None and same.revealed is None and same.sections_rev == 1


@pytest.mark.parametrize("guard", ["cancel_requested_at", "recorded_at"])
def test_the_writer_update_is_refused_after_a_cancel_or_a_record_and_its_rows_roll_back(pg: _Pg, guard: str) -> None:
    job_id = _running(pg, f"refused-{guard}")
    pg.sql(f"UPDATE mip_app.genie_completion_jobs SET {guard} = now() WHERE job_id = %s::uuid", (job_id,))  # noqa: S608 - fixed identifiers
    before = len(pg.revealed_rows())

    _reveal(pg, job_id)

    assert pg.sections_of(job_id) is None
    assert len(pg.revealed_rows()) == before


@pytest.mark.parametrize("ending", ["succeed", "fail", "cancel", "expire_read", "expire_sweep"])
def test_every_terminal_transition_leaves_the_sections_null(pg: _Pg, ending: str) -> None:
    job_id = _running(pg, f"end-{ending}")
    _reveal(pg, job_id)
    assert pg.sections_of(job_id) is not None

    if ending == "succeed":
        assert jobs.succeed(pg, job_id, {"v": 1}) is True  # type: ignore[arg-type]
    elif ending == "fail":
        assert jobs.fail(pg, job_id, GenieJobFailureKind.INTERNAL) is True  # type: ignore[arg-type]
    elif ending == "cancel":
        pg.sql("UPDATE mip_app.genie_completion_jobs SET cancel_requested_at = now() WHERE job_id = %s::uuid", (job_id,))
        assert record.end_cancelled(pg, job_id) is True  # type: ignore[arg-type]
    else:
        pg.sql(
            "UPDATE mip_app.genie_completion_jobs SET lease_until = now() - interval '1 minute' "
            "WHERE job_id = %s::uuid",
            (job_id,),
        )
        if ending == "expire_read":
            job = jobs.read_for_actor(pg, job_id=job_id, actor="lo@example.com", conversation_id="conv-1",  # type: ignore[arg-type]
                                      message_id=f"end-{ending}", with_sections=True)
            assert job is not None and job.status.value == "expired"
        else:
            assert jobs.sweep_expired(pg) >= 1  # type: ignore[arg-type]

    assert pg.sections_of(job_id) is None
