"""Real-PostgreSQL contract for the Genie completion-job store (audit genie-01).

The unit suites drive the job store through an in-memory double that models
each statement's semantics; this suite runs the ACTUAL SQL of
``backend/services/genie_completion_jobs.py`` against the ACTUAL DDL block of
``lakebase/schema.sql``: the ON CONFLICT join, the lease-owner guards, the
heartbeat's uuid[] renewal, compare-and-set expiry on read, the bounded
``FOR UPDATE SKIP LOCKED`` sweep, and the closed CHECKs. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database (the schema-upgrade
suite's convention); the whole migration's apply is covered there.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row

from backend.services import genie_completion_jobs as jobs
from backend.services.genie_completion_stages import GenieJobFailureKind, GenieJobStage

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_HASH = "b" * 64


def _job_table_ddl() -> str:
    start = _SCHEMA.index("CREATE TABLE IF NOT EXISTS mip_app.genie_completion_jobs (")
    end = _SCHEMA.index("INSERT INTO mip_app.schema_migrations", start)
    return _SCHEMA[start:end]


class _PgLakebase:
    """The three LakebaseClient calls the job store makes, over psycopg."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() if cur.description else None

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchmany(limit) if cur.description else []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)

    def sql(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(statement, params)  # type: ignore[arg-type]

    def row(self, job_id: str) -> dict[str, Any]:
        found = self.fetchone(
            "SELECT *, now() AS db_now FROM mip_app.genie_completion_jobs WHERE job_id = %(id)s::uuid",
            {"id": job_id},
        )
        assert found is not None
        return found


@pytest.fixture
def pg() -> Iterator[_PgLakebase]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
        cur.execute("CREATE SCHEMA mip_app")
        cur.execute(_job_table_ddl())  # type: ignore[arg-type]
    try:
        yield _PgLakebase(dsn)
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _enroll(pg: _PgLakebase, *, actor: str = "lo@example.com", conversation_id: str = "conv-1", message_id: str = "msg-1", ttl_s: int = 900) -> jobs.JobEnrollment:
    import time

    return jobs.create_or_join(
        pg,  # type: ignore[arg-type]
        actor=actor,
        conversation_id=conversation_id,
        message_id=message_id,
        question_hash=_HASH,
        expires_at_epoch=int(time.time()) + ttl_s,
    )


def _read(pg: _PgLakebase, job_id: str, *, actor: str = "lo@example.com") -> jobs.GenieCompletionJob | None:
    return jobs.read_for_actor(
        pg,  # type: ignore[arg-type]
        job_id=job_id,
        actor=actor,
        conversation_id="conv-1",
        message_id="msg-1",
    )


def test_one_row_per_turn_a_second_enrollment_joins_it(pg: _PgLakebase) -> None:
    first = _enroll(pg)
    second = _enroll(pg)

    assert (first.created, second.created) == (True, False)
    assert second.job.job_id == first.job.job_id
    assert (first.job.status.value, first.job.stage.value) == ("queued", "queued")
    assert first.job.lease_owner == jobs.PROCESS_ID
    with pytest.raises(HTTPException) as mismatch:
        jobs.create_or_join(
            pg,  # type: ignore[arg-type]
            actor="lo@example.com",
            conversation_id="conv-1",
            message_id="msg-1",
            question_hash="c" * 64,
            expires_at_epoch=2_000_000_000,
        )
    assert mismatch.value.status_code == 400


def test_claim_stage_succeed_are_owner_only_and_single_shot(pg: _PgLakebase, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = _enroll(pg).job.job_id

    assert jobs.claim(pg, job_id) is True  # type: ignore[arg-type]
    assert jobs.claim(pg, job_id) is False  # type: ignore[arg-type]
    jobs.write_stage(pg, job_id, GenieJobStage.RESEARCHING, 3, 7)  # type: ignore[arg-type]
    assert (_read(pg, job_id).stage.value, _read(pg, job_id).parts_done) == ("researching", 3)  # type: ignore[union-attr]
    monkeypatch.setattr(jobs, "PROCESS_ID", "another-process")
    assert jobs.succeed(pg, job_id, {"v": 1}) is False  # type: ignore[arg-type]
    monkeypatch.undo()
    assert jobs.succeed(pg, job_id, {"v": 1, "response": {"answer": "ok"}}) is True  # type: ignore[arg-type]
    assert jobs.succeed(pg, job_id, {"v": 1}) is False  # type: ignore[arg-type]

    done = _read(pg, job_id)
    assert done is not None
    assert (done.status.value, done.stage.value, done.parts_done) == ("succeeded", "done", None)
    assert done.result_json == {"v": 1, "response": {"answer": "ok"}}
    assert pg.row(job_id)["finished_at"] is not None


def test_fail_records_the_family_only(pg: _PgLakebase) -> None:
    job_id = _enroll(pg).job.job_id
    jobs.claim(pg, job_id)  # type: ignore[arg-type]

    assert jobs.fail(pg, job_id, GenieJobFailureKind.UPSTREAM_ERROR) is True  # type: ignore[arg-type]
    failed = _read(pg, job_id)
    assert failed is not None
    assert (failed.status.value, failed.failure_kind) == ("failed", GenieJobFailureKind.UPSTREAM_ERROR)


def test_a_stale_lease_expires_on_read_and_a_live_one_is_untouched(pg: _PgLakebase) -> None:
    job_id = _enroll(pg).job.job_id
    jobs.claim(pg, job_id)  # type: ignore[arg-type]

    live = _read(pg, job_id)
    pg.sql("UPDATE mip_app.genie_completion_jobs SET lease_until = now() - interval '1 second'")
    stale = _read(pg, job_id)

    assert live is not None and live.status.value == "running"
    assert stale is not None and (stale.status.value, stale.stage.value) == ("expired", "expired")


def test_the_heartbeat_renews_only_this_process_live_leases(pg: _PgLakebase) -> None:
    mine = _enroll(pg).job.job_id
    theirs = _enroll(pg, message_id="msg-2").job.job_id
    pg.sql("UPDATE mip_app.genie_completion_jobs SET lease_until = now() + interval '1 second'")
    pg.sql("UPDATE mip_app.genie_completion_jobs SET lease_owner = 'other' WHERE job_id = %s::uuid", (theirs,))
    beat = jobs._Heartbeat()
    beat._jobs = {mine: pg, theirs: pg}  # type: ignore[dict-item]

    beat.beat()

    assert (pg.row(mine)["lease_until"] - pg.row(mine)["db_now"]).total_seconds() > jobs.LEASE_S - 5
    assert (pg.row(theirs)["lease_until"] - pg.row(theirs)["db_now"]).total_seconds() < 5


def test_a_served_answer_past_expiry_expires_on_read_with_its_result_nulled(pg: _PgLakebase) -> None:
    job_id = _enroll(pg).job.job_id
    jobs.claim(pg, job_id)  # type: ignore[arg-type]
    jobs.succeed(pg, job_id, {"v": 1})  # type: ignore[arg-type]
    pg.sql("UPDATE mip_app.genie_completion_jobs SET expires_at = now() - interval '1 second'")

    expired = _read(pg, job_id)

    assert expired is not None and expired.status.value == "expired"
    assert pg.row(job_id)["result_json"] is None


def test_the_sweep_expires_any_actor_stale_rows_bounded_and_nulls_answers(pg: _PgLakebase) -> None:
    served = [_enroll(pg, actor=f"a{i}@example.com", message_id=f"m-{i}").job.job_id for i in range(3)]
    for job_id in served:
        jobs.claim(pg, job_id)  # type: ignore[arg-type]
        jobs.succeed(pg, job_id, {"v": 1})  # type: ignore[arg-type]
    dead = [_enroll(pg, actor="b@example.com", message_id=f"d-{i}").job.job_id for i in range(jobs.SWEEP_LIMIT + 5)]
    fresh = _enroll(pg, actor="c@example.com", message_id="fresh").job.job_id
    # Age the rows only now: every enrollment above ran its own sweep.
    pg.sql("UPDATE mip_app.genie_completion_jobs SET expires_at = now() - interval '1 second' WHERE status = 'succeeded'")
    pg.sql(
        "UPDATE mip_app.genie_completion_jobs SET lease_until = now() - interval '1 second' "
        "WHERE status = 'queued' AND message_id <> 'fresh'"
    )

    first = jobs.sweep_expired(pg)  # type: ignore[arg-type]
    second = jobs.sweep_expired(pg)  # type: ignore[arg-type]

    assert first == jobs.SWEEP_LIMIT
    assert second == len(served) + len(dead) - jobs.SWEEP_LIMIT
    for job_id in [*served, *dead]:
        row = pg.row(job_id)
        assert (row["status"], row["result_json"]) == ("expired", None)
    assert pg.row(fresh)["status"] == "queued"


def test_the_checks_refuse_off_vocabulary_values_and_question_shaped_hashes(pg: _PgLakebase) -> None:
    job_id = _enroll(pg).job.job_id
    for statement in (
        "UPDATE mip_app.genie_completion_jobs SET stage = 'answer ready' WHERE job_id = %s::uuid",
        "UPDATE mip_app.genie_completion_jobs SET status = 'done' WHERE job_id = %s::uuid",
        "UPDATE mip_app.genie_completion_jobs SET failure_kind = 'boom' WHERE job_id = %s::uuid",
        "UPDATE mip_app.genie_completion_jobs SET question_hash = 'How many borrowers?' WHERE job_id = %s::uuid",
        "UPDATE mip_app.genie_completion_jobs SET parts_done = -1 WHERE job_id = %s::uuid",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            pg.sql(statement, (job_id,))
    assert re.fullmatch(r"[0-9a-f-]{36}", job_id)
    assert _read(pg, str(uuid4())) is None
