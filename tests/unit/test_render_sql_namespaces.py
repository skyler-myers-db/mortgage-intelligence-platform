from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.render_sql import _render_text, main, render

REPO = Path(__file__).resolve().parents[2]
TARGET = "mip_pr105_staging"
SOURCE_FILES = (
    "sql/ddl/001_catalogs_schemas.sql",
    "sql/ddl/003_gold_tables.sql",
    "sql/ddl/004_ref_tables.sql",
    "sql/ddl/refresh_run_state.sql",
    "sql/transformations/capture_refresh_timestamp.sql",
)


def _render(path: str, catalog: str = TARGET) -> str:
    source = (REPO / path).read_text(encoding="utf-8")
    return _render_text(source, catalog, demo_first_party_enabled=False)[0]


def test_custom_catalog_rewrites_catalog_schema_and_table_ddl_together() -> None:
    rendered = _render("sql/ddl/001_catalogs_schemas.sql")

    assert f"CREATE CATALOG IF NOT EXISTS {TARGET}" in rendered
    for schema in (
        "raw",
        "silver",
        "first_party",
        "gold",
        "semantics",
        "app",
        "audit",
    ):
        assert f"CREATE SCHEMA IF NOT EXISTS {TARGET}.{schema}" in rendered
    assert f"CREATE TABLE IF NOT EXISTS {TARGET}.audit.campaign_treatment_snapshot" in rendered
    assert "CREATE CATALOG IF NOT EXISTS mip\n" not in rendered
    assert (
        re.search(
            r"^\s*CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+mip\.",
            rendered,
            re.IGNORECASE | re.MULTILINE,
        )
        is None
    )


def test_custom_catalog_rewrites_every_standalone_schema_bootstrap() -> None:
    expectations = {
        "sql/ddl/003_gold_tables.sql": "first_party",
        "sql/ddl/004_ref_tables.sql": "ref",
        "sql/ddl/refresh_run_state.sql": "ref",
        "sql/transformations/capture_refresh_timestamp.sql": "ref",
    }

    for path, schema in expectations.items():
        rendered = _render(path)
        assert f"CREATE SCHEMA IF NOT EXISTS {TARGET}.{schema}" in rendered
        assert f"CREATE TABLE IF NOT EXISTS {TARGET}.{schema}." in rendered
        assert f"CREATE SCHEMA IF NOT EXISTS mip.{schema}" not in rendered


def test_default_catalog_render_remains_byte_identical_for_namespace_sources() -> None:
    for path in SOURCE_FILES:
        source = (REPO / path).read_text(encoding="utf-8")
        assert _render_text(source, "mip", demo_first_party_enabled=False)[0] == source


def test_namespace_rewrite_is_limited_to_exact_create_ddl_contexts() -> None:
    source = """\
-- CREATE SCHEMA IF NOT EXISTS mip.audit
SELECT 'CREATE CATALOG IF NOT EXISTS mip';
CREATE SCHEMA IF NOT EXISTS mip.audit;
CREATE TABLE IF NOT EXISTS mip.audit.events (id STRING);
"""

    rendered, substitutions = _render_text(source, TARGET, demo_first_party_enabled=False)

    assert "-- CREATE SCHEMA IF NOT EXISTS mip.audit" in rendered
    assert "'CREATE CATALOG IF NOT EXISTS mip'" in rendered
    assert f"CREATE SCHEMA IF NOT EXISTS {TARGET}.audit" in rendered
    assert f"CREATE TABLE IF NOT EXISTS {TARGET}.audit.events" in rendered
    assert substitutions == 2


@pytest.mark.parametrize("catalog", ["MIP_CUSTOMER", "m" * 256, "mip-customer"])
def test_renderer_rejects_noncanonical_uc_catalogs(catalog: str) -> None:
    assert main(["--catalog", catalog]) == 2


def test_custom_destination_never_rerenders_existing_artifact_tree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sql"
    canonical = source / "ddl" / "catalog.sql"
    stale_rendered = source / "_rendered" / "ddl" / "catalog.sql"
    custom_destination = tmp_path / "custom-output"
    canonical.parent.mkdir(parents=True)
    stale_rendered.parent.mkdir(parents=True)
    canonical.write_text("CREATE CATALOG IF NOT EXISTS mip;\n", encoding="utf-8")
    stale_rendered.write_text("CREATE CATALOG IF NOT EXISTS stale_catalog;\n", encoding="utf-8")

    result = render(
        catalog=TARGET,
        demo_first_party_enabled=False,
        source_root=source,
        dest_root=custom_destination,
    )

    assert result == (1, 1, 1)
    assert (custom_destination / "ddl" / "catalog.sql").read_text(
        encoding="utf-8"
    ) == f"CREATE CATALOG IF NOT EXISTS {TARGET};\n"
    assert not (custom_destination / "_rendered").exists()
