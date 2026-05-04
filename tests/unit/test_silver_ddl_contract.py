"""Slice-2 contract test for the five silver DDL files.

Parses every `sql/ddl/silver_*.sql` file (plus the paired transformation
file under `sql/transformations/silver_*.sql`) and asserts:

1. DDL file is non-empty.
2. `CREATE TABLE IF NOT EXISTS` is present (idempotent posture).
3. The 6-state multi-state filter intent
   (``IN ('IL','CA','FL','TX','WA','CO')``) is documented either:
   - in the DDL header comments for the file, OR
   - in the paired transformation file (which carries the actual WHERE
     clause). We check the transformation side because that is where
     the filter is enforced; the DDL itself can only document intent.
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

# The five silver tables landed by Slice 2. `silver_market_rates_weekly`
# belongs to Slice 1 (FRED ingest) and is covered by its own tests; we
# deliberately exclude it here so this file tracks the Slice-2 contract
# independently.
SILVER_DDL_FILES: tuple[str, ...] = (
    "silver_property_master.sql",
    "silver_lien_current.sql",
    "silver_mortgage_events.sql",
    "silver_owner_transfer_events.sql",
    "silver_owner_property_bridge.sql",
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

# The 6-state footprint string. Accepts either single or double quotes and
# tolerates whitespace variations; the canonical form is
# ``IN ('IL','CA','FL','TX','WA','CO')``.
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
def test_six_state_filter_documented_or_enforced(name: str) -> None:
    """Multi-state filter appears either in the DDL header or in the paired
    transformation file (where the actual WHERE clause lives)."""
    ddl_text = _ddl_path(name).read_text(encoding="utf-8")
    transform_path = _transformation_path(name)
    transform_text = transform_path.read_text(encoding="utf-8") if transform_path.exists() else ""

    combined = ddl_text + "\n" + transform_text
    assert SIX_STATE_RE.search(combined), (
        f"{name}: the 6-state filter (IN ('IL','CA','FL','TX','WA','CO')) is "
        f"not documented in the DDL header AND not enforced in the paired "
        f"transformation file {transform_path}. Every silver table must "
        f"carry this filter explicitly."
    )


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
    assert text.count('_zip5("situs_zip_code").alias("situs_zip_code")') >= 2
    assert text.count('@dlt.expect("zip_is_zip5_or_null"') >= 2
