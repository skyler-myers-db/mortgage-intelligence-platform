"""Migration inputs: version/compat-tag parsing, the schema-input digest, and apply's use of it."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from jobs import lakebase_migrate, lakebase_migration_contracts, lakebase_migration_ledger
from jobs.lakebase_migration_inputs import (
    SCHEMA_INPUT_COMPONENTS,
    MigrationInputError,
    SchemaMigration,
    parse_schema_migrations,
    render_schema_inputs,
    schema_input_components,
    schema_input_digest,
    validated_digest,
)
from jobs.lakebase_migration_ledger import (
    CATALOG_FINGERPRINT_SQL,
    SEEDED_REFERENCE_TABLES,
    schema_input_digest_sql,
)

ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = (ROOT / "lakebase" / "schema.sql").read_text(encoding="utf-8")
_DIGEST_VERSION = "2026_10_29_deployment_schema_digest"


@pytest.fixture(autouse=True)
def _default_to_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABRICKS_BUNDLE_TARGET", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", raising=False)


def _insert(version: str, description: str = "Test migration") -> str:
    return (
        "INSERT INTO mip_app.schema_migrations (version, description)\n"
        "VALUES (\n"
        f"    '{version}',\n"
        f"    '{description}'\n"
        ")\n"
        "ON CONFLICT (version) DO NOTHING;\n"
    )


def _schema(*parts: str) -> str:
    return "\n".join(parts)


# -- tag parser -------------------------------------------------------------


def test_repo_schema_declares_every_version_and_tags_only_the_reviewed_one() -> None:
    migrations = parse_schema_migrations(_SCHEMA)

    assert len(migrations) == _SCHEMA.count("INSERT INTO mip_app.schema_migrations")
    assert len({migration.version for migration in migrations}) == len(migrations)
    assert {m.version: m.compat for m in migrations if m.tagged} == {_DIGEST_VERSION: "expand"}
    assert all(m.compat == "contract" for m in migrations if not m.tagged)


def test_versions_come_back_in_file_order_including_the_guarded_do_block_insert() -> None:
    versions = [migration.version for migration in parse_schema_migrations(_SCHEMA)]

    # The one ledger insert inside a DO block has no ON CONFLICT clause.
    assert "2026_07_25_campaign_activation_delivery_reproof" in versions
    assert versions.index(_DIGEST_VERSION) == 0


def test_a_missing_tag_counts_as_contract() -> None:
    schema = _schema(
        "-- mip:migration version=2026_01_02_tagged compat=expand",
        _insert("2026_01_01_untagged"),
        _insert("2026_01_02_tagged"),
    )

    assert parse_schema_migrations(schema) == (
        SchemaMigration(version="2026_01_01_untagged", compat="contract", tagged=False),
        SchemaMigration(version="2026_01_02_tagged", compat="expand", tagged=True),
    )


def test_an_explicit_contract_tag_is_recorded_as_tagged() -> None:
    schema = _schema(
        _insert("2026_01_01_drop"), "-- mip:migration version=2026_01_01_drop compat=contract"
    )

    assert parse_schema_migrations(schema) == (
        SchemaMigration(version="2026_01_01_drop", compat="contract", tagged=True),
    )


def test_the_documented_grammar_example_is_not_a_tag() -> None:
    schema = _schema(
        "--   -- mip:migration version=<schema_migrations version> compat=expand|contract",
        _insert("2026_01_01_a"),
    )

    assert parse_schema_migrations(schema)[0].tagged is False


@pytest.mark.parametrize(
    "line",
    (
        "-- mip:migration version=2026_01_01_a compat=Expand",
        "--mip:migration version=2026_01_01_a compat=expand",
        "  -- mip:migration version=2026_01_01_a compat=expand",
        "-- mip:migration version=2026_01_01_a compat=expand reviewed",
        "-- mip:migration version=2026_01_01_a",
        "-- mip:migration compat=expand version=2026_01_01_a",
        "-- mip:migration version=2026_01_01_a compat=additive",
    ),
)
def test_a_malformed_tag_refuses_instead_of_defaulting(line: str) -> None:
    with pytest.raises(MigrationInputError, match="malformed migration compatibility tag"):
        parse_schema_migrations(_schema(line, _insert("2026_01_01_a")))


def test_a_tag_naming_no_declared_version_refuses() -> None:
    schema = _schema(
        "-- mip:migration version=2026_01_09_typo compat=expand", _insert("2026_01_01_a")
    )

    with pytest.raises(MigrationInputError, match="names no declared version"):
        parse_schema_migrations(schema)


def test_two_tags_for_one_version_refuse() -> None:
    schema = _schema(
        "-- mip:migration version=2026_01_01_a compat=expand",
        "-- mip:migration version=2026_01_01_a compat=expand",
        _insert("2026_01_01_a"),
    )

    with pytest.raises(MigrationInputError, match="two compatibility tags"):
        parse_schema_migrations(schema)


def test_a_version_declared_twice_refuses() -> None:
    with pytest.raises(MigrationInputError, match="declared twice"):
        parse_schema_migrations(_schema(_insert("2026_01_01_a"), _insert("2026_01_01_a")))


@pytest.mark.parametrize(
    "statement",
    (
        "INSERT INTO mip_app.schema_migrations (version, description)\n"
        "VALUES ('2026_01_01_a', 'one'), ('2026_01_01_b', 'two');\n",
        "INSERT INTO mip_app.schema_migrations (version, description) "
        "VALUES ('2026_01_01_a', 'one');\n",
        "INSERT INTO mip_app.schema_migrations (description, version)\n"
        "VALUES (\n    'one',\n    '2026_01_01_a'\n);\n",
    ),
    ids=("multi-row", "single-line", "reordered-columns"),
)
def test_an_unreviewed_insert_shape_refuses_rather_than_hiding_a_version(statement: str) -> None:
    with pytest.raises(MigrationInputError, match="outside the reviewed single-row shape"):
        parse_schema_migrations(_schema(_insert("2026_01_01_z"), statement))


def test_a_malformed_version_refuses() -> None:
    with pytest.raises(MigrationInputError, match="malformed"):
        parse_schema_migrations(_insert("NotADatedVersion"))


# -- schema-input digest ----------------------------------------------------

_TEXTS = ("pre-seed schema", "seed", "tenant seed", "post-seed schema")


def test_digest_is_deterministic_hex() -> None:
    digest = schema_input_digest(_TEXTS)

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert schema_input_digest(list(_TEXTS)) == digest


@pytest.mark.parametrize("index", range(4))
def test_digest_binds_each_sql_text(index: int) -> None:
    changed = list(_TEXTS)
    changed[index] += " "

    assert schema_input_digest(changed) != schema_input_digest(_TEXTS)


def test_digest_frames_texts_so_moved_bytes_change_it() -> None:
    moved = ("pre-seed schem", "aseed", "tenant seed", "post-seed schema")

    assert "".join(moved) == "".join(_TEXTS)
    assert schema_input_digest(moved) != schema_input_digest(_TEXTS)


@pytest.mark.parametrize(
    ("matrix", "key", "value"),
    (
        ("_APP_ROLE_TABLE_PRIVILEGES", "campaigns", ("SELECT",)),
        ("_APP_ROLE_SEQUENCE_PRIVILEGES", "action_audit_audit_sequence_seq", ()),
        ("_APP_ROLE_ROUTINE_PRIVILEGES", ("campaign_criteria_is_reviewed", "jsonb"), ()),
    ),
)
def test_digest_binds_the_grant_matrix(
    monkeypatch: pytest.MonkeyPatch,
    matrix: str,
    key: Any,
    value: tuple[str, ...],
) -> None:
    before = schema_input_digest(_TEXTS)
    monkeypatch.setitem(getattr(lakebase_migration_contracts, matrix), key, value)

    assert schema_input_digest(_TEXTS) != before


def test_digest_binds_the_verifier_privileges(monkeypatch: pytest.MonkeyPatch) -> None:
    before = schema_input_digest(_TEXTS)
    monkeypatch.setattr(
        "jobs.lakebase_migration_inputs._AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES", ("SELECT",)
    )

    assert schema_input_digest(_TEXTS) != before


def test_digest_requires_exactly_the_four_apply_texts() -> None:
    with pytest.raises(MigrationInputError, match="needs 4 SQL texts"):
        schema_input_digest(_TEXTS[:3])


@pytest.mark.parametrize("index", range(4))
def test_each_text_moves_exactly_its_own_component(index: int) -> None:
    changed = list(_TEXTS)
    changed[index] += " "
    owner = ("schema", "seed", "tenant_disclosure_seed", "schema")[index]

    before, after = schema_input_components(_TEXTS), schema_input_components(changed)

    assert tuple(before) == SCHEMA_INPUT_COMPONENTS
    assert [name for name in SCHEMA_INPUT_COMPONENTS if before[name] != after[name]] == [owner]


def test_a_grant_matrix_change_moves_only_the_grant_matrix_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = schema_input_components(_TEXTS)
    monkeypatch.setitem(lakebase_migration_contracts._APP_ROLE_TABLE_PRIVILEGES, "campaigns", ())
    after = schema_input_components(_TEXTS)

    assert [name for name in SCHEMA_INPUT_COMPONENTS if before[name] != after[name]] == [
        "grant_matrix"
    ]


def test_component_digests_are_domain_separated_from_each_other_and_the_input_digest() -> None:
    same = ("x", "x", "x", "x")
    components = schema_input_components(same)

    assert len({*components.values(), schema_input_digest(same)}) == 5


def test_digest_sql_upserts_the_single_scoped_row_with_the_catalog_fingerprint() -> None:
    statement = schema_input_digest_sql(_TEXTS)
    components = schema_input_components(_TEXTS)
    values = ", ".join(
        f"'{digest}'"
        for digest in (
            schema_input_digest(_TEXTS),
            *(components[name] for name in SCHEMA_INPUT_COMPONENTS),
        )
    )

    assert f"SELECT 'lakebase_schema', {values}, fingerprint.value" in statement
    assert f"FROM ({CATALOG_FINGERPRINT_SQL}) AS fingerprint(value)" in statement
    assert (
        "INSERT INTO mip_app.deployment_schema_digest (\n    scope, input_digest, schema_digest, "
        "seed_digest, tenant_disclosure_seed_digest, grant_matrix_digest, catalog_fingerprint\n)"
    ) in statement
    assert "ON CONFLICT (scope) DO UPDATE" in statement
    for column in ("input_digest", "grant_matrix_digest", "catalog_fingerprint"):
        assert f"{column} = EXCLUDED.{column}," in statement


@pytest.mark.parametrize("value", ("", "ABC", "0" * 63, "g" * 64, "0" * 64 + "'; DROP TABLE x; --"))
def test_digest_sql_renders_only_a_validated_digest(value: str) -> None:
    with pytest.raises(MigrationInputError, match="64 lowercase hex"):
        validated_digest(value)


def test_the_digest_row_columns_match_the_recorded_read_and_the_components() -> None:
    table = _SCHEMA[
        _SCHEMA.index("CREATE TABLE IF NOT EXISTS mip_app.deployment_schema_digest (") :
    ]
    table = table[: table.index(");")]
    columns = re.findall(r"^    ([a-z_]+)\s", table, re.MULTILINE)
    read = lakebase_migration_ledger._RECORDED_DIGEST_SQL

    expected = [f"{name}_digest" for name in SCHEMA_INPUT_COMPONENTS]
    assert columns == ["scope", "input_digest", *expected, "catalog_fingerprint", "recorded_at"]
    assert re.findall(r"^    ([a-z_]+),?$", read, re.MULTILINE) == [
        "input_digest",
        *expected,
        "catalog_fingerprint",
    ]


# -- catalog fingerprint ----------------------------------------------------


def test_the_fingerprint_hashes_exactly_the_reviewed_seeded_reference_tables() -> None:
    hashed = re.findall(r"FROM mip_app\.([a-z_]+) seeded", CATALOG_FINGERPRINT_SQL)
    labelled = re.findall(r"'row\|([a-z_]+)\|%L'", CATALOG_FINGERPRINT_SQL)

    assert hashed == labelled == list(SEEDED_REFERENCE_TABLES)
    # Rows the App could write are runtime state, not drift: each table here
    # is one the App only reads, so only apply's texts change its rows.
    for table in SEEDED_REFERENCE_TABLES:
        assert lakebase_migration_contracts._APP_ROLE_TABLE_PRIVILEGES[table] == ("SELECT",)


def test_every_seeded_reference_table_is_written_by_the_apply_texts() -> None:
    _schema_sql, sql_texts = render_schema_inputs(
        ROOT, lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
    )
    seeded = set(re.findall(r"INSERT INTO mip_app\.([a-z_]+)", "\n".join(sql_texts)))

    assert set(SEEDED_REFERENCE_TABLES) <= seeded


def test_the_fingerprint_reads_no_search_path_or_datestyle_dependent_rendering() -> None:
    """Apply computes it under schema.sql's search_path, plan under the default."""

    lowered = CATALOG_FINGERPRINT_SQL.lower()
    for rendering in (
        "pg_get_",
        "::regclass",
        "::regproc",
        "::regtype",
        "format_type",
        "oidvectortypes",
    ):
        assert rendering not in lowered
    # Timestamps render per TimeZone; the seeded rows drop them.
    assert lowered.count("- array['created_at', 'updated_at']") == len(SEEDED_REFERENCE_TABLES)


def test_schema_declares_the_digest_table_before_the_first_seed_dependent_block() -> None:
    pre_seed, _post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)

    assert "CREATE TABLE IF NOT EXISTS mip_app.deployment_schema_digest (" in pre_seed
    assert pre_seed.index("mip_app.deployment_schema_digest") < pre_seed.index(
        "CREATE TABLE IF NOT EXISTS mip_app.campaigns ("
    )
    assert lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES["deployment_schema_digest"] == ()


# -- apply path -------------------------------------------------------------


def test_apply_appends_the_digest_row_to_the_unchanged_schema_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda: {"host": "test"})
    monkeypatch.setattr(
        lakebase_migrate,
        "_preflight_database_roles",
        lambda _kwargs, **_options: ("app-role", None),
    )

    def _run_transaction(sql_texts: tuple[str, ...], _kwargs: dict, **options: Any) -> None:
        captured["sql_texts"] = sql_texts
        captured["options"] = options

    def _apply_grants(_kwargs: dict, **options: Any) -> None:
        captured["grants"] = options

    monkeypatch.setattr(lakebase_migrate, "_run_transaction", _run_transaction)
    monkeypatch.setattr(lakebase_migrate, "_apply_app_role_grants", _apply_grants)

    lakebase_migrate.main()

    # The four texts apply has always sent, built here independently of main.
    seed = (ROOT / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8")
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)
    tenant_seed = lakebase_migrate._tenant_disclosure_seed_sql(
        lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
    )
    expected = (pre_seed, seed, tenant_seed, post_seed)
    assert captured["sql_texts"][:4] == expected
    assert captured["sql_texts"][4] == schema_input_digest_sql(expected)
    assert len(captured["sql_texts"]) == 5
    assert captured["options"] == {
        "app_role": "app-role",
        "ai_gateway_verifier_role": None,
        "verify_outreach_integrity": True,
    }
    assert captured["grants"] == {"resolved_roles": ("app-role", None)}


def test_plan_renders_exactly_the_texts_apply_executes() -> None:
    schema_sql, sql_texts = render_schema_inputs(
        ROOT, lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
    )
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)

    assert schema_sql == _SCHEMA
    assert sql_texts[0] == pre_seed and sql_texts[3] == post_seed
    assert sql_texts[1] == (ROOT / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8")
    assert sql_texts[2] == lakebase_migrate._tenant_disclosure_seed_sql(
        lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
    )
