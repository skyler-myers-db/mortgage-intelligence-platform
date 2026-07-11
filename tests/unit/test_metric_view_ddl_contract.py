"""Slice13-accuracy contract test for the Module 0 metric views.

Asserts that the two dashboard-referenced metric views expose the columns
the dashboards expect (``approval_rate``, ``outreach_rate``, per-delta
columns) and that the SQL shapes match the contract agreed in
``docs/validation/metric-views.md``. Guards against a regression where
the dashboard widgets reference columns the view doesn't publish.

Contract guarded here:

1. ``segment_performance_metric_view.sql``:
   - Reads from ``mip.gold.segment_population`` (unchanged) AND joins
     ``mip.gold.funnel_snapshot_daily`` for today/prior snapshots.
   - Publishes ``approval_rate``, ``outreach_rate``,
     ``delta_vs_prior_count``, ``delta_vs_prior_approved``,
     ``delta_vs_prior_in_the_money``.
   - The approval-rate computation divides approved by count and rounds
     to 2 decimal places (``ROUND(100.0 * ... / NULLIF(count, 0), 2)``).

2. ``lead_generation_metric_view.sql``:
   - Joins ``mip.gold.borrower_lifecycle_state`` onto the lead queue
     with a LEFT JOIN + COALESCE fallback to ``'pending'`` /
     ``'none'``.
   - Publishes per-row ``approval_status`` + ``outreach_status``.
   - Publishes aggregate ``approval_rate``, ``outreach_rate``,
     ``delta_vs_prior_count``.

3. Both files use ``CREATE OR REPLACE VIEW mip.semantics.*`` and end
   with a ``COMMENT ON VIEW`` line so Genie / dashboards can read the
   semantic description.

4. The new gold DDL files register ``mip.gold.borrower_lifecycle_state``
   and ``mip.gold.funnel_snapshot_daily`` with the expected column
   contract (just the presence of the column names — full column-type
   checks live in the gold DDL contract test where they belong).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
METRIC_VIEW_DIR = REPO_ROOT / "sql" / "metric_views"
DDL_DIR = REPO_ROOT / "sql" / "ddl"


def _strip_line_comments(sql_text: str) -> str:
    out: list[str] = []
    for line in sql_text.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _read_sql(path: Path) -> tuple[str, str]:
    raw = path.read_text(encoding="utf-8")
    return raw, _strip_line_comments(raw)


# -----------------------------------------------------------------------------
# segment_performance_metric_view.sql
# -----------------------------------------------------------------------------
class TestSegmentPerformanceMetricView:
    view_path = METRIC_VIEW_DIR / "segment_performance_metric_view.sql"

    def test_file_is_non_empty(self) -> None:
        assert self.view_path.exists(), "segment_performance_metric_view.sql missing"
        assert self.view_path.stat().st_size > 200, "view file is a stub"

    def test_declares_view(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+VIEW\s+mip\.semantics\.segment_performance_metric_view",
            sql_nc,
            re.IGNORECASE,
        ), "view must be declared as CREATE OR REPLACE VIEW mip.semantics.segment_performance_metric_view"

    def test_reads_segment_population_and_snapshots(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert "mip.gold.segment_population" in sql_nc, (
            "view must still source counts from mip.gold.segment_population"
        )
        assert "mip.gold.funnel_snapshot_daily" in sql_nc, (
            "view must join mip.gold.funnel_snapshot_daily to derive approval_rate / delta_vs_prior_*"
        )

    def test_publishes_approval_and_outreach_rates(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        # The column must appear as an AS alias, not merely in a comment.
        assert re.search(
            r"\bAS\s+approval_rate\b", sql_nc, re.IGNORECASE
        ), "view must expose an `approval_rate` column"
        assert re.search(
            r"\bAS\s+outreach_rate\b", sql_nc, re.IGNORECASE
        ), "view must expose an `outreach_rate` column"

    def test_approval_rate_formula(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        # approved / count × 100, ROUNDed to 2dp, NULLIF-safe.
        pattern = re.compile(
            r"ROUND\s*\(\s*100\.0\s*\*\s*"
            r"COALESCE\s*\(\s*t\.approved_borrowers\s*,\s*0\s*\)\s*/\s*"
            r"NULLIF\s*\(\s*sp\.count\s*,\s*0\s*\)\s*,\s*2\s*\)",
            re.IGNORECASE | re.DOTALL,
        )
        assert pattern.search(sql_nc), (
            "approval_rate must compute as "
            "ROUND(100.0 * COALESCE(t.approved_borrowers, 0) / NULLIF(sp.count, 0), 2)"
        )

    def test_publishes_delta_vs_prior_columns(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        for col in (
            "delta_vs_prior_count",
            "delta_vs_prior_approved",
            "delta_vs_prior_in_the_money",
        ):
            assert re.search(rf"\bAS\s+{col}\b", sql_nc, re.IGNORECASE), (
                f"view must expose `{col}` column"
            )

    def test_has_semantic_comment(self) -> None:
        raw, _ = _read_sql(self.view_path)
        assert "COMMENT ON VIEW mip.semantics.segment_performance_metric_view" in raw, (
            "view must publish a `COMMENT ON VIEW` line for Genie / dashboards"
        )


# -----------------------------------------------------------------------------
# lead_generation_metric_view.sql
# -----------------------------------------------------------------------------
class TestLeadGenerationMetricView:
    view_path = METRIC_VIEW_DIR / "lead_generation_metric_view.sql"

    def test_file_is_non_empty(self) -> None:
        assert self.view_path.exists(), "lead_generation_metric_view.sql missing"
        assert self.view_path.stat().st_size > 200

    def test_declares_view(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+VIEW\s+mip\.semantics\.lead_generation_metric_view",
            sql_nc,
            re.IGNORECASE,
        )

    def test_joins_lifecycle_state(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert "mip.gold.borrower_lifecycle_state" in sql_nc, (
            "view must join gold.borrower_lifecycle_state for approval / outreach columns"
        )
        # LEFT JOIN so borrowers without a Lakebase decision still appear.
        assert re.search(
            r"LEFT\s+JOIN\s+mip\.gold\.borrower_lifecycle_state",
            sql_nc,
            re.IGNORECASE,
        ), "lifecycle state must be LEFT JOINed so unreviewed borrowers are not dropped"

    def test_coalesces_pending_default(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert re.search(r"COALESCE\s*\(\s*ls\.approval_status\s*,\s*'pending'", sql_nc), (
            "approval_status must fall back to 'pending' when no Lakebase row exists"
        )
        assert re.search(r"COALESCE\s*\(\s*ls\.outreach_status\s*,\s*'none'", sql_nc), (
            "outreach_status must fall back to 'none' when no Lakebase row exists"
        )

    def test_publishes_row_and_aggregate_columns(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        for col in (
            "segment_codes",
            "primary_segment",
            "approval_status",
            "outreach_status",
            "approval_rate",
            "outreach_rate",
            "delta_vs_prior_count",
        ):
            assert re.search(rf"\bAS\s+{col}\b", sql_nc, re.IGNORECASE), (
                f"lead_generation_metric_view must expose `{col}` column"
            )

    def test_publishes_listing_and_propensity_columns(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        for col in (
            "listed_for_sale",
            "listing_status_category",
            "listing_price",
            "listing_days_on_market",
            "has_heloc_propensity_trigger",
            "heloc_propensity_score",
            "has_refi_propensity_trigger",
            "refi_propensity_score",
        ):
            assert col in sql_nc, f"lead_generation_metric_view must expose `{col}`"

    def test_preserves_borrower_grain_without_segment_explode(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert "LATERAL VIEW EXPLODE" not in sql_nc.upper()
        assert "lp.segment_codes" in sql_nc

    def test_has_semantic_comment(self) -> None:
        raw, _ = _read_sql(self.view_path)
        assert "COMMENT ON VIEW mip.semantics.lead_generation_metric_view" in raw

    def test_rank_bucket_does_not_mislabel_rows_outside_top_10000(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert "WHEN lp.rank_overall <= 10000 THEN 'top_10000'" in sql_nc
        assert "ELSE                               'outside_top_10000'" in sql_nc


# -----------------------------------------------------------------------------
# borrower_opportunity_metric_view.sql
# -----------------------------------------------------------------------------
class TestBorrowerOpportunityMetricView:
    view_path = METRIC_VIEW_DIR / "borrower_opportunity_metric_view.sql"

    def test_file_is_non_empty(self) -> None:
        assert self.view_path.exists(), "borrower_opportunity_metric_view.sql missing"
        assert self.view_path.stat().st_size > 200

    def test_declares_borrower_grain_view(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+VIEW\s+mip\.semantics\.borrower_opportunity_metric_view",
            sql_nc,
            re.IGNORECASE,
        )
        assert "mip.gold.borrower_360" in sql_nc
        assert "LATERAL VIEW EXPLODE" not in sql_nc
        assert "b.segment_codes" in sql_nc
        assert re.search(r"\bAS\s+primary_segment\b", sql_nc, re.IGNORECASE)
        assert re.search(r"\bAS\s+segment\b", sql_nc, re.IGNORECASE), (
            "view must expose deprecated `segment` alias for stale dashboard / Genie SQL"
        )

    def test_has_semantic_comment(self) -> None:
        raw, _ = _read_sql(self.view_path)
        assert "COMMENT ON VIEW mip.semantics.borrower_opportunity_metric_view" in raw

    def test_publishes_public_safe_lender_overlay_dimensions(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        for col in ("is_current_customer", "is_former_customer", "is_competitor_lien", "current_lender_ref"):
            assert col in sql_nc, f"borrower opportunity view must expose `{col}`"

    def test_publishes_listing_and_propensity_dimensions(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        for col in (
            "has_permit",
            "listed_for_sale",
            "listing_status_category",
            "listing_price",
            "listing_days_on_market",
            "has_heloc_propensity_trigger",
            "heloc_propensity_score",
            "has_refi_propensity_trigger",
            "refi_propensity_score",
        ):
            assert col in sql_nc, f"borrower opportunity view must expose `{col}`"

    def test_deploy_manifest_stays_in_sync_for_lender_overlay_dimensions(self) -> None:
        raw, _ = _read_sql(DDL_DIR / "005_semantics_views.sql")
        for col in (
            "b.is_former_customer",
            "b.is_competitor_lien",
            "b.current_lender_ref",
            "b.listed_for_sale",
            "b.has_heloc_propensity_trigger",
            "b.refi_propensity_score",
        ):
            assert col in raw, f"005_semantics_views.sql missing `{col}`"


# -----------------------------------------------------------------------------
# portfolio_headline_metric_view.sql (S1 headline KPI home)
# -----------------------------------------------------------------------------
class TestPortfolioHeadlineMetricView:
    view_path = METRIC_VIEW_DIR / "portfolio_headline_metric_view.sql"

    def test_file_is_non_empty(self) -> None:
        assert self.view_path.exists(), "portfolio_headline_metric_view.sql missing"
        assert self.view_path.stat().st_size > 200

    def test_declares_borrower_grain_view(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+VIEW\s+mip\.semantics\.portfolio_headline_metric_view",
            sql_nc,
            re.IGNORECASE,
        )
        assert "mip.gold.borrower_360" in sql_nc

    def test_headline_measures_use_canonical_primitives(self) -> None:
        """The high-opportunity indicator and the display band must come from
        the canonical UC functions — a literal threshold here is drift
        (S1; tests/unit/test_score_threshold_guard.py)."""
        _, sql_nc = _read_sql(self.view_path)
        assert "mip.gold.fn_high_opportunity(b.opportunity_score)" in sql_nc
        assert "mip.gold.fn_score_band(b.opportunity_score)" in sql_nc

    def test_publishes_headline_indicator_columns(self) -> None:
        _, sql_nc = _read_sql(self.view_path)
        for col in (
            "is_high_opportunity",
            "score_band",
            "offer_available",
            "offer_recommended",
        ):
            assert re.search(rf"\bAS\s+{col}\b", sql_nc, re.IGNORECASE), (
                f"portfolio_headline_metric_view must expose `{col}`"
            )
        for passthrough in ("b.opportunity_score", "b.in_the_money"):
            assert passthrough in sql_nc

    def test_offers_available_means_non_null_next_best_offer(self) -> None:
        """"Offers available" is COUNT of borrowers with a non-null
        fn_next_best_offer decision — pinned as the recommended_offer_code
        IS NOT NULL indicator."""
        _, sql_nc = _read_sql(self.view_path)
        assert re.search(
            r"\(\s*b\.recommended_offer_code\s+IS\s+NOT\s+NULL\s*\)\s+AS\s+offer_available",
            sql_nc,
            re.IGNORECASE,
        )

    def test_passes_through_preview_predicate_vocabulary(self) -> None:
        """Every column build_preview_predicates can emit must survive the
        view, or criteria-filtered previews fail at the warehouse."""
        _, sql_nc = _read_sql(self.view_path)
        for col in (
            "state",
            "is_owner_occupied",
            "current_lien_balance",
            "second_pos_amount",
            "related_property_count",
            "listed_for_sale",
            "has_heloc_propensity_trigger",
            "is_current_customer",
            "is_former_customer",
            "is_competitor_lien",
            "current_lender_ref",
            "recommended_offer_code",
            "loan_product_type",
            "origination_channel",
            "equity_pct",
            "marketing_eligible",
            "consent_status",
            "suppression_reason",
            "dnc",
            "eligible_recontact_at",
            "last_touch_at",
        ):
            assert col in sql_nc, f"view must pass through `{col}` for criteria push-down"

    def test_has_semantic_comment(self) -> None:
        raw, _ = _read_sql(self.view_path)
        assert "COMMENT ON VIEW mip.semantics.portfolio_headline_metric_view" in raw

    def test_deploy_manifest_stays_in_sync(self) -> None:
        raw, _ = _read_sql(DDL_DIR / "005_semantics_views.sql")
        assert "CREATE OR REPLACE VIEW mip.semantics.portfolio_headline_metric_view" in raw
        for needle in (
            "mip.gold.fn_high_opportunity(b.opportunity_score)",
            "mip.gold.fn_score_band(b.opportunity_score)",
            "AS offer_available",
            "AS offer_recommended",
        ):
            assert needle in raw, f"005_semantics_views.sql missing `{needle}`"

    def test_preview_repository_aggregates_over_this_view(self) -> None:
        """Home KPIs must resolve to the named metric view, not an ad-hoc
        aggregate over borrower_360."""
        from backend.services.repositories.databricks_portfolio import (
            DatabricksPortfolioRepository,
        )

        template = DatabricksPortfolioRepository._PREVIEW_SQL_TEMPLATE
        assert "portfolio_headline_metric_view" in template
        assert re.search(r"(>=|≥)\s*\d", template) is None, (
            "preview SQL must not carry a literal score threshold"
        )
        for measure in (
            "is_high_opportunity",
            "offer_recommended",
            "offer_available",
        ):
            assert measure in template


# -----------------------------------------------------------------------------
# Gold DDL additions (lifecycle_state + funnel_snapshot_daily)
# -----------------------------------------------------------------------------
class TestGoldDdlAdditions:
    ddl_path = DDL_DIR / "003_gold_tables.sql"

    def test_lifecycle_state_table_declared(self) -> None:
        _, sql_nc = _read_sql(self.ddl_path)
        assert re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+mip\.gold\.borrower_lifecycle_state",
            sql_nc,
            re.IGNORECASE,
        ), "mip.gold.borrower_lifecycle_state must be declared in 003_gold_tables.sql"

    def test_lifecycle_state_columns(self) -> None:
        _, sql_nc = _read_sql(self.ddl_path)
        # Check the required column names appear within the lifecycle_state
        # declaration block.
        m = re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+mip\.gold\.borrower_lifecycle_state\s*\((?P<body>.+?)\)\s*USING\s+DELTA",
            sql_nc,
            re.IGNORECASE | re.DOTALL,
        )
        assert m is not None, "lifecycle_state CREATE TABLE body not matchable"
        body = m.group("body").lower()
        for col in (
            "borrower_id",
            "approval_status",
            "outreach_status",
            "offer_code",
            "approved_at",
            "outreach_at",
            "synced_at",
            "refreshed_at",
        ):
            assert col in body, f"lifecycle_state missing column `{col}`"

    def test_funnel_snapshot_table_declared(self) -> None:
        _, sql_nc = _read_sql(self.ddl_path)
        assert re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+mip\.gold\.funnel_snapshot_daily",
            sql_nc,
            re.IGNORECASE,
        )

    def test_funnel_snapshot_columns(self) -> None:
        _, sql_nc = _read_sql(self.ddl_path)
        m = re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+mip\.gold\.funnel_snapshot_daily\s*\((?P<body>.+?)\)\s*USING\s+DELTA",
            sql_nc,
            re.IGNORECASE | re.DOTALL,
        )
        assert m is not None, "funnel_snapshot_daily CREATE TABLE body not matchable"
        body = m.group("body").lower()
        for col in (
            "snapshot_date",
            "state",
            "segment_code",
            "addressable_borrowers",
            "in_the_money_borrowers",
            "high_opportunity_borrowers",
            "offer_recommended_borrowers",
            "approved_borrowers",
            "actioned_borrowers",
            "avg_opportunity_score",
            "snapshot_at",
        ):
            assert col in body, f"funnel_snapshot_daily missing column `{col}`"


# -----------------------------------------------------------------------------
# Transformation files exist + use idempotent write pattern
# -----------------------------------------------------------------------------
TRANSFORM_DIR = REPO_ROOT / "sql" / "transformations"


@pytest.mark.parametrize(
    "filename,required",
    [
        (
            "gold_borrower_lifecycle_state.sql",
            ("CREATE OR REPLACE TABLE mip.gold.borrower_lifecycle_state",),
        ),
        (
            "gold_funnel_snapshot_daily.sql",
            ("MERGE INTO mip.gold.funnel_snapshot_daily",),
        ),
    ],
)
def test_transformation_files_use_idempotent_write(
    filename: str, required: tuple[str, ...]
) -> None:
    path = TRANSFORM_DIR / filename
    assert path.exists(), f"transformation file {filename} missing"
    raw = path.read_text(encoding="utf-8")
    for needle in required:
        assert needle in raw, (
            f"{filename} must contain `{needle}` (idempotent write pattern)"
        )
