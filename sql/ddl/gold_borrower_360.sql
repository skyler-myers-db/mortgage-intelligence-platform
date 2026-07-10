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
--            refreshed source geography first and then drills by
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
--   - first_party.*            (optional lender LOS/servicing/CRM/
--                               interaction/product-balance feeds, or the
--                               explicit demo_synthetic seed)
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
-- Live intent overlays:
--   - `listed_for_sale` comes from Cotality MLS rows in silver.listing_activity.
--   - `has_permit` remains FALSE until a true filed-permit source exists.
--   - Cotality HELOC/refi propensity feeds are model signals, not permit
--     filings, and are exposed through separate *_propensity fields.
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
  state                     STRING    NOT NULL COMMENT 'Situs state from refreshed source coverage.',
  zip                       STRING             COMMENT '5-digit situs ZIP.',
  situs_cbsa_code           STRING             COMMENT 'CBSA metro code. Gold-only; used for geography drill-down.',
  county_fips_5             STRING             COMMENT '5-char FIPS county code (2-char state + 3-char county) from silver.property_master.fips_county_code. Feeds gold.county_rollup + gold.zip_rollup. NULL for the ~0.2% of rows where silver has no county geocode.',
  segment_codes             ARRAY<STRING> NOT NULL COMMENT 'Ordered list of SegmentCode Literals (itm/listed/permit/investor/equity/retention + S1.3 overlays second_lien_itm/heloc_draw_to_payback/home_equity_history/refi_propensity/itm_on_related_property/payoff_loss_leads/permit_activity) this borrower belongs to.',
  equity_estimate           BIGINT    NOT NULL COMMENT 'USD: GREATEST(0, avm_value - estimated current lien balance). Current lien uses fn_estimated_upb(first_pos_amount, first_pos_rate, months_elapsed) plus second-position amount when first-lien inputs are present.',
  equity_pct                INT       NOT NULL COMMENT '0..100 int available-equity percentage from AVM and estimated current lien balance; falls back to Cotality estimated_cltv only when AVM is missing. Underwater borrowers clamp to 0 for scoring while display LTV can exceed 100. Feeds fn_in_the_money + fn_next_best_offer.',
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
  current_lien_balance      BIGINT    NOT NULL COMMENT 'Estimated current lien balance in USD: fn_estimated_upb(first_pos_amount, first_pos_rate, months_elapsed) plus second-position amount when first-lien inputs are present; otherwise COALESCE(total_open_lien_balance, 0).',
  current_lien_balance_low  BIGINT    NOT NULL COMMENT 'Lower bound of the estimated current lien balance confidence band in USD: fn_estimated_upb_confidence_band lower_upb plus second-position amount when first-lien inputs are present; otherwise equals current_lien_balance.',
  current_lien_balance_high BIGINT    NOT NULL COMMENT 'Upper bound of the estimated current lien balance confidence band in USD: fn_estimated_upb_confidence_band upper_upb plus second-position amount when first-lien inputs are present; otherwise equals current_lien_balance.',
  current_rate              DOUBLE    NOT NULL COMMENT 'PERCENT form (5.75, not 0.0575). Matches Pydantic current_rate and mock_data convention.',
  ltv                       INT       NOT NULL COMMENT 'Display LTV int from estimated current lien balance divided by AVM when AVM is present; not upper-capped, so underwater borrowers may exceed 100.',
  related_property_count    INT       NOT NULL COMMENT 'COALESCE(property_owner_bridge.related_property_count, 1).',
  owner_count               INT       NOT NULL COMMENT 'S1.1: occupied owner slots on this CLIP in silver.property_owners (max 4, duplicate Owner Links collapsed). 0 when the source record has no owner information. Drives the multi-owner caveat chip.',
  has_unresolved_owner      BOOLEAN   NOT NULL COMMENT 'S1.1: TRUE when any owner slot classifies unresolved OR no owner rows exist. Fails marketing_eligible closed with suppression_reason unresolved_owner. ROADMAP-TEMPORARY classify+caveat+suppress scope pending Cotality entity resolution (data-contract §2.6).',
  primary_owner_entity_type STRING             COMMENT 'S1.1: owner_entity_type of the slot-1 owner (individual | trust | llc | unresolved). NULL when no owner rows exist.',
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'owner_occupancy_code = "O". Feeds fit sub-score.',
  is_absentee               BOOLEAN   NOT NULL COMMENT 'property_master.is_absentee. Feeds investor branch.',
  is_corporate_owner        BOOLEAN   NOT NULL COMMENT 'property_master.owner_is_corporate. Feeds investor branch.',
  has_permit                BOOLEAN   NOT NULL COMMENT 'Filed building-permit flag. FALSE until a true Cotality Building Permits source table is present.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'TRUE when silver.listing_activity has a current active/under-contract Cotality MLS row for this CLIP.',
  listing_status_category   STRING             COMMENT 'Cotality standardized MLS listing status category.',
  listing_status_description STRING            COMMENT 'Display-safe Cotality MLS status description. No address, remarks, agent, phone, or email.',
  listing_date              DATE               COMMENT 'MLS listing date.',
  listing_status_date       DATE               COMMENT 'Most recent MLS status/change date.',
  listing_price             BIGINT             COMMENT 'Current MLS listing price in USD, when supplied.',
  listing_days_on_market    INT                COMMENT 'MLS days-on-market value, when supplied.',
  listing_service           STRING             COMMENT 'MLS/listing service label when supplied. No agent or consumer contact data.',
  heloc_propensity_score    INT                COMMENT 'Cotality HELOC propensity score, 0..999 in the current feed. Model signal, not a permit filing.',
  heloc_propensity_run_date DATE               COMMENT 'Cotality HELOC propensity model run date.',
  has_heloc_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when heloc_propensity_score >= 700. Drives HELOC Intent without setting has_permit.',
  refi_propensity_score     INT                COMMENT 'Cotality refinance propensity score, 0..999 in the current feed.',
  refi_propensity_run_date  DATE               COMMENT 'Cotality refinance propensity model run date.',
  has_refi_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when refi_propensity_score >= 700. Adds intent score context.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'Derived: related_property_count >= 2 OR is_corporate_owner OR is_absentee.',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'Current-servicer relationship to tenant: governed lender_dictionary says non-competitor.',
  is_former_customer        BOOLEAN   NOT NULL COMMENT 'Historical tenant-lender Owner Link relationship with no current tenant-serviced lien.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'Current servicer is known and not the tenant. Competitor/recapture signal; mutually exclusive with is_current_customer in the current CLIP-grain refresh path.',
  has_first_party_relationship BOOLEAN NOT NULL COMMENT 'TRUE when LOS, servicing, CRM, interaction, or product-balance feeds resolve to this borrower.',
  first_party_relationship_depth INT   NOT NULL COMMENT 'Bounded count of resolved first-party feed categories for relationship scoring.',
  first_party_recent_interactions INT  NOT NULL COMMENT 'Recent call-center/digital interaction count resolved through first-party feeds.',
  first_party_recent_application BOOLEAN NOT NULL COMMENT 'TRUE when a recent LOS/application event exists.',
  first_party_synthetic_demo     BOOLEAN NOT NULL COMMENT 'TRUE only when resolved first-party rows come from the Summit demo_synthetic seed.',
  marketing_eligible      BOOLEAN   NOT NULL COMMENT 'TRUE only when latest first-party CRM consent is opt-in, no suppression exists, the 30-day touch cap is clear, AND no owner slot is unresolved (S1.1). Campaign and draft APIs fail closed on FALSE.',
  consent_status          STRING    NOT NULL COMMENT 'Controlled first-party CRM consent enum: opt_in / opt_out / unknown. No raw contact data.',
  suppression_reason      STRING             COMMENT 'Controlled suppression reason: do_not_contact / recent_contact_cap (first-party CRM, takes precedence) or unresolved_owner (S1.1 owner-resolution gate).',
  last_touch_at           TIMESTAMP          COMMENT 'Most recent first-party marketing/contact touch timestamp used for frequency-cap enforcement.',
  eligible_recontact_at   TIMESTAMP          COMMENT 'Earliest timestamp the borrower can be contacted again when a frequency cap is active.',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference: Summit Mortgage, Competitor A/B/etc., or Competitor Other. Never the raw Cotality lender string.',
  second_pos_amount         BIGINT             COMMENT '2nd-lien balance passthrough; NULL or 0 both mean no active 2nd-lien. Feeds the equity segment clean-lien predicate.',
  second_pos_rate           DOUBLE             COMMENT 'S1.3: 2nd-lien note rate in PERCENT form (8.25, not 0.0825) after silver+gold source-quality bounding. NULL when missing/invalid.',
  second_pos_rate_spread_bps INT       NOT NULL COMMENT 'S1.3: fn_rate_spread(second_pos_rate_fraction, market_rate_fraction). 0 when the second rate is unknown.',
  second_lien_itm           BOOLEAN   NOT NULL COMMENT 'S1.3 second_lien_itm segment flag: open 2nd position AND fn_in_the_money(second_pos_rate_spread_bps, equity_pct, governed thresholds). Consolidation-refi economics screen.',
  heloc_open_date           DATE               COMMENT 'S1.3: latest OPEN equity-loan lien event date from silver.mortgage_events (is_equity_loan, no release_date).',
  heloc_draw_end_date       DATE               COMMENT 'S1.3: heloc_open_date + 120 months (standard 10-year draw period).',
  has_heloc_draw_ending     BOOLEAN   NOT NULL COMMENT 'S1.3 heloc_draw_to_payback segment flag: open equity-loan lien originated 102-126 months ago (standard 120-month draw ending within 18 months or ended within the last 6).',
  purchase_amount           BIGINT             COMMENT 'S1.3: last recorded purchase amount from the Cotality lien share.',
  purchase_date             DATE               COMMENT 'S1.3: last recorded purchase recording date from the Cotality lien share.',
  home_value_appreciation_pct INT              COMMENT 'S1.3: ROUND(100 * (avm_value - purchase_amount) / purchase_amount). NULL when either side is missing/zero.',
  months_since_purchase     INT                COMMENT 'S1.3: whole months between purchase_date and this refresh. NULL when purchase_date is missing.',
  has_home_equity_history   BOOLEAN   NOT NULL COMMENT 'S1.3 home_equity_history segment flag: appreciation >= 40% AND tenure >= 36 months AND equity_pct >= 20.',
  first_pos_age_months      INT                COMMENT 'S1.3: whole months since first-lien origination at refresh time. Feeds fn_refi_propensity_heuristic seasoning points.',
  refi_propensity_heuristic INT       NOT NULL COMMENT 'S1.3: fn_refi_propensity_heuristic output 0..100. TRANSPARENT deterministic points table (published in the glossary). NOT the Cotality refi propensity model score.',
  has_refi_propensity_heuristic_trigger BOOLEAN NOT NULL COMMENT 'S1.3 refi_propensity segment flag: refi_propensity_heuristic >= 60.',
  tenant_payoff_date        DATE               COMMENT 'S1.3: most recent released tenant-lender lien date from silver.mortgage_events joined to ref.lender_dictionary.',
  is_payoff_loss            BOOLEAN   NOT NULL COMMENT 'S1.3 payoff_loss_leads segment flag: tenant lien released within 24 months AND the property now carries a competitor lien. Also feeds the future S2.7 competitive view.',
  itm_on_related_property   BOOLEAN   NOT NULL COMMENT 'S1.3 itm_on_related_property segment flag: any Owner Link on this CLIP (S1.1 silver.property_owners, all slots) also holds a DIFFERENT clip that is in the money under the same refresh thresholds.',
  related_itm_property_count INT      NOT NULL COMMENT 'S1.3: count of OTHER in-the-money clips on the strongest Owner Link for this CLIP. Evidence display for itm_on_related_property.',
  first_pos_loan_type       STRING             COMMENT '1st-lien loan type code (CONV / FHA / VA / etc). Feeds fit sub-score.',
  loan_product_type         STRING             COMMENT 'fn_loan_product_type(first_pos_loan_type, first_pos_amount, conforming_loan_limit_applied): conventional / jumbo / fha / va / other. NULL when the Cotality loan type code is missing. Drives the PRODUCT TYPE filter and SegmentCard facets.',
  origination_channel       STRING             COMMENT 'LOS channel of the most recent funded first-party application (loan_officer / digital / branch / call_center in the demo feed). NULL when no funded application resolves to this borrower -- rendered "Unknown", never invented.',
  owner_name_hash           STRING    NOT NULL COMMENT 'sha2(LOWER(TRIM(name)) || salt, 256) propagated from silver.property_master. Internal only -- router strips before /api/*.',
  min_spread_bps_applied    INT       NOT NULL COMMENT 'Threshold applied when computing ITM for THIS refresh. Carried so WhyPanel.min_spread_bps is the run-specific value.',
  min_equity_pct_applied    INT       NOT NULL COMMENT 'Equity threshold applied this refresh.',
  heloc_equity_min_applied  INT       NOT NULL COMMENT 'HELOC equity threshold applied this refresh (fn_next_best_offer branch 2/3 and equity segment).',
  cashout_equity_min_applied INT      NOT NULL COMMENT 'Cash-out equity threshold applied this refresh (fn_next_best_offer branch 5).',
  retention_min_spread_applied INT    NOT NULL COMMENT 'Retention spread threshold applied this refresh (fn_next_best_offer branch 7 and retention segment).',
  conforming_loan_limit_applied BIGINT NOT NULL COMMENT 'Conforming loan limit (USD) applied this refresh when classifying jumbo via fn_loan_product_type.',
  in_the_money              BOOLEAN   NOT NULL COMMENT 'fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied).',
  trigger_timeline_json     STRING    NOT NULL COMMENT 'JSON-encoded top-3 EvidenceEvent rows pre-materialized to avoid per-row fan-out at read. Router json_decodes into List[EvidenceEvent].',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp; used as EvidenceDrawer footer provenance chip.'
)
USING DELTA
CLUSTER BY (state, clip)
COMMENT 'CLIP-grain borrower projection that backs every Module 0 UI surface. Joins silver.lien_current (spine) + silver.property_master + gold.property_owner_bridge + silver.market_rates_weekly(is_latest) + live MLS/propensity overlays. Synthesized display_name, no raw PII; has_permit stays false until true permit filings land. See docs/data-contract-module0.md §3.2 + docs/governance-real-data-review.md §1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
