-- =============================================================================
-- gold_lead_population.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.lead_population` via CTAS. Ranked
--            quality-filtered cut of gold.borrower_360
--            (opportunity_score >= 50), with both national rank and
--            within-state rank pre-materialized.
--
-- Grain:     One row per clip (subset of gold.borrower_360).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.5.
--
-- Filtering: WHERE opportunity_score >= 50. Every borrower that clears
--            the quality floor lands in lead_population; the UI paginates.
--            A real lender's queue is sized by opportunity quality, not
--            by a UI convenience number, so no arbitrary row cap.
--
-- Ranking:
--   rank_overall      = DENSE_RANK() OVER (ORDER BY opportunity_score DESC, clip)
--   rank_within_state = DENSE_RANK() OVER (PARTITION BY state
--                                          ORDER BY opportunity_score DESC, clip)
--   The secondary `, clip` in ORDER BY is a deterministic tiebreaker --
--   otherwise ties within a state would shuffle between refreshes.
--
-- population_version: CONCAT(DATE_FORMAT(refreshed_at, 'yyyyMMdd'), '-v1').
--   When the gold schema bumps, bump '-v1' to '-v2' etc. in one place here.
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_lead_population.sql; the
-- column list order matches the final SELECT projection 1:1.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.lead_population
CLUSTER BY (opportunity_score)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH ranked AS (
  SELECT
    b.clip,
    b.borrower_id,
    b.display_name,
    b.city,
    b.state,
    b.zip,
    b.segment_codes,
    b.equity_estimate,
    -- equity_pct is carried through from borrower_360 so the executive
    -- dashboard's top-borrowers widget can read percent equity without
    -- joining back. Nightly "Lakeview widgets resolve" test asserts the
    -- column exists on lead_population; adding it here is the canonical
    -- fix to CI failures on 2026-04-22 (UNRESOLVED_COLUMN equity_pct).
    b.equity_pct,
    b.rate_spread_bps,
    b.opportunity_score,
    b.confidence,
    b.recommended_offer_code,
    b.recommended_offer,
    b.why_now,
    b.evidence_ids,
    b.approval_status,
    b.current_lender_ref,
    -- Secondary-filter fields (2026-04-23). Carried through from
    -- gold.borrower_360 so /segment-intelligence runs real client-side
    -- predicates against occupancy, owner-link (related properties),
    -- lien state, purchase intent, and HELOC propensity. listed_for_sale is
    -- live from Cotality MLS rows. has_permit remains FALSE until a true
    -- filed-permit source lands; Cotality HELOC propensity is carried as a
    -- separate model signal.
    b.is_owner_occupied,
    b.is_investor,
    b.is_current_customer,
    b.is_former_customer,
    b.is_competitor_lien,
    b.related_property_count,
    -- S1.1 multi-owner caveat columns. has_unresolved_owner rows are already
    -- marketing_eligible = FALSE upstream; carrying the flags lets the UI
    -- show the multi-owner / unresolved-owner caveat chips without a join.
    b.owner_count,
    b.has_unresolved_owner,
    b.primary_owner_entity_type,
    b.current_lien_balance,
    b.second_pos_amount,
    b.has_permit,
    b.listed_for_sale,
    b.listing_status_category,
    b.listing_status_description,
    b.listing_date,
    b.listing_status_date,
    b.listing_price,
    b.listing_days_on_market,
    b.listing_service,
    b.heloc_propensity_score,
    b.heloc_propensity_run_date,
    b.has_heloc_propensity_trigger,
    b.refi_propensity_score,
    b.refi_propensity_run_date,
    b.has_refi_propensity_trigger,
    b.loan_product_type,
    b.origination_channel,
    b.conforming_loan_limit_applied,
    b.marketing_eligible,
    b.consent_status,
    b.suppression_reason,
    b.last_touch_at,
    b.eligible_recontact_at,
    b.dnc,
    b.eligibility_source,
    DENSE_RANK() OVER (ORDER BY b.opportunity_score DESC, b.clip) AS rank_overall,
    DENSE_RANK() OVER (PARTITION BY b.state
                       ORDER BY b.opportunity_score DESC, b.clip) AS rank_within_state,
    b.refreshed_at
  FROM mip.gold.borrower_360 AS b
  WHERE b.opportunity_score >= 50
)
SELECT
  clip,
  borrower_id,
  display_name,
  city,
  state,
  zip,
  segment_codes,
  equity_estimate,
  equity_pct,
  rate_spread_bps,
  opportunity_score,
  confidence,
  recommended_offer_code,
  recommended_offer,
  why_now,
  evidence_ids,
  approval_status,
  current_lender_ref,
  is_owner_occupied,
  is_investor,
  is_current_customer,
  is_former_customer,
  is_competitor_lien,
  related_property_count,
  owner_count,
  has_unresolved_owner,
  primary_owner_entity_type,
  current_lien_balance,
  second_pos_amount,
  has_permit,
  listed_for_sale,
  listing_status_category,
  listing_status_description,
  listing_date,
  listing_status_date,
  listing_price,
  listing_days_on_market,
  listing_service,
  heloc_propensity_score,
  heloc_propensity_run_date,
  has_heloc_propensity_trigger,
  refi_propensity_score,
  refi_propensity_run_date,
  has_refi_propensity_trigger,
  loan_product_type,
  origination_channel,
  conforming_loan_limit_applied,
  marketing_eligible,
  consent_status,
  suppression_reason,
  last_touch_at,
  eligible_recontact_at,
  dnc,
  eligibility_source,
  rank_overall,
  rank_within_state,
  CONCAT(DATE_FORMAT(refreshed_at, 'yyyyMMdd'), '-v1') AS population_version,
  refreshed_at
-- (clip is already in the SELECT above as column 1; the LeadSummary
-- repository reads `clip` from this table directly -- no second
-- projection needed. 2026-04-22: the FE-boundary LeadSummary now
-- carries a `clip` field that previously the frontend derived as
-- `clip_${borrower_id.toLowerCase()...}`. Surfaces the real Cotality
-- CLIP so the segment-row preview and Borrower 360 agree.)
FROM ranked;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.lead_population.clip IS 'Cotality CLIP. PK.';
COMMENT ON COLUMN mip.gold.lead_population.borrower_id IS 'From gold.borrower_360.borrower_id.';
COMMENT ON COLUMN mip.gold.lead_population.display_name IS 'Synthesized label. No PII.';
COMMENT ON COLUMN mip.gold.lead_population.city IS 'Situs city.';
COMMENT ON COLUMN mip.gold.lead_population.state IS 'Situs state.';
COMMENT ON COLUMN mip.gold.lead_population.zip IS '5-digit situs ZIP.';
COMMENT ON COLUMN mip.gold.lead_population.segment_codes IS 'Ordered SegmentCode list.';
COMMENT ON COLUMN mip.gold.lead_population.equity_estimate IS 'From gold.borrower_360.';
COMMENT ON COLUMN mip.gold.lead_population.equity_pct IS 'From gold.borrower_360 [0..100]. Used by executive dashboard top-borrower widget.';
COMMENT ON COLUMN mip.gold.lead_population.rate_spread_bps IS 'From gold.borrower_360.';
COMMENT ON COLUMN mip.gold.lead_population.opportunity_score IS 'fn_lead_score output 0..100.';
COMMENT ON COLUMN mip.gold.lead_population.confidence IS 'Mean of 5 sub-scores 0..100.';
COMMENT ON COLUMN mip.gold.lead_population.recommended_offer_code IS 'fn_next_best_offer output code; canonical offer enum for operational filters and audit grouping.';
COMMENT ON COLUMN mip.gold.lead_population.recommended_offer IS 'Human label (resolved in gold via product_labels map).';
COMMENT ON COLUMN mip.gold.lead_population.why_now IS 'Deterministic template per offer code.';
COMMENT ON COLUMN mip.gold.lead_population.evidence_ids IS 'Ordered evidence_ids (mirrors gold.borrower_360 for this CLIP).';
COMMENT ON COLUMN mip.gold.lead_population.approval_status IS 'Approval state mirrored from Lakebase at refresh time (inherited from borrower_360); Lakebase remains authoritative between refreshes.';
COMMENT ON COLUMN mip.gold.lead_population.current_lender_ref IS 'Public-demo-safe current-servicer reference from borrower_360. Never the raw Cotality lender string.';
COMMENT ON COLUMN mip.gold.lead_population.is_owner_occupied IS 'From gold.borrower_360; drives /segment-intelligence DEMOGRAPHICS filter.';
COMMENT ON COLUMN mip.gold.lead_population.is_investor IS 'Carried from gold.borrower_360 (derived: multi-property OR corporate OR absentee).';
COMMENT ON COLUMN mip.gold.lead_population.is_current_customer IS 'From gold.borrower_360; current servicer or first-party servicing relationship to the tenant lender.';
COMMENT ON COLUMN mip.gold.lead_population.is_former_customer IS 'From gold.borrower_360; historical tenant-lender relationship with no current tenant lien.';
COMMENT ON COLUMN mip.gold.lead_population.is_competitor_lien IS 'From gold.borrower_360; current servicer is known and not the tenant lender.';
COMMENT ON COLUMN mip.gold.lead_population.related_property_count IS 'From gold.borrower_360; drives /segment-intelligence OWNER LINK filter.';
COMMENT ON COLUMN mip.gold.lead_population.owner_count IS 'From gold.borrower_360 (S1.1); occupied owner slots on the CLIP (max 4). Drives the multi-owner caveat chip.';
COMMENT ON COLUMN mip.gold.lead_population.has_unresolved_owner IS 'From gold.borrower_360 (S1.1); TRUE when any owner slot is unresolved. Such rows are never marketing_eligible (suppression_reason unresolved_owner).';
COMMENT ON COLUMN mip.gold.lead_population.primary_owner_entity_type IS 'From gold.borrower_360 (S1.1); slot-1 owner entity type: individual | trust | llc | unresolved.';
COMMENT ON COLUMN mip.gold.lead_population.current_lien_balance IS 'From gold.borrower_360; drives /segment-intelligence LIEN filter.';
COMMENT ON COLUMN mip.gold.lead_population.second_pos_amount IS 'From gold.borrower_360; nullable (no second-position lien).';
COMMENT ON COLUMN mip.gold.lead_population.has_permit IS 'Filed building-permit flag. FALSE until a true Cotality Building Permits source table is present.';
COMMENT ON COLUMN mip.gold.lead_population.listed_for_sale IS 'TRUE when borrower_360 has a current active/under-contract Cotality MLS listing row.';
COMMENT ON COLUMN mip.gold.lead_population.listing_status_category IS 'Cotality standardized MLS listing status category.';
COMMENT ON COLUMN mip.gold.lead_population.listing_status_description IS 'Display-safe Cotality MLS status description. No address, remarks, agent, phone, or email.';
COMMENT ON COLUMN mip.gold.lead_population.listing_date IS 'MLS listing date.';
COMMENT ON COLUMN mip.gold.lead_population.listing_status_date IS 'Most recent MLS status/change date.';
COMMENT ON COLUMN mip.gold.lead_population.listing_price IS 'Current MLS listing price in USD, when supplied.';
COMMENT ON COLUMN mip.gold.lead_population.listing_days_on_market IS 'MLS days-on-market value, when supplied.';
COMMENT ON COLUMN mip.gold.lead_population.listing_service IS 'MLS/listing service label when supplied.';
COMMENT ON COLUMN mip.gold.lead_population.heloc_propensity_score IS 'Cotality HELOC propensity score, 0..999 in the current feed. Model signal, not a permit filing.';
COMMENT ON COLUMN mip.gold.lead_population.heloc_propensity_run_date IS 'Cotality HELOC propensity model run date.';
COMMENT ON COLUMN mip.gold.lead_population.has_heloc_propensity_trigger IS 'TRUE when heloc_propensity_score >= 700. Drives HELOC Intent without setting has_permit.';
COMMENT ON COLUMN mip.gold.lead_population.refi_propensity_score IS 'Cotality refinance propensity score, 0..999 in the current feed.';
COMMENT ON COLUMN mip.gold.lead_population.refi_propensity_run_date IS 'Cotality refinance propensity model run date.';
COMMENT ON COLUMN mip.gold.lead_population.has_refi_propensity_trigger IS 'TRUE when refi_propensity_score >= 700. Adds intent score context.';
COMMENT ON COLUMN mip.gold.lead_population.loan_product_type IS 'From gold.borrower_360; conventional / jumbo / fha / va / other, NULL when the Cotality loan type code is missing. Drives the PRODUCT TYPE filter.';
COMMENT ON COLUMN mip.gold.lead_population.origination_channel IS 'From gold.borrower_360; LOS channel of the most recent funded first-party application, NULL when unknown. Drives the ORIGINATION CHANNEL filter.';
COMMENT ON COLUMN mip.gold.lead_population.conforming_loan_limit_applied IS 'From gold.borrower_360; conforming loan limit (USD) applied this refresh when classifying jumbo via fn_loan_product_type. Provenance for loan_product_type.';
COMMENT ON COLUMN mip.gold.lead_population.marketing_eligible IS 'From gold.borrower_360; TRUE only when consent, suppression, and frequency-cap gates are clear.';
COMMENT ON COLUMN mip.gold.lead_population.consent_status IS 'From gold.borrower_360; opt_in / opt_out / unknown.';
COMMENT ON COLUMN mip.gold.lead_population.suppression_reason IS 'From gold.borrower_360; controlled suppression reason.';
COMMENT ON COLUMN mip.gold.lead_population.last_touch_at IS 'From gold.borrower_360; most recent first-party marketing/contact touch.';
COMMENT ON COLUMN mip.gold.lead_population.eligible_recontact_at IS 'From gold.borrower_360; earliest permitted re-contact time when capped.';
COMMENT ON COLUMN mip.gold.lead_population.dnc IS 'From gold.borrower_360; TRUE when a first-party do_not_contact suppression exists. Synthetic-by-design consent signal.';
COMMENT ON COLUMN mip.gold.lead_population.eligibility_source IS 'From gold.borrower_360; provenance of the consent/eligibility fields. synthetic_seed until a CRM/CDP connector supplies it.';
COMMENT ON COLUMN mip.gold.lead_population.rank_overall IS 'DENSE_RANK OVER (ORDER BY opportunity_score DESC, clip). 1 = highest.';
COMMENT ON COLUMN mip.gold.lead_population.rank_within_state IS 'DENSE_RANK OVER (PARTITION BY state ORDER BY opportunity_score DESC, clip). 1 = highest in state.';
COMMENT ON COLUMN mip.gold.lead_population.population_version IS 'CONCAT(DATE_FORMAT(refreshed_at, "yyyyMMdd"), "-v1"). EvidenceDrawer footer uses this as a provenance chip.';
COMMENT ON COLUMN mip.gold.lead_population.refreshed_at IS 'Refresh timestamp.';
