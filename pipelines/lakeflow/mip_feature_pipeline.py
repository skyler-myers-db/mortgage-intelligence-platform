"""Lakeflow (Delta Live Tables) pipeline for Module 0 silver tables.

Declares the five silver tables lifted from `cotality_mortgage_data.corelogic`:

    * silver.property_master          (CLIP-grain, 1:1 from property_domain_v3)
    * silver.lien_current             (CLIP-grain, 1:1 from voluntary_lien)   -- THE SPINE
    * silver.mortgage_events          (event-grain from mortgage_domain_v1)
    * silver.owner_transfer_events    (event-grain from owner_transfer_domain_v1)
    * silver.owner_property_bridge    (Owner-Link rollup from silver.property_master + lien_current)

All tables are filtered to the 6-state share footprint
(IL / CA / FL / TX / WA / CO) per CLAUDE.md + docs/data-contract-module0.md §2.
Raw owner names and street addresses are NEVER projected into silver
(governance-real-data-review §1); the only owner-identity column that lands
is `owner_name_hash` on property_master, computed inside this pipeline from
`owner_1_full_name` and then dropped from the output row before the @dlt.table
returns.

Runtime contract:
- Target `target = "silver"`, `catalog = "mip"` are set on the pipeline
  resource in databricks.yml.
- Source `cotality_mortgage_data.corelogic.*` is attached to the DEFAULT
  workspace; the pipeline principal needs SELECT on those share tables.
- Streaming reads (`dlt.read_stream`) are preferred where the underlying is
  Delta. If the share's physical format is not Delta-compatible with CDF, the
  @dlt.table will fall back to batch via `spark.table(...)` -- see the
  `_read_share_table` helper for the one-line swap.

Slice 2 non-negotiables checked here:
- Multi-state filter baked into every @dlt.table (no single-metro cells).
- No raw PII columns in any projected row.
- Idempotency: DLT table identities are declared by @dlt.table name; reruns
  are managed by the DLT runtime via CHECKPOINT and transaction log.

See:
- sql/ddl/silver_*.sql                 -- authoritative column contract.
- sql/transformations/silver_*.sql     -- warehouse-MERGE equivalent for
                                          the non-DLT code path (e.g. ad-hoc
                                          backfills via a SQL warehouse).
- docs/data-contract-module0.md §2    -- column specs.
"""
from __future__ import annotations

# NOTE: `dlt` and `pyspark.sql.functions` are only available when this file
# runs inside a Databricks Lakeflow (Delta Live Tables) pipeline. For local
# import-time checks (ruff / pytest contract test), we guard the imports so
# the file remains parseable without Databricks runtime dependencies.
try:  # pragma: no cover -- Databricks runtime only
    import dlt
    from pyspark.sql import DataFrame
    from pyspark.sql import functions as F
except ImportError:  # pragma: no cover -- local parse-only
    dlt = None  # type: ignore[assignment]
    DataFrame = None  # type: ignore[assignment,misc]
    F = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The 6-state share footprint. Hardcoded here -- if we ever need to broaden or
# narrow the footprint, update docs/data-contract-module0.md §2 first, then
# this tuple, then re-run the slice-2 contract test.
SIX_STATE_FOOTPRINT: tuple[str, ...] = ("IL", "CA", "FL", "TX", "WA", "CO")

# PII salt. In the deployed workspace this is read from the Databricks secret
# scope `mip`, key `pii-salt-v1` (per governance-real-data-review §1 and
# data-contract §7). The literal fallback keeps the pipeline runnable on a
# fresh workspace before the secret is provisioned; the fallback value is
# also documented in the data-contract so rotations stay auditable.
_PII_SALT_SCOPE = "mip"
_PII_SALT_KEY = "pii-salt-v1"
_PII_SALT_FALLBACK = "mip_pii_salt_v1"

# Source share tables (full UC path).
_SHARE_CATALOG = "cotality_mortgage_data.corelogic"
_SHARE_PROPERTY_V3 = f"{_SHARE_CATALOG}.entrada_eval_property_domain_v3"
_SHARE_VOLUNTARY_LIEN = f"{_SHARE_CATALOG}.entrada_eval_voluntary_lien_status_marketing_v2"
_SHARE_MORTGAGE_DOMAIN = f"{_SHARE_CATALOG}.entrada_eval_mortgage_domain_v1"
_SHARE_OWNER_TRANSFER = f"{_SHARE_CATALOG}.entrada_eval_owner_transfer_domain_v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_share_table(name: str, streaming: bool = True):  # pragma: no cover
    """Read a Delta-shared source table.

    Prefers `dlt.read_stream` for Change-Data-Feed-friendly incremental
    refresh; falls back to batch `dlt.read` if the pipeline runtime reports
    the source as non-streamable. The pipeline resource in databricks.yml
    sets `continuous: false` so streaming here means "incremental micro-batch
    against the share's Delta log," not a long-running stream.
    """
    if streaming:
        try:
            return dlt.read_stream(name)
        except Exception:  # noqa: BLE001 -- DLT raises various subclasses
            # Fall through to batch read. Share tables without CDF enabled
            # can only be read as a full snapshot per refresh.
            pass
    return dlt.read(name)


def _pii_salt_expr():  # pragma: no cover -- Databricks-runtime only
    """Return a Spark column expression carrying the PII salt.

    DLT's analyzer enforces SECRET_FUNCTION_SCOPE_NOT_CONSTANT, which
    rejects the scope/key args unless they're parsed as bare string
    literals at analyze time. The earlier ``try_cast(secret(...))``
    form defeated that check. Pass the literals directly and rely on
    the pipeline's secret-scope preflight (scope `mip`, key
    `pii-salt-v1`) to guarantee the secret exists at runtime. If the
    scope / secret is ever missing, the @dlt.expect_or_fail on CLIP
    will still surface a hard failure — there is no silent fallback
    on the data path.
    """
    return F.expr(
        f"secret('{_PII_SALT_SCOPE}', '{_PII_SALT_KEY}')"
    )


def _in_six_states(col):  # pragma: no cover -- Databricks-runtime only
    """Column expression: TRUE when `col` is in the 6-state footprint."""
    return col.isin(*SIX_STATE_FOOTPRINT)


def _zip5(col_name: str):  # pragma: no cover -- Databricks-runtime only
    """Normalize Cotality ZIP/ZIP+4 strings to the Module 0 ZIP5 contract."""
    digits = F.regexp_replace(
        F.coalesce(F.col(col_name).cast("string"), F.lit("")),
        "[^0-9]",
        "",
    )
    return F.when(F.length(digits) > 0, F.substring(digits, 1, 5)).otherwise(
        F.lit(None).cast("string")
    )


# ---------------------------------------------------------------------------
# silver.property_master
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover -- Databricks-runtime only
    name="property_master",
    comment=(
        "CLIP-grain property snapshot from entrada_eval_property_domain_v3, "
        "filtered to IN (IL,CA,FL,TX,WA,CO). Raw owner names + street "
        "addresses never land here; see docs/data-contract-module0.md §2.2."
    ),
    table_properties={
        "quality": "silver",
        "pipelines.pii.level": "hashed-only",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["situs_state", "situs_cbsa_code", "clip"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect("valid_state", "situs_state IN ('IL','CA','FL','TX','WA','CO')")
@dlt.expect("zip_is_zip5_or_null", "situs_zip_code IS NULL OR length(situs_zip_code) <= 5")
def silver_property_master() -> DataFrame:  # pragma: no cover
    src = _read_share_table(_SHARE_PROPERTY_V3)
    return (
        src.filter(_in_six_states(F.col("situs_state")))
        .filter(F.col("clip").isNotNull())
        .select(
            F.col("clip"),
            F.col("fips_county_code"),
            F.col("situs_state"),
            F.col("situs_city"),
            # Data contract §2.2 requires ZIP5. Cotality frequently ships
            # ZIP+4; normalize in the live Lakeflow path, not just the
            # warehouse MERGE fallback.
            _zip5("situs_zip_code").alias("situs_zip_code"),
            F.col("situs_core_based_statistical_area_cbsa").alias("situs_cbsa_code"),
            F.col("block_level_latitude").cast("double").alias("situs_lat"),
            F.col("block_level_longitude").cast("double").alias("situs_lon"),
            F.col("owner_1_identifier").alias("owner_link_id"),
            # PII HASH: raw owner_1_full_name is consumed here and never
            # emitted. sha2(LOWER(TRIM(name)) || ':' || salt, 256).
            F.sha2(
                F.concat(
                    F.lower(F.trim(F.coalesce(F.col("owner_1_full_name"), F.lit("")))),
                    F.lit(":"),
                    _pii_salt_expr(),
                ),
                256,
            ).alias("owner_name_hash"),
            F.coalesce(F.col("owner_1_corporate_indicator"), F.lit(0))
                .cast("boolean")
                .alias("owner_is_corporate"),
            F.col("owner_occupancy_code"),
            F.col("mailing_city"),
            F.col("mailing_state"),
            F.when(
                F.col("mailing_state").isNotNull()
                & (F.upper(F.trim(F.col("mailing_state"))) != F.upper(F.trim(F.col("situs_state")))),
                F.lit(True),
            ).otherwise(F.lit(False)).alias("is_absentee"),
            F.col("foreclosure_stage_code"),
            F.expr(
                "try_to_date(cast(nullif(last_foreclosure_transaction_date, 0) as string), 'yyyyMMdd')"
            ).alias("last_foreclosure_date"),
            F.col("year_built").cast("int").alias("year_built"),
            F.col("total_living_area_square_feet_all_bldgs").cast("int").alias("living_area_sqft"),
            F.col("total_number_of_bedrooms_all_bldgs").cast("int").alias("bedrooms"),
            F.col("total_number_of_bathrooms").cast("double").alias("bathrooms"),
            F.col("calculated_total_value").cast("bigint").alias("calculated_total_value"),
            F.col("assessed_total_value").cast("bigint").alias("assessed_total_value"),
            F.col("total_tax_amount").cast("double").alias("total_tax_amount"),
            F.col("tax_year").cast("int").alias("tax_year"),
            F.current_timestamp().alias("ingest_ts"),
            F.lit(None).cast("string").alias("_meta_batch_id"),
        )
    )


# ---------------------------------------------------------------------------
# silver.lien_current  (THE SPINE)
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover
    name="lien_current",
    comment=(
        "CLIP-grain lien + rates snapshot from entrada_eval_voluntary_lien_"
        "status_marketing_v2 filtered to IN (IL,CA,FL,TX,WA,CO). THE SPINE "
        "of Module 0 scoring. See docs/data-contract-module0.md §2.1."
    ),
    table_properties={
        "quality": "silver",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["situs_state", "clip"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect("valid_state", "situs_state IN ('IL','CA','FL','TX','WA','CO')")
@dlt.expect("zip_is_zip5_or_null", "situs_zip_code IS NULL OR length(situs_zip_code) <= 5")
@dlt.expect("rate_is_fractional", "first_pos_rate IS NULL OR first_pos_rate BETWEEN 0 AND 0.25")
def silver_lien_current() -> DataFrame:  # pragma: no cover
    src = _read_share_table(_SHARE_VOLUNTARY_LIEN)
    # Fractional rate expression: share rate is DOUBLE in percent form; divide
    # by 100 and coerce <= 0 to NULL. Applied to both first- and second-pos.
    first_pos_rate_frac = F.when(
        F.col("first_position_mortgage_interest_rate").isNull()
        | (F.col("first_position_mortgage_interest_rate").cast("double") <= 0),
        F.lit(None).cast("double"),
    ).otherwise(F.col("first_position_mortgage_interest_rate").cast("double") / F.lit(100.0))
    second_pos_rate_frac = F.when(
        F.col("second_position_mortgage_interest_rate").isNull()
        | (F.col("second_position_mortgage_interest_rate").cast("double") <= 0),
        F.lit(None).cast("double"),
    ).otherwise(F.col("second_position_mortgage_interest_rate").cast("double") / F.lit(100.0))
    return (
        src.filter(_in_six_states(F.col("situs_state")))
        .filter(F.col("clip").isNotNull())
        .select(
            F.col("clip"),
            F.col("situs_state"),
            # Data contract §2.1 requires ZIP5. Keep the DLT path in parity
            # with sql/transformations/silver_lien_current.sql.
            _zip5("situs_zip_code").alias("situs_zip_code"),
            F.col("owner_occupancy_code"),
            F.col("total_number_of_open_mortgage_liens").cast("int").alias("total_open_liens"),
            F.col("total_amount_of_open_mortgage_liens").cast("bigint").alias("total_open_lien_balance"),
            F.col("estimated_value_mktg").cast("bigint").alias("avm_value"),
            F.col("estimated_value_high_mktg").cast("bigint").alias("avm_value_high"),
            F.col("estimated_value_low_mktg").cast("bigint").alias("avm_value_low"),
            F.col("confidence_score_mktg").cast("double").alias("avm_confidence"),
            F.expr("try_to_date(cast(value_as_of_date_mktg as string))").alias("avm_as_of_date"),
            F.col("estimated_equity").cast("bigint").alias("estimated_equity"),
            F.col("estimated_combined_ltv_loan_to_value").cast("double").alias("estimated_cltv"),
            F.col("purchase_amount").cast("bigint").alias("purchase_amount"),
            F.expr(
                "try_to_date(cast(nullif(purchase_recording_date, 0) as string), 'yyyyMMdd')"
            ).alias("purchase_date"),
            F.col("purchase_combined_ltv_loan_to_value").cast("double").alias("purchase_cltv"),
            F.expr(
                "try_to_date(cast(nullif(first_position_mortgage_date, 0) as string), 'yyyyMMdd')"
            ).alias("first_pos_date"),
            F.col("first_position_mortgage_amount").cast("bigint").alias("first_pos_amount"),
            first_pos_rate_frac.alias("first_pos_rate"),
            F.col("first_position_mortgage_interest_rate_type_code").alias("first_pos_rate_type"),
            F.col("first_position_mortgage_term").cast("int").alias("first_pos_term_months"),
            F.col("first_position_mortgage_loan_type_code").alias("first_pos_loan_type"),
            F.col("first_position_mortgage_purpose_code").alias("first_pos_purpose"),
            F.col("first_position_mortgage_ltv_loan_to_value").cast("double").alias("first_pos_ltv"),
            F.col("first_position_lender_company_name").alias("first_pos_lender_original"),
            F.col("first_position_currently_assigned_lender_company_name").alias(
                "first_pos_lender_current"
            ),
            F.col("second_position_mortgage_amount").cast("bigint").alias("second_pos_amount"),
            second_pos_rate_frac.alias("second_pos_rate"),
            F.col("second_position_mortgage_purpose_code").alias("second_pos_purpose"),
            F.col("second_position_lender_company_name").alias("second_pos_lender"),
            F.current_timestamp().alias("ingest_ts"),
            F.lit(None).cast("string").alias("_meta_batch_id"),
        )
    )


# ---------------------------------------------------------------------------
# silver.mortgage_events
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover
    name="mortgage_events",
    comment=(
        "Event-grain mortgage history from entrada_eval_mortgage_domain_v1 "
        "filtered to deed_situs_state_static IN (IL,CA,FL,TX,WA,CO). "
        "Feeds intent_trigger + gold.evidence_events. "
        "See docs/data-contract-module0.md §2.3."
    ),
    table_properties={
        "quality": "silver",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["clip", "event_date"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect_or_fail("valid_txn_id", "mortgage_txn_id IS NOT NULL")
@dlt.expect("valid_state", "situs_state IN ('IL','CA','FL','TX','WA','CO')")
def silver_mortgage_events() -> DataFrame:  # pragma: no cover
    src = _read_share_table(_SHARE_MORTGAGE_DOMAIN)
    event_date_expr = F.expr(
        "try_to_date(cast(nullif(mortgage_derived_date, 0) as string), 'yyyyMMdd')"
    )
    return (
        src.filter(_in_six_states(F.col("deed_situs_state_static")))
        .filter(F.col("mortgage_composite_transaction_id").isNotNull())
        .filter(F.col("clip").isNotNull())
        .select(
            F.col("mortgage_composite_transaction_id").alias("mortgage_txn_id"),
            F.col("clip"),
            F.col("deed_situs_state_static").alias("situs_state"),
            event_date_expr.alias("event_date"),
            F.year(event_date_expr).alias("event_year"),
            F.col("mortgage_amount").cast("bigint").alias("mortgage_amount"),
            F.col("mortgage_interest_rate_cascade").cast("double").alias("rate_cascade"),
            F.col("mortgage_purpose_code").alias("purpose_code"),
            F.col("mortgage_loan_type_code").alias("loan_type_code"),
            F.coalesce(F.col("refinance_loan_indicator"), F.lit(0))
                .cast("boolean").alias("is_refinance"),
            F.coalesce(F.col("equity_loan_indicator"), F.lit(0))
                .cast("boolean").alias("is_equity_loan"),
            F.coalesce(F.col("reverse_mortgage_indicator"), F.lit(0))
                .cast("boolean").alias("is_reverse_mortgage"),
            F.col("lender_company_name").alias("lender_name"),
            F.expr(
                "try_to_date(cast(nullif(mortgage_release_date, 0) as string), 'yyyyMMdd')"
            ).alias("release_date"),
            F.col("mortgage_status_indicator").alias("status_indicator"),
            F.col("borrower_1_identifier").alias("borrower_identifier"),
            F.current_timestamp().alias("ingest_ts"),
            F.lit(None).cast("string").alias("_meta_batch_id"),
        )
    )


# ---------------------------------------------------------------------------
# silver.owner_transfer_events
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover
    name="owner_transfer_events",
    comment=(
        "Event-grain owner-transfer history from entrada_eval_owner_transfer_"
        "domain_v1 filtered to deed_situs_state_static IN (IL,CA,FL,TX,WA,"
        "CO). Buyer names NEVER landed. See docs/data-contract-module0.md §2.4."
    ),
    table_properties={
        "quality": "silver",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["clip", "sale_date"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect_or_fail("valid_txn_id", "transfer_txn_id IS NOT NULL")
@dlt.expect("valid_state", "situs_state IN ('IL','CA','FL','TX','WA','CO')")
def silver_owner_transfer_events() -> DataFrame:  # pragma: no cover
    src = _read_share_table(_SHARE_OWNER_TRANSFER)
    sale_date_expr = F.expr(
        "try_to_date(cast(nullif(sale_derived_date, 0) as string), 'yyyyMMdd')"
    )
    return (
        src.filter(_in_six_states(F.col("deed_situs_state_static")))
        .filter(F.col("owner_transfer_composite_transaction_id").isNotNull())
        .filter(F.col("clip").isNotNull())
        .select(
            F.col("owner_transfer_composite_transaction_id").alias("transfer_txn_id"),
            F.col("clip"),
            F.col("deed_situs_state_static").alias("situs_state"),
            sale_date_expr.alias("sale_date"),
            F.col("sale_amount").cast("bigint").alias("sale_amount"),
            F.col("sale_type_code").alias("sale_type_code"),
            F.coalesce(F.col("cash_purchase_indicator"), F.lit(0))
                .cast("boolean").alias("is_cash_purchase"),
            F.coalesce(F.col("investor_purchase_indicator"), F.lit(0))
                .cast("boolean").alias("is_investor_purchase"),
            F.coalesce(F.col("foreclosure_reo_indicator"), F.lit(0))
                .cast("boolean").alias("is_reo"),
            F.coalesce(F.col("short_sale_indicator"), F.lit(0))
                .cast("boolean").alias("is_short_sale"),
            F.coalesce(F.col("new_construction_indicator"), F.lit(0))
                .cast("boolean").alias("is_new_construction"),
            F.coalesce(F.col("resale_indicator"), F.lit(0))
                .cast("boolean").alias("is_resale"),
            F.coalesce(F.col("interfamily_related_indicator"), F.lit(0))
                .cast("boolean").alias("is_interfamily"),
            F.coalesce(F.col("buyer_1_corporate_indicator"), F.lit(0))
                .cast("boolean").alias("buyer_is_corporate"),
            F.col("buyer_1_identifier").alias("buyer_identifier"),
            F.col("buyer_mailing_state"),
            F.current_timestamp().alias("ingest_ts"),
            F.lit(None).cast("string").alias("_meta_batch_id"),
        )
    )


# ---------------------------------------------------------------------------
# silver.owner_property_bridge  (Owner-Link rollup)
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover
    name="owner_property_bridge",
    comment=(
        "Owner-Link rollup derived from silver.property_master + "
        "silver.lien_current. One row per distinct owner_link_id. Feeds "
        "gold.property_owner_bridge and investor-segment signals. "
        "See docs/data-contract-module0.md §3.1."
    ),
    table_properties={
        "quality": "silver",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["owner_link_id"],
)
@dlt.expect_or_fail("valid_owner_link", "owner_link_id IS NOT NULL")
@dlt.expect("positive_property_count", "related_property_count >= 1")
def silver_owner_property_bridge() -> DataFrame:  # pragma: no cover
    pm = dlt.read("property_master")
    lc = dlt.read("lien_current")
    joined = (
        pm.alias("pm")
        .join(lc.alias("lc"), F.col("pm.clip") == F.col("lc.clip"), "left")
        .where(F.col("pm.owner_link_id").isNotNull())
        # Defense-in-depth 6-state filter (upstream silver already filters,
        # but asserting here guards any future backfill where an un-filtered
        # property_master batch is produced).
        .where(_in_six_states(F.col("pm.situs_state")))
        .select(
            F.col("pm.owner_link_id").alias("owner_link_id"),
            F.col("pm.clip").alias("clip"),
            F.col("pm.situs_state").alias("situs_state"),
            F.col("pm.situs_cbsa_code").alias("situs_cbsa_code"),
            F.col("pm.owner_is_corporate").alias("owner_is_corporate"),
            F.col("pm.is_absentee").alias("is_absentee"),
            F.col("pm.owner_occupancy_code").alias("owner_occupancy_code"),
            F.col("lc.avm_value").alias("avm_value"),
            F.col("lc.total_open_lien_balance").alias("total_open_lien_balance"),
            F.col("lc.estimated_equity").alias("estimated_equity"),
        )
    )
    return (
        joined.groupBy("owner_link_id")
        .agg(
            F.countDistinct("clip").cast("int").alias("related_property_count"),
            F.sum(F.when(F.col("owner_is_corporate"), 1).otherwise(0))
                .cast("int").alias("corporate_property_count"),
            F.sum(F.when(F.col("is_absentee"), 1).otherwise(0))
                .cast("int").alias("absentee_property_count"),
            F.countDistinct("situs_state").cast("int").alias("distinct_states_count"),
            F.countDistinct("situs_cbsa_code").cast("int").alias("distinct_cbsa_count"),
            F.sum(F.coalesce(F.col("avm_value"), F.lit(0))).cast("bigint")
                .alias("total_avm_value"),
            F.sum(F.coalesce(F.col("total_open_lien_balance"), F.lit(0))).cast("bigint")
                .alias("total_open_lien_balance"),
            F.sum(F.coalesce(F.col("estimated_equity"), F.lit(0))).cast("bigint")
                .alias("total_estimated_equity"),
            F.max(F.when(F.col("owner_occupancy_code") == F.lit("O"), F.col("clip")))
                .alias("primary_clip"),
            F.current_timestamp().alias("ingest_ts"),
            F.lit(None).cast("string").alias("_meta_batch_id"),
        )
    )
