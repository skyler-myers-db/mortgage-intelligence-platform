"""The App's observed-lineage view: a least-privilege projection of a system table.

``sql/ddl/audit_asset_lineage_view.sql`` is the only path from the App to
``system.access.table_lineage``, which holds lineage for every table on a
metastore other teams share. These tests pin what the view may expose (four
columns), which rows (edges whose source or target is in the install catalog),
and that ``tools/render_sql.py`` retargets both the view name and the catalog
filter for a renamed-catalog install while leaving every other literal alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.render_sql import _render_text, render, render_for_catalog

REPO = Path(__file__).resolve().parents[2]
VIEW_SQL = REPO / "sql" / "ddl" / "audit_asset_lineage_view.sql"
COLUMNS = ("source_table_full_name", "target_table_full_name", "event_time", "event_date")


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
def test_the_view_keeps_only_edges_touching_the_install_catalog(catalog: str) -> None:
    statement = _normalized(_statement(catalog))
    where = statement.split(" WHERE ", 1)[1]

    assert where == (
        f"source_table_catalog = '{catalog}' OR target_table_catalog = '{catalog}';"
    )
    # No other catalog -- the Cotality share included -- widens the filter.
    assert "cotality" not in statement.lower()
    assert " LIKE " not in statement.upper()


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
    assert rendered.count("_catalog = 'summit_mortgage'") == 2


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
