-- =============================================================================
-- gold_equity_spread_points.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.equity_spread_points` via CTAS. One row per
--            borrower_360 record inside the economics plot domain
--            (equity_pct 0..100, rate_spread_bps -100..400), carrying the
--            equity position, canonical fn_rate_spread output (as already
--            materialized on borrower_360), the fn_score_band band, and the
--            precomputed density-bin coordinates the Analytics -> Economics
--            scatter reads (server-side bins for overview, bounded real
--            points for a zoomed viewport).
--
-- Grain:     One row per borrower_id.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent full rebuild;
--            matches the sibling gold CTAS posture. Runs after
--            assert_borrower_360_fresh in the mip_refresh_scores job.
-- Slice:     s7-economics-scatter.
--
-- Bin math:  equity_bin_pct  = FLOOR(equity_pct / 5) * 5       (5-pct bins)
--            spread_bin_bps  = FLOOR(rate_spread_bps / 25) * 25 (25-bps bins)
--            FLOOR keeps negative spreads on their bin's lower edge
--            (e.g. -1 bps -> bin -25). Python parity lives in
--            backend/services/economics_scatter.py; golden fixtures in
--            tests/fixtures/equity_spread_bins_golden.json pin both.
--
-- Domain:    The -100..400 bps / 0..100 pct window matches the predicates
--            the analytics economics surface has always applied to the
--            rate-spread histogram and the equity x spread scatter, so the
--            precomputed table and the on-the-fly aggregates tell one story.
--
-- CTAS re-declares clustering/comments/properties because COR TABLE drops
-- DDL metadata on every refresh (2026-06-11 audit P2-8). Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_equity_spread_points.sql.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.equity_spread_points
CLUSTER BY (equity_bin_pct, spread_bin_bps)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
SELECT
  b.borrower_id,
  b.display_name,
  b.state,
  -- Display segment for the dot: first (strongest) segment code, NULL when
  -- the borrower is unsegmented. The SIZE guard keeps [0] off empty arrays.
  CASE WHEN SIZE(b.segment_codes) > 0 THEN b.segment_codes[0] END AS primary_segment_code,
  b.segment_codes,
  b.is_current_customer,
  b.is_former_customer,
  b.is_competitor_lien,
  b.current_lender_ref,
  b.equity_pct,
  b.rate_spread_bps,
  b.opportunity_score,
  mip.gold.fn_score_band(b.opportunity_score) AS score_band,
  b.in_the_money,
  CAST(FLOOR(b.equity_pct / 5) * 5 AS INT)        AS equity_bin_pct,
  CAST(FLOOR(b.rate_spread_bps / 25) * 25 AS INT) AS spread_bin_bps,
  -- Shared refresh_at captured once per run. See audit-holes-round-3 #7.
  (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1) AS refreshed_at
FROM mip.gold.borrower_360 AS b
WHERE b.state IS NOT NULL
  AND b.equity_pct BETWEEN 0 AND 100
  AND b.rate_spread_bps BETWEEN -100 AND 400;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh. COMMENT ON
-- COLUMN keeps the Genie grounding / asset-page comments refresh-stable;
-- the SQL file task executes the statements in order.
COMMENT ON COLUMN mip.gold.equity_spread_points.borrower_id IS 'Masked synthetic borrower id (B-[0-9A-Z]{13}) carried from gold.borrower_360. PK. No raw CLIP in this table.';
COMMENT ON COLUMN mip.gold.equity_spread_points.display_name IS 'Synthesized label carried from gold.borrower_360.display_name. Never a real name.';
COMMENT ON COLUMN mip.gold.equity_spread_points.state IS '2-char USPS situs state carried from gold.borrower_360.';
COMMENT ON COLUMN mip.gold.equity_spread_points.primary_segment_code IS 'First entry of gold.borrower_360.segment_codes (display segment for the dot). NULL when the borrower is unsegmented.';
COMMENT ON COLUMN mip.gold.equity_spread_points.segment_codes IS 'Full ordered SegmentCode list carried from gold.borrower_360 so segment filters apply without a join.';
COMMENT ON COLUMN mip.gold.equity_spread_points.is_current_customer IS 'Carried from gold.borrower_360 for the lender-relationship filter.';
COMMENT ON COLUMN mip.gold.equity_spread_points.is_former_customer IS 'Carried from gold.borrower_360 for the lender-relationship filter.';
COMMENT ON COLUMN mip.gold.equity_spread_points.is_competitor_lien IS 'Carried from gold.borrower_360 for the lender-relationship filter.';
COMMENT ON COLUMN mip.gold.equity_spread_points.current_lender_ref IS 'Public-demo-safe current-servicer reference carried from gold.borrower_360 for the target-lien-holder filter.';
COMMENT ON COLUMN mip.gold.equity_spread_points.equity_pct IS '0..100 available-equity percentage carried from gold.borrower_360.equity_pct (scatter x-axis).';
COMMENT ON COLUMN mip.gold.equity_spread_points.rate_spread_bps IS 'fn_rate_spread output carried from gold.borrower_360.rate_spread_bps, domain-filtered to -100..400 (scatter y-axis).';
COMMENT ON COLUMN mip.gold.equity_spread_points.opportunity_score IS 'fn_lead_score output carried from gold.borrower_360.opportunity_score. 0..100.';
COMMENT ON COLUMN mip.gold.equity_spread_points.score_band IS 'mip.gold.fn_score_band(opportunity_score): high / med / low. Canonical band vocabulary for dot + bin coloring.';
COMMENT ON COLUMN mip.gold.equity_spread_points.in_the_money IS 'Carried from gold.borrower_360.in_the_money for tooltip + evidence display.';
COMMENT ON COLUMN mip.gold.equity_spread_points.equity_bin_pct IS 'Density-bin lower edge: FLOOR(equity_pct / 5) * 5. 5-pct bins over 0..100.';
COMMENT ON COLUMN mip.gold.equity_spread_points.spread_bin_bps IS 'Density-bin lower edge: FLOOR(rate_spread_bps / 25) * 25. 25-bps bins over -100..400.';
COMMENT ON COLUMN mip.gold.equity_spread_points.refreshed_at IS 'Deterministic refresh anchor from mip.ref.refresh_run_state.';
