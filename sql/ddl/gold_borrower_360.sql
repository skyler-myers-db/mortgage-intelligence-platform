-- =============================================================================
-- gold_borrower_360.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.borrower_360` -- the single CLIP-grain
--            projection that backs every Module 0 UI surface (Lead Queue,
--            Segment Intelligence, Borrower 360 dossier, Offer Orchestrator,
--            Approval rail). Super-set of `backend.schemas.lead.LeadSummary`
--            + `Borrower360`; router projects 1:1 with one rename
--            (`clip -> clip_id`) documented below.
--
-- Grain:     One row per `clip`.
-- PK:        clip.
-- Clustering: Liquid cluster on (situs_state, clip). Frontend always
--            state-filters first (6-state footprint) and then drills by
--            score; liquid clustering beats partitioning for this access
--            pattern at ~5M rows. Z-order on `opportunity_score` is applied
--            after the first refresh (out-of-band, not DDL).
--
-- Data contract reference: docs/data-contract-module0.md §3.2.
-- Slice:     module0-real-data-slice3 (gold layer build).
--
-- Source joins (the transformation file executes this; this DDL declares
-- the target shape):
--   - silver.lien_current      (SPINE: current-state lien stack, rates, AVM)
--   - silver.property_master   (on clip: city/state/zip/occupancy/built year)
--   - gold.property_owner_bridge (on owner_link_id: related-property count)
--   - silver.market_rates_weekly (is_latest=TRUE, cross-like: one row)
--
-- PII posture (NON-NEGOTIABLE, docs/governance-real-data-review.md §1):
--   - No raw owner name or street address column exists here.
--   - `display_name` is SYNTHESIZED: 'Owner ' || SUBSTR(owner_name_hash, 1, 8).
--     The hash itself is salted + 256-bit; surfacing 8 hex chars is not
--     reversible.
--   - `subject_property` is city + state + ZIP5 only -- no street, no
--     block-level lat/lon.
--   - `owner_name_hash` is an internal-only join/provenance column; the
--     router strips it before `/api/*` emission. It lives here so the
--     evidence drawer can correlate rows without joining back to silver.
--
-- BLOCKED columns (data-contract §9, hardcoded FALSE until Cotality Building
-- Permits + MLS Listings land):
--   - `has_permit`       : FALSE. `intent_trigger` permit term is 0.
--   - `listed_for_sale`  : FALSE. `fn_next_best_offer` 'purchase' branch
--                          never fires on real data. Mock-mode retains the
--                          B-48295 (Thompson) listed-for-sale dossier.
--
-- Threshold columns: carried alongside the score columns so the WhyPanel
-- can show WHICH thresholds produced the current ITM flag, without a
-- round-trip join to mip_app.thresholds.
--
-- Pydantic mapping (router-level, NOT DDL):
--   gold.borrower_360.clip           -> Borrower360.clip_id
--   gold.borrower_360.market_rate_fraction -> WhyPanel.market_rate
--   Every other column passes through by name. See docs/data-contract §3.2.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS; populated by CTAS
--            (CREATE OR REPLACE TABLE ... AS SELECT) in the transformation
--            file. Full rebuild is the default refresh posture per
--            data-contract §3.2 -- 5M rows in minutes on serverless;
--            precomputed gold is cheap to refresh, and the CTAS guarantees
--            no stale columns from previous schema versions.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.borrower_360 (
  clip                      STRING    NOT NULL COMMENT 'Cotality CLIP. PK. Router maps to Borrower360.clip_id.',
  borrower_id               STRING    NOT NULL COMMENT 'Synthetic stable id from CLIP: CONCAT("B-", LPAD(CONV(ABS(xxhash64(clip)), 10, 36), 13, "0")). Base36 encoding of the 64-bit hash, width 13 => 36^13 slots. No PII.',
  display_name              STRING    NOT NULL COMMENT 'Synthesized label "Owner " || SUBSTR(owner_name_hash, 1, 8). Never a real name.',
  city                      STRING             COMMENT 'Situs city from property_master.',
  state                     STRING    NOT NULL COMMENT 'Situs state (6-state footprint).',
  zip                       STRING             COMMENT '5-digit situs ZIP.',
  situs_cbsa_code           STRING             COMMENT 'CBSA metro code. Gold-only; used for geography drill-down.',
  segment_codes             ARRAY<STRING> NOT NULL COMMENT 'Ordered list of SegmentCode Literals (itm/listed/permit/investor/equity/retention) this borrower belongs to.',
  equity_estimate           BIGINT    NOT NULL COMMENT 'USD: GREATEST(0, avm_value - total_open_lien_balance).',
  equity_pct                INT       NOT NULL COMMENT '0..100. CAST(100 - estimated_cltv AS INT) fallback to derived avm/lien. Feeds fn_in_the_money + fn_next_best_offer.',
  rate_spread_bps           INT       NOT NULL COMMENT 'fn_rate_spread(first_pos_rate, market_rate_fraction). Positive = above market = refi opportunity.',
  market_rate_fraction      DOUBLE    NOT NULL COMMENT 'Fractional market rate from silver.market_rates_weekly WHERE is_latest=TRUE. Router maps to WhyPanel.market_rate.',
  opportunity_score         INT       NOT NULL COMMENT 'fn_lead_score output. 0..100.',
  confidence                INT       NOT NULL COMMENT 'ROUND(mean(5 sub-scores)). 0..100. Matches mock_data._build_borrower.',
  recommended_offer_code    STRING    NOT NULL COMMENT 'fn_next_best_offer output (lowercase code). Router resolves to human label via NBO_PRODUCT_LABELS.',
  recommended_offer         STRING    NOT NULL COMMENT 'Human label for recommended_offer_code (resolved in SQL via product_labels map).',
  why_now                   STRING    NOT NULL COMMENT 'Deterministic one-sentence template per offer_code. No PII. See data-contract §6.',
  evidence_ids              ARRAY<STRING> NOT NULL COMMENT 'Ordered evidence_ids from gold.evidence_events (ORDER BY signal_rank).',
  approval_status           STRING    NOT NULL COMMENT 'Default "pending"; Lakebase authoritative for actual state.',
  owner_link_id             STRING             COMMENT 'Cotality Owner Link id. Opaque Cotality identifier; not a direct PII risk.',
  subject_property          STRING    NOT NULL COMMENT 'Synthetic city/state/ZIP5 string. No street address.',
  avm_value                 BIGINT    NOT NULL COMMENT 'COALESCE(avm_value, 0).',
  current_lien_balance      BIGINT    NOT NULL COMMENT 'COALESCE(total_open_lien_balance, 0).',
  current_rate              DOUBLE    NOT NULL COMMENT 'PERCENT form (5.75, not 0.0575). Matches Pydantic current_rate and mock_data convention.',
  ltv                       INT       NOT NULL COMMENT '0..100 int. ROUND(100 * total_open_lien_balance / avm_value).',
  related_property_count    INT       NOT NULL COMMENT 'COALESCE(property_owner_bridge.related_property_count, 1).',
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'owner_occupancy_code = "O". Feeds fit sub-score.',
  is_absentee               BOOLEAN   NOT NULL COMMENT 'property_master.is_absentee. Feeds investor branch.',
  is_corporate_owner        BOOLEAN   NOT NULL COMMENT 'property_master.owner_is_corporate. Feeds investor branch.',
  has_permit                BOOLEAN   NOT NULL COMMENT 'BLOCKED (data-contract §9) -- hardcoded FALSE until Cotality Building Permits product lands. intent_trigger permit term is 0.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'BLOCKED (data-contract §9) -- hardcoded FALSE until Cotality MLS Listings lands. fn_next_best_offer purchase branch never fires on real data.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'Derived: related_property_count >= 2 OR is_corporate_owner OR is_absentee.',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'UPPER(first_pos_lender_current) LIKE "%SUMMIT%". Default tenant lender per CLAUDE.md. Production swaps to ref.lender_dictionary join.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'first_pos_lender_current IS NOT NULL AND NOT is_current_customer. 263K-row recapture universe.',
  second_pos_amount         BIGINT             COMMENT '2nd-lien balance passthrough; NULL / 0 when no 2nd-lien. Feeds "equity" segment predicate (HELOC-clean only).',
  first_pos_loan_type       STRING             COMMENT '1st-lien loan type code (CONV / FHA / VA / etc). Feeds fit sub-score.',
  owner_name_hash           STRING    NOT NULL COMMENT 'sha2(LOWER(TRIM(name)) || salt, 256) propagated from silver.property_master. Internal only -- router strips before /api/*.',
  min_spread_bps_applied    INT       NOT NULL COMMENT 'Threshold applied when computing ITM for THIS refresh. Carried so WhyPanel.min_spread_bps is the run-specific value.',
  min_equity_pct_applied    INT       NOT NULL COMMENT 'Equity threshold applied this refresh.',
  in_the_money              BOOLEAN   NOT NULL COMMENT 'fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied).',
  trigger_timeline_json     STRING    NOT NULL COMMENT 'JSON-encoded top-3 EvidenceEvent rows pre-materialized to avoid per-row fan-out at read. Router json_decodes into List[EvidenceEvent].',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp; used as EvidenceDrawer footer provenance chip.'
)
USING DELTA
CLUSTER BY (state, clip)
COMMENT 'CLIP-grain borrower projection that backs every Module 0 UI surface. Joins silver.lien_current (spine) + silver.property_master + gold.property_owner_bridge + silver.market_rates_weekly(is_latest). Synthesized display_name, no raw PII, has_permit + listed_for_sale BLOCKED. See docs/data-contract-module0.md §3.2 + docs/governance-real-data-review.md §1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
