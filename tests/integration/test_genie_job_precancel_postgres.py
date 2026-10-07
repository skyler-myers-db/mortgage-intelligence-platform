"""Real-PostgreSQL contract of the Genie job pre-cancel migration (W5c genie-03).

The block adds ``precancelled_at`` and the named
``genie_completion_jobs_precancel_shape_chk``. The executable-hook preflight
inventories the live catalog, so only a RE-RUN can fail on a new expression
(the PR #143 fossil class): this suite applies the schema just before the
block with a live row, then the full schema TWICE through
``lakebase_migrate._run_transaction`` (every prefix replay under
``contract_as_of``), and pins the column, the CHECK and what it refuses.

Then the real SQL of the Stop before the 202 (``request_cancel`` with no job
id): the pre-cancelled row and its one audit row; a later ``create_or_join``
joins it (``created`` False, terminal, never adopted or claimed); a repeat
Stop writes nothing; and the two-connection race, where a complete's INSERT
is still uncommitted when the Stop arrives: the pre-cancel INSERT waits on
the turn UNIQUE, inserts nothing, and the Stop is decided on the committed
row (accepted, exactly one audit row). Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services import genie_completion_jobs as jobs
from backend.services.genie_answers import GenieCancelResponse
from backend.services.genie_completion_cancel import GenieCancelRequest, request_cancel
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_MARKER = "-- Genie completion-job pre-cancel ---"
#: Dated at the W5c merge: the fourth of the five W5c blocks, after the dossier index.
PRECANCEL_VERSION = "2026_10_07_genie_job_precancel"
_ACTOR = "lo@example.com"
_HASH = "c" * 64


class _Pg:
    """The LakebaseClient calls the job store and the cancel make, over psycopg."""

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


@pytest.fixture
def pg(conn_kwargs: dict[str, str]) -> _Pg:
    _apply(conn_kwargs, _SCHEMA[: _SCHEMA.index(_MARKER)])
    with psycopg.connect(**conn_kwargs) as conn:
        conn.execute(
            "INSERT INTO mip_app.genie_completion_jobs (actor_email, conversation_id, message_id, question_hash, "
            "status, stage, lease_owner, lease_until, expires_at) VALUES (%s, 'conv-old', 'msg-old', %s, "
            "'running', 'verifying', 'old-process', now() + interval '1 minute', now() + interval '15 minutes')",
            (_ACTOR, _HASH),
        )
    _apply(conn_kwargs, _SCHEMA)
    # The executable-hook preflight inventories the new CHECK on this run.
    _apply(conn_kwargs, _SCHEMA)
    return _Pg(conn_kwargs["conninfo"])


def _payload(message_id: str, job_id: str | None = None) -> GenieCancelRequest:
    return GenieCancelRequest(
        conversation_id="conv-1", message_id=message_id, progress_token="t", job_id=job_id, question_hash=_HASH[:16]
    )


def _stop(pg: _Pg, message_id: str, job_id: str | None = None) -> GenieCancelResponse:
    return request_cancel(
        pg,  # type: ignore[arg-type]
        actor=_ACTOR,
        payload=_payload(message_id, job_id),
        binding_hash=_HASH,
        expires_at_epoch=int(time.time()) + 900,
    )


def _cancelled_rows(pg: _Pg) -> list[dict[str, Any]]:
    return pg.fetchall(
        "SELECT metadata FROM mip_app.action_audit WHERE event_type = 'GENIE_TURN_CANCELLED' ORDER BY audit_sequence"
    )


def test_the_block_applies_twice_with_its_column_and_named_check(pg: _Pg) -> None:
    column = pg.fetchone(
        "SELECT data_type, is_nullable FROM information_schema.columns WHERE table_schema = 'mip_app' "
        "AND table_name = 'genie_completion_jobs' AND column_name = 'precancelled_at'"
    )
    checks = pg.fetchall(
        "SELECT conname, convalidated, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'mip_app.genie_completion_jobs'::regclass AND conname LIKE '%precancel%'"
    )
    versions = pg.fetchone(
        "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = %(v)s", {"v": PRECANCEL_VERSION}
    )
    old = pg.fetchone("SELECT status, precancelled_at FROM mip_app.genie_completion_jobs WHERE message_id = 'msg-old'")

    assert column == {"data_type": "timestamp with time zone", "is_nullable": "YES"}
    assert [(row["conname"], row["convalidated"]) for row in checks] == [
        ("genie_completion_jobs_precancel_shape_chk", True)
    ]
    assert "precancelled_at IS NULL" in checks[0]["def"]
    assert versions == {"n": 1}
    assert old == {"status": "running", "precancelled_at": None}
    assert pg.fetchone(jobs._PROBE_SQL) == {"present": True}


def test_the_check_refuses_a_precancel_mark_on_a_live_or_recorded_row(pg: _Pg) -> None:
    with pytest.raises(psycopg.errors.CheckViolation, match="genie_completion_jobs_precancel_shape_chk"):
        pg.execute("UPDATE mip_app.genie_completion_jobs SET precancelled_at = now() WHERE message_id = 'msg-old'")
    with pytest.raises(psycopg.errors.CheckViolation, match="genie_completion_jobs_precancel_shape_chk"):
        pg.execute(
            "INSERT INTO mip_app.genie_completion_jobs (actor_email, conversation_id, message_id, question_hash, "
            "status, stage, lease_owner, lease_until, expires_at, recorded_at, precancelled_at) VALUES "
            "('lo@example.com', 'conv-1', 'msg-x', %(h)s, 'succeeded', 'done', 'p', now(), now(), now(), now())",
            {"h": _HASH},
        )


def test_the_real_precancel_is_joined_by_a_later_complete_and_never_runs(pg: _Pg) -> None:
    stopped = _stop(pg, "msg-1")

    assert (stopped.outcome, stopped.status.value) == ("cancelled", "cancelled")
    row = pg.fetchone(
        "SELECT job_id::text AS job_id, status, stage, question_hash, deep, result_json, sections_json, "
        "recorded_at, cancel_requested_at IS NOT NULL AS cancel_requested, precancelled_at IS NOT NULL AS pre "
        "FROM mip_app.genie_completion_jobs WHERE message_id = 'msg-1'"
    )
    assert row is not None and row["job_id"] == stopped.job_id
    assert (row["status"], row["stage"], row["question_hash"]) == ("cancelled", "cancelled", _HASH)
    assert (row["deep"], row["result_json"], row["sections_json"], row["recorded_at"]) == (None, None, None, None)
    assert (row["cancel_requested"], row["pre"]) == (True, True)
    [audited] = _cancelled_rows(pg)
    assert audited["metadata"]["genie_job_id"] == stopped.job_id
    assert audited["metadata"]["status"] == "pre_job"

    enrollment = jobs.create_or_join(
        pg,  # type: ignore[arg-type]
        actor=_ACTOR,
        conversation_id="conv-1",
        message_id="msg-1",
        question_hash=_HASH,
        expires_at_epoch=int(time.time()) + 900,
        deep=True,
    )

    assert enrollment.created is False
    assert (enrollment.job.job_id, enrollment.job.status.value, enrollment.job.terminal) == (
        stopped.job_id,
        "cancelled",
        True,
    )
    assert jobs.adoptable(enrollment.job) is False
    assert jobs.claim(pg, enrollment.job.job_id) is False  # type: ignore[arg-type]


def test_a_repeat_stop_on_a_precancelled_turn_adds_no_second_audit_row(pg: _Pg) -> None:
    first = _stop(pg, "msg-2")
    again = _stop(pg, "msg-2")
    named = _stop(pg, "msg-2", first.job_id)

    assert [r.outcome for r in (first, again, named)] == ["cancelled", "cancelled", "cancelled"]
    assert {r.job_id for r in (first, again, named)} == {first.job_id}
    assert len(_cancelled_rows(pg)) == 1


def test_a_re_run_never_drops_and_re_adds_the_kpi_snapshot_checks(conn_kwargs: dict[str, str]) -> None:
    # W5c R1 NB-8: the KPI snapshot event-measures block used to DROP and
    # re-ADD its two CHECKs on every migrate run (a full re-validation each
    # deploy). Now each is added only when pg_constraint lacks it.
    def checks() -> dict[str, tuple[int, bool]]:
        with psycopg.connect(**conn_kwargs) as conn:
            rows = conn.execute(
                "SELECT conname, oid::bigint, convalidated FROM pg_constraint "
                "WHERE conrelid = 'mip_app.kpi_snapshots'::regclass "
                "AND conname IN ('kpi_snapshots_listed_for_sale_chk', 'kpi_snapshots_competitor_lien_chk')"
            ).fetchall()
        return {str(name): (int(oid), bool(valid)) for name, oid, valid in rows}

    _apply(conn_kwargs, _SCHEMA)
    first = checks()
    _apply(conn_kwargs, _SCHEMA)
    second = checks()

    assert set(first) == {"kpi_snapshots_listed_for_sale_chk", "kpi_snapshots_competitor_lien_chk"}
    assert all(valid for _oid, valid in first.values())
    assert second == first, "the same constraints (same OIDs), still validated, after a second apply"


def _blocked_backends(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
        ).fetchone()
    return int(row[0]) if row else 0


def test_a_stop_racing_an_uncommitted_complete_waits_then_accepts_the_committed_job(pg: _Pg) -> None:
    results: list[GenieCancelResponse] = []
    with psycopg.connect(pg.dsn, row_factory=dict_row) as complete:
        inserted = complete.execute(
            jobs._INSERT_SQL,
            {
                "actor_email": _ACTOR,
                "conversation_id": "conv-1",
                "message_id": "msg-race",
                "question_hash": _HASH,
                "lease_owner": jobs.PROCESS_ID,
                "expires_at_epoch": int(time.time()) + 900,
                "deep": False,
            },
        ).fetchone()
        assert inserted is not None
        stop = Thread(target=lambda: results.append(_stop(pg, "msg-race")))
        stop.start()
        deadline = time.monotonic() + 10
        while _blocked_backends(pg.dsn) == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _blocked_backends(pg.dsn) == 1, "the pre-cancel INSERT must wait on the complete's uncommitted row"
        assert results == []
        complete.commit()
    stop.join(10)

    [result] = results
    assert result.job_id == inserted["job_id"], "decided on the committed job, never a second row"
    assert (result.outcome, result.status.value) == ("cancelled", "cancelled")
    rows = pg.fetchall("SELECT status, precancelled_at FROM mip_app.genie_completion_jobs WHERE message_id = 'msg-race'")
    assert rows == [{"status": "cancelled", "precancelled_at": None}]
    [audited] = _cancelled_rows(pg)
    assert (audited["metadata"]["genie_job_id"], audited["metadata"]["status"]) == (inserted["job_id"], "queued")
