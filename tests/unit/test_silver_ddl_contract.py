"""Contract tests for the silver DDL files.

Parses every `sql/ddl/silver_*.sql` file (plus the paired transformation
file under `sql/transformations/silver_*.sql`) and asserts:

1. DDL file is non-empty.
2. `CREATE TABLE IF NOT EXISTS` is present (idempotent posture).
3. No transformation hard-codes the current demo/evaluation state list.
   Silver keeps every source row with a non-null state so the product can
   adapt when the Cotality footprint changes.
4. No raw PII column names appear anywhere in the silver DDL. Governance
   review (docs/governance-real-data-review.md §1) forbids landing these
   in silver when gold will not mask them. Forbidden set below.
5. Every silver DDL names a reasonable PK-grain column in the first few
   declared columns: `clip` for CLIP-grain tables,
   `mortgage_txn_id` / `transfer_txn_id` (composite-txn equivalents) for
   event-grain tables, `owner_link_id` for the owner rollup bridge.

See docs/module0-real-data-plan.md for slice ownership and
docs/data-contract-module0.md §2 for the authoritative column spec.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = REPO_ROOT / "sql" / "ddl"
TRANSFORM_DIR = REPO_ROOT / "sql" / "transformations"
PIPELINE_PATH = REPO_ROOT / "pipelines" / "lakeflow" / "mip_feature_pipeline.py"

# CLIP/event-grain Cotality silver lifts. `silver_market_rates_weekly`
# belongs to the FRED ingest path and is covered by its own tests.
SILVER_DDL_FILES: tuple[str, ...] = (
    "silver_property_master.sql",
    "silver_lien_current.sql",
    "silver_mortgage_events.sql",
    "silver_owner_transfer_events.sql",
    "silver_owner_property_bridge.sql",
    "silver_listing_activity.sql",
    "silver_heloc_propensity.sql",
    "silver_refi_propensity.sql",
)

# Expected PK-grain column per table. The contract check asserts the column
# appears within the first 6 declared columns of the CREATE TABLE body (not
# just somewhere in the file), so a stray mention in a comment doesn't count.
PK_EXPECTATIONS: dict[str, tuple[str, ...]] = {
    "silver_property_master.sql":      ("clip",),
    "silver_lien_current.sql":         ("clip",),
    "silver_mortgage_events.sql":      ("mortgage_txn_id", "clip"),
    "silver_owner_transfer_events.sql": ("transfer_txn_id", "clip"),
    "silver_owner_property_bridge.sql": ("owner_link_id",),
    "silver_listing_activity.sql":     ("clip",),
    "silver_heloc_propensity.sql":     ("clip",),
    "silver_refi_propensity.sql":      ("clip",),
}

# Forbidden raw-PII column names. If any of these appear in a silver DDL
# CREATE TABLE body, the test fails -- governance review forbids landing
# real names / street addresses in silver when gold is not configured to
# mask them. Names are checked case-insensitively against the DDL body.
FORBIDDEN_PII_COLUMNS: tuple[str, ...] = (
    "owner_1_full_name",
    "owner_1_last_name",
    "owner_2_full_name",
    "owner_2_last_name",
    "buyer_1_full_name",
    "buyer_1_last_name",
    "situs_street_address",
    "mailing_street_address",
    # Data-contract §2.2 once named `situs_street_address_raw` / `owner_full_
    # name_raw` / `buyer_full_name_raw` / `mailing_street_raw` as silver PII
    # columns; governance review blocked landing them. Check here so a
    # future edit that tries to re-introduce them is caught at CI time.
    "situs_street_address_raw",
    "owner_full_name_raw",
    "buyer_full_name_raw",
    "mailing_street_raw",
)

# Hard-coded evaluation state filters are forbidden in the product pipeline.
SIX_STATE_RE = re.compile(
    r"IN\s*\(\s*['\"]IL['\"]\s*,\s*"
    r"['\"]CA['\"]\s*,\s*"
    r"['\"]FL['\"]\s*,\s*"
    r"['\"]TX['\"]\s*,\s*"
    r"['\"]WA['\"]\s*,\s*"
    r"['\"]CO['\"]\s*\)",
    re.IGNORECASE,
)


def _ddl_path(name: str) -> Path:
    return DDL_DIR / name


def _transformation_path(name: str) -> Path:
    return TRANSFORM_DIR / name


def _strip_line_comments(sql_text: str) -> str:
    """Drop SQL line-comments (-- ...) so they can't confuse our parenthesis
    counter. We keep the original newlines so downstream line-based column
    extraction still works."""
    out_lines: list[str] = []
    for line in sql_text.splitlines():
        # Find an unquoted `--` and truncate. Our DDLs don't quote '--' inside
        # strings, so a plain find is safe here.
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out_lines.append(line)
    return "\n".join(out_lines)


def _create_table_body(sql_text: str) -> str:
    """Extract the CREATE TABLE body (between the first '(' and matching ')')
    after stripping line-comments.

    Not a full SQL parser -- we want the column-declaration block, which is
    the parenthesized list that follows CREATE TABLE IF NOT EXISTS <name>.
    We count parentheses to handle COMMENT strings that themselves contain
    parentheses.
    """
    cleaned = _strip_line_comments(sql_text)
    match = re.search(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS[^(]+\(", cleaned, re.IGNORECASE
    )
    if not match:
        return ""
    start = match.end()
    depth = 1
    i = start
    while i < len(cleaned) and depth > 0:
        ch = cleaned[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return cleaned[start:i]
        i += 1
    return ""


def _first_n_column_names(body: str, n: int = 6) -> list[str]:
    """Return the first N column names from a CREATE TABLE body.

    A column line starts at the beginning of a line (after stripping leading
    whitespace) and matches an identifier followed by a type keyword.
    """
    cols: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(?:STRING|BIGINT|INT|DOUBLE|BOOLEAN|TIMESTAMP|DATE)", line)
        if m:
            cols.append(m.group(1))
            if len(cols) >= n:
                break
    return cols


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SILVER_DDL_FILES)
def test_ddl_file_non_empty(name: str) -> None:
    path = _ddl_path(name)
    assert path.exists(), f"missing DDL file: {path}"
    text = path.read_text(encoding="utf-8").strip()
    assert text, f"DDL file is empty: {path}"
    # Not just a 1-line placeholder like `# Placeholder` or similar.
    assert len(text) > 200, f"DDL file looks like a placeholder stub: {path}"


@pytest.mark.parametrize("name", SILVER_DDL_FILES)
def test_ddl_file_uses_create_table_if_not_exists(name: str) -> None:
    text = _ddl_path(name).read_text(encoding="utf-8")
    assert re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", text, re.IGNORECASE), (
        f"{name}: must use `CREATE TABLE IF NOT EXISTS` (idempotent posture)."
    )


@pytest.mark.parametrize("name", SILVER_DDL_FILES)
def test_silver_transformations_do_not_hardcode_demo_state_filter(name: str) -> None:
    """Silver must discover source coverage from data, not from a demo tuple."""
    ddl_text = _ddl_path(name).read_text(encoding="utf-8")
    transform_path = _transformation_path(name)
    transform_text = transform_path.read_text(encoding="utf-8") if transform_path.exists() else ""

    combined = ddl_text + "\n" + transform_text
    assert SIX_STATE_RE.search(combined) is None, (
        f"{name}: found the old hard-coded evaluation state filter. Silver must "
        f"retain all non-null source states so future Cotality coverage changes "
        f"do not require a code change."
    )

    if name in {
        "silver_property_master.sql",
        "silver_lien_current.sql",
        "silver_owner_property_bridge.sql",
    }:
        assert "situs_state IS NOT NULL" in transform_text
    elif name in {
        "silver_mortgage_events.sql",
        "silver_owner_transfer_events.sql",
    }:
        assert "deed_situs_state_static IS NOT NULL" in transform_text
    else:
        assert "RLIKE '^[A-Z]{2}$'" in transform_text


def _declared_column_names(body: str) -> list[str]:
    """Return every declared column name in a CREATE TABLE body.

    A column line starts at the beginning of a line (after stripping leading
    whitespace) and matches an identifier followed by a type keyword. This
    deliberately ignores identifiers inside COMMENT string literals, which
    may legitimately reference forbidden PII column names in a documentation
    context (e.g. "hashed from owner_1_full_name").
    """
    cols: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(?:STRING|BIGINT|INT|DOUBLE|BOOLEAN|TIMESTAMP|DATE)", line)
        if m:
            cols.append(m.group(1).lower())
    return cols


@pytest.mark.parametrize("name", SILVER_DDL_FILES)
def test_no_raw_pii_columns_in_silver_ddl(name: str) -> None:
    body = _create_table_body(_ddl_path(name).read_text(encoding="utf-8"))
    assert body, f"{name}: could not extract CREATE TABLE body."
    # Check declared column names only. COMMENT string literals are allowed
    # to reference forbidden names in a documentation context (e.g. explaining
    # that `owner_name_hash` is derived from `owner_1_full_name` at ingest).
    declared = set(_declared_column_names(body))
    for forbidden in FORBIDDEN_PII_COLUMNS:
        assert forbidden.lower() not in declared, (
            f"{name}: forbidden raw PII column `{forbidden}` declared in "
            f"silver DDL. Governance review forbids landing raw names / "
            f"street addresses at silver unless gold masks them. See "
            f"docs/governance-real-data-review.md §1."
        )


@pytest.mark.parametrize("name", SILVER_DDL_FILES)
def test_pk_grain_column_in_first_few_columns(name: str) -> None:
    body = _create_table_body(_ddl_path(name).read_text(encoding="utf-8"))
    first_six = _first_n_column_names(body, n=6)
    expected = PK_EXPECTATIONS[name]
    # At least one of the expected PK-grain columns must appear in the first
    # six declared columns. (CLIP-grain tables have `clip` first; event-grain
    # tables lead with the composite txn id, then carry clip as a join key.)
    match = any(col in first_six for col in expected)
    assert match, (
        f"{name}: expected one of {expected} within the first six declared "
        f"columns, but got {first_six!r}."
    )


def test_all_five_silver_ddl_files_exist() -> None:
    """Guard rail: if a future edit removes one of the five silver DDL files,
    fail loudly here instead of silently skipping parametrized cases."""
    missing = [name for name in SILVER_DDL_FILES if not _ddl_path(name).exists()]
    assert not missing, f"missing Slice-2 silver DDL file(s): {missing}"


def test_all_five_transformation_files_exist() -> None:
    """Paired transformation files must exist alongside the DDL."""
    missing = [
        name for name in SILVER_DDL_FILES if not _transformation_path(name).exists()
    ]
    assert not missing, (
        f"missing Slice-2 silver transformation file(s): {missing}. The DDL "
        f"declares the schema; the transformation populates it."
    )


def test_lakeflow_pipeline_normalizes_zip5_for_live_silver_path() -> None:
    """Live silver refresh uses Lakeflow, so the DLT path must enforce ZIP5.

    The warehouse MERGE SQL already truncates ZIP+4. This guard prevents the
    pipeline implementation from drifting and reintroducing 9-digit ZIPs.
    """
    text = PIPELINE_PATH.read_text(encoding="utf-8")
    assert "def _zip5" in text
    assert "LEADING_ZERO_ZIP_STATES" in text
    assert "F.lpad(digits, 5, \"0\")" in text
    assert text.count('_zip5("situs_zip_code").alias("situs_zip_code")') >= 2
    assert text.count('length(situs_zip_code) = 5') >= 2


def test_lakeflow_pipeline_coerces_owner_corporate_indicator_as_yn_string() -> None:
    """DLT path must match the warehouse property_master corporate flag rule."""
    text = PIPELINE_PATH.read_text(encoding="utf-8")
    assert "def _y_flag" in text
    assert 'F.col(col_name).cast("string")' in text
    assert 'F.lit("Y")' in text
    assert '_y_flag("owner_1_corporate_indicator").alias("owner_is_corporate")' in text
    assert 'F.coalesce(F.col("owner_1_corporate_indicator"), F.lit(0))' not in text


def test_property_master_ddl_documents_owner_corporate_indicator_as_yn_string() -> None:
    """Schema comments must not contradict the Y/N coercion contract."""
    text = _ddl_path("silver_property_master.sql").read_text(encoding="utf-8")
    assert "Corporate-owner flag from Y/N indicator" in text
    assert "owner_1_corporate_indicator (STRING Y/N)" in text
    assert "owner_1_corporate_indicator (BIGINT 1/0)" not in text
    assert "Corporate-owner flag from 1/0 indicator" not in text


def test_warehouse_silver_zip_normalization_rejects_short_non_zip5_fragments() -> None:
    """Warehouse MERGE path must not leak 4-digit fragments into gold."""
    for name in ("silver_property_master.sql", "silver_lien_current.sql"):
        text = _transformation_path(name).read_text(encoding="utf-8")
        assert "LENGTH(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', '')) >= 5" in text
        assert "LPAD(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 5, '0')" in text
        assert "UPPER(TRIM(situs_state)) IN ('CT','MA','ME','NH','NJ','RI','VT','PR','VI')" in text
        assert "ELSE NULL" in text
