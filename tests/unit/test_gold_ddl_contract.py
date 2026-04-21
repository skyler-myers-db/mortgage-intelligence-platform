"""Slice-3 contract test for the gold DDL files.

Mirrors the shape of tests/unit/test_silver_ddl_contract.py but applies to
the six gold DDL files produced in this slice. Asserts:

1. Every gold DDL file exists and is non-trivial (not a placeholder stub).
2. Each file uses `CREATE TABLE IF NOT EXISTS` (idempotent posture).
3. Each file declares a `USING DELTA` storage clause.
4. Each file declares a `CLUSTER BY` (liquid clustering per data-contract).
5. No raw-PII column name is declared in any gold DDL. Governance forbids
   real owner names / street addresses from surfacing at gold.
6. The `003_gold_tables.sql` manifest names every per-file gold DDL either
   in a comment section or via an inline CREATE TABLE IF NOT EXISTS block
   for the same table, so `databricks bundle` sql_tasks that point only at
   the manifest still apply the full schema.
7. The transformation files exist for every gold table and use either
   `CREATE OR REPLACE TABLE` (CTAS) or `MERGE INTO` (idempotent writes).

See docs/data-contract-module0.md §3 for the authoritative column spec;
this test guards against drift at the DDL surface.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = REPO_ROOT / "sql" / "ddl"
TRANSFORM_DIR = REPO_ROOT / "sql" / "transformations"

GOLD_DDL_FILES: tuple[str, ...] = (
    "gold_property_owner_bridge.sql",
    "gold_borrower_360.sql",
    "gold_lead_scores.sql",
    "gold_evidence_events.sql",
    "gold_lead_population.sql",
    "gold_segment_population.sql",
)

GOLD_TRANSFORMATION_FILES: tuple[str, ...] = (
    "gold_property_owner_bridge.sql",
    "gold_borrower_360.sql",
    "gold_lead_scores.sql",
    "gold_evidence_events.sql",
    "gold_lead_population.sql",
    "gold_segment_population.sql",
)

# Target UC paths. The manifest (003_gold_tables.sql) must reference each.
GOLD_TABLE_PATHS: tuple[str, ...] = (
    "mip.gold.property_owner_bridge",
    "mip.gold.borrower_360",
    "mip.gold.lead_scores",
    "mip.gold.evidence_events",
    "mip.gold.lead_population",
    "mip.gold.segment_population",
)

FORBIDDEN_PII_COLUMNS: tuple[str, ...] = (
    "owner_1_full_name",
    "owner_2_full_name",
    "owner_full_name_raw",
    "buyer_1_full_name",
    "buyer_full_name_raw",
    "situs_street_address",
    "situs_street_address_raw",
    "mailing_street_address",
    "mailing_street_raw",
)


def _strip_line_comments(sql_text: str) -> str:
    out: list[str] = []
    for line in sql_text.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _declared_column_names_in_block(block: str) -> set[str]:
    """Extract declared column names from a CREATE TABLE column block.

    Matches lines like `  some_col STRING   NOT NULL COMMENT ...` -- the
    identifier followed by a Spark-SQL type keyword. Backtick-quoted
    identifiers (like `` `timestamp` ``) are supported.
    """
    cols: set[str] = set()
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        m = re.match(
            r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s+"
            r"(?:STRING|BIGINT|INT|DOUBLE|BOOLEAN|TIMESTAMP|DATE|ARRAY<[^>]+>)",
            line,
        )
        if m:
            cols.add(m.group(1).lower())
    return cols


def _create_table_blocks(sql_text: str) -> list[str]:
    """Return every CREATE TABLE (...) column-block body in a SQL file.

    A file can have multiple CREATE TABLE IF NOT EXISTS statements (the
    manifest 003_gold_tables.sql has six). We return a list of bodies, one
    per CREATE TABLE statement.
    """
    cleaned = _strip_line_comments(sql_text)
    bodies: list[str] = []
    for match in re.finditer(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS[^(]+\(", cleaned, re.IGNORECASE
    ):
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
                    bodies.append(cleaned[start:i])
                    break
            i += 1
    return bodies


# ---------------------------------------------------------------------------
# Per-file DDL contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", GOLD_DDL_FILES)
def test_ddl_file_exists_and_nontrivial(name: str) -> None:
    path = DDL_DIR / name
    assert path.exists(), f"missing gold DDL file: {path}"
    text = path.read_text(encoding="utf-8").strip()
    assert len(text) > 200, f"gold DDL looks like a placeholder stub: {path}"


@pytest.mark.parametrize("name", GOLD_DDL_FILES)
def test_ddl_uses_create_table_if_not_exists(name: str) -> None:
    text = (DDL_DIR / name).read_text(encoding="utf-8")
    assert re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", text, re.IGNORECASE), (
        f"{name}: must use `CREATE TABLE IF NOT EXISTS`."
    )


@pytest.mark.parametrize("name", GOLD_DDL_FILES)
def test_ddl_declares_delta_and_cluster_by(name: str) -> None:
    text = (DDL_DIR / name).read_text(encoding="utf-8")
    assert re.search(r"USING\s+DELTA", text, re.IGNORECASE), (
        f"{name}: must declare `USING DELTA`."
    )
    assert re.search(r"CLUSTER\s+BY\s*\(", text, re.IGNORECASE), (
        f"{name}: must declare `CLUSTER BY (...)` (liquid clustering)."
    )


@pytest.mark.parametrize("name", GOLD_DDL_FILES)
def test_no_raw_pii_columns_declared(name: str) -> None:
    text = (DDL_DIR / name).read_text(encoding="utf-8")
    blocks = _create_table_blocks(text)
    assert blocks, f"{name}: could not extract any CREATE TABLE body."
    declared: set[str] = set()
    for b in blocks:
        declared |= _declared_column_names_in_block(b)
    for forbidden in FORBIDDEN_PII_COLUMNS:
        assert forbidden.lower() not in declared, (
            f"{name}: forbidden raw PII column `{forbidden}` declared in "
            f"gold DDL. Governance forbids landing raw names / street "
            f"addresses at gold. See docs/governance-real-data-review.md §1."
        )


# ---------------------------------------------------------------------------
# Manifest covers every gold table
# ---------------------------------------------------------------------------


def test_manifest_references_every_gold_table() -> None:
    """003_gold_tables.sql is the bundle-deploy entry point. It must apply
    (or reference) every gold table so a sql_task pointing at the manifest
    creates the full schema."""
    manifest = (DDL_DIR / "003_gold_tables.sql").read_text(encoding="utf-8")
    missing = [p for p in GOLD_TABLE_PATHS if p not in manifest]
    assert not missing, (
        f"003_gold_tables.sql does not reference these gold tables: "
        f"{missing}. Either inline a CREATE TABLE IF NOT EXISTS block for "
        f"each, or cite the per-file DDL in a comment."
    )


def test_borrower_360_declares_blocked_columns() -> None:
    """Data-contract §9: has_permit and listed_for_sale are BLOCKED FALSE
    until Cotality Permits + MLS land. The columns must still exist on
    gold.borrower_360 so the scoring layer can read a stable value."""
    text = (DDL_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    blocks = _create_table_blocks(text)
    assert blocks
    declared = _declared_column_names_in_block(blocks[0])
    assert "has_permit" in declared, "gold.borrower_360 must declare has_permit (BLOCKED FALSE per §9)."
    assert "listed_for_sale" in declared, "gold.borrower_360 must declare listed_for_sale (BLOCKED FALSE per §9)."


# ---------------------------------------------------------------------------
# Transformations: idempotent pattern (CTAS or MERGE)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", GOLD_TRANSFORMATION_FILES)
def test_transformation_exists_and_idempotent(name: str) -> None:
    path = TRANSFORM_DIR / name
    assert path.exists(), f"missing gold transformation: {path}"
    text = path.read_text(encoding="utf-8")
    assert len(text) > 200, f"gold transformation looks like a placeholder stub: {path}"
    ctas = re.search(r"CREATE\s+OR\s+REPLACE\s+TABLE", text, re.IGNORECASE)
    merge = re.search(r"MERGE\s+INTO", text, re.IGNORECASE)
    assert ctas or merge, (
        f"{name}: must use either `CREATE OR REPLACE TABLE ... AS SELECT` "
        f"(CTAS) or `MERGE INTO` (idempotent writes)."
    )


def test_borrower_360_transformation_forces_blocked_false() -> None:
    """Data-contract §9: the CTAS for borrower_360 must hardcode
    has_permit and listed_for_sale to FALSE on real data (Cotality Permits
    + MLS not yet licensed). Match the literal pattern so a future edit
    that wires real columns silently is flagged."""
    text = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    assert re.search(
        r"CAST\(FALSE\s+AS\s+BOOLEAN\)\s+AS\s+has_permit", text, re.IGNORECASE
    ), "gold.borrower_360 CTAS must hardcode has_permit = FALSE (data-contract §9)."
    assert re.search(
        r"CAST\(FALSE\s+AS\s+BOOLEAN\)\s+AS\s+listed_for_sale", text, re.IGNORECASE
    ), "gold.borrower_360 CTAS must hardcode listed_for_sale = FALSE (data-contract §9)."


def test_evidence_events_excludes_blocked_signal_types() -> None:
    """gold.evidence_events must never emit 'permit' or 'listing' rows on
    real data per data-contract §9."""
    text = (TRANSFORM_DIR / "gold_evidence_events.sql").read_text(encoding="utf-8")
    # Accept the controlled vocab being documented in the header, but the
    # critical assertion is that no row-producing CTE emits those literals
    # as signal_type. We conservatively forbid the string literals
    # `'permit'` and `'listing'` as VALUES (emission), tolerating them in
    # EXCLUSION clauses (`NOT IN (...)`).
    emitted_permit = re.search(r"'permit'\s+AS\s+signal_type", text, re.IGNORECASE)
    emitted_listing = re.search(r"'listing'\s+AS\s+signal_type", text, re.IGNORECASE)
    assert not emitted_permit, (
        "gold.evidence_events emits a 'permit' signal_type row. Permit is "
        "BLOCKED until Cotality Permits lands (data-contract §9)."
    )
    assert not emitted_listing, (
        "gold.evidence_events emits a 'listing' signal_type row. Listing is "
        "BLOCKED until Cotality MLS lands (data-contract §9)."
    )
