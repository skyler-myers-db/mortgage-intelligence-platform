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

CREATE OR REPLACE TABLE mip.gold.lead_population (
  clip                      COMMENT 'Cotality CLIP. PK.',
  borrower_id               COMMENT 'From gold.borrower_360.borrower_id.',
  display_name              COMMENT 'Synthesized label. No PII.',
  city                      COMMENT 'Situs city.',
  state                     COMMENT 'Situs state.',
  zip                       COMMENT '5-digit situs ZIP.',
  segment_codes             COMMENT 'Ordered SegmentCode list.',
  equity_estimate           COMMENT 'From gold.borrower_360.',
  equity_pct                COMMENT 'From gold.borrower_360 [0..100]. Used by executive dashboard top-borrower widget.',
  rate_spread_bps           COMMENT 'From gold.borrower_360.',
  opportunity_score         COMMENT 'fn_lead_score output 0..100.',
  confidence                COMMENT 'Mean of 5 sub-scores 0..100.',
  recommended_offer_code    COMMENT 'fn_next_best_offer output code; canonical offer enum for operational filters and audit grouping.',
  recommended_offer         COMMENT 'Human label (resolved in gold via product_labels map).',
  why_now                   COMMENT 'Deterministic template per offer code.',
  evidence_ids              COMMENT 'Ordered evidence_ids (mirrors gold.borrower_360 for this CLIP).',
  approval_status           COMMENT '"pending" by default; Lakebase is authoritative for actual state.',
  current_lender_ref        COMMENT 'Public-demo-safe current-servicer reference from borrower_360. Never the raw Cotality lender string.',
  is_owner_occupied         COMMENT 'From gold.borrower_360; drives /segment-intelligence DEMOGRAPHICS filter.',
  is_investor               COMMENT 'Carried from gold.borrower_360 (derived: multi-property OR corporate OR absentee).',
  is_current_customer       COMMENT 'From gold.borrower_360; current servicer or first-party servicing relationship to the tenant lender.',
  is_former_customer        COMMENT 'From gold.borrower_360; historical tenant-lender relationship with no current tenant lien.',
  is_competitor_lien        COMMENT 'From gold.borrower_360; current servicer is known and not the tenant lender.',
  related_property_count    COMMENT 'From gold.borrower_360; drives /segment-intelligence OWNER LINK filter.',
  current_lien_balance      COMMENT 'From gold.borrower_360; drives /segment-intelligence LIEN filter.',
  second_pos_amount         COMMENT 'From gold.borrower_360; nullable (no second-position lien).',
  has_permit                COMMENT 'BLOCKED: FALSE until Cotality Building Permits Delta share lands.',
  listed_for_sale           COMMENT 'BLOCKED: FALSE until Cotality MLS Listings Delta share lands.',
  marketing_eligible        COMMENT 'From gold.borrower_360; TRUE only when consent, suppression, and frequency-cap gates are clear.',
  consent_status            COMMENT 'From gold.borrower_360; opt_in / opt_out / unknown.',
  suppression_reason        COMMENT 'From gold.borrower_360; controlled suppression reason.',
  last_touch_at             COMMENT 'From gold.borrower_360; most recent first-party marketing/contact touch.',
  eligible_recontact_at     COMMENT 'From gold.borrower_360; earliest permitted re-contact time when capped.',
  rank_overall              COMMENT 'DENSE_RANK OVER (ORDER BY opportunity_score DESC, clip). 1 = highest.',
  rank_within_state         COMMENT 'DENSE_RANK OVER (PARTITION BY state ORDER BY opportunity_score DESC, clip). 1 = highest in state.',
  population_version        COMMENT 'CONCAT(DATE_FORMAT(refreshed_at, "yyyyMMdd"), "-v1"). EvidenceDrawer footer uses this as a provenance chip.',
  refreshed_at              COMMENT 'Refresh timestamp.'
)
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
    -- lien state, and purchase intent. Permit + listing columns remain
    -- BLOCKED FALSE until the Cotality Delta shares land; the UI surfaces
    -- a "data-dependency pending" note on that filter.
    b.is_owner_occupied,
    b.is_investor,
    b.is_current_customer,
    b.is_former_customer,
    b.is_competitor_lien,
    b.related_property_count,
    b.current_lien_balance,
    b.second_pos_amount,
    b.has_permit,
    b.listed_for_sale,
    b.marketing_eligible,
    b.consent_status,
    b.suppression_reason,
    b.last_touch_at,
    b.eligible_recontact_at,
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
  current_lien_balance,
  second_pos_amount,
  has_permit,
  listed_for_sale,
  marketing_eligible,
  consent_status,
  suppression_reason,
  last_touch_at,
  eligible_recontact_at,
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
