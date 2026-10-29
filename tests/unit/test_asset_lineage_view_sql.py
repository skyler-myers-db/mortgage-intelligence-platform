"""The App's observed-lineage view: a least-privilege projection of a system table.

``sql/ddl/audit_asset_lineage_view.sql`` is the only path from the App to
``system.access.table_lineage``, which holds lineage for every table on a
metastore other teams share. These tests pin what the view may expose (four
columns), which rows (edges into the install catalog from the install catalog
or the Cotality share catalog the asset registry names -- never an edge that
carries another catalog's table name), and that ``tools/render_sql.py``
retargets both the view name and the install-catalog filter for a
renamed-catalog install while leaving every other literal alone. The row
contract is evaluated, not only pinned as text: the rendered SELECT runs in
SQLite over a synthetic ``table_lineage``.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from backend.services.asset_registry import ASSET_DESCRIPTORS
from tools.render_sql import _render_text, render, render_for_catalog

REPO = Path(__file__).resolve().parents[2]
VIEW_SQL = REPO / "sql" / "ddl" / "audit_asset_lineage_view.sql"
COLUMNS = ("source_table_full_name", "target_table_full_name", "event_time", "event_date")
SHARE_CATALOG = "cotality_mortgage_data"


def _statement(catalog: str) -> str:
    rendered = render_for_catalog(VIEW_SQL.read_text(encoding="utf-8"), catalog=catalog)
    return "\n".join(line for line in rendered.splitlines() if not line.lstrip().startswith("--"))


def _normalized(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("catalog", ["mip", "summit_mortgage"])
def test_the_view_renders_one_statement_for_the_install_catalog(catalog: str) -> None:
    statement = _statement(catalog).strip()

    assert statement.count(";") == 1 and statement.endswith(";")
    assert statement.startswith(f"CREATE OR REPLACE VIEW {catalog}.audit.asset_lineage (")
    assert "FROM system.access.table_lineage\nWHERE" in statement


@pytest.mark.parametrize("catalog", ["mip", "summit_mortgage"])
def test_the_view_keeps_only_edges_into_the_install_catalog_from_registry_catalogs(
    catalog: str,
) -> None:
    statement = _normalized(_statement(catalog))
    where = statement.split(" WHERE ", 1)[1]

    assert where == (
        f"target_table_catalog = '{catalog}' AND (source_table_catalog = '{catalog}' "
        f"OR source_table_catalog = '{SHARE_CATALOG}');"
    )
    assert " LIKE " not in statement.upper()
    assert " IS NULL" not in statement.upper()


def test_the_view_source_catalogs_are_exactly_the_install_and_the_registry_share() -> None:
    """The view names no catalog the App's asset registry does not render."""
    statement = _normalized(_statement("summit_mortgage"))
    registry_catalogs = {d.catalog_name for d in ASSET_DESCRIPTORS if d.catalog_name}

    assert registry_catalogs == {SHARE_CATALOG}
    assert set(re.findall(r"source_table_catalog = '(\w+)'", statement)) == (
        {"summit_mortgage"} | registry_catalogs
    )
    assert re.findall(r"target_table_catalog = '(\w+)'", statement) == ["summit_mortgage"]


def _other_install(catalog: str) -> str:
    return "summit_mortgage" if catalog == "mip" else "mip"


def _synthetic_edges(catalog: str) -> list[tuple[str | None, str | None, bool]]:
    """``(source, target, exposed)``: every edge shape the system table can hold."""
    other = _other_install(catalog)
    return [
        (f"{catalog}.silver.property_master", f"{catalog}.gold.lead_population", True),
        (f"{SHARE_CATALOG}.corelogic.property", f"{catalog}.silver.property_master", True),
        # Another team reads a product table into its own catalog: their lineage.
        (f"{catalog}.gold.lead_population", "other_team.restricted.payroll_join_q4", False),
        # Another catalog feeds this one: its table name stays in the system table.
        ("other_team.restricted.inputs", f"{catalog}.gold.lead_population", False),
        ("other_team.restricted.a", "other_team.restricted.b", False),
        (f"{SHARE_CATALOG}.corelogic.property", "other_team.restricted.copy", False),
        # Another MIP install on the same metastore, in both directions.
        (f"{other}.gold.lead_population", f"{catalog}.gold.borrower_360", False),
        (f"{catalog}.gold.borrower_360", f"{other}.gold.lead_population", False),
        # Path reads and writes carry no table name on that side.
        (None, f"{catalog}.gold.lead_population", False),
        (f"{catalog}.gold.lead_population", None, False),
    ]


def _catalog_of(name: str | None) -> str | None:
    return None if name is None else name.split(".", 1)[0]


def _rows_through_the_view(catalog: str) -> list[tuple[object, ...]]:
    body = _statement(catalog).split("\nAS\n", 1)[1].strip().rstrip(";")
    assert body.count("FROM system.access.table_lineage") == 1
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE table_lineage (source_table_full_name, source_table_catalog, "
        "target_table_full_name, target_table_catalog, event_time, event_date, created_by)"
    )
    db.executemany(
        "INSERT INTO table_lineage VALUES (?, ?, ?, ?, '2026-10-20 08:00:00', '2026-10-20', "
        "'someone@example.com')",
        [
            (source, _catalog_of(source), target, _catalog_of(target))
            for source, target, _exposed in _synthetic_edges(catalog)
        ],
    )
    db.execute(
        "CREATE VIEW asset_lineage AS "
        + body.replace("FROM system.access.table_lineage", "FROM table_lineage")
    )
    cursor = db.execute("SELECT * FROM asset_lineage")
    assert tuple(column[0] for column in cursor.description) == COLUMNS
    return cursor.fetchall()


@pytest.mark.parametrize("catalog", ["mip", "summit_mortgage"])
def test_the_rendered_view_exposes_no_other_catalogs_table_name(catalog: str) -> None:
    rows = _rows_through_the_view(catalog)

    assert sorted((row[0], row[1]) for row in rows) == sorted(
        (source, target) for source, target, exposed in _synthetic_edges(catalog) if exposed
    )
    readable = {name for row in rows for name in row[:2]}
    assert {_catalog_of(name) for name in readable} == {catalog, SHARE_CATALOG}
    assert all(row[1].startswith(f"{catalog}.") for row in rows)


@pytest.mark.parametrize("catalog", ["mip", "summit_mortgage"])
def test_the_view_projects_exactly_the_four_columns_the_app_reads(catalog: str) -> None:
    statement = _normalized(_statement(catalog))
    select_list = statement.split(" AS SELECT ", 1)[1].split(" FROM ", 1)[0]
    declared = re.findall(r"[(,] (\w+) COMMENT '", statement.split(" AS SELECT ", 1)[0])

    assert tuple(part.strip() for part in select_list.split(",")) == COLUMNS
    assert tuple(declared) == COLUMNS
    for withheld in ("created_by", "entity_id", "statement_id", "source_path", "target_path", "*"):
        assert withheld not in select_list


def test_the_default_catalog_render_is_byte_identical() -> None:
    source = VIEW_SQL.read_text(encoding="utf-8")

    assert render_for_catalog(source, catalog="mip") == source


def test_a_renamed_catalog_leaves_no_default_catalog_reference_behind() -> None:
    rendered = render_for_catalog(VIEW_SQL.read_text(encoding="utf-8"), catalog="summit_mortgage")

    assert "'mip'" not in rendered
    assert re.search(r"\bmip\.", rendered) is None
    assert rendered.count("summit_mortgage.audit.asset_lineage") == 2  # header + DDL
    assert rendered.count("_catalog = 'summit_mortgage'") == 2  # target + source arm
    assert rendered.count(f"source_table_catalog = '{SHARE_CATALOG}'") == 1  # never rewritten


def test_the_catalog_literal_rule_is_a_closed_shape() -> None:
    source = (
        "SELECT * FROM t WHERE source_table_catalog = 'mip' "
        "AND table_catalog = 'mip_app' AND routine_catalog <> 'mip' "
        "AND name = 'mip' AND label IN ('mip') AND TARGET_TABLE_CATALOG='mip'"
    )

    rendered, substitutions = _render_text(source, "summit_mortgage", demo_first_party_enabled=False)

    assert substitutions == 2
    assert rendered == (
        "SELECT * FROM t WHERE source_table_catalog = 'summit_mortgage' "
        "AND table_catalog = 'mip_app' AND routine_catalog <> 'mip' "
        "AND name = 'mip' AND label IN ('mip') AND TARGET_TABLE_CATALOG='summit_mortgage'"
    )


@pytest.mark.parametrize("catalog", ["Summit", "summit-mortgage", "mip; DROP VIEW x", ""])
def test_render_for_catalog_refuses_a_noncanonical_catalog(catalog: str) -> None:
    with pytest.raises(ValueError, match="lowercase unquoted UC identifier"):
        render_for_catalog("SELECT 1", catalog=catalog)


def test_the_bundle_render_tree_carries_the_view(tmp_path: Path) -> None:
    render(
        catalog="summit_mortgage",
        demo_first_party_enabled=False,
        source_root=REPO / "sql",
        dest_root=tmp_path,
    )

    rendered = (tmp_path / "ddl" / "audit_asset_lineage_view.sql").read_text(encoding="utf-8")
    assert rendered == render_for_catalog(
        VIEW_SQL.read_text(encoding="utf-8"), catalog="summit_mortgage"
    )
