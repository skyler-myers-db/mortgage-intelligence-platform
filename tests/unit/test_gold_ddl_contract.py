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
    "gold_household_rollup.sql",
    "gold_lead_population.sql",
    "gold_segment_population.sql",
    # slice13-accuracy-validation: geography rollups for the USChoroplethMap drill.
    "gold_county_rollup.sql",
    "gold_zip_rollup.sql",
    "gold_state_top_segment.sql",
    "gold_source_readiness.sql",
)

GOLD_TRANSFORMATION_FILES: tuple[str, ...] = (
    "gold_property_owner_bridge.sql",
    "gold_borrower_360.sql",
    "gold_lead_scores.sql",
    "gold_evidence_events.sql",
    "gold_household_rollup.sql",
    "gold_lead_population.sql",
    "gold_segment_population.sql",
    "gold_county_rollup.sql",
    "gold_zip_rollup.sql",
    "gold_state_top_segment.sql",
    "gold_source_readiness.sql",
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
    "mip.gold.household_rollup",
    "mip.gold.lead_population",
    "mip.gold.segment_population",
    "mip.gold.borrower_dossier",
    "mip.gold.county_rollup",
    "mip.gold.zip_rollup",
    "mip.gold.state_top_segment",
    "mip.gold.source_readiness",
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


def test_borrower_360_declares_listing_permit_and_propensity_columns() -> None:
    """Cotality MLS is live, true filed permits remain fail-closed, and
    propensity model signals must stay separate from filed-permit facts."""
    text = (DDL_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    blocks = _create_table_blocks(text)
    assert blocks
    declared = _declared_column_names_in_block(blocks[0])
    assert "has_permit" in declared, "gold.borrower_360 must declare filed-permit status."
    assert "listed_for_sale" in declared, "gold.borrower_360 must declare the live MLS listing flag."
    assert "listing_status_category" in declared
    assert "listing_price" in declared
    assert "has_heloc_propensity_trigger" in declared
    assert "heloc_propensity_score" in declared
    assert "has_refi_propensity_trigger" in declared
    assert "is_former_customer" in declared, (
        "gold.borrower_360 must declare is_former_customer so Portfolio "
        "Builder does not alias Former customer to competitor-lien."
    )


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


def test_borrower_360_transformation_keeps_permits_fail_closed_but_wires_listing() -> None:
    """The CTAS must keep true filed permits false until a real permit table
    lands, while MLS and HELOC propensity are live sourced signals."""
    text = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    assert re.search(
        r"CAST\(FALSE\s+AS\s+BOOLEAN\)\s+AS\s+has_permit", text, re.IGNORECASE
    ), "gold.borrower_360 CTAS must hardcode filed has_permit = FALSE until a true permit table exists."
    assert re.search(
        r"mip\.silver\.listing_activity", text, re.IGNORECASE
    ), "gold.borrower_360 CTAS must source listed_for_sale from silver.listing_activity."
    assert re.search(
        r"COALESCE\(cl\.is_active_listing,\s*FALSE\)\s+AS\s+listed_for_sale", text, re.IGNORECASE
    ), "gold.borrower_360 CTAS must derive listed_for_sale from current active MLS rows."
    assert re.search(
        r"mip\.silver\.heloc_propensity", text, re.IGNORECASE
    ), "gold.borrower_360 CTAS must source HELOC intent from silver.heloc_propensity."


def test_source_readiness_core_input_lanes_are_fail_closed() -> None:
    """Core input lanes must not claim live status if their source table is empty."""
    text = (TRANSFORM_DIR / "gold_source_readiness.sql").read_text(encoding="utf-8")
    for source_name in (
        "Cotality Public Records",
        "Voluntary Lien",
        "MMA Mortgage Analytics",
        "CLIP",
        "Owner Link",
        "AVM",
        "FRED Market Rates",
    ):
        pattern = (
            rf"'{re.escape(source_name)}'\s+AS\s+source_name,.*?"
            r"CASE\s+WHEN\s+COUNT(?:_IF|\()"
        )
        assert re.search(pattern, text, re.IGNORECASE | re.DOTALL), (
            f"{source_name}: source_readiness must use a COUNT-based CASE "
            "instead of an unconditional live status."
        )


def test_source_readiness_avm_has_defensible_freshness_fallback() -> None:
    """AVM may not ship a distinct as-of date in every Cotality share row.

    If AVM is marked live, source_readiness still needs a non-null freshness
    timestamp. Use the valuation as-of date where present, otherwise the lien
    silver ingest timestamp for rows carrying AVM values.
    """
    text = (TRANSFORM_DIR / "gold_source_readiness.sql").read_text(encoding="utf-8")
    assert "COALESCE(CAST(avm_as_of_date AS TIMESTAMP), ingest_ts)" in text
    assert "WHEN avm_value IS NOT NULL" in text


def test_borrower_360_transformation_derives_former_customer_distinctly() -> None:
    """Former customer must be a historical tenant-lender Owner Link signal,
    not a synonym for competitor current servicer."""
    text = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    assert "AS is_former_customer" in text
    assert "historical_tenant_distinct_clips" in text
    assert "LIKE '%SUMMIT%'" not in text
    assert not re.search(
        r"is_competitor_lien\s+AS\s+is_former_customer",
        text,
        re.IGNORECASE,
    )


def test_borrower_360_transformation_enforces_zip5_or_null_boundary() -> None:
    """Gold is the API-facing table, so it must not expose short ZIP fragments."""
    text = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    assert "AS zip" in text
    assert "LENGTH(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', '')) = 5" in text
    assert "LPAD(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', ''), 5, '0')" in text
    assert "ELSE NULL" in text


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
    "gold_household_rollup.sql",
    "gold_lead_scores.sql",
    "gold_segment_population.sql",
    "gold_lockin_cohort.sql",
    "gold_borrower_dossier.sql",
    "gold_county_rollup.sql",
    "gold_zip_rollup.sql",
    "gold_source_readiness.sql",
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


def test_borrower_360_bounds_source_mortgage_rates_before_scoring() -> None:
    text = (
        TRANSFORM_DIR / "gold_borrower_360.sql"
    ).read_text(encoding="utf-8")

    assert "WHEN lc.first_pos_rate < 0.01 THEN NULL" in text
    assert "WHEN lc.first_pos_rate > 0.15 THEN 0.15" in text
    assert "mip.gold.fn_rate_spread(b.first_pos_rate" in text


def test_rate_spread_evidence_uses_same_bounded_rate_contract() -> None:
    text = (
        TRANSFORM_DIR / "gold_evidence_events.sql"
    ).read_text(encoding="utf-8")

    assert "rate_spread_inputs AS" in text
    assert "WHEN lc.first_pos_rate < 0.01 THEN NULL" in text
    assert "WHEN lc.first_pos_rate > 0.15 THEN 0.15" in text
    assert "FROM rate_spread_inputs AS lc" in text


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
    """databricks.yml must declare the sentinel task
    (`assert_borrower_360_fresh`) and the seed task
    (`capture_refresh_timestamp`) inside the `mip_refresh_scores` job
    definition. A future edit that reverts either wiring should fail CI.

    2026-06-11 audit P2-9: resources/jobs.yml was a dead, drifting mirror
    (databricks.yml has no `include:`), so it was deleted and dropped from
    this loop — databricks.yml is the single source of bundle truth."""
    for name in ("databricks.yml",):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "capture_refresh_timestamp" in text, (
            f"{name}: missing capture_refresh_timestamp task "
            f"(audit-holes-round-3 #7 wiring regressed)."
        )
        assert "seed_demo_first_party_feeds" in text, (
            f"{name}: missing seed_demo_first_party_feeds task "
            "first-party relationship scoring would silently ignore the "
            "configured lender-owned feeds."
        )
        assert "assert_borrower_360_fresh" in text, (
            f"{name}: missing assert_borrower_360_fresh task "
            f"(audit-holes-round-3 #8 wiring regressed)."
        )


def test_uc_functions_are_wired_before_gold_ctas() -> None:
    """The gold refresh job must apply UC function DDL before CTAS tasks.

    Golden-fixture parity only means something if deploy refreshes the UDFs.
    A missing task here leaves old function bodies live while Python fixtures
    pass locally, which is exactly the failure this guard prevents.
    """
    expected_tasks = (
        "init_fn_rate_spread",
        "init_fn_estimated_upb",
        "init_fn_in_the_money",
        "init_fn_lead_score",
        "init_fn_next_best_offer",
    )
    expected_rendered_paths = (
        "sql/_rendered/uc_functions/fn_rate_spread.sql",
        "sql/_rendered/uc_functions/fn_estimated_upb.sql",
        "sql/_rendered/uc_functions/fn_in_the_money.sql",
        "sql/_rendered/uc_functions/fn_lead_score.sql",
        "sql/_rendered/uc_functions/fn_next_best_offer.sql",
    )
    # 2026-06-11 audit P2-9: resources/jobs.yml mirror deleted —
    # databricks.yml is the single source of bundle truth.
    bundle = (REPO_ROOT / "databricks.yml").read_text(encoding="utf-8")

    for task_key in expected_tasks:
        assert f"task_key: {task_key}" in bundle

    for path in expected_rendered_paths:
        assert path in bundle

    init_gold_match = re.search(
        r"- task_key:\s*init_gold_ddl\s*.*?depends_on:\s*(.*?)\n\s*sql_task:",
        bundle,
        re.DOTALL,
    )
    assert init_gold_match, "databricks.yml init_gold_ddl must depend on all UC function tasks."
    init_gold_depends = init_gold_match.group(1)
    for task_key in expected_tasks:
        assert f"task_key: {task_key}" in init_gold_depends


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


def test_evidence_events_emits_listing_and_propensity_but_not_filed_permits() -> None:
    """gold.evidence_events must emit live MLS/propensity evidence while
    keeping filed-permit evidence disabled until a true permit source lands."""
    text = (TRANSFORM_DIR / "gold_evidence_events.sql").read_text(encoding="utf-8")
    emitted_permit = re.search(r"'permit'\s+AS\s+signal_type", text, re.IGNORECASE)
    assert not emitted_permit, (
        "gold.evidence_events emits a 'permit' signal_type row. Permit is "
        "disabled until a true filed-permit Cotality source lands."
    )
    assert re.search(r"'listing'\s+AS\s+signal_type", text, re.IGNORECASE)
    assert re.search(r"'heloc_propensity'\s+AS\s+signal_type", text, re.IGNORECASE)
    assert re.search(r"'refi_propensity'\s+AS\s+signal_type", text, re.IGNORECASE)


def test_evidence_events_uses_silver_lien_spine_not_borrower_360() -> None:
    """gold.evidence_events must be buildable before gold.borrower_360.

    borrower_360 reads evidence_events for scoring/timeline fields, so evidence
    cannot safely read borrower_360 as its spine without creating stale gold
    feedback across refreshes.
    """
    text = (TRANSFORM_DIR / "gold_evidence_events.sql").read_text(encoding="utf-8")
    body = re.sub(r"--.*$", "", text, flags=re.MULTILINE)

    assert "mip.gold.borrower_360" not in body
    assert "mip.silver.lien_current" in body
    assert re.search(
        r"borrower_spine\s+AS\s*\(.*?"
        r"FROM\s+mip\.silver\.lien_current.*?"
        r"situs_state\s+IS\s+NOT\s+NULL.*?"
        r"clip\s+IS\s+NOT\s+NULL",
        body,
        re.IGNORECASE | re.DOTALL,
    ), "evidence_events borrower_spine must match borrower_360's silver lien spine."


def test_evidence_event_source_table_literals_are_uc_paths() -> None:
    """EvidenceDrawer renders source_table verbatim, so each literal must be a
    real single UC table path, not prose or a compound lineage expression."""
    text = (TRANSFORM_DIR / "gold_evidence_events.sql").read_text(encoding="utf-8")
    source_table_literals = re.findall(
        r"'([^']+)'\s+AS\s+source_table",
        text,
        flags=re.IGNORECASE,
    )

    assert source_table_literals, "gold.evidence_events must emit source_table literals."
    for literal in source_table_literals:
        assert re.fullmatch(r"mip\.(silver|gold|ref)\.[a-z0-9_]+", literal), (
            f"source_table literal must be one real UC path, got {literal!r}."
        )

    assert "'Voluntary Lien + Market Rates'                  AS source_product" in text


def test_offer_thresholds_are_ref_table_sourced_and_materialized() -> None:
    """The five next-best-offer thresholds must come from the governed ref table.

    This pins the Slice-5 business contract: operators retune offer aggressiveness
    in mip.ref.offer_rules_config, then refresh gold. The UDF still receives all
    thresholds explicitly; the CTAS, not the UDF, owns configuration lookup.
    """
    borrower_sql = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    lead_scores_sql = (TRANSFORM_DIR / "gold_lead_scores.sql").read_text(encoding="utf-8")
    borrower_ddl = (DDL_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")

    assert "FROM mip.ref.offer_rules_config" in borrower_sql
    for key in (
        "mip_min_spread_bps",
        "mip_min_equity_pct",
        "mip_heloc_equity_min_pct",
        "mip_cashout_equity_min_pct",
        "mip_retention_min_spread_bps",
    ):
        assert key in borrower_sql

    for column in (
        "min_spread_bps_applied",
        "min_equity_pct_applied",
        "heloc_equity_min_applied",
        "cashout_equity_min_applied",
        "retention_min_spread_applied",
    ):
        assert column in borrower_sql
        assert column in lead_scores_sql
        assert column in borrower_ddl

    assert "s.heloc_equity_min_applied" in lead_scores_sql
    assert "s.cashout_equity_min_applied" in lead_scores_sql
    assert "s.retention_min_spread_applied" in lead_scores_sql
    assert not re.search(
        r"fn_next_best_offer\([\s\S]*?75\s*,\s*15\s*,\s*35\s*,\s*25\s*,\s*50",
        borrower_sql,
        re.IGNORECASE,
    )
    assert not re.search(
        r"fn_next_best_offer\([\s\S]*?35\s*,\s*25\s*,\s*50",
        lead_scores_sql,
        re.IGNORECASE,
    )


def test_next_best_offer_udf_fails_closed_on_null_thresholds() -> None:
    """A missing threshold row is a configuration failure, not a positive offer."""
    udf_sql = (REPO_ROOT / "sql" / "uc_functions" / "fn_next_best_offer.sql").read_text(
        encoding="utf-8"
    )
    fixture_text = (REPO_ROOT / "tests" / "fixtures" / "next_best_offer_golden.json").read_text(
        encoding="utf-8"
    )
    validation_sql = (
        REPO_ROOT / "sql" / "fixtures" / "next_best_offer_validation.sql"
    ).read_text(encoding="utf-8")

    assert "threshold is a configuration failure" in udf_sql
    assert re.search(
        r"min_spread_bps\s+IS\s+NULL.*?THEN\s+'nurture'",
        udf_sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert "case_16_null_thresholds_fail_closed_to_nurture" in fixture_text
    assert "case_16_null_thresholds_fail_closed_to_nurture" in validation_sql


def test_fit_loan_type_parity_and_explainability_contract() -> None:
    """CONV/FHA/VA must remain symmetric and compliance-visible.

    FHA/VA treatment is acceptable here because it is inclusive and equal to
    CONV. If this ever becomes asymmetric, it needs a lender fair-lending review.
    """
    borrower_sql = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    lead_scores_sql = (TRANSFORM_DIR / "gold_lead_scores.sql").read_text(encoding="utf-8")
    evidence_sql = (TRANSFORM_DIR / "gold_evidence_events.sql").read_text(encoding="utf-8")
    docs = (REPO_ROOT / "docs" / "data-contract-module0.md").read_text(encoding="utf-8")

    parity_pattern = r"first_pos_loan_type\s+IN\s+\('CONV','FHA','VA'\)\s+THEN\s+70"
    assert re.search(parity_pattern, borrower_sql)
    assert re.search(parity_pattern, lead_scores_sql)
    assert "'loan_type_fit'                                  AS signal_type" in evidence_sql
    assert "first_pos_loan_type IN ('CONV','FHA','VA')" in evidence_sql
    assert "signal_type NOT IN ('permit', 'loan_type_fit')" in borrower_sql
    assert "signal_type NOT IN ('permit', 'loan_type_fit')" in lead_scores_sql
    assert "CONV/FHA/VA parity is a contract" in docs
    assert "customer compliance team should explicitly review" in re.sub(r"\s+", " ", docs)


def test_borrower_360_and_lead_scores_subscore_terms_stay_aligned() -> None:
    """Pin the mirrored sub-score clauses that feed lead_score.

    The two CTAS files intentionally recompute the same five sub-scores at
    different grains. This static guard catches the high-risk drift modes:
    changing weights, branch constants, or first-party relationship terms in
    one CTAS but not the other.
    """
    borrower_sql = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    lead_scores_sql = (TRANSFORM_DIR / "gold_lead_scores.sql").read_text(encoding="utf-8")

    aligned_terms = (
        ("LEAST(55, CAST(ROUND(3 * sqrt(GREATEST(0, w.rate_spread_bps))) AS INT))",
         "LEAST(55, CAST(ROUND(3 * sqrt(GREATEST(0, b.rate_spread_bps))) AS INT))"),
        ("LEAST(50, CAST(ROUND(0.5 * LEAST(100, GREATEST(0, w.equity_pct))) AS INT))",
         "LEAST(50, CAST(ROUND(0.5 * LEAST(100, GREATEST(0, b.equity_pct))) AS INT))"),
        ("20 * CASE WHEN w.is_competitor_lien THEN 1 ELSE 0 END",
         "20 * CASE WHEN b.is_competitor_lien THEN 1 ELSE 0 END"),
        ("LEAST(25, GREATEST(0, (COALESCE(w.related_property_count, 1) - 1) * 10))",
         "LEAST(25, GREATEST(0, (COALESCE(b.related_property_count, 1) - 1) * 10))"),
        ("LEAST(30, CAST(ROUND(2 * sqrt(GREATEST(0, w.rate_spread_bps))) AS INT))",
         "LEAST(30, CAST(ROUND(2 * sqrt(GREATEST(0, b.rate_spread_bps))) AS INT))"),
        ("LEAST(10, GREATEST(0, CAST(w.equity_pct / 10 AS INT)))",
         "LEAST(10, GREATEST(0, CAST(b.equity_pct / 10 AS INT)))"),
        ("CASE WHEN w.is_current_customer THEN 8 ELSE 0 END",
         "CASE WHEN b.is_current_customer THEN 8 ELSE 0 END"),
        ("WHEN w.is_current_customer", "WHEN b.is_current_customer"),
        ("THEN 70", "THEN 70"),
        ("WHEN w.is_former_customer", "WHEN b.is_former_customer"),
        ("THEN 60", "THEN 60"),
        ("WHEN w.is_competitor_lien", "WHEN b.is_competitor_lien"),
        ("THEN 55", "THEN 55"),
        ("WHEN COALESCE(w.related_property_count, 1) > 1", "WHEN COALESCE(b.related_property_count, 1) > 1"),
        ("THEN 45", "THEN 45"),
        ("ELSE 35", "ELSE 35"),
        ("THEN LEAST(25, 5 * LEAST(5, w.historical_tenant_distinct_clips))",
         "THEN LEAST(25, 5 * LEAST(5, b.historical_tenant_distinct_clips))"),
        ("ELSE LEAST(25, GREATEST(0, (COALESCE(w.related_property_count, 1) - 1) * 5))",
         "ELSE LEAST(25, GREATEST(0, (COALESCE(b.related_property_count, 1) - 1) * 5))"),
        ("LEAST(12, 3 * COALESCE(w.fp_relationship_depth, 0))",
         "LEAST(12, 3 * COALESCE(b.first_party_relationship_depth, 0))"),
        ("LEAST(8, 4 * COALESCE(w.fp_recent_positive_interactions, 0))",
         "LEAST(8, 4 * COALESCE(b.first_party_recent_interactions, 0))"),
        ("CASE WHEN w.fp_has_recent_application THEN 5 ELSE 0 END",
         "CASE WHEN b.first_party_recent_application THEN 5 ELSE 0 END"),
        ("10 * COALESCE(ec.evidence_event_count, 0)",
         "10 * b.evidence_event_count"),
        ("THEN LEAST(20, CAST(ROUND(sqrt(w.second_pos_amount / 1000.0)) AS INT))",
         "THEN LEAST(20, CAST(ROUND(sqrt(b.second_pos_amount / 1000.0)) AS INT))"),
    )

    for borrower_term, lead_scores_term in aligned_terms:
        assert borrower_term in borrower_sql
        assert lead_scores_term in lead_scores_sql
