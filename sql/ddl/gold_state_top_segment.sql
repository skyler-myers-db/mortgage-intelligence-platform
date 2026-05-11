-- =============================================================================
-- gold_state_top_segment.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.state_top_segment` -- the dominant segment
--            code per state per snapshot, plus the share (%) of the state's
--            borrowers that fall into it. Feeds the `top_segment_code`
--            extension on /api/geo/state-rollups so the USChoroplethMap's
--            per-state tooltips + segment-filter dim logic read from gold
--            geography rollups.
--
-- Grain:     (state, snapshot_date). One row per state per refresh day.
-- PK:        (state, snapshot_date).
-- Clustering: Liquid cluster on (state). Cardinality follows refreshed
--            source coverage; kept for parity with siblings + the gold DDL
--            contract test.
--
-- Source:    `mip.gold.borrower_360.segment_codes` (ARRAY<STRING>) exploded
--            per-state, grouped by segment_code, ranked DESC by count, pick
--            rank 1 per state. Borrowers with empty segment_codes contribute
--            to the state total but not to any top_segment_code.
--
-- top_segment_share_pct = ROUND(100 * segment_count / addressable) clipped
--            to [0, 100]. Zero when the state has no borrowers with any
--            segment set -- the row is still emitted so the frontend sees
--            an explicit NULL segment.
--
-- Data contract reference: docs/data-contract-module0.md §3 (new).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.state_top_segment (
  state                    STRING    NOT NULL COMMENT '2-char USPS state code (uppercase). PK part.',
  top_segment_code         STRING    NOT NULL COMMENT 'Dominant SegmentCode for this state on snapshot_date (itm/listed/permit/investor/equity/retention). "none" when no borrower in the state has a non-empty segment_codes array.',
  top_segment_share_pct    INT       NOT NULL COMMENT '0..100. Share of the state population in the top segment.',
  snapshot_date            DATE      NOT NULL COMMENT 'Refresh date; daily grain. PK part.'
)
USING DELTA
CLUSTER BY (state)
COMMENT 'Per-state top-segment rollup derived from mip.gold.borrower_360.segment_codes (exploded). Feeds the top_segment_code extension on /api/geo/state-rollups.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
