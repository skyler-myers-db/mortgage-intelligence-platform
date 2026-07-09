-- =============================================================================
-- 003_gold_tables.sql  (gold-layer bootstrap manifest)
-- -----------------------------------------------------------------------------
-- Purpose:   Umbrella bootstrap for the gold layer. Runs every per-table
--            CREATE TABLE IF NOT EXISTS in dependency order, so a single
--            `sql_task` in the Databricks bundle can apply the full gold
--            schema.
--
-- Individual per-table files (authoritative column contracts, one per file
-- for diff clarity):
--
--   sql/ddl/gold_property_owner_bridge.sql   -- Owner-Link rollup.
--   sql/ddl/gold_borrower_360.sql            -- CLIP-grain projection.
--   sql/ddl/gold_lead_scores.sql             -- Scoring sub-scores + fn_lead_score.
--   sql/ddl/gold_evidence_events.sql         -- Per-(CLIP, signal) rows.
--   sql/ddl/gold_lead_population.sql         -- Ranked quality-filtered cut for /leads.
--   sql/ddl/gold_segment_population.sql      -- Segment counts + prior snapshot.
--
-- Dependency order (alphabetical is NOT sufficient; respect FK-ish order):
--   1. gold.property_owner_bridge     (owner-link-keyed; no deps)
--   2. gold.evidence_events           (silver-derived; scoped to silver lien_current spine)
--   3. gold.borrower_360              (depends on property_owner_bridge + evidence_events)
--   4. gold.lead_scores               (depends on borrower_360 + evidence_events)
--   5. gold.lead_population           (depends on borrower_360 + lead_scores)
--   6. gold.segment_population        (depends on borrower_360 + lead_scores)
--   7. gold.source_readiness          (non-PII Admin source summary)
--
-- Each file uses CREATE TABLE IF NOT EXISTS so re-running is a no-op if the
-- schema hasn't changed. If a column is added to a per-file DDL, this
-- manifest stays unchanged; the transformation CTAS (CREATE OR REPLACE
-- TABLE) handles schema evolution on the populated rows.
--
-- Data contract reference: docs/data-contract-module0.md §3.
-- Slice:     module0-real-data-slice3.
--
-- Usage (Databricks bundle / SQL task):
--   sql_task:
--     warehouse_id: ${var.sql_warehouse_id}
--     file:
--       path: sql/ddl/003_gold_tables.sql
--
-- The SQL warehouse file-runner executes statements top-to-bottom; we
-- expand each per-table file inline below via a series of independent
-- CREATE TABLE IF NOT EXISTS blocks. Keep the blocks IN SYNC with the
-- per-file DDLs (same column list, same types, same comments) -- the
-- per-file versions remain authoritative for diff review; this file is
-- the deployable aggregate.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS mip.first_party
COMMENT 'Optional customer-owned LOS, servicing, CRM, interaction, and product-balance feeds. Empty until customer ingestion is configured or the demo_synthetic seed is explicitly enabled.';

CREATE TABLE IF NOT EXISTS mip.first_party.loan_applications (
  application_id_hash STRING NOT NULL COMMENT 'Customer application id hash. No raw application id.',
  customer_key_hash   STRING COMMENT 'Customer or household hash supplied by the lender.',
  borrower_id         STRING COMMENT 'Synthetic MIP borrower id after governed resolution, when available.',
  clip_ref            STRING COMMENT 'Masked CLIP ref or null until Cotality resolution.',
  state               STRING,
  zip                 STRING,
  application_status  STRING,
  application_channel STRING,
  product_intent      STRING,
  application_at      TIMESTAMP,
  source_system       STRING COMMENT 'Customer source system name, e.g. LOS vendor or demo seed.',
  feed_mode           STRING COMMENT 'customer_connected or demo_synthetic.',
  synthetic_demo      BOOLEAN COMMENT 'TRUE only for the Summit Mortgage public demo seed.',
  refreshed_at        TIMESTAMP
)
USING DELTA
COMMENT 'Optional lender LOS/application feed. No names, emails, phones, SSNs, or street addresses.';

CREATE TABLE IF NOT EXISTS mip.first_party.servicing_portfolio (
  servicing_loan_id_hash STRING NOT NULL COMMENT 'Customer loan id hash. No raw account number.',
  customer_key_hash      STRING,
  borrower_id            STRING,
  clip_ref               STRING,
  state                  STRING,
  zip                    STRING,
  product_type           STRING,
  current_upb            DOUBLE,
  note_rate_pct          DOUBLE,
  delinquency_bucket     STRING,
  servicing_status       STRING,
  source_system          STRING COMMENT 'Customer source system name, e.g. servicing platform or demo seed.',
  feed_mode              STRING COMMENT 'customer_connected or demo_synthetic.',
  synthetic_demo         BOOLEAN COMMENT 'TRUE only for the Summit Mortgage public demo seed.',
  refreshed_at           TIMESTAMP
)
USING DELTA
COMMENT 'Optional lender servicing-book feed. Used for current-customer, retention, and recapture context when connected.';

CREATE TABLE IF NOT EXISTS mip.first_party.crm_campaign_membership (
  campaign_member_id_hash STRING NOT NULL,
  customer_key_hash       STRING,
  borrower_id             STRING,
  campaign_key_hash       STRING,
  channel                 STRING,
  last_touch_at           TIMESTAMP,
  suppression_reason      STRING,
  consent_status          STRING,
  source_system           STRING,
  feed_mode               STRING,
  synthetic_demo          BOOLEAN,
  refreshed_at            TIMESTAMP
)
USING DELTA
COMMENT 'Optional CRM/campaign feed for suppression, recency, and outreach-history controls. PII-free.';

CREATE TABLE IF NOT EXISTS mip.first_party.customer_interactions (
  interaction_id_hash STRING NOT NULL,
  customer_key_hash   STRING,
  borrower_id         STRING,
  interaction_channel STRING,
  interaction_type    STRING,
  outcome_code        STRING,
  interaction_at      TIMESTAMP,
  source_system       STRING,
  feed_mode           STRING,
  synthetic_demo      BOOLEAN,
  refreshed_at        TIMESTAMP
)
USING DELTA
COMMENT 'Optional call-center/digital interaction feed. PII-free interaction metadata only.';

CREATE TABLE IF NOT EXISTS mip.first_party.product_balances (
  product_balance_id_hash STRING NOT NULL,
  customer_key_hash       STRING,
  borrower_id             STRING,
  product_family          STRING,
  balance_band            STRING,
  relationship_tenure_months INT,
  source_system           STRING,
  feed_mode               STRING,
  synthetic_demo          BOOLEAN,
  refreshed_at            TIMESTAMP
)
USING DELTA
COMMENT 'Optional banking-product balance feed. Uses bands and hashes, not account numbers or precise balances.';

-- -----------------------------------------------------------------------------
-- 1. mip.gold.property_owner_bridge
--    (see sql/ddl/gold_property_owner_bridge.sql for column comments)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.property_owner_bridge (
  owner_link_id             STRING    NOT NULL COMMENT 'Cotality Owner Link. PK.',
  related_property_count    INT       NOT NULL COMMENT 'Count of distinct CLIPs tied to this Owner Link across refreshed source coverage. Drives Borrower360.related_property_count and the investor branch of fn_next_best_offer.',
  corporate_property_count  INT       NOT NULL COMMENT 'Number of related properties with owner_is_corporate = TRUE.',
  absentee_property_count   INT       NOT NULL COMMENT 'Number of related properties with is_absentee = TRUE.',
  distinct_states_count     INT       NOT NULL COMMENT 'Number of distinct situs_state values. Multi-market investor signal.',
  distinct_cbsa_count       INT       NOT NULL COMMENT 'Number of distinct situs_cbsa_code values.',
  primary_clip              STRING             COMMENT 'CLIP of the owner-occupied property for this Owner Link (if any). NULL when no owner-occupant in set.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp for audit / provenance chips.'
)
USING DELTA
CLUSTER BY (owner_link_id)
COMMENT 'Owner-Link rollup projected into gold. See docs/data-contract-module0.md §3.1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 2. mip.gold.borrower_360
--    (see sql/ddl/gold_borrower_360.sql for column comments + PII posture)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.borrower_360 (
  clip                      STRING    NOT NULL COMMENT 'Cotality CLIP. PK. Router maps to Borrower360.clip_id.',
  borrower_id               STRING    NOT NULL COMMENT 'Synthetic stable id from CLIP: CONCAT("B-", LPAD(CONV(ABS(xxhash64(clip)), 10, 36), 13, "0")). Base36 encoding of the 64-bit hash, width 13 => 36^13 slots. No PII.',
  display_name              STRING    NOT NULL COMMENT 'Synthesized label "Owner " || SUBSTR(owner_name_hash, 1, 8). Never a real name.',
  city                      STRING             COMMENT 'Situs city from property_master.',
  state                     STRING    NOT NULL COMMENT 'Situs state from refreshed source coverage.',
  zip                       STRING             COMMENT '5-digit situs ZIP.',
  situs_cbsa_code           STRING             COMMENT 'CBSA metro code. Gold-only; used for geography drill-down.',
  county_fips_5             STRING             COMMENT '5-char FIPS county code (2-char state + 3-char county) from silver.property_master.fips_county_code. Feeds gold.county_rollup + gold.zip_rollup. NULL for the ~0.2% of rows where silver has no county geocode.',
  segment_codes             ARRAY<STRING> NOT NULL COMMENT 'Ordered list of SegmentCode Literals (itm/listed/permit/investor/equity/retention) this borrower belongs to.',
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
  current_rate              DOUBLE    NOT NULL COMMENT 'PERCENT form (5.75, not 0.0575). Matches Pydantic current_rate and mock_data convention.',
  ltv                       INT       NOT NULL COMMENT 'Display LTV int from estimated current lien balance divided by AVM when AVM is present; not upper-capped, so underwater borrowers may exceed 100.',
  related_property_count    INT       NOT NULL COMMENT 'COALESCE(property_owner_bridge.related_property_count, 1).',
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
  marketing_eligible      BOOLEAN   NOT NULL COMMENT 'TRUE only when latest first-party CRM consent is opt-in, no suppression exists, and the 30-day touch cap is clear. Campaign and draft APIs fail closed on FALSE.',
  consent_status          STRING    NOT NULL COMMENT 'Controlled first-party CRM consent enum: opt_in / opt_out / unknown. No raw contact data.',
  suppression_reason      STRING             COMMENT 'Controlled first-party CRM suppression reason, e.g. do_not_contact or recent_contact_cap.',
  last_touch_at           TIMESTAMP          COMMENT 'Most recent first-party marketing/contact touch timestamp used for frequency-cap enforcement.',
  eligible_recontact_at   TIMESTAMP          COMMENT 'Earliest timestamp the borrower can be contacted again when a frequency cap is active.',
  dnc                     BOOLEAN   NOT NULL COMMENT 'TRUE when a first-party do_not_contact suppression exists. Synthetic-by-design consent signal; the backend EligibilityService fails closed on TRUE.',
  eligibility_source      STRING    NOT NULL COMMENT 'Provenance of the consent/eligibility fields: synthetic_seed for the governed demo feed, else the connected CRM/CDP connector id (source_system).',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference: Summit Mortgage, Competitor A/B/etc., or Competitor Other. Never the raw Cotality lender string.',
  second_pos_amount         BIGINT             COMMENT '2nd-lien balance passthrough; NULL or 0 both mean no active 2nd-lien. Feeds the equity segment clean-lien predicate.',
  first_pos_loan_type       STRING             COMMENT '1st-lien loan type code (CONV / FHA / VA / etc). Feeds fit sub-score.',
  owner_name_hash           STRING    NOT NULL COMMENT 'sha2(LOWER(TRIM(name)) || salt, 256) propagated from silver.property_master. Internal only -- router strips before /api/*.',
  min_spread_bps_applied    INT       NOT NULL COMMENT 'Threshold applied when computing ITM for THIS refresh. Carried so WhyPanel.min_spread_bps is the run-specific value.',
  min_equity_pct_applied    INT       NOT NULL COMMENT 'Equity threshold applied this refresh.',
  heloc_equity_min_applied  INT       NOT NULL COMMENT 'HELOC equity threshold applied this refresh (fn_next_best_offer branch 2/3 and equity segment).',
  cashout_equity_min_applied INT      NOT NULL COMMENT 'Cash-out equity threshold applied this refresh (fn_next_best_offer branch 5).',
  retention_min_spread_applied INT    NOT NULL COMMENT 'Retention spread threshold applied this refresh (fn_next_best_offer branch 7 and retention segment).',
  in_the_money              BOOLEAN   NOT NULL COMMENT 'fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied).',
  trigger_timeline_json     STRING    NOT NULL COMMENT 'JSON-encoded top-3 EvidenceEvent rows pre-materialized to avoid per-row fan-out at read. Router json_decodes into List[EvidenceEvent].',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp; used as EvidenceDrawer footer provenance chip.'
)
USING DELTA
CLUSTER BY (state, clip)
COMMENT 'CLIP-grain borrower projection. See docs/data-contract-module0.md §3.2 + governance §1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 3. mip.gold.evidence_events
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.evidence_events (
  clip           STRING NOT NULL COMMENT 'Cotality CLIP. Not in Pydantic EvidenceEvent (router strips); used for join / filter.',
  evidence_id    STRING NOT NULL COMMENT 'Deterministic: "ev-" || substr(sha2(clip || signal_type || timestamp, 256), 1, 12). Stable across refreshes so Borrower360.evidence_ids stays consistent.',
  source_product STRING NOT NULL COMMENT 'Human label: Voluntary Lien / AVM / Owner Link / Property / Mortgage Domain / Owner Transfer / Market Rates / MLS Listings / HELOC Propensity / Refi Propensity.',
  source_table   STRING NOT NULL COMMENT 'Real UC path. Shown verbatim in EvidenceDrawer -- must be a resolvable mip.silver.* or mip.gold.* path.',
  signal_type    STRING NOT NULL COMMENT 'Controlled vocab: listing / rate_spread / equity / market_trend / heloc_propensity / refi_propensity / loan_type_fit / competitor_lien / multi_property / absentee_mailing / corporate_owner / foreclosure_stage / recent_refi / recent_payoff / recent_sale. BLOCKED vocab permit is NEVER emitted without a true permit source.',
  signal_value   STRING NOT NULL COMMENT 'Human-readable value: "+88 bps", "$285K", "3 properties", "competitor refi".',
  display_text   STRING NOT NULL COMMENT 'One-sentence deterministic template per signal_type. No PII.',
  confidence     DOUBLE NOT NULL COMMENT '0..1. Per-signal: AVM uses upstream confidence_score_mktg; count-based rows 0.85-0.92 (see header).',
  `timestamp`    STRING NOT NULL COMMENT 'ISO-8601 STRING (matches Pydantic EvidenceEvent.timestamp: str).',
  signal_rank    INT    NOT NULL COMMENT 'Deterministic priority order for Borrower360.evidence_ids: listing=0, rate_spread=1, equity=2, market_trend=3, etc. Smaller = higher priority. Gold-only.'
)
USING DELTA
CLUSTER BY (clip)
COMMENT 'Per-(CLIP, signal) evidence rows. See docs/data-contract-module0.md §3.4.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 4. mip.gold.source_readiness
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.source_readiness (
  source_name   STRING    NOT NULL COMMENT 'Admin panel display name.',
  status        STRING    NOT NULL COMMENT 'live / demo_synthetic / configured_empty / not_configured / roadmap / error.',
  row_count     BIGINT             COMMENT 'Source row count when live.',
  last_updated  TIMESTAMP          COMMENT 'Latest source ingest timestamp when live.',
  note          STRING    NOT NULL COMMENT 'Human-readable source note.',
  source_table  STRING             COMMENT 'UC source table used by ETL; null for roadmap sources.',
  synthetic_demo BOOLEAN  NOT NULL COMMENT 'TRUE when rows come from the explicit Summit demo_synthetic first-party seed.',
  sort_order    INT       NOT NULL COMMENT 'Stable Admin panel order.',
  checked_at    TIMESTAMP NOT NULL COMMENT 'Gold refresh anchor used for this readiness snapshot.'
)
USING DELTA
CLUSTER BY (sort_order)
COMMENT 'Non-PII source-readiness summary for Admin. Populated by gold_source_readiness.sql so the running app does not need direct silver grants.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 5. mip.gold.lead_scores
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.lead_scores (
  clip                     STRING    NOT NULL COMMENT 'Cotality CLIP. PK. FK to gold.borrower_360.clip.',
  economic_incentive       INT       NOT NULL COMMENT '0..100 sub-score on rate_spread_bps + equity_pct. Weight 0.35 in fn_lead_score.',
  intent_trigger           INT       NOT NULL COMMENT '0..100 sub-score on recent mortgage events, competitor/investor signals, rate drift, equity proxy, and current-customer bump. Weight 0.30.',
  fit                      INT       NOT NULL COMMENT '0..100 sub-score on owner-occupancy + loan_type + corporate/investor fit. Weight 0.15.',
  relationship             INT       NOT NULL COMMENT '0..100 sub-score on customer / competitor / investor relationship ladder plus owner-level distinct tenant-lender CLIP history. Weight 0.10.',
  evidence                 INT       NOT NULL COMMENT '0..100: 10 pts per live evidence row plus bounded second-position balance tail. Weight 0.10.',
  opportunity_score        INT       NOT NULL COMMENT 'mip.gold.fn_lead_score(...) output. 0..100. Mirrors gold.borrower_360 for the same CLIP.',
  confidence               INT       NOT NULL COMMENT 'ROUND(mean(5 sub-scores)). Mirrors gold.borrower_360 for the same CLIP.',
  in_the_money             BOOLEAN   NOT NULL COMMENT 'mip.gold.fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied).',
  recommended_offer_code   STRING    NOT NULL COMMENT 'mip.gold.fn_next_best_offer(...) lowercase code.',
  rate_spread_bps          INT       NOT NULL COMMENT 'Input to fn_in_the_money / fn_next_best_offer. Carried here so the table is self-contained for parity testing.',
  equity_pct               INT       NOT NULL COMMENT 'Input to fn_in_the_money / fn_next_best_offer.',
  has_permit               BOOLEAN   NOT NULL COMMENT 'Filed building-permit flag. FALSE until a true Cotality Building Permits source table is present.',
  listed_for_sale          BOOLEAN   NOT NULL COMMENT 'TRUE when borrower_360 has a current active/under-contract Cotality MLS listing row.',
  heloc_propensity_score   INT                COMMENT 'Cotality HELOC propensity score carried from borrower_360. Model signal, not a permit filing.',
  has_heloc_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when heloc_propensity_score >= 700. Used as the HELOC-intent input without setting has_permit.',
  refi_propensity_score    INT                COMMENT 'Cotality refinance propensity score carried from borrower_360.',
  has_refi_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when refi_propensity_score >= 700. Adds intent-trigger weight.',
  is_investor              BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_current_customer      BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_former_customer       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360. Distinct from competitor lien; requires historical tenant relationship and no current tenant lien.',
  is_competitor_lien       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  has_first_party_relationship BOOLEAN NOT NULL COMMENT 'Carried from borrower_360. TRUE when optional first-party feeds resolve to this borrower.',
  first_party_relationship_depth INT   NOT NULL COMMENT 'Bounded count of resolved first-party feed categories.',
  first_party_recent_interactions INT  NOT NULL COMMENT 'Recent positive interaction count from the first-party engagement feed.',
  first_party_recent_application BOOLEAN NOT NULL COMMENT 'TRUE when a recent first-party LOS/application event exists.',
  first_party_synthetic_demo     BOOLEAN NOT NULL COMMENT 'TRUE only for rows touched by the Summit demo_synthetic first-party seed.',
  min_spread_bps_applied   INT       NOT NULL COMMENT 'Threshold applied this refresh.',
  min_equity_pct_applied   INT       NOT NULL COMMENT 'Threshold applied this refresh.',
  heloc_equity_min_applied INT       NOT NULL COMMENT 'HELOC equity threshold applied this refresh (fn_next_best_offer branch 2/3).',
  cashout_equity_min_applied INT     NOT NULL COMMENT 'Cash-out equity threshold applied this refresh (fn_next_best_offer branch 5).',
  retention_min_spread_applied INT   NOT NULL COMMENT 'Retention spread threshold applied this refresh (fn_next_best_offer branch 7).',
  refreshed_at             TIMESTAMP NOT NULL COMMENT 'Refresh timestamp for audit / provenance.'
)
USING DELTA
CLUSTER BY (clip)
COMMENT 'CLIP-grain scoring surface. See docs/data-contract-module0.md §3.3 + §5.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 6. mip.gold.lead_population
-- -----------------------------------------------------------------------------
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
  recommended_offer_code    STRING    NOT NULL COMMENT 'fn_next_best_offer output code; canonical offer enum for operational filters and audit grouping.',
  recommended_offer         STRING    NOT NULL COMMENT 'Human label (resolved in gold via product_labels map).',
  why_now                   STRING    NOT NULL COMMENT 'Deterministic template per offer code.',
  evidence_ids              ARRAY<STRING> NOT NULL COMMENT 'Ordered evidence_ids (mirrors gold.borrower_360 for this CLIP).',
  approval_status           STRING    NOT NULL COMMENT '"pending" by default; Lakebase is authoritative for actual state.',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference from borrower_360. Never the raw Cotality lender string.',
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; drives /segment-intelligence DEMOGRAPHICS filter.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'Carried from gold.borrower_360 (derived: multi-property OR corporate OR absentee).',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; current servicer or first-party servicing relationship to the tenant lender.',
  is_former_customer        BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; historical tenant-lender relationship with no current tenant lien.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; current servicer is known and not the tenant lender.',
  related_property_count    INT       NOT NULL COMMENT 'From gold.borrower_360; drives /segment-intelligence OWNER LINK filter.',
  current_lien_balance      BIGINT    NOT NULL COMMENT 'From gold.borrower_360; drives /segment-intelligence LIEN filter.',
  second_pos_amount         BIGINT             COMMENT 'From gold.borrower_360; nullable (no second-position lien).',
  has_permit                BOOLEAN   NOT NULL COMMENT 'Filed building-permit flag. FALSE until a true Cotality Building Permits source table is present.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'TRUE when borrower_360 has a current active/under-contract Cotality MLS listing row.',
  listing_status_category   STRING             COMMENT 'Cotality standardized MLS listing status category.',
  listing_status_description STRING            COMMENT 'Display-safe Cotality MLS status description. No address, remarks, agent, phone, or email.',
  listing_date              DATE               COMMENT 'MLS listing date.',
  listing_status_date       DATE               COMMENT 'Most recent MLS status/change date.',
  listing_price             BIGINT             COMMENT 'Current MLS listing price in USD, when supplied.',
  listing_days_on_market    INT                COMMENT 'MLS days-on-market value, when supplied.',
  listing_service           STRING             COMMENT 'MLS/listing service label when supplied.',
  heloc_propensity_score    INT                COMMENT 'Cotality HELOC propensity score, 0..999 in the current feed. Model signal, not a permit filing.',
  heloc_propensity_run_date DATE               COMMENT 'Cotality HELOC propensity model run date.',
  has_heloc_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when heloc_propensity_score >= 700. Drives HELOC Intent without setting has_permit.',
  refi_propensity_score     INT                COMMENT 'Cotality refinance propensity score, 0..999 in the current feed.',
  refi_propensity_run_date  DATE               COMMENT 'Cotality refinance propensity model run date.',
  has_refi_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when refi_propensity_score >= 700. Adds intent score context.',
  marketing_eligible        BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; TRUE only when consent, suppression, and frequency-cap gates are clear.',
  consent_status            STRING    NOT NULL COMMENT 'From gold.borrower_360; opt_in / opt_out / unknown.',
  suppression_reason        STRING             COMMENT 'From gold.borrower_360; controlled suppression reason.',
  last_touch_at             TIMESTAMP          COMMENT 'From gold.borrower_360; most recent first-party marketing/contact touch.',
  eligible_recontact_at     TIMESTAMP          COMMENT 'From gold.borrower_360; earliest permitted re-contact time when capped.',
  dnc                       BOOLEAN   NOT NULL COMMENT 'From gold.borrower_360; TRUE when a first-party do_not_contact suppression exists. Synthetic-by-design consent signal.',
  eligibility_source        STRING    NOT NULL COMMENT 'From gold.borrower_360; provenance of the consent/eligibility fields. synthetic_seed until a CRM/CDP connector supplies it.',
  rank_overall              INT       NOT NULL COMMENT 'DENSE_RANK OVER (ORDER BY opportunity_score DESC, clip). 1 = highest.',
  rank_within_state         INT       NOT NULL COMMENT 'DENSE_RANK OVER (PARTITION BY state ORDER BY opportunity_score DESC, clip). 1 = highest in state.',
  population_version        STRING    NOT NULL COMMENT 'CONCAT(DATE_FORMAT(refreshed_at, "yyyyMMdd"), "-v1"). EvidenceDrawer footer uses this as a provenance chip.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
)
USING DELTA
CLUSTER BY (opportunity_score)
COMMENT 'Ranked quality-filtered cut; backs /leads. See docs/data-contract-module0.md §3.5.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 7. mip.gold.segment_population (+ segment_population_prior)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.segment_population (
  segment_code    STRING    NOT NULL COMMENT 'itm / listed / permit / investor / equity / retention. Matches SegmentCode Literal exactly; permit is the backward-compatible code for customer-facing HELOC Intent.',
  state           STRING    NOT NULL COMMENT '2-char state code from refreshed source coverage or "_ALL" for national rollup.',
  name            STRING    NOT NULL COMMENT 'Static label per segment_code (e.g., "Prime Refi Candidates").',
  count           INT       NOT NULL COMMENT 'Member count for this (segment, state) cell.',
  delta_vs_prior  STRING    NOT NULL COMMENT 'Quarter-over-quarter delta as "+NN%" / "-NN%". Router maps to SegmentSummary.delta. "+0%" on first refresh.',
  avg_score       INT       NOT NULL COMMENT 'CAST(ROUND(AVG(opportunity_score)) AS INT) over the segment cell.',
  description     STRING    NOT NULL COMMENT 'Static description per segment_code.',
  color           STRING    NOT NULL COMMENT 'Hex color for segment tile.',
  refreshed_at    TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
)
USING DELTA
CLUSTER BY (segment_code)
COMMENT 'Per-segment counts; listed is live from MLS and permit is the backward-compatible code for HELOC Intent while true building-permit filings remain pending. See docs/data-contract-module0.md §3.6.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

CREATE TABLE IF NOT EXISTS mip.gold.segment_population_prior (
  segment_code    STRING    NOT NULL COMMENT 'Matches segment_population.segment_code.',
  state           STRING    NOT NULL COMMENT 'Matches segment_population.state.',
  snapshot_date   DATE      NOT NULL COMMENT 'Date the count was snapshotted (daily granularity).',
  count           INT       NOT NULL COMMENT 'Member count on snapshot_date.',
  avg_score       INT       NOT NULL COMMENT 'Avg opportunity_score on snapshot_date.'
)
USING DELTA
CLUSTER BY (segment_code, snapshot_date)
COMMENT 'Daily snapshot of segment counts; drives segment_population.delta_vs_prior.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 7. mip.gold.borrower_lifecycle_state
--    Mirror of the Lakebase mip_app.approvals + outreach state, keyed by
--    borrower_id, so UC metric views can surface per-segment approval_rate
--    and outreach_rate without a runtime federated join. Authoritative state
--    still lives in Lakebase; this table is a scheduled sync (hourly) written
--    by jobs/sync_lifecycle_state.py. Metric views JOIN this, not Lakebase.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.borrower_lifecycle_state (
  borrower_id       STRING    NOT NULL COMMENT 'Masked borrower id; matches borrower_360.borrower_id.',
  approval_status   STRING    NOT NULL COMMENT 'pending / approved / rejected / hold. Derived from latest decided_at row in mip_app.approvals.',
  outreach_status   STRING    NOT NULL COMMENT 'queued / actioned / none. Derived from latest outreach state.',
  offer_code        STRING             COMMENT 'Latest offer_code associated with the approval decision.',
  approved_at       TIMESTAMP          COMMENT 'decided_at for the latest approve action; NULL when not approved.',
  outreach_at       TIMESTAMP          COMMENT 'Timestamp of latest outreach action.',
  synced_at         TIMESTAMP NOT NULL COMMENT 'Last sync run that touched this row.',
  refreshed_at      TIMESTAMP NOT NULL COMMENT 'Lakebase mirror refresh boundary for this lifecycle snapshot; distinct from the scoring gold refresh boundary.'
)
USING DELTA
CLUSTER BY (borrower_id)
COMMENT 'Hourly sync of Lakebase mip_app.approvals + outreach into gold for metric-view joins. Lakebase remains authoritative. Sync job: jobs/sync_lifecycle_state.py.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 8. mip.gold.funnel_snapshot_daily
--    One row per (snapshot_date, state, segment_code) recorded by every
--    scoring refresh. Powers delta_vs_prior_* (WoW / QoQ / YoY) on dashboard
--    KPIs + metric views without recomputing against cold data. Idempotent
--    on (snapshot_date, state, segment_code) via MERGE in the transformation.
--    state='_ALL' is the national rollup; segment_code='_ALL' is the full
--    cross-segment population so the executive-funnel KPIs can read a single
--    row per day.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.funnel_snapshot_daily (
  snapshot_date                 DATE      NOT NULL COMMENT 'Refresh date; daily grain.',
  state                         STRING    NOT NULL COMMENT '2-char state or "_ALL" national rollup.',
  segment_code                  STRING    NOT NULL COMMENT 'Segment code or "_ALL" for the full population.',
  addressable_borrowers         INT       NOT NULL COMMENT 'Population count for this (state, segment) cell.',
  in_the_money_borrowers        INT       NOT NULL COMMENT 'COUNT where in_the_money = TRUE.',
  high_opportunity_borrowers    INT       NOT NULL COMMENT 'COUNT where opportunity_score >= 75.',
  offer_recommended_borrowers   INT       NOT NULL COMMENT 'COUNT where recommended_offer_code <> "nurture".',
  approved_borrowers            INT       NOT NULL COMMENT 'COUNT of approved lifecycle states at snapshot time.',
  actioned_borrowers            INT       NOT NULL COMMENT 'COUNT of outreach_status = "actioned" at snapshot time.',
  avg_opportunity_score         INT       NOT NULL COMMENT 'AVG(opportunity_score) for the cell.',
  snapshot_at                   TIMESTAMP NOT NULL COMMENT 'Precise refresh timestamp.'
)
USING DELTA
CLUSTER BY (snapshot_date, state)
COMMENT 'Daily funnel snapshot for delta_vs_prior (WoW/QoQ/YoY) on dashboards and metric views.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 9. mip.gold.lockin_cohort
--    Borrowers who originated (or most-recently refinanced into) a sub-3 %
--    first-position mortgage between 2020-01-01 and 2022-12-31. Addresses
--    sample question 5 inside the trusted gold boundary (silver would be
--    out-of-scope for Genie). One row per CLIP; refreshed by the
--    mip_refresh_scores job via sql/transformations/gold_lockin_cohort.sql.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.lockin_cohort (
  clip                  STRING    NOT NULL COMMENT 'Cotality property identifier.',
  borrower_id           STRING    NOT NULL COMMENT 'Synthetic B-###… id; matches borrower_360.borrower_id.',
  state                 STRING             COMMENT '2-char state from refreshed source coverage.',
  zip                   STRING             COMMENT '5-digit ZIP.',
  situs_cbsa_code       STRING             COMMENT 'CBSA code for the property.',
  city                  STRING             COMMENT 'Property city.',
  segment_codes         ARRAY<STRING>      COMMENT 'Segment tags from borrower_360.',
  opportunity_score     INT                COMMENT '0-100 opportunity score at refresh time.',
  equity_estimate       BIGINT             COMMENT 'AVM - total open liens, floored at 0.',
  equity_pct            INT                COMMENT 'Equity %, [0,100].',
  rate_spread_bps       INT                COMMENT 'First-position rate vs MORTGAGE30US, bps.',
  recommended_offer     STRING             COMMENT 'Human-readable next-best-offer label.',
  origination_date      DATE      NOT NULL COMMENT 'first_pos_date from silver.lien_current.',
  origination_rate      DOUBLE    NOT NULL COMMENT 'first_pos_rate (fractional; < 0.03).',
  first_pos_loan_type   STRING             COMMENT 'CONV / FHA / VA / other; from silver.',
  first_pos_term_months INT                COMMENT 'Loan term in months; from silver.',
  origination_year      INT                COMMENT 'YEAR(origination_date) for fast GROUP BY.',
  cohort_tag            STRING    NOT NULL COMMENT 'Stable tag for GROUP BY; today always "sub3_2020_2022".',
  refreshed_at          TIMESTAMP NOT NULL COMMENT 'CTAS refresh timestamp.'
)
USING DELTA
CLUSTER BY (state, origination_year)
COMMENT 'Sub-3% 2020-2022 lock-in cohort. Gold-layer alternative to silver.lien_current for sample question 5.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 10. mip.gold.borrower_dossier
--     Pre-joined dossier surface keyed by borrower_id. One row per borrower,
--     carrying every column the `/api/borrowers/{id}` response needs plus the
--     full evidence array (capped at 20) and the top-3 trigger timeline —
--     so the repository read path collapses to a single indexed row lookup
--     on the cluster key.
--
--     Slice13-accuracy perf fix: the serial borrower_360 + evidence_events
--     fetch cost ~3300 ms p95; a pre-joined single-statement read targets
--     < 2000 ms (ideally < 1000 ms warm). Populated by the CTAS at
--     sql/transformations/gold_borrower_dossier.sql. Mirror schema (column
--     order + types) with that SELECT list; liquid clustering on
--     borrower_id so the indexed-row lookup hits the cluster key.
-- -----------------------------------------------------------------------------
-- -----------------------------------------------------------------------------
-- 11. mip.gold.county_rollup
--     Per-county aggregate keyed on 5-char FIPS + snapshot_date. Backs
--     /api/geo/county-rollups for the USChoroplethMap county drill.
--     See sql/ddl/gold_county_rollup.sql for column comments.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.county_rollup (
  fips_5                       STRING    NOT NULL COMMENT '5-char FIPS: 2-char state + 3-char county. PK part.',
  state                        STRING    NOT NULL COMMENT '2-char USPS state code (uppercase).',
  county_name                  STRING             COMMENT 'Human county name. NULL until a FIPS->name crosswalk seed lands; UI falls back to fips_5.',
  addressable_borrowers        INT       NOT NULL COMMENT 'Population count for this county on snapshot_date.',
  in_the_money_borrowers       INT       NOT NULL COMMENT 'COUNT where borrower_360.in_the_money = TRUE.',
  high_opportunity_borrowers   INT       NOT NULL COMMENT 'COUNT where borrower_360.opportunity_score >= 75.',
  avg_opportunity_score        INT       NOT NULL COMMENT 'AVG(borrower_360.opportunity_score) rounded to int.',
  top_segment_code             STRING             COMMENT 'Dominant segment_code by count. NULL when every borrower in the county has empty segment_codes.',
  snapshot_date                DATE      NOT NULL COMMENT 'Refresh date; daily grain. PK part.',
  snapshot_at                  TIMESTAMP NOT NULL COMMENT 'Precise refresh timestamp.'
)
USING DELTA
CLUSTER BY (state, fips_5)
COMMENT 'Per-county aggregate from gold.borrower_360.county_fips_5.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 12. mip.gold.zip_rollup
--     Per-ZIP aggregate with county FIPS + a stable-ranked sample_borrower_id
--     so the USChoroplethMap's ZIP-tile deep-link lands on a real dossier.
--     See sql/ddl/gold_zip_rollup.sql for column comments.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.zip_rollup (
  state                     STRING    NOT NULL COMMENT '2-char USPS state code (uppercase). PK part.',
  county_fips_5             STRING             COMMENT '5-char county FIPS. Nullable when silver lacked a county geocode. PK part when present.',
  zip                       STRING    NOT NULL COMMENT '5-digit ZIP (STRING preserves leading zeros). PK part.',
  addressable_borrowers     INT       NOT NULL COMMENT 'Population count for this ZIP on snapshot_date.',
  avg_opportunity_score     INT       NOT NULL COMMENT 'AVG(borrower_360.opportunity_score) rounded to int.',
  top_segment_code          STRING             COMMENT 'Dominant segment_code by count. NULL when every borrower in the ZIP has empty segment_codes.',
  sample_borrower_id        STRING             COMMENT 'Stable-ranked top borrower_id in the ZIP (ORDER BY opportunity_score DESC, borrower_id ASC). Used by UI deep-link.',
  snapshot_date             DATE      NOT NULL COMMENT 'Refresh date; daily grain. PK part.',
  snapshot_at               TIMESTAMP NOT NULL COMMENT 'Precise refresh timestamp.'
)
USING DELTA
CLUSTER BY (state, zip)
COMMENT 'Per-ZIP aggregate from gold.borrower_360 + stable sample_borrower_id for UI deep-link.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 13. mip.gold.state_top_segment
--     Per-state dominant segment + share %. Feeds top_segment_code extension
--     on /api/geo/state-rollups so the map reads gold rollups.
--     See sql/ddl/gold_state_top_segment.sql for column comments.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.state_top_segment (
  state                    STRING    NOT NULL COMMENT '2-char USPS state code (uppercase). PK part.',
  top_segment_code         STRING    NOT NULL COMMENT 'Dominant SegmentCode for this state on snapshot_date (itm/listed/permit/investor/equity/retention). "none" when no borrower in the state has a non-empty segment_codes array.',
  top_segment_share_pct    INT       NOT NULL COMMENT '0..100. Share of the state population in the top segment.',
  snapshot_date            DATE      NOT NULL COMMENT 'Refresh date; daily grain. PK part.'
)
USING DELTA
CLUSTER BY (state)
COMMENT 'Per-state top-segment rollup from gold.borrower_360.segment_codes exploded.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

CREATE TABLE IF NOT EXISTS mip.gold.borrower_dossier (
  clip                      STRING    NOT NULL COMMENT 'CLIP. 1:1 with borrower_360.clip.',
  borrower_id               STRING    NOT NULL COMMENT 'Synthetic id; cluster key. Matches borrower_360.borrower_id.',
  display_name              STRING    NOT NULL COMMENT 'Synthesized label; never a real name.',
  city                      STRING             COMMENT 'Situs city.',
  state                     STRING    NOT NULL COMMENT 'Situs state from refreshed source coverage.',
  zip                       STRING             COMMENT '5-digit situs ZIP.',
  situs_cbsa_code           STRING             COMMENT 'CBSA metro code.',
  segment_codes             ARRAY<STRING> NOT NULL COMMENT 'Ordered SegmentCode list.',
  equity_estimate           BIGINT    NOT NULL COMMENT 'USD.',
  equity_pct                INT       NOT NULL COMMENT '0..100.',
  rate_spread_bps           INT       NOT NULL COMMENT 'fn_rate_spread output.',
  market_rate_fraction      DOUBLE    NOT NULL COMMENT 'Fractional market rate.',
  opportunity_score         INT       NOT NULL COMMENT 'fn_lead_score output 0..100.',
  confidence                INT       NOT NULL COMMENT 'Mean of 5 sub-scores.',
  recommended_offer_code    STRING    NOT NULL COMMENT 'fn_next_best_offer code.',
  recommended_offer         STRING    NOT NULL COMMENT 'Human label.',
  why_now                   STRING    NOT NULL COMMENT 'Deterministic template per offer code.',
  evidence_ids              ARRAY<STRING> NOT NULL COMMENT 'Ordered evidence ids.',
  approval_status           STRING    NOT NULL COMMENT 'Default "pending"; Lakebase authoritative.',
  owner_link_id             STRING             COMMENT 'Cotality Owner Link id.',
  subject_property          STRING    NOT NULL COMMENT 'Synthetic city/state/ZIP5 string.',
  avm_value                 BIGINT    NOT NULL COMMENT 'AVM value; 0 when missing.',
  current_lien_balance      BIGINT    NOT NULL COMMENT 'Total open lien balance.',
  current_rate              DOUBLE    NOT NULL COMMENT 'Percent form (5.75).',
  ltv                       INT       NOT NULL COMMENT 'Display LTV int; underwater borrowers may exceed 100.',
  related_property_count    INT       NOT NULL COMMENT 'From gold.property_owner_bridge.',
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'owner_occupancy_code = "O".',
  is_absentee               BOOLEAN   NOT NULL COMMENT 'From silver.property_master.',
  is_corporate_owner        BOOLEAN   NOT NULL COMMENT 'From silver.property_master.',
  has_permit                BOOLEAN   NOT NULL COMMENT 'Filed building-permit flag. FALSE until a true Cotality Building Permits source table is present.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'TRUE when borrower_360 has a current active/under-contract Cotality MLS listing row.',
  listing_status_category   STRING             COMMENT 'Cotality standardized MLS listing status category.',
  listing_status_description STRING            COMMENT 'Display-safe Cotality MLS status description.',
  listing_date              DATE               COMMENT 'MLS listing date.',
  listing_status_date       DATE               COMMENT 'Most recent MLS status/change date.',
  listing_price             BIGINT             COMMENT 'Current MLS listing price in USD, when supplied.',
  listing_days_on_market    INT                COMMENT 'MLS days-on-market value, when supplied.',
  listing_service           STRING             COMMENT 'MLS/listing service label when supplied.',
  heloc_propensity_score    INT                COMMENT 'Cotality HELOC propensity score, 0..999 in the current feed. Model signal, not a permit filing.',
  heloc_propensity_run_date DATE               COMMENT 'Cotality HELOC propensity model run date.',
  has_heloc_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when heloc_propensity_score >= 700. Drives HELOC Intent without setting has_permit.',
  refi_propensity_score     INT                COMMENT 'Cotality refinance propensity score, 0..999 in the current feed.',
  refi_propensity_run_date  DATE               COMMENT 'Cotality refinance propensity model run date.',
  has_refi_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when refi_propensity_score >= 700. Adds intent score context.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'Derived: multi-property OR corporate OR absentee.',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'Current servicer is a tenant-lender alias in ref.lender_dictionary.',
  is_former_customer        BOOLEAN   NOT NULL COMMENT 'Historical tenant-lender relationship with no current tenant lien.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'Current servicer is known and not a tenant-lender alias.',
  has_first_party_relationship BOOLEAN NOT NULL COMMENT 'TRUE when optional first-party feeds resolve to this borrower.',
  first_party_relationship_depth INT   NOT NULL COMMENT 'Bounded count of resolved first-party feed categories.',
  first_party_recent_interactions INT  NOT NULL COMMENT 'Recent interaction count from the first-party engagement feed.',
  first_party_recent_application BOOLEAN NOT NULL COMMENT 'TRUE when a recent first-party LOS/application event exists.',
  first_party_synthetic_demo     BOOLEAN NOT NULL COMMENT 'TRUE only for rows touched by the Summit demo_synthetic first-party seed.',
  marketing_eligible      BOOLEAN   NOT NULL COMMENT 'From borrower_360; TRUE only when consent, suppression, and frequency-cap gates are clear.',
  consent_status          STRING    NOT NULL COMMENT 'From borrower_360; opt_in / opt_out / unknown.',
  suppression_reason      STRING             COMMENT 'From borrower_360; controlled suppression reason.',
  last_touch_at           TIMESTAMP          COMMENT 'From borrower_360; most recent first-party marketing/contact touch.',
  eligible_recontact_at   TIMESTAMP          COMMENT 'From borrower_360; earliest permitted re-contact time when capped.',
  dnc                     BOOLEAN   NOT NULL COMMENT 'From borrower_360; TRUE when a first-party do_not_contact suppression exists. Synthetic-by-design consent signal.',
  eligibility_source      STRING    NOT NULL COMMENT 'From borrower_360; provenance of the consent/eligibility fields. synthetic_seed until a CRM/CDP connector supplies it.',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference.',
  second_pos_amount         BIGINT             COMMENT 'For "equity" segment predicate.',
  first_pos_loan_type       STRING             COMMENT 'For fit sub-score.',
  owner_name_hash           STRING    NOT NULL COMMENT 'sha2 hash from silver; internal only, router strips.',
  min_spread_bps_applied    INT       NOT NULL COMMENT 'Threshold this refresh.',
  min_equity_pct_applied    INT       NOT NULL COMMENT 'Threshold this refresh.',
  heloc_equity_min_applied  INT       NOT NULL COMMENT 'HELOC equity threshold this refresh.',
  cashout_equity_min_applied INT      NOT NULL COMMENT 'Cash-out equity threshold this refresh.',
  retention_min_spread_applied INT    NOT NULL COMMENT 'Retention spread threshold this refresh.',
  in_the_money              BOOLEAN   NOT NULL COMMENT 'fn_in_the_money output.',
  trigger_timeline_json     STRING    NOT NULL COMMENT 'JSON-encoded top-3 evidence rows (carried from borrower_360 for parity).',
  evidence_events           ARRAY<STRUCT<evidence_id: STRING, source_product: STRING, source_table: STRING, signal_type: STRING, signal_value: STRING, display_text: STRING, confidence: DOUBLE, `timestamp`: STRING, signal_rank: INT>>
                                      NOT NULL COMMENT 'Full evidence array (capped at 20 per CLIP) sorted by signal_rank.',
  trigger_timeline          ARRAY<STRUCT<evidence_id: STRING, source_product: STRING, source_table: STRING, signal_type: STRING, signal_value: STRING, display_text: STRING, confidence: DOUBLE, `timestamp`: STRING, signal_rank: INT>>
                                      NOT NULL COMMENT 'Top-3 slice of evidence_events for the trigger timeline.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
)
USING DELTA
CLUSTER BY (borrower_id)
COMMENT 'Pre-joined dossier surface for /api/borrowers/{id}. Superset of borrower_360 + top-20 evidence events per CLIP. Slice13-accuracy perf optimisation — collapses a 2-statement read into one indexed row lookup.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- -----------------------------------------------------------------------------
-- 15. mip.gold.address_lookup
--     Governed "property loan lookup" spine. One row per address_hash =
--     sha2(canonicalized_street_address || '|' || zip5, 256). Backs the
--     /api/v1/lookup/property-loan endpoint, the Growth Agent dossier
--     specialist tool fn_property_loan_lookup, and future org agents.
--
--     Populated by the CTAS at sql/transformations/gold_address_lookup.sql,
--     which reads the SAME raw-share sources silver_property_master +
--     silver_lien_current read (ETL identity runs it). Mirror schema (column
--     order + types) with that SELECT list.
--
--     PII invariant: `situs_street_address` EXISTS in the raw share but is
--     NEVER a column here — only its hash. The raw street address is read only
--     inside the CTAS `hashed` CTE and dropped at the outer SELECT. clip /
--     owner_link_id are raw in gold and masked at the app/audit boundary.
--     v1 is EXACT-after-canonicalization against the refreshed share only
--     (no fuzzy match, no CLIP mastering).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mip.gold.address_lookup (
  address_hash              STRING    NOT NULL COMMENT 'PK. sha2(CONCAT(normalized_address, "|", zip5), 256), lowercase hex. Normalization: UPPER, TRIM, collapse whitespace runs, strip . , # (no abbreviation expansion). Mirrors backend/services/address_normalization.py. The raw street address is not stored; note this is a salt-free join key, so a privileged UC reader holding candidate addresses can test membership by hashing them — the audit ledger uses a tenant-secret HMAC token instead, and a keyed gold join key is the documented customer-deploy hardening.',
  zip5                      STRING             COMMENT '5-digit situs ZIP used in the hash.',
  situs_city                STRING             COMMENT 'Situs city (already in silver/gold; safe to display).',
  situs_state               STRING             COMMENT 'Situs state (already in silver/gold; safe to display).',
  clip                      STRING    NOT NULL COMMENT 'Cotality CLIP for the matched property. Raw in gold; masked to clip_ref_* at the app/audit boundary.',
  owner_link_id             STRING             COMMENT 'Cotality Owner Link id when present. Raw in gold; masked to owner_link_ref_* at egress.',
  has_open_lien             BOOLEAN   NOT NULL COMMENT 'TRUE when the lien spine shows any open mortgage lien for this CLIP.',
  current_lien_balance      BIGINT    NOT NULL COMMENT 'USD total_amount_of_open_mortgage_liens (COALESCE 0). Same source as borrower_360.current_lien_balance.',
  ltv                       INT       NOT NULL COMMENT 'Display LTV int from estimated_combined_ltv_loan_to_value (>=0, not upper-capped). 0 when no CLTV signal.',
  first_pos_lender_current  STRING             COMMENT 'Raw first-position currently-assigned servicer string. Generalized to a public-safe alias at the app boundary; never displayed raw.',
  current_rate              DOUBLE    NOT NULL COMMENT 'First-position mortgage rate in PERCENT form (5.75), bounded 1-15% as in silver. 0.0 when no rate signal.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Deterministic refresh anchor from mip.ref.refresh_run_state.'
)
USING DELTA
CLUSTER BY (address_hash)
COMMENT 'Governed property loan lookup spine. One row per address_hash. Share-scoped EXACT-after-canonicalization lookup (NOT Cotality CLIP mastering; no fuzzy match). Raw street address is NEVER stored — only its hash. Consumed by the property-loan-lookup API, the Growth Agent dossier specialist, and future org agents.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
