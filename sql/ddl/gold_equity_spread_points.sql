-- =============================================================================
-- gold_equity_spread_points.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.equity_spread_points` -- the precomputed
--            economics scatter surface (S7). One row per borrower record
--            inside the plot domain (equity_pct 0..100, rate_spread_bps
--            -100..400), carrying the borrower's equity position, canonical
--            fn_rate_spread output, fn_score_band band, and precomputed
--            density-bin coordinates so the Analytics -> Economics scatter
--            reads server-side bins for the overview and a bounded set of
--            real borrower points for a zoomed viewport.
--
-- Grain:     One row per borrower_id (mirrors gold.borrower_360 CLIP grain
--            after the domain filter).
-- PK:        borrower_id.
-- Clustering: Liquid cluster on (equity_bin_pct, spread_bin_bps). Both the
--            overview GROUP BY and the zoom viewport range predicates hit
--            the bin columns first, so bin-locality is the read pattern.
--
-- Source:    `mip.gold.borrower_360` only. The filter columns
--            (segment_codes, relationship flags, current_lender_ref) are
--            carried so the analytics filter bag applies to this table
--            without joining back to borrower_360 at read time.
--
-- Bin math:  equity_bin_pct  = FLOOR(equity_pct / 5) * 5      (5-pct bins)
--            spread_bin_bps  = FLOOR(rate_spread_bps / 25) * 25 (25-bps bins)
--            Bin edges are the bin lower bound. Python parity lives in
--            backend/services/economics_scatter.py and is pinned by
--            tests/fixtures/equity_spread_bins_golden.json.
--
-- Safety:    borrower_id is the masked B-[0-9A-Z]{13} synthetic id and
--            display_name is the synthesized owner label -- no raw CLIP,
--            no PII beyond the existing masked pattern crosses this table.
--
-- Data contract reference: docs/data-contract-module0.md §3 (S7 addition).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.equity_spread_points (
  borrower_id           STRING    NOT NULL COMMENT 'Masked synthetic borrower id (B-[0-9A-Z]{13}) carried from gold.borrower_360. PK. No raw CLIP in this table.',
  display_name          STRING    NOT NULL COMMENT 'Synthesized label carried from gold.borrower_360.display_name. Never a real name.',
  state                 STRING    NOT NULL COMMENT '2-char USPS situs state carried from gold.borrower_360.',
  primary_segment_code  STRING             COMMENT 'First entry of gold.borrower_360.segment_codes (display segment for the dot). NULL when the borrower is unsegmented.',
  segment_codes         ARRAY<STRING> NOT NULL COMMENT 'Full ordered SegmentCode list carried from gold.borrower_360 so segment filters apply without a join.',
  is_current_customer   BOOLEAN   NOT NULL COMMENT 'Carried from gold.borrower_360 for the lender-relationship filter.',
  is_former_customer    BOOLEAN   NOT NULL COMMENT 'Carried from gold.borrower_360 for the lender-relationship filter.',
  is_competitor_lien    BOOLEAN   NOT NULL COMMENT 'Carried from gold.borrower_360 for the lender-relationship filter.',
  current_lender_ref    STRING             COMMENT 'Public-demo-safe current-servicer reference carried from gold.borrower_360 for the target-lien-holder filter.',
  equity_pct            INT       NOT NULL COMMENT '0..100 available-equity percentage carried from gold.borrower_360.equity_pct (scatter x-axis).',
  rate_spread_bps       INT       NOT NULL COMMENT 'fn_rate_spread output carried from gold.borrower_360.rate_spread_bps, domain-filtered to -100..400 (scatter y-axis).',
  opportunity_score     INT       NOT NULL COMMENT 'fn_lead_score output carried from gold.borrower_360.opportunity_score. 0..100.',
  score_band            STRING    NOT NULL COMMENT 'mip.gold.fn_score_band(opportunity_score): high / med / low. Canonical band vocabulary for dot + bin coloring.',
  in_the_money          BOOLEAN   NOT NULL COMMENT 'Carried from gold.borrower_360.in_the_money for tooltip + evidence display.',
  equity_bin_pct        INT       NOT NULL COMMENT 'Density-bin lower edge: FLOOR(equity_pct / 5) * 5. 5-pct bins over 0..100.',
  spread_bin_bps        INT       NOT NULL COMMENT 'Density-bin lower edge: FLOOR(rate_spread_bps / 25) * 25. 25-bps bins over -100..400.',
  refreshed_at          TIMESTAMP NOT NULL COMMENT 'Deterministic refresh anchor from mip.ref.refresh_run_state.'
)
USING DELTA
CLUSTER BY (equity_bin_pct, spread_bin_bps)
COMMENT 'Precomputed economics scatter surface (S7): per-borrower equity x rate-spread points with fn_score_band bands and density-bin coordinates. Overview reads GROUP BY (equity_bin_pct, spread_bin_bps); zoom reads bounded real-point sets. Derived from mip.gold.borrower_360 inside the plot domain (equity 0..100, spread -100..400 bps).'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
