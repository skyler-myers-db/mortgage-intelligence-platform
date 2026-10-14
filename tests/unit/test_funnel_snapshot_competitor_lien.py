"""Per-state competitor-lien counts on gold.funnel_snapshot_daily (audit wow-ai-3).

The Delta Explainer attributes a 'since your last login' number to the states
that moved. Its competitor-lien measure needs the headline
SUM(is_competitor_lien) carried per (state, segment) cell of the MERGE-
maintained funnel snapshot. Pins:

* the MERGE source carries ``b.is_competitor_lien`` in BOTH CTEs and the
  measure in BOTH aggregates, and the MERGE writes it on UPDATE and INSERT;
* 003 declares it a NULLABLE INT (snapshots recorded before the column stay
  NULL, never backfilled with 0);
* the ensure step's ALTER carries the 003 comment byte-identically, and is
  idempotent: a second run against an upgraded table issues no ALTER.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jobs import sync_lifecycle_state as job

REPO = Path(__file__).resolve().parents[2]
_TRANSFORM = (REPO / "sql/transformations/gold_funnel_snapshot_daily.sql").read_text(encoding="utf-8")
_DDL_003 = (REPO / "sql/ddl/003_gold_tables.sql").read_text(encoding="utf-8")


def _funnel_block() -> str:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS mip\.gold\.funnel_snapshot_daily \((?P<body>.*?)\n\)",
        _DDL_003,
        re.DOTALL,
    )
    assert match is not None
    return match.group("body")


def _ddl_line() -> str:
    lines = [line for line in _funnel_block().splitlines() if "competitor_lien_borrowers" in line]
    assert len(lines) == 1
    return lines[0]


def test_both_source_ctes_carry_the_competitor_lien_flag() -> None:
    for cte in ("exploded", "all_segments"):
        body = re.search(rf"{cte} AS \((?P<body>.*?)\n  \)", _TRANSFORM, re.DOTALL)
        assert body is not None, cte
        assert "b.is_competitor_lien," in body.group("body"), cte


def test_both_aggregates_carry_the_headline_measure() -> None:
    measure = (
        "CAST(SUM(CASE WHEN is_competitor_lien THEN 1 ELSE 0 END) AS INT)"
    )
    for cte in ("per_state", "national"):
        body = re.search(rf"{cte} AS \((?P<body>.*?)\n  \)", _TRANSFORM, re.DOTALL)
        assert body is not None, cte
        assert re.search(re.escape(measure) + r"\s+AS competitor_lien_borrowers", body.group("body"))


def test_the_merge_writes_the_measure_on_update_and_insert() -> None:
    update = _TRANSFORM.split("WHEN MATCHED THEN UPDATE SET", 1)[1].split("WHEN NOT MATCHED", 1)[0]
    insert = _TRANSFORM.split("WHEN NOT MATCHED THEN INSERT", 1)[1]
    assert "competitor_lien_borrowers    = s.competitor_lien_borrowers" in update
    columns, values = insert.split(") VALUES (", 1)
    assert "competitor_lien_borrowers" in columns
    assert "s.competitor_lien_borrowers" in values
    # Positional INSERT: the column and its value sit at the same index.
    column_list = [c.strip() for c in columns.strip(" (\n").split(",")]
    value_list = [v.strip() for v in values.split(")")[0].split(",")]
    assert column_list.index("competitor_lien_borrowers") == value_list.index(
        "s.competitor_lien_borrowers"
    )


def test_003_declares_the_column_a_nullable_int() -> None:
    line = _ddl_line()
    assert re.match(r"\s+competitor_lien_borrowers\s+INT\s+COMMENT '", line)
    assert "NOT NULL" not in line


def test_the_ensure_comment_is_byte_identical_to_003() -> None:
    literal = re.search(r"COMMENT '(?P<text>(?:[^']|'')*)'", _ddl_line())
    assert literal is not None
    assert literal.group("text").replace("''", "'") == (
        job.FUNNEL_SNAPSHOT_COLUMN_COMMENTS["competitor_lien_borrowers"]
    )
    migration = job._build_funnel_snapshot_schema_migration(
        catalog="mip", columns=("competitor_lien_borrowers",)
    )
    assert f"competitor_lien_borrowers INT COMMENT '{literal.group('text')}'" in migration
    assert "ALTER TABLE `mip`.`gold`.`funnel_snapshot_daily`" in migration


def test_the_ensure_step_is_idempotent_when_applied_twice() -> None:
    installed: set[str] = set()
    statements: list[str] = []

    def execute(statement: str) -> list[dict[str, Any]]:
        statements.append(statement)
        if "information_schema" in statement:
            assert "'funnel_snapshot_daily'" in statement
            return [{"column_name": column} for column in sorted(installed)]
        if "ALTER TABLE" in statement:
            installed.add("competitor_lien_borrowers")
            return []
        raise AssertionError(f"unexpected statement: {statement}")

    assert job._ensure_funnel_snapshot_schema(execute, catalog="mip") is True
    assert job._ensure_funnel_snapshot_schema(execute, catalog="mip") is False
    assert sum("ALTER TABLE" in statement for statement in statements) == 1


def test_a_concurrent_add_is_tolerated_and_any_other_failure_raises() -> None:
    installed: set[str] = set()

    def racing(statement: str) -> list[dict[str, Any]]:
        if "information_schema" in statement:
            return [{"column_name": column} for column in sorted(installed)]
        installed.add("competitor_lien_borrowers")  # another run won the race
        raise RuntimeError("[FIELDS_ALREADY_EXISTS] competitor_lien_borrowers")

    assert job._ensure_funnel_snapshot_schema(racing, catalog="mip") is False

    def refused(statement: str) -> list[dict[str, Any]]:
        if "information_schema" in statement:
            return []
        raise PermissionError("refused")

    try:
        job._ensure_funnel_snapshot_schema(refused, catalog="mip")
    except PermissionError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("a non-progressing failure must propagate")


def test_the_migration_refuses_an_unknown_column() -> None:
    try:
        job._build_funnel_snapshot_schema_migration(catalog="mip", columns=("approved_borrowers",))
    except ValueError:
        return
    raise AssertionError("an unregistered column must never be ALTERed")  # pragma: no cover
