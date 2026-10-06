"""Lakebase contract for server-side Genie completion jobs (audit genie-01),
and the 2026_09_25 cancel migration (audit genie-03)."""

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


def _cancel_block() -> str:
    start = _SCHEMA.index("-- Genie completion-job cancel ---")
    end = _SCHEMA.index("ON CONFLICT (version) DO NOTHING;", start)
    return _SCHEMA[start:end]


def _named_check(name: str) -> str:
    match = re.search(
        rf"ADD CONSTRAINT {name}\s+CHECK \((.*?)\);\n\s+END IF;", _cancel_block(), flags=re.DOTALL
    )
    assert match is not None, name
    return match.group(1)


_NEW_CHECKS = (
    "genie_completion_jobs_status_chk",
    "genie_completion_jobs_stage_chk",
    "genie_completion_jobs_cancel_or_record_chk",
    "genie_completion_jobs_cancelled_shape_chk",
)


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
    assert "parts_done       SMALLINT CHECK (parts_done IS NULL OR parts_done >= 0::smallint)" in ddl
    assert "parts_planned    SMALLINT CHECK (parts_planned IS NULL OR parts_planned >= 0::smallint)" in ddl
    assert "pg_column_size(result_json) <= 8388608" in ddl
    # The ids are opaque and broad on purpose; the binding digest is exact.
    # No regex repetition bound above 255: PostgreSQL rejects it at INSERT.
    assert "conversation_id ~ '^[A-Za-z0-9_-]+$' AND length(conversation_id) <= 256" in ddl
    assert "message_id ~ '^[A-Za-z0-9_-]+$' AND length(message_id) <= 256" in ddl
    for low, high in re.findall(r"\{(\d+),(\d+)\}", ddl):
        assert int(high) <= 255, (low, high)
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
    status = set(re.findall(r"'([a-z_]+)'", _named_check("genie_completion_jobs_status_chk")))
    stage = set(re.findall(r"'([a-z_]+)'", _named_check("genie_completion_jobs_stage_chk")))
    assert _named_check("genie_completion_jobs_status_chk").lstrip().startswith("status IN (")
    assert _named_check("genie_completion_jobs_stage_chk").lstrip().startswith("stage IN (")

    # The named 2026_09_25 CHECKs are the live vocabularies; the 2026_09_24
    # CREATE TABLE text stays byte-identical, one value short of them.
    assert status == {status.value for status in GenieJobStatus}
    assert stage == {stage.value for stage in GenieJobStage}
    assert _closed_set(ddl, "status") == status - {"cancelled"}
    assert _closed_set(ddl, "stage") == stage - {"cancelled"}
    assert _closed_set(ddl, "failure_kind") == {kind.value for kind in GenieJobFailureKind}


def test_the_cancel_migration_adds_three_nullable_columns_and_no_text() -> None:
    block = _cancel_block()

    for column, kind in (("cancel_requested_at", "TIMESTAMPTZ"), ("recorded_at", "TIMESTAMPTZ"), ("deep", "BOOLEAN")):
        assert re.search(
            rf"ALTER TABLE mip_app\.genie_completion_jobs\s+ADD COLUMN IF NOT EXISTS {column} {kind};", block
        ), column
        assert f"COMMENT ON COLUMN mip_app.genie_completion_jobs.{column} IS" in block
    assert re.search(r"ADD COLUMN IF NOT EXISTS [a-z_]+ (TEXT|JSONB|VARCHAR)", block) is None
    assert re.search(r"\bNOT NULL\b", block.split("DO $$")[0]) is None
    comments = re.findall(r"COMMENT ON COLUMN [a-z_.]+ IS\s+'(.*?)';", block, flags=re.DOTALL)
    assert len(comments) == 3
    assert not any("question" in comment.lower() for comment in comments)
    assert re.search(
        r"INSERT INTO mip_app\.schema_migrations \(version, description\)\s+VALUES \(\s+"
        r"'2026_09_25_genie_job_cancel',",
        block,
    )
    assert _SCHEMA.rstrip().endswith("ON CONFLICT (version) DO NOTHING;")
    assert _SCHEMA.index("-- Genie completion-job cancel ---") > _SCHEMA.index("'2026_09_24_genie_completion_jobs'")


def test_the_do_block_drops_only_auto_named_status_and_stage_checks_by_catalog_lookup() -> None:
    block = _cancel_block()

    assert "c.conrelid = 'mip_app.genie_completion_jobs'::regclass" in block
    assert "c.contype = 'c'" in block
    assert "cardinality(c.conkey) = 1" in block
    assert "a.attname IN ('status', 'stage')" in block
    assert re.search(
        r"c\.conname NOT IN \(\s+'genie_completion_jobs_status_chk', 'genie_completion_jobs_stage_chk'\s+\)",
        block,
    )
    # The catalog-driven DROP is the only dynamic SQL; every ADD is static.
    assert block.count("EXECUTE format(") == 1
    assert "'ALTER TABLE mip_app.genie_completion_jobs DROP CONSTRAINT %I'" in block
    for name in _NEW_CHECKS:
        assert re.search(
            rf"IF NOT EXISTS \(\s+SELECT 1 FROM pg_constraint\s+"
            rf"WHERE conrelid = 'mip_app\.genie_completion_jobs'::regclass\s+"
            rf"AND conname = '{name}'\s+\) THEN",
            block,
        ), name
    assert " ".join(_named_check("genie_completion_jobs_cancel_or_record_chk").split()) == (
        "cancel_requested_at IS NULL OR recorded_at IS NULL"
    )
    shape = " ".join(_named_check("genie_completion_jobs_cancelled_shape_chk").split())
    assert shape == "status <> 'cancelled' OR (cancel_requested_at IS NOT NULL AND result_json IS NULL)"
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_genie_completion_jobs_recorded\s+"
        r"ON mip_app\.genie_completion_jobs \(deep, created_at DESC\)\s+"
        r"WHERE recorded_at IS NOT NULL;",
        block,
    )


def test_the_replay_scanner_sees_every_new_check() -> None:
    # tests/unit/test_lakebase_schema_immutability.py reviews every
    # ``ADD CONSTRAINT name CHECK (`` expression for the executable-hook
    # re-run; static DDL keeps the four new ones inside that scan.
    scanned = set(re.findall(r"ADD CONSTRAINT\s+([a-z0-9_]+)\s+CHECK\s*\(", _SCHEMA))

    assert set(_NEW_CHECKS) <= scanned


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


# ------------------------------- 2026_10_01_genie_job_sections verified sections (genie-01 1b)


def _sections_block() -> str:
    start = _SCHEMA.index("-- Genie completion-job verified sections ---")
    end = _SCHEMA.index("ON CONFLICT (version) DO NOTHING;", start)
    return _SCHEMA[start:end]


def test_the_sections_migration_adds_one_nullable_jsonb_column_with_a_named_size_check() -> None:
    from backend.services import genie_completion_jobs as jobs

    block = _sections_block()

    assert re.search(
        r"ALTER TABLE mip_app\.genie_completion_jobs\s+ADD COLUMN IF NOT EXISTS sections_json JSONB;", block
    )
    assert re.search(r"\bNOT NULL\b|CREATE (UNIQUE )?INDEX|CREATE TABLE|GRANT ", block) is None
    assert re.search(
        r"IF NOT EXISTS \(\s+SELECT 1 FROM pg_constraint\s+"
        r"WHERE conrelid = 'mip_app\.genie_completion_jobs'::regclass\s+"
        r"AND conname = 'genie_completion_jobs_sections_size_chk'\s+\) THEN\s+"
        r"ALTER TABLE mip_app\.genie_completion_jobs\s+"
        r"ADD CONSTRAINT genie_completion_jobs_sections_size_chk\s+"
        r"CHECK \(sections_json IS NULL OR pg_column_size\(sections_json\) <= 8388608\);",
        block,
    )
    assert "EXECUTE" not in block, "static DDL keeps the CHECK inside the replay scanner"
    assert "genie_completion_jobs_sections_size_chk" in set(
        re.findall(r"ADD CONSTRAINT\s+([a-z0-9_]+)\s+CHECK\s*\(", _SCHEMA)
    )
    comments = re.findall(r"COMMENT ON COLUMN mip_app\.genie_completion_jobs\.sections_json IS\s+'(.*?)';", block,
                          flags=re.DOTALL)
    assert len(comments) == 1 and "question" not in comments[0].lower()
    assert "NULLed at every terminal state" in comments[0]
    assert re.search(
        r"INSERT INTO mip_app\.schema_migrations \(version, description\)\s+VALUES \(\s+"
        r"'2026_10_01_genie_job_sections',",
        block,
    )
    assert "'sections_json'" in jobs._PROBE_SQL and ") = 5 AS present" in jobs._PROBE_SQL


def test_every_terminal_statement_nulls_the_sections() -> None:
    from backend.services import genie_completion_cancel as cancel
    from backend.services import genie_completion_jobs as jobs
    from backend.services import genie_completion_record as record

    for name, sql in (
        ("_SUCCEED_SQL", jobs._SUCCEED_SQL),
        ("_FAIL_SQL", jobs._FAIL_SQL),
        ("_SWEEP_SQL", jobs._SWEEP_SQL),
        ("_EXPIRE_JOB_SQL", jobs._EXPIRE_JOB_SQL),
        ("_END_CANCELLED_SQL", record._END_CANCELLED_SQL),
    ):
        assert "sections_json = NULL" in sql, name
    # W5c: the accepted cancel ends a queued job at once; that branch NULLs too.
    assert "sections_json = CASE WHEN status = 'queued' THEN NULL ELSE sections_json END" in cancel._ACCEPT_SQL
    # The pre-cancel is born terminal: an explicit NULL for every payload column.
    columns, values = re.search(r"\((.*?)\)\s+VALUES\s+\((.*?)\)\s+ON CONFLICT", cancel._PRECANCEL_SQL, re.DOTALL).groups()  # type: ignore[union-attr]
    named = dict(zip((c.strip() for c in columns.split(",")), (v.strip() for v in values.split(",")), strict=True))
    assert (named["sections_json"], named["result_json"], named["deep"]) == ("NULL", "NULL", "NULL")
    assert (named["status"], named["stage"]) == ("'cancelled'", "'cancelled'")


# ------------------------------------------- the pre-cancel block (W5c genie-03)

#: The integrator dates the placeholder at merge (it renumbers this constant).
PRECANCEL_VERSION = "2026_10_0X_genie_job_precancel"


def _precancel_block() -> str:
    start = _SCHEMA.index("-- Genie completion-job pre-cancel ---")
    end = _SCHEMA.index("ON CONFLICT (version) DO NOTHING;", start)
    return _SCHEMA[start:end]


def test_the_precancel_block_adds_one_nullable_column_and_a_named_static_check() -> None:
    from backend.services import genie_completion_jobs as jobs

    block = _precancel_block()

    assert _SCHEMA.index("-- Genie completion-job pre-cancel ---") > _SCHEMA.index("'2026_10_01_genie_job_sections'")
    assert re.search(
        r"ALTER TABLE mip_app\.genie_completion_jobs\s+ADD COLUMN IF NOT EXISTS precancelled_at TIMESTAMPTZ;", block
    )
    statements = re.sub(r"--[^\n]*", "", block)
    assert re.search(r"CREATE (UNIQUE )?INDEX|CREATE TABLE|GRANT |TRIGGER|FUNCTION", statements) is None
    assert re.search(r"\bNOT NULL\b", statements.split("DO $$")[0]) is None, "a nullable column"
    assert "EXECUTE" not in statements, "static DDL keeps the CHECK inside the replay scanner"
    check = re.search(
        r"IF NOT EXISTS \(\s+SELECT 1 FROM pg_constraint\s+"
        r"WHERE conrelid = 'mip_app\.genie_completion_jobs'::regclass\s+"
        r"AND conname = 'genie_completion_jobs_precancel_shape_chk'\s+\) THEN\s+"
        r"ALTER TABLE mip_app\.genie_completion_jobs\s+"
        r"ADD CONSTRAINT genie_completion_jobs_precancel_shape_chk\s+CHECK \((.*?)\);\s+END IF;",
        block,
        flags=re.DOTALL,
    )
    assert check is not None
    assert " ".join(check.group(1).replace("(", " ( ").replace(")", " ) ").split()) == (
        "precancelled_at IS NULL OR ( status = 'cancelled' AND cancel_requested_at IS NOT NULL "
        "AND recorded_at IS NULL AND result_json IS NULL )"
    )
    # Reviewed operators on same-type operands only (the hook preflight).
    assert re.search(r"cardinality|array_length|::|<>|\bIN\b", check.group(1)) is None
    assert "genie_completion_jobs_precancel_shape_chk" in set(
        re.findall(r"ADD CONSTRAINT\s+([a-z0-9_]+)\s+CHECK\s*\(", _SCHEMA)
    )
    comments = re.findall(
        r"COMMENT ON COLUMN mip_app\.genie_completion_jobs\.precancelled_at IS\s+'(.*?)';", block, flags=re.DOTALL
    )
    assert len(comments) == 1 and "question" not in comments[0].lower()
    assert re.search(
        rf"INSERT INTO mip_app\.schema_migrations \(version, description\)\s+VALUES \(\s+'{PRECANCEL_VERSION}',",
        block,
    )
    assert "'precancelled_at'" in jobs._PROBE_SQL and ") = 5 AS present" in jobs._PROBE_SQL
