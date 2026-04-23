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
    # slice13-accuracy-validation: geography rollups for the USChoroplethMap drill.
    "gold_county_rollup.sql",
    "gold_zip_rollup.sql",
    "gold_state_top_segment.sql",
)

GOLD_TRANSFORMATION_FILES: tuple[str, ...] = (
    "gold_property_owner_bridge.sql",
    "gold_borrower_360.sql",
    "gold_lead_scores.sql",
    "gold_evidence_events.sql",
    "gold_lead_population.sql",
    "gold_segment_population.sql",
    "gold_county_rollup.sql",
    "gold_zip_rollup.sql",
    "gold_state_top_segment.sql",
)

# Target UC paths. The manifest (003_gold_tables.sql) must reference each.
# Slice13-accuracy perf: `mip.gold.borrower_dossier` is a pre-joined superset
# of borrower_360 + top-20 evidence events per CLIP, backing the
# /api/borrowers/{id} read path. The DDL manifest must declare it so
# `bundle deploy -t dev` provisions it for every client.
GOLD_TABLE_PATHS: tuple[str, ...] = (
    "mip.gold.property_owner_bridge",
    "mip.gold.borrower_360",
    "mip.gold.lead_scores",
    "mip.gold.evidence_events",
    "mip.gold.lead_population",
    "mip.gold.segment_population",
    "mip.gold.borrower_dossier",
    "mip.gold.county_rollup",
    "mip.gold.zip_rollup",
    "mip.gold.state_top_segment",
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


# ---------------------------------------------------------------------------
# Audit-holes-round-3 #7 + #8: deterministic refresh_at + freshness sentinel
# ---------------------------------------------------------------------------


# Every CTAS in mip_refresh_scores that writes a `refreshed_at` or
# `snapshot_at` column MUST source it from mip.ref.refresh_run_state rather
# than a per-task CURRENT_TIMESTAMP(). The only SQL file on the gold-refresh
# path allowed to call CURRENT_TIMESTAMP() is the seed task
# (capture_refresh_timestamp.sql).
_TIMESTAMP_SHARED_CTAS_FILES: tuple[str, ...] = (
    "gold_property_owner_bridge.sql",
    "gold_borrower_360.sql",
    "gold_lead_scores.sql",
    "gold_segment_population.sql",
    "gold_lockin_cohort.sql",
    "gold_borrower_dossier.sql",
    "gold_county_rollup.sql",
    "gold_zip_rollup.sql",
)


@pytest.mark.parametrize("name", _TIMESTAMP_SHARED_CTAS_FILES)
def test_ctas_reads_shared_refresh_at(name: str) -> None:
    """Every gold CTAS in mip_refresh_scores must pull its refresh/snapshot
    timestamp from mip.ref.refresh_run_state, not call CURRENT_TIMESTAMP()
    in-line. Fixes audit-holes-round-3 #7 (per-task drift)."""
    text = (TRANSFORM_DIR / name).read_text(encoding="utf-8")
    cleaned = _strip_line_comments(text)
    # Must reference the shared anchor at least once.
    assert "mip.ref.refresh_run_state" in cleaned, (
        f"{name}: must read refresh_at from mip.ref.refresh_run_state "
        f"instead of calling CURRENT_TIMESTAMP() per-task."
    )
    # And must NOT call CURRENT_TIMESTAMP() in the body (comments stripped).
    assert not re.search(r"CURRENT_TIMESTAMP\s*\(", cleaned, re.IGNORECASE), (
        f"{name}: CURRENT_TIMESTAMP() is forbidden on the gold-refresh path. "
        f"Use (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY "
        f"captured_at DESC LIMIT 1) instead."
    )


def test_capture_refresh_timestamp_seed_exists() -> None:
    """The seed task is the ONE place CURRENT_TIMESTAMP() lives on the
    gold-refresh path. It must exist and must INSERT into
    mip.ref.refresh_run_state."""
    path = TRANSFORM_DIR / "capture_refresh_timestamp.sql"
    assert path.exists(), "missing capture_refresh_timestamp.sql (audit-holes-round-3 #7)."
    text = path.read_text(encoding="utf-8")
    assert re.search(
        r"INSERT\s+INTO\s+mip\.ref\.refresh_run_state", text, re.IGNORECASE
    ), "capture_refresh_timestamp.sql must INSERT a row into mip.ref.refresh_run_state."
    assert re.search(r"CURRENT_TIMESTAMP\s*\(", text, re.IGNORECASE), (
        "capture_refresh_timestamp.sql must call CURRENT_TIMESTAMP() "
        "exactly once -- it is the anchor every other CTAS reads."
    )


def test_assert_borrower_360_fresh_sentinel_exists() -> None:
    """The freshness sentinel must exist and must compare borrower_360's
    MAX(refreshed_at) against the run's refresh_at. Fixes audit-holes-
    round-3 #8 (stale-population drift)."""
    path = TRANSFORM_DIR / "assert_borrower_360_fresh.sql"
    assert path.exists(), "missing assert_borrower_360_fresh.sql (audit-holes-round-3 #8)."
    text = path.read_text(encoding="utf-8")
    assert re.search(r"RAISE_ERROR", text, re.IGNORECASE), (
        "assert_borrower_360_fresh.sql must call RAISE_ERROR on staleness."
    )
    assert re.search(r"mip\.gold\.borrower_360", text, re.IGNORECASE), (
        "assert_borrower_360_fresh.sql must read mip.gold.borrower_360."
    )
    assert re.search(r"mip\.ref\.refresh_run_state", text, re.IGNORECASE), (
        "assert_borrower_360_fresh.sql must compare against the run's "
        "refresh_at captured in mip.ref.refresh_run_state."
    )


def test_sentinel_wired_in_bundle_definitions() -> None:
    """Both databricks.yml and resources/jobs.yml must declare the sentinel
    task (`assert_borrower_360_fresh`) and the seed task
    (`capture_refresh_timestamp`) inside the `mip_refresh_scores` job
    definition. A future edit that reverts either wiring should fail CI."""
    for name in ("databricks.yml", "resources/jobs.yml"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "capture_refresh_timestamp" in text, (
            f"{name}: missing capture_refresh_timestamp task "
            f"(audit-holes-round-3 #7 wiring regressed)."
        )
        assert "assert_borrower_360_fresh" in text, (
            f"{name}: missing assert_borrower_360_fresh task "
            f"(audit-holes-round-3 #8 wiring regressed)."
        )


def test_downstream_ctas_depend_on_sentinel() -> None:
    """The canonical bundle definition (databricks.yml) must route every
    scoring / rollup / dossier task through the sentinel instead of
    directly depending on ctas_borrower_360. Pins audit-holes-round-3 #8."""
    text = (REPO_ROOT / "databricks.yml").read_text(encoding="utf-8")
    # Locate the mip_refresh_scores block and assert that within it,
    # ctas_lead_scores' depends_on names the sentinel, not ctas_borrower_360.
    block_match = re.search(
        r"mip_refresh_scores:(.*?)(?:\n    [a-zA-Z_]+:|\Z)",
        text,
        re.DOTALL,
    )
    assert block_match, "could not locate mip_refresh_scores block in databricks.yml."
    block = block_match.group(1)
    # ctas_lead_scores is the canary -- it is the first task that would
    # silently score a stale population if the sentinel were bypassed.
    lead_scores_match = re.search(
        r"- task_key:\s*ctas_lead_scores\s*.*?depends_on:\s*\n\s*- task_key:\s*([A-Za-z0-9_]+)",
        block,
        re.DOTALL,
    )
    assert lead_scores_match, "could not parse ctas_lead_scores.depends_on."
    assert lead_scores_match.group(1) == "assert_borrower_360_fresh", (
        "ctas_lead_scores must depend on assert_borrower_360_fresh, not "
        "directly on ctas_borrower_360 -- the sentinel is the freshness gate."
    )


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
