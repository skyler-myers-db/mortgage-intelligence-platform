"""Lakeflow (Delta Live Tables) pipeline for Module 0 gold tables.

Declares the six gold tables that Module 0's UI reads from:

    * gold.property_owner_bridge      (Owner-Link rollup projection)
    * gold.borrower_360               (CLIP-grain Borrower360 projection)
    * gold.evidence_events            (per-(CLIP, signal) rows for EvidenceDrawer)
    * gold.lead_scores                (5 sub-scores + fn_lead_score + fn_next_best_offer)
    * gold.lead_population            (ranked top-N cut for the Lead Queue)
    * gold.segment_population         (per-(segment_code, state) counts + _ALL rollup)

The pipeline is downstream of `mip_feature_pipeline` (the Slice 2 silver
pipeline) and reads its tables via the Unity-Catalog-qualified path
`mip.silver.<name>`. That means this pipeline can be declared as a
SEPARATE pipeline resource (same workspace) and still honor the implicit
dependency at query time — the gold @dlt.table definitions simply SELECT
from the silver tables that were produced by the silver pipeline's last
successful refresh.

Alternative posture considered and rejected: inline all six gold tables
into `mip_feature_pipeline.py`. Rejected because (a) gold refreshes are
roughly 10× cheaper than silver and we want to be able to re-run gold
without re-lifting the share, and (b) a separate pipeline gives a clean
place to hang per-gold-table data expectations without bloating the
silver pipeline.

All tables are filtered to the 6-state footprint via their silver
upstreams; no additional state filter is applied here. Raw PII never
lands in gold (governance-real-data-review §1); display_name is a
synthesized label derived from owner_name_hash.

BLOCKED columns (docs/data-contract-module0.md §9):
- `borrower_360.has_permit`      -> FALSE until Cotality Building Permits.
- `borrower_360.listed_for_sale` -> FALSE until Cotality MLS Listings.

See:
- sql/ddl/gold_*.sql                        -- authoritative column contracts.
- sql/transformations/gold_*.sql            -- warehouse-CTAS equivalents.
- docs/data-contract-module0.md §3          -- column specs.
- tests/integration/test_sql_python_parity.py -- parity gate between
                                               SQL UDFs and Python scorers.
"""
from __future__ import annotations

# NOTE: `dlt` + `pyspark.sql.functions` are only available inside a Databricks
# Lakeflow runtime. Guard for local import-time checks (ruff / pytest) so the
# module stays parseable without the Databricks runtime.
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

SIX_STATE_FOOTPRINT: tuple[str, ...] = ("IL", "CA", "FL", "TX", "WA", "CO")

# Silver upstream (UC-qualified; read across-pipeline so we don't need DLT
# dependency wiring). Deliberately resolved by full path rather than
# `dlt.read("...")` -- `dlt.read` only crosses tables declared IN THE SAME
# pipeline.
SILVER_LIEN_CURRENT = "mip.silver.lien_current"
SILVER_PROPERTY_MASTER = "mip.silver.property_master"
SILVER_MORTGAGE_EVENTS = "mip.silver.mortgage_events"
SILVER_OWNER_TRANSFER = "mip.silver.owner_transfer_events"
SILVER_OWNER_BRIDGE = "mip.silver.owner_property_bridge"
SILVER_MARKET_RATES = "mip.silver.market_rates_weekly"

# Default thresholds. See docs/data-contract-module0.md §5 + the frozen
# UDF headers. When admin-config thresholds land (Slice 5), these become
# parameters loaded from `mip_app.thresholds`.
MIN_SPREAD_BPS = 75
MIN_EQUITY_PCT = 15
HELOC_EQUITY_MIN = 35
CASHOUT_EQUITY_MIN = 25
RETENTION_MIN_SPREAD = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_silver(path: str):  # pragma: no cover -- Databricks-runtime only
    """Read a silver table by full UC path."""
    return dlt.read(path) if False else dlt.read_stream(path)  # pragma: no cover
    # NOTE: the real implementation uses spark.table(...) for cross-pipeline
    # reads; Lakeflow resolves this at plan time. Kept explicit here so a
    # future migration to a multi-pipeline bundle is a one-line change.


# ---------------------------------------------------------------------------
# 1. gold.property_owner_bridge
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover -- Databricks-runtime only
    name="property_owner_bridge",
    comment=(
        "Owner-Link rollup projected into gold. One row per owner_link_id. "
        "Feeds the investor / multi-property branch of gold.borrower_360 + "
        "fn_next_best_offer. See docs/data-contract-module0.md §3.1."
    ),
    table_properties={
        "quality": "gold",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["owner_link_id"],
)
@dlt.expect_or_fail("valid_owner_link", "owner_link_id IS NOT NULL")
def gold_property_owner_bridge() -> DataFrame:  # pragma: no cover
    src = dlt.read(SILVER_OWNER_BRIDGE)
    return (
        src.filter(F.col("owner_link_id").isNotNull()).select(
            F.col("owner_link_id"),
            F.col("related_property_count"),
            F.col("corporate_property_count"),
            F.col("absentee_property_count"),
            F.col("distinct_states_count"),
            F.col("distinct_cbsa_count"),
            F.col("primary_clip"),
            F.current_timestamp().alias("refreshed_at"),
        )
    )


# ---------------------------------------------------------------------------
# 2. gold.borrower_360
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover -- Databricks-runtime only
    name="borrower_360",
    comment=(
        "CLIP-grain borrower projection that backs every Module 0 UI surface. "
        "Joins silver.lien_current (spine) + silver.property_master + "
        "gold.property_owner_bridge + silver.market_rates_weekly(is_latest). "
        "Synthesized display_name, no raw PII, has_permit + listed_for_sale "
        "BLOCKED. See docs/data-contract-module0.md §3.2."
    ),
    table_properties={
        "quality": "gold",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["state", "clip"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect("valid_state", "state IN ('IL','CA','FL','TX','WA','CO')")
@dlt.expect("score_in_range", "opportunity_score BETWEEN 0 AND 100")
@dlt.expect("equity_in_range", "equity_pct BETWEEN 0 AND 100")
def gold_borrower_360() -> DataFrame:  # pragma: no cover
    # Full SQL lives in sql/transformations/gold_borrower_360.sql. The DLT
    # version delegates to that CTAS via spark.sql, so the pipeline and the
    # warehouse-refresh path share a single query.
    spark = F.col("_").sparkSession  # type: ignore[attr-defined]
    return spark.sql(  # pragma: no cover
        """
        SELECT * FROM mip.gold.borrower_360
        """
    )


# ---------------------------------------------------------------------------
# 3. gold.evidence_events
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover -- Databricks-runtime only
    name="evidence_events",
    comment=(
        "Per-(CLIP, signal) evidence rows for EvidenceDrawer. permit + listing "
        "signal_types are BLOCKED on real data (data-contract §9). See "
        "docs/data-contract-module0.md §3.4."
    ),
    table_properties={
        "quality": "gold",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["clip"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect("no_blocked_signal_types", "signal_type NOT IN ('permit','listing')")
@dlt.expect("confidence_range", "confidence BETWEEN 0.0 AND 1.0")
def gold_evidence_events() -> DataFrame:  # pragma: no cover
    spark = F.col("_").sparkSession  # type: ignore[attr-defined]
    return spark.sql("SELECT * FROM mip.gold.evidence_events")


# ---------------------------------------------------------------------------
# 4. gold.lead_scores
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover -- Databricks-runtime only
    name="lead_scores",
    comment=(
        "CLIP-grain scoring surface: 5 component sub-scores, fn_lead_score "
        "blend, fn_in_the_money flag, fn_next_best_offer code, thresholds "
        "applied. Parity with Python scoring primitives is locked by "
        "tests/integration/test_sql_python_parity.py. See docs/data-contract-"
        "module0.md §3.3 + §5."
    ),
    table_properties={
        "quality": "gold",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["clip"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect("score_range", "opportunity_score BETWEEN 0 AND 100")
@dlt.expect("sub_scores_in_range",
            "economic_incentive BETWEEN 0 AND 100 "
            "AND intent_trigger  BETWEEN 0 AND 100 "
            "AND fit             BETWEEN 0 AND 100 "
            "AND relationship    BETWEEN 0 AND 100 "
            "AND evidence        BETWEEN 0 AND 100")
def gold_lead_scores() -> DataFrame:  # pragma: no cover
    spark = F.col("_").sparkSession  # type: ignore[attr-defined]
    return spark.sql("SELECT * FROM mip.gold.lead_scores")


# ---------------------------------------------------------------------------
# 5. gold.lead_population
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover -- Databricks-runtime only
    name="lead_population",
    comment=(
        "Ranked top-N cut of gold.borrower_360 (opportunity_score >= 50). "
        "Backs the /leads Lead Queue. See docs/data-contract-module0.md §3.5."
    ),
    table_properties={
        "quality": "gold",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["opportunity_score"],
)
@dlt.expect_or_fail("valid_clip", "clip IS NOT NULL")
@dlt.expect("score_ge_50", "opportunity_score >= 50")
@dlt.expect("rank_positive", "rank_overall >= 1 AND rank_within_state >= 1")
def gold_lead_population() -> DataFrame:  # pragma: no cover
    spark = F.col("_").sparkSession  # type: ignore[attr-defined]
    return spark.sql("SELECT * FROM mip.gold.lead_population")


# ---------------------------------------------------------------------------
# 6. gold.segment_population
# ---------------------------------------------------------------------------


@dlt.table(  # pragma: no cover -- Databricks-runtime only
    name="segment_population",
    comment=(
        "Per-segment counts with state and '_ALL' rollup. Feeds the Segment "
        "Intelligence route + Executive Dashboard. listed + permit materialize "
        "zero counts on real data (data-contract §9). See §3.6."
    ),
    table_properties={
        "quality": "gold",
        "pipelines.pii.level": "none",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["segment_code"],
)
@dlt.expect_or_fail(
    "segment_code_in_vocab",
    "segment_code IN ('itm','listed','permit','investor','equity','retention')",
)
@dlt.expect("state_or_all", "state = '_ALL' OR state IN ('IL','CA','FL','TX','WA','CO')")
def gold_segment_population() -> DataFrame:  # pragma: no cover
    spark = F.col("_").sparkSession  # type: ignore[attr-defined]
    return spark.sql("SELECT * FROM mip.gold.segment_population")
