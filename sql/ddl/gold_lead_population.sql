-- =============================================================================
-- gold_lead_population.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.lead_population` -- the ranked, quality-
--            filtered cut of gold.borrower_360 that populates the Lead
--            Queue UI. Borrowers with opportunity_score < 50 stay in
--            borrower_360 but do not surface here. Carries two ranking
--            columns so the UI can split "national top 100" vs. "top in
--            my state."
--
-- Grain:     One row per CLIP (filtered by opportunity_score >= 50; see
--            the transformation file for ranking). No new PII surfaces
--            beyond what borrower_360 already emits.
-- PK:        clip.
-- Clustering: Liquid cluster on (opportunity_score). The UI always orders
--            by rank_overall or rank_within_state, both monotone with
--            opportunity_score DESC, so cluster-by-score lets the
--            top-of-queue scan skip aggressively.
--
-- Data contract reference: docs/data-contract-module0.md §3.5.
-- Slice:     module0-real-data-slice3 (gold layer build).
-- Pydantic target: backend.schemas.lead.LeadSummary (router emits the
--            LeadSummary subset; Borrower360 dossier reads back to
--            gold.borrower_360 by clip for the super-set columns).
--
-- Why a separate table from borrower_360 (vs. a view)? Two reasons:
--   1. Precomputed rankings make pagination an O(1) index-skip rather
--      than a ROW_NUMBER() OVER computation per request.
--   2. rank_overall + rank_within_state carry a refresh-time snapshot;
--      inserting a borrower mid-session shouldn't shuffle ranks visible
--      to the operator.
--
-- Columns include the exact superset of LeadSummary (to avoid a join-back
-- on the hot path) PLUS rank_overall + rank_within_state + population_
-- version. The columns that are also in borrower_360 are redundantly
-- materialized here on purpose -- single-table reads are cheap and
-- deterministic at request time.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS; populated via CTAS
--            (CREATE OR REPLACE TABLE ... AS SELECT) in the transformation
--            file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.lead_population (
  clip                      STRING    NOT NULL COMMENT 'Cotality CLIP. PK.',
  borrower_id               STRING    NOT NULL COMMENT 'From gold.borrower_360.borrower_id.',
  display_name              STRING    NOT NULL COMMENT 'Synthesized label. No PII.',
  city                      STRING             COMMENT 'Situs city.',
  state                     STRING    NOT NULL COMMENT 'Situs state.',
  zip                       STRING             COMMENT '5-digit situs ZIP.',
  segment_codes             ARRAY<STRING> NOT NULL COMMENT 'Ordered SegmentCode list.',
  equity_estimate           BIGINT    NOT NULL COMMENT 'From gold.borrower_360.',
  equity_pct                INT       NOT NULL COMMENT 'From gold.borrower_360 [0..100]. Used by executive dashboard top-borrower widget.',
  rate_spread_bps           INT       NOT NULL COMMENT 'From gold.borrower_360.',
  opportunity_score         INT       NOT NULL COMMENT 'fn_lead_score output 0..100.',
  confidence                INT       NOT NULL COMMENT 'Mean of 5 sub-scores 0..100.',
  recommended_offer         STRING    NOT NULL COMMENT 'Human label (resolved in gold via product_labels map).',
  why_now                   STRING    NOT NULL COMMENT 'Deterministic template per offer code.',
  evidence_ids              ARRAY<STRING> NOT NULL COMMENT 'Ordered evidence_ids (mirrors gold.borrower_360 for this CLIP).',
  approval_status           STRING    NOT NULL COMMENT '"pending" by default; Lakebase is authoritative for actual state.',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference from borrower_360. Never the raw Cotality lender string.',
  -- Secondary-filter fields (2026-04-23). Carried through from
  -- gold.borrower_360 so /segment-intelligence can run real client-side
  -- predicates against occupancy, owner-link, lien state, and purchase
  -- intent. All are NOT NULL because borrower_360 already emits them NOT
  -- NULL (with BLOCKED FALSE defaults for the permit / listing columns
  -- until the Cotality Building Permits + MLS Delta shares land).
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; drives /segment-intelligence DEMOGRAPHICS filter.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'Carried from gold.borrower_360 (derived: multi-property OR corporate OR absentee).',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; current servicer or first-party servicing relationship to the tenant lender.',
  is_former_customer        BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; historical tenant-lender relationship with no current tenant lien.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; current servicer is known and not the tenant lender.',
  related_property_count    INT       NOT NULL COMMENT 'From gold.borrower_360; drives /segment-intelligence OWNER LINK filter.',
  current_lien_balance      BIGINT    NOT NULL COMMENT 'From gold.borrower_360; drives /segment-intelligence LIEN filter.',
  second_pos_amount         BIGINT             COMMENT 'From gold.borrower_360; nullable (no second-position lien).',
  has_permit                BOOLEAN   NOT NULL COMMENT 'BLOCKED: FALSE until Cotality Building Permits Delta share lands.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'BLOCKED: FALSE until Cotality MLS Listings Delta share lands.',
  rank_overall              INT       NOT NULL COMMENT 'DENSE_RANK OVER (ORDER BY opportunity_score DESC, clip). 1 = highest.',
  rank_within_state         INT       NOT NULL COMMENT 'DENSE_RANK OVER (PARTITION BY state ORDER BY opportunity_score DESC, clip). 1 = highest in state.',
  population_version        STRING    NOT NULL COMMENT 'CONCAT(DATE_FORMAT(refreshed_at, "yyyyMMdd"), "-v1"). EvidenceDrawer footer uses this as a provenance chip.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
)
USING DELTA
CLUSTER BY (opportunity_score)
COMMENT 'Ranked quality-filtered cut of gold.borrower_360 (opportunity_score >= 50). Backs the /leads Lead Queue; pagination is O(1) over rank_overall. See docs/data-contract-module0.md §3.5.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
