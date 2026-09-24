"""Lakebase contract for server-side Genie completion jobs (audit genie-01)."""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.genie_completion_stages import (
    GenieJobFailureKind,
    GenieJobStage,
    GenieJobStatus,
)
from jobs import lakebase_migrate

ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = (ROOT / "lakebase" / "schema.sql").read_text(encoding="utf-8")


def _job_table_ddl() -> str:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS mip_app\.genie_completion_jobs \((.*?)\n\);",
        _SCHEMA,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def _closed_set(ddl: str, column: str) -> set[str]:
    match = re.search(rf"\b{column} IN \((.*?)\)\)", ddl, flags=re.DOTALL)
    assert match is not None, column
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_one_job_per_actor_turn_with_a_lease_and_an_expiry() -> None:
    ddl = _job_table_ddl()

    assert "job_id           UUID PRIMARY KEY DEFAULT gen_random_uuid()" in ddl
    assert re.search(
        r"CONSTRAINT uq_genie_completion_jobs_turn\s+UNIQUE \(actor_email, conversation_id, message_id\)",
        ddl,
    )
    for column in ("lease_owner      TEXT NOT NULL", "lease_until      TIMESTAMPTZ NOT NULL",
                   "expires_at       TIMESTAMPTZ NOT NULL",
                   "created_at       TIMESTAMPTZ NOT NULL DEFAULT now()",
                   "updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()",
                   "finished_at      TIMESTAMPTZ,"):
        assert column in ddl, column
    assert "parts_done       SMALLINT CHECK (parts_done IS NULL OR parts_done >= 0)" in ddl
    assert "parts_planned    SMALLINT CHECK (parts_planned IS NULL OR parts_planned >= 0)" in ddl
    assert "pg_column_size(result_json) <= 8388608" in ddl
    # The ids are opaque and broad on purpose; the binding digest is exact.
    assert "conversation_id ~ '^[A-Za-z0-9_-]{1,256}$'" in ddl
    assert "message_id ~ '^[A-Za-z0-9_-]{1,256}$'" in ddl
    assert "question_hash ~ '^[0-9a-f]{64}$'" in ddl
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_genie_completion_jobs_live_lease\s+"
        r"ON mip_app\.genie_completion_jobs \(lease_until\)\s+"
        r"WHERE status IN \('queued', 'running'\);",
        _SCHEMA,
    )
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_genie_completion_jobs_served_expiry\s+"
        r"ON mip_app\.genie_completion_jobs \(expires_at\)\s+"
        r"WHERE status = 'succeeded';",
        _SCHEMA,
    )
    assert re.search(
        r"INSERT INTO mip_app\.schema_migrations \(version, description\)\s+VALUES \(\s+"
        r"'2026_09_24_genie_completion_jobs',.*?\)\s+ON CONFLICT \(version\) DO NOTHING;",
        _SCHEMA,
        flags=re.DOTALL,
    )


def test_the_closed_checks_match_the_python_vocabularies() -> None:
    ddl = _job_table_ddl()

    assert _closed_set(ddl, "status") == {status.value for status in GenieJobStatus}
    assert _closed_set(ddl, "stage") == {stage.value for stage in GenieJobStage}
    assert _closed_set(ddl, "failure_kind") == {kind.value for kind in GenieJobFailureKind}


def test_no_column_can_hold_question_text() -> None:
    ddl = _job_table_ddl()

    text_columns = re.findall(r"^\s+([a-z_]+)\s+TEXT\b", ddl, flags=re.MULTILINE)
    assert set(text_columns) == {
        "actor_email",
        "conversation_id",
        "message_id",
        "question_hash",
        "status",
        "stage",
        "failure_kind",
        "lease_owner",
    }
    assert re.search(r"\b(question|question_text|prompt|comment)\s+(TEXT|JSONB)\b", ddl) is None
    comment = re.search(
        r"COMMENT ON TABLE mip_app\.genie_completion_jobs IS\s+'(.*?)';", _SCHEMA, flags=re.DOTALL
    )
    assert comment is not None
    assert "No question text is stored" in comment.group(1)
    assert "de-authorized" in comment.group(1)
    assert "until expires_at" in comment.group(1)


def test_the_app_role_reads_and_writes_but_never_deletes() -> None:
    privileges = lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES["genie_completion_jobs"]

    assert privileges == ("SELECT", "INSERT", "UPDATE")
    assert "DELETE" not in privileges


def test_every_schema_table_has_a_reviewed_privilege_entry() -> None:
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS mip_app\.([a-z_]+) \(", _SCHEMA))

    assert created == set(lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES)
