"""Real-Postgres re-run contract of the 2026_09_25 Genie job-cancel migration.

Audit 2026-09-21 ``genie-03``. The block replaces the 2026_09_24 table's
AUTO-NAMED inline status/stage CHECKs (dropped by catalog lookup) with
named, widened ones and adds the cancel/record CHECKs. The executable-hook
preflight inventories the live catalog, so only a RE-RUN can fail on a new
expression (the PR #143 fossil class): this suite starts from the exact
2026_09_24 shape with a queued and a succeeded row, applies the governed
migration TWICE through ``lakebase_migrate._run_transaction``, and pins the
surviving CHECK set, the rows, and what the new CHECKs accept and refuse.
Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from jobs import lakebase_migrate

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_CANCEL_MARKER = "-- Genie completion-job cancel ---"
_HASH = "e" * 64

#: Every CHECK on the table after the migration: the untouched 2026_09_24
#: auto-named column CHECKs (result_json's pg_column_size and failure_kind
#: included) plus the four named 2026_09_25 ones. No status/stage auto-name.
_EXPECTED_CHECKS = {
    "genie_completion_jobs_conversation_id_check",
    "genie_completion_jobs_message_id_check",
    "genie_completion_jobs_question_hash_check",
    "genie_completion_jobs_parts_done_check",
    "genie_completion_jobs_parts_planned_check",
    "genie_completion_jobs_failure_kind_check",
    "genie_completion_jobs_result_json_check",
    "genie_completion_jobs_status_chk",
    "genie_completion_jobs_stage_chk",
    "genie_completion_jobs_cancel_or_record_chk",
    "genie_completion_jobs_cancelled_shape_chk",
}


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


def _schema_as_of_2026_09_24() -> str:
    return _SCHEMA[: _SCHEMA.index(_CANCEL_MARKER)]


def _checks(conn_kwargs: dict[str, str]) -> dict[str, str]:
    with psycopg.connect(**conn_kwargs) as conn:
        rows = conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'mip_app.genie_completion_jobs'::regclass AND contype = 'c'"
        ).fetchall()
    return {str(name): str(definition) for name, definition in rows}


def _insert(conn: psycopg.Connection[dict[str, object]], message_id: str, status: str, stage: str, **extra: object) -> None:
    columns = ["actor_email", "conversation_id", "message_id", "question_hash", "status", "stage",
               "lease_owner", "lease_until", "expires_at", *extra]
    values = ["%s"] * 7 + ["now() + interval '1 minute'", "now() + interval '15 minutes'"] + ["%s"] * len(extra)
    conn.execute(
        f"INSERT INTO mip_app.genie_completion_jobs ({', '.join(columns)}) VALUES ({', '.join(values)})",  # noqa: S608 - fixed identifiers
        ("lo@example.com", "conv-1", message_id, _HASH, status, stage, "old-process", *extra.values()),
    )


def test_the_2026_09_24_shape_upgrades_twice_to_the_exact_check_set(conn_kwargs: dict[str, str]) -> None:
    _apply(conn_kwargs, _schema_as_of_2026_09_24())
    before = _checks(conn_kwargs)
    assert {"genie_completion_jobs_status_check", "genie_completion_jobs_stage_check"} <= set(before)
    assert "cancelled" not in before["genie_completion_jobs_status_check"]
    with psycopg.connect(**conn_kwargs) as conn:
        _insert(conn, "queued-1", "queued", "queued")
        _insert(conn, "served-1", "succeeded", "done", result_json='{"v": 1}')

    _apply(conn_kwargs, _SCHEMA)
    # The executable-hook preflight inventories the new CHECKs on this run.
    _apply(conn_kwargs, _SCHEMA)

    after = _checks(conn_kwargs)
    assert set(after) == _EXPECTED_CHECKS
    assert "pg_column_size(result_json)" in after["genie_completion_jobs_result_json_check"]
    assert "'internal'" in after["genie_completion_jobs_failure_kind_check"]
    assert "'cancelled'" in after["genie_completion_jobs_status_chk"]
    assert "'cancelled'" in after["genie_completion_jobs_stage_chk"]
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT message_id, status, stage, result_json, cancel_requested_at, recorded_at, deep "
            "FROM mip_app.genie_completion_jobs ORDER BY message_id"
        ).fetchall()
        versions = conn.execute(
            "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = '2026_09_25_genie_job_cancel'"
        ).fetchone()
        index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_genie_completion_jobs_recorded'"
        ).fetchone()
    assert rows == [
        {"message_id": "queued-1", "status": "queued", "stage": "queued", "result_json": None,
         "cancel_requested_at": None, "recorded_at": None, "deep": None},
        {"message_id": "served-1", "status": "succeeded", "stage": "done", "result_json": {"v": 1},
         "cancel_requested_at": None, "recorded_at": None, "deep": None},
    ]
    assert versions == {"n": 1}
    assert index is not None and "WHERE (recorded_at IS NOT NULL)" in str(index["indexdef"])


def test_a_fresh_install_reaches_the_same_check_set_and_the_new_checks_hold(conn_kwargs: dict[str, str]) -> None:
    _apply(conn_kwargs, _SCHEMA)
    _apply(conn_kwargs, _SCHEMA)

    assert set(_checks(conn_kwargs)) == _EXPECTED_CHECKS
    with psycopg.connect(**conn_kwargs) as conn:
        _insert(conn, "stopped-1", "cancelled", "cancelled", cancel_requested_at="2026-09-25T12:00:00Z")
    for message_id, status, stage, extra, constraint in (
        (
            "both-1",
            "running",
            "finalizing",
            {"cancel_requested_at": "2026-09-25T12:00:00Z", "recorded_at": "2026-09-25T12:00:01Z"},
            "genie_completion_jobs_cancel_or_record_chk",
        ),
        ("bare-1", "cancelled", "cancelled", {}, "genie_completion_jobs_cancelled_shape_chk"),
        (
            "answer-1",
            "cancelled",
            "cancelled",
            {"cancel_requested_at": "2026-09-25T12:00:00Z", "result_json": '{"v": 1}'},
            "genie_completion_jobs_cancelled_shape_chk",
        ),
        ("vocab-1", "stopped", "cancelled", {}, "genie_completion_jobs_status_chk"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation) as refused, psycopg.connect(**conn_kwargs) as conn:
            _insert(conn, message_id, status, stage, **extra)
        assert refused.value.diag.constraint_name == constraint, message_id
