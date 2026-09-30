"""DDL and grant pins for mip_app.saved_views (tables-09 phase 2)."""

from __future__ import annotations

import re
from pathlib import Path

from jobs import lakebase_migrate
from jobs.lakebase_migration_contracts import _SAFE_SCHEMA_HOOK_FUNCTION_NAMES
from jobs.lakebase_migration_schema_hooks import _schema_hook_function_calls

ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = (ROOT / "lakebase" / "schema.sql").read_text(encoding="utf-8")
_MARKER = "-- Saved Lead Queue views ---"


def _block() -> str:
    start = _SCHEMA.index(_MARKER)
    end = _SCHEMA.index("ON CONFLICT (version) DO NOTHING;", start)
    return _SCHEMA[start:end]


def _table_ddl() -> str:
    match = re.search(r"CREATE TABLE IF NOT EXISTS mip_app\.saved_views \((.*?)\n\);", _block(), flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def _columns() -> dict[str, str]:
    return {
        name: rest.strip().rstrip(",")
        for name, rest in re.findall(r"^\s{4}([a-z_]+)\s+(.+)$", _table_ddl(), flags=re.MULTILINE)
    }


def test_the_block_is_appended_last_and_versioned_once() -> None:
    assert _SCHEMA.count(_MARKER) == 1
    assert _SCHEMA.rstrip().endswith("ON CONFLICT (version) DO NOTHING;")
    assert _SCHEMA.index(_MARKER) > _SCHEMA.index("'2026_09_25_genie_job_cancel'")
    assert "'2026_10_01_saved_views'" in _block()


def test_the_columns_and_their_checks() -> None:
    columns = _columns()
    assert list(columns) == [
        "view_id", "actor_email", "surface", "name", "name_key", "params",
        "created_at", "updated_at", "deleted_at",
    ]
    assert columns["view_id"] == "UUID PRIMARY KEY DEFAULT gen_random_uuid()"
    assert columns["actor_email"] == "TEXT NOT NULL"
    assert columns["surface"] == "TEXT NOT NULL CHECK (surface IN ('lead_queue'))"
    assert columns["name"] == "TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60 AND btrim(name) = name)"
    assert columns["name_key"] == "TEXT NOT NULL CHECK (length(name_key) BETWEEN 1 AND 60)"
    assert columns["params"] == "TEXT NOT NULL CHECK (length(params) BETWEEN 1 AND 2048)"
    assert columns["created_at"] == "TIMESTAMPTZ NOT NULL DEFAULT now()"
    assert columns["updated_at"] == "TIMESTAMPTZ NOT NULL DEFAULT now()"
    assert columns["deleted_at"] == "TIMESTAMPTZ"


def test_live_names_are_unique_per_actor_and_listed_by_recency() -> None:
    block = _block()
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_saved_views_actor_surface_name_live\s+"
        r"ON mip_app\.saved_views \(actor_email, surface, name_key\)\s+WHERE deleted_at IS NULL;",
        block,
    )
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_saved_views_actor_surface_updated\s+"
        r"ON mip_app\.saved_views \(actor_email, surface, updated_at DESC\)\s+WHERE deleted_at IS NULL;",
        block,
    )
    assert "COMMENT ON TABLE mip_app.saved_views IS" in block


def test_only_reviewed_hooks_and_no_expression_or_json_index() -> None:
    block = _block()
    calls = _schema_hook_function_calls(_table_ddl()) - {"in", "check"}
    assert calls <= _SAFE_SCHEMA_HOOK_FUNCTION_NAMES
    assert calls == {"gen_random_uuid", "length", "btrim", "now"}
    for banned in ("lower(", "JSONB", "USING gin", "USING GIN", "CREATE TRIGGER", "CREATE OR REPLACE FUNCTION"):
        assert banned not in block


def test_the_app_role_selects_inserts_and_updates_but_never_deletes() -> None:
    assert lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES["saved_views"] == ("SELECT", "INSERT", "UPDATE")


def test_every_schema_table_has_a_reviewed_privilege_entry() -> None:
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS mip_app\.([a-z_]+) \(", _SCHEMA))
    assert "saved_views" in created
    assert created == set(lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES)
