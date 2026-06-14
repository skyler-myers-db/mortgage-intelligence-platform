-- =============================================================================
-- gold_segment_population.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.segment_population` -- per-segment
--            aggregate counts backing the Segment Intelligence route and the
--            Executive Dashboard. Two rows per segment per state (by state
--            and by '_ALL'), so the UI can filter to a state or render the
--            national total without re-aggregating at request time.
--
-- Grain:     One row per (segment_code, state) + one row per (segment_code,
--            '_ALL') for the national rollup. Row count follows refreshed
--            source state coverage.
-- PK:        (segment_code, state).
-- Clustering: Liquid cluster on (segment_code).
--
-- Data contract reference: docs/data-contract-module0.md §3.6.
-- Slice:     module0-real-data-slice3 (gold layer build).
-- Pydantic target: backend.schemas.lead.SegmentSummary (router filters to
--            state='_ALL' for the national route or to a specific state.)
--            Column rename at router: delta_vs_prior -> delta.
--
-- Segment membership predicates (canonical SegmentCode registry,
-- data-contract §4):
--   itm         : fn_in_the_money(rate_spread_bps, equity_pct, 75, 15) = TRUE
--   equity      : equity_pct >= 35 AND COALESCE(second_pos_amount, 0) = 0
--                 (clean 1st-lien, HELOC-grade equity). NOTE: predicate
--                 requires second_pos_amount column to be present on
--                 gold.borrower_360 (and it is -- see gold_borrower_360.sql).
--   investor    : related_property_count >= 2 OR is_corporate_owner OR
--                 is_absentee.
--   retention   : is_current_customer = TRUE AND (rate_spread_bps >= 50 OR
--                 is_competitor_lien OR listed_for_sale). In the current CLIP-grain refresh path,
--                 is_current_customer and is_competitor_lien are mutually
--                 exclusive current-servicer flags, so this behaves as
--                 customer AND spread >= 50 until a portfolio-history
--                 recapture join lands.
--   listed      : listed_for_sale = TRUE from current active/under-contract
--                 Cotality MLS rows.
--   permit      : backward-compatible code for customer-facing HELOC Intent:
--                 has_permit OR has_heloc_propensity_trigger. has_permit
--                 remains FALSE until true filed permit rows land.
--
-- `delta_vs_prior` requires a prior-period snapshot. The transformation
-- file maintains a mip.gold.segment_population_prior Delta table
-- (daily partition-rollup). On first refresh, delta_vs_prior is emitted
-- as '+0%' as a safe default.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS; populated via CTAS in
--            the transformation file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.segment_population (
  segment_code    STRING    NOT NULL COMMENT 'itm / listed / permit / investor / equity / retention. Matches SegmentCode Literal exactly; permit is the backward-compatible code for customer-facing HELOC Intent.',
  state           STRING    NOT NULL COMMENT '2-char state code from refreshed source coverage or "_ALL" for national rollup.',
  name            STRING    NOT NULL COMMENT 'Static label per segment_code (e.g., "In the Money").',
  count           INT       NOT NULL COMMENT 'Member count for this (segment, state) cell.',
  delta_vs_prior  STRING    NOT NULL COMMENT 'Quarter-over-quarter delta as "+NN%" / "-NN%". Router maps to SegmentSummary.delta. "+0%" on first refresh.',
  avg_score       INT       NOT NULL COMMENT 'CAST(ROUND(AVG(opportunity_score)) AS INT) over the segment cell.',
  description     STRING    NOT NULL COMMENT 'Static description per segment_code.',
  color           STRING    NOT NULL COMMENT 'Hex color for segment tile.',
  refreshed_at    TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
)
USING DELTA
CLUSTER BY (segment_code)
COMMENT 'Per-segment aggregate counts with state and "_ALL" rollup. Feeds the Segment Intelligence route + Executive Dashboard. listed is live from MLS; permit is the backward-compatible code for HELOC Intent while true building-permit filings remain pending. See docs/data-contract-module0.md §3.6.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- Prior-period snapshot table: rolled forward daily by the transformation's
-- pre-step so delta_vs_prior has something to compare against. One row per
-- (segment_code, state, snapshot_date).

CREATE TABLE IF NOT EXISTS mip.gold.segment_population_prior (
  segment_code    STRING    NOT NULL COMMENT 'Matches segment_population.segment_code.',
  state           STRING    NOT NULL COMMENT 'Matches segment_population.state.',
  snapshot_date   DATE      NOT NULL COMMENT 'Date the count was snapshotted (daily granularity).',
  count           INT       NOT NULL COMMENT 'Member count on snapshot_date.',
  avg_score       INT       NOT NULL COMMENT 'Avg opportunity_score on snapshot_date.'
)
USING DELTA
CLUSTER BY (segment_code, snapshot_date)
COMMENT 'Daily snapshot of segment counts; source for segment_population.delta_vs_prior. Maintained by the gold transformation pre-step (CTAS appends per snapshot_date).'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
