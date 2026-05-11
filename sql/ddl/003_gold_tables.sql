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
  customer_key_hash   STRING,
  borrower_id         STRING,
  clip_ref            STRING,
  state               STRING,
  zip                 STRING,
  application_status  STRING,
  application_channel STRING,
  product_intent      STRING,
  application_at      TIMESTAMP,
  source_system       STRING,
  feed_mode           STRING,
  synthetic_demo      BOOLEAN,
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
  source_system          STRING,
  feed_mode              STRING,
  synthetic_demo         BOOLEAN,
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
  related_property_count    INT       NOT NULL COMMENT 'Count of distinct CLIPs tied to this Owner Link.',
  corporate_property_count  INT       NOT NULL COMMENT 'Number of related properties with owner_is_corporate = TRUE.',
  absentee_property_count   INT       NOT NULL COMMENT 'Number of related properties with is_absentee = TRUE.',
  distinct_states_count     INT       NOT NULL COMMENT 'Number of distinct situs_state values.',
  distinct_cbsa_count       INT       NOT NULL COMMENT 'Number of distinct situs_cbsa_code values.',
  primary_clip              STRING             COMMENT 'Owner-occupied CLIP; NULL when no owner-occupant.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
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
  clip                      STRING    NOT NULL COMMENT 'CLIP. PK. Router maps to Borrower360.clip_id.',
  borrower_id               STRING    NOT NULL COMMENT 'Synthetic id from CLIP hash.',
  display_name              STRING    NOT NULL COMMENT 'Synthesized label; never a real name.',
  city                      STRING             COMMENT 'Situs city.',
  state                     STRING    NOT NULL COMMENT 'Situs state from refreshed source coverage.',
  zip                       STRING             COMMENT '5-digit situs ZIP.',
  situs_cbsa_code           STRING             COMMENT 'CBSA metro code.',
  county_fips_5             STRING             COMMENT '5-char FIPS county code from silver.property_master.fips_county_code. Feeds gold.county_rollup + gold.zip_rollup.',
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
  ltv                       INT       NOT NULL COMMENT '0..100.',
  related_property_count    INT       NOT NULL COMMENT 'From gold.property_owner_bridge.',
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'owner_occupancy_code = "O".',
  is_absentee               BOOLEAN   NOT NULL COMMENT 'From silver.property_master.',
  is_corporate_owner        BOOLEAN   NOT NULL COMMENT 'From silver.property_master.',
  has_permit                BOOLEAN   NOT NULL COMMENT 'BLOCKED: FALSE until Cotality Building Permits lands.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'BLOCKED: FALSE until Cotality MLS Listings lands.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'Derived: multi-property OR corporate OR absentee.',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'Current servicer is a tenant-lender alias in ref.lender_dictionary.',
  is_former_customer        BOOLEAN   NOT NULL COMMENT 'Historical tenant-lender relationship with no current tenant lien.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'Current servicer is known and not a tenant-lender alias.',
  has_first_party_relationship BOOLEAN NOT NULL COMMENT 'TRUE when optional first-party feeds resolve to this borrower.',
  first_party_relationship_depth INT   NOT NULL COMMENT 'Bounded count of resolved first-party feed categories.',
  first_party_recent_interactions INT  NOT NULL COMMENT 'Recent interaction count from the first-party engagement feed.',
  first_party_recent_application BOOLEAN NOT NULL COMMENT 'TRUE when a recent first-party LOS/application event exists.',
  first_party_synthetic_demo     BOOLEAN NOT NULL COMMENT 'TRUE only for rows touched by the Summit demo_synthetic first-party seed.',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference.',
  second_pos_amount         BIGINT             COMMENT 'For "equity" segment predicate.',
  first_pos_loan_type       STRING             COMMENT 'For fit sub-score.',
  owner_name_hash           STRING    NOT NULL COMMENT 'sha2 hash from silver; internal only, router strips.',
  min_spread_bps_applied    INT       NOT NULL COMMENT 'Threshold this refresh.',
  min_equity_pct_applied    INT       NOT NULL COMMENT 'Threshold this refresh.',
  in_the_money              BOOLEAN   NOT NULL COMMENT 'fn_in_the_money output.',
  trigger_timeline_json     STRING    NOT NULL COMMENT 'JSON-encoded top-3 evidence rows.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
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
  clip           STRING NOT NULL COMMENT 'CLIP; router strips.',
  evidence_id    STRING NOT NULL COMMENT 'Deterministic ev-<12hex>.',
  source_product STRING NOT NULL COMMENT 'Voluntary Lien / AVM / etc.',
  source_table   STRING NOT NULL COMMENT 'Real UC path.',
  signal_type    STRING NOT NULL COMMENT 'Controlled vocab; permit + listing BLOCKED.',
  signal_value   STRING NOT NULL COMMENT 'Human-readable value.',
  display_text   STRING NOT NULL COMMENT 'Deterministic template per signal_type.',
  confidence     DOUBLE NOT NULL COMMENT '0..1.',
  `timestamp`    STRING NOT NULL COMMENT 'ISO-8601 string.',
  signal_rank    INT    NOT NULL COMMENT 'Priority order within CLIP.'
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
  clip                     STRING    NOT NULL COMMENT 'CLIP. PK.',
  economic_incentive       INT       NOT NULL COMMENT '0..100 sub-score; weight 0.35.',
  intent_trigger           INT       NOT NULL COMMENT '0..100 sub-score; weight 0.30.',
  fit                      INT       NOT NULL COMMENT '0..100 sub-score; weight 0.15.',
  relationship             INT       NOT NULL COMMENT '0..100 sub-score; weight 0.10.',
  evidence                 INT       NOT NULL COMMENT '0..100 sub-score; weight 0.10.',
  opportunity_score        INT       NOT NULL COMMENT 'fn_lead_score output.',
  confidence               INT       NOT NULL COMMENT 'ROUND(mean(sub-scores)).',
  in_the_money             BOOLEAN   NOT NULL COMMENT 'fn_in_the_money output.',
  recommended_offer_code   STRING    NOT NULL COMMENT 'fn_next_best_offer output.',
  rate_spread_bps          INT       NOT NULL COMMENT 'Input carried for parity transparency.',
  equity_pct               INT       NOT NULL COMMENT 'Input carried for parity transparency.',
  has_permit               BOOLEAN   NOT NULL COMMENT 'BLOCKED FALSE.',
  listed_for_sale          BOOLEAN   NOT NULL COMMENT 'BLOCKED FALSE.',
  is_investor              BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_current_customer      BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_former_customer       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_competitor_lien       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  has_first_party_relationship BOOLEAN NOT NULL COMMENT 'Carried from borrower_360. TRUE when optional first-party feeds resolve to this borrower.',
  first_party_relationship_depth INT   NOT NULL COMMENT 'Bounded count of resolved first-party feed categories.',
  first_party_recent_interactions INT  NOT NULL COMMENT 'Recent positive interaction count from the first-party engagement feed.',
  first_party_recent_application BOOLEAN NOT NULL COMMENT 'TRUE when a recent first-party LOS/application event exists.',
  first_party_synthetic_demo     BOOLEAN NOT NULL COMMENT 'TRUE only for rows touched by the Summit demo_synthetic first-party seed.',
  min_spread_bps_applied   INT       NOT NULL COMMENT 'Threshold applied this refresh.',
  min_equity_pct_applied   INT       NOT NULL COMMENT 'Threshold applied this refresh.',
  heloc_equity_min_applied INT       NOT NULL COMMENT 'HELOC equity threshold this refresh.',
  cashout_equity_min_applied INT     NOT NULL COMMENT 'Cash-out equity threshold this refresh.',
  retention_min_spread_applied INT   NOT NULL COMMENT 'Retention spread threshold this refresh.',
  refreshed_at             TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
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
  clip                      STRING    NOT NULL COMMENT 'CLIP. PK.',
  borrower_id               STRING    NOT NULL COMMENT 'Synthetic id.',
  display_name              STRING    NOT NULL COMMENT 'Synthesized label.',
  city                      STRING             COMMENT 'Situs city.',
  state                     STRING    NOT NULL COMMENT 'Situs state.',
  zip                       STRING             COMMENT '5-digit ZIP.',
  segment_codes             ARRAY<STRING> NOT NULL COMMENT 'Ordered SegmentCode list.',
  equity_estimate           BIGINT    NOT NULL COMMENT 'USD.',
  equity_pct                INT       NOT NULL COMMENT '0..100; carried from borrower_360 for dashboard top-borrower widgets.',
  rate_spread_bps           INT       NOT NULL COMMENT 'bps.',
  opportunity_score         INT       NOT NULL COMMENT '0..100.',
  confidence                INT       NOT NULL COMMENT '0..100.',
  recommended_offer         STRING    NOT NULL COMMENT 'Human label.',
  why_now                   STRING    NOT NULL COMMENT 'Deterministic template.',
  evidence_ids              ARRAY<STRING> NOT NULL COMMENT 'Evidence ids (ordered).',
  approval_status           STRING    NOT NULL COMMENT 'Default "pending".',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference.',
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'From borrower_360.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'From borrower_360.',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'From borrower_360.',
  is_former_customer        BOOLEAN   NOT NULL COMMENT 'From borrower_360.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'From borrower_360.',
  related_property_count    INT       NOT NULL COMMENT 'From borrower_360.',
  current_lien_balance      BIGINT    NOT NULL COMMENT 'From borrower_360.',
  second_pos_amount         BIGINT             COMMENT 'From borrower_360.',
  has_permit                BOOLEAN   NOT NULL COMMENT 'BLOCKED FALSE until Cotality Permits lands.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'BLOCKED FALSE until Cotality MLS lands.',
  rank_overall              INT       NOT NULL COMMENT 'DENSE_RANK across population.',
  rank_within_state         INT       NOT NULL COMMENT 'DENSE_RANK within state.',
  population_version        STRING    NOT NULL COMMENT 'YYYYMMDD-v1 provenance chip.',
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
  segment_code    STRING    NOT NULL COMMENT 'itm/listed/permit/investor/equity/retention.',
  state           STRING    NOT NULL COMMENT '2-char state or "_ALL".',
  name            STRING    NOT NULL COMMENT 'Static label.',
  count           INT       NOT NULL COMMENT 'Member count.',
  delta_vs_prior  STRING    NOT NULL COMMENT 'QoQ delta "+NN%" / "-NN%"; "+0%" on first refresh.',
  avg_score       INT       NOT NULL COMMENT 'AVG opportunity_score.',
  description     STRING    NOT NULL COMMENT 'Static description.',
  color           STRING    NOT NULL COMMENT 'Hex color.',
  refreshed_at    TIMESTAMP NOT NULL COMMENT 'Refresh timestamp.'
)
USING DELTA
CLUSTER BY (segment_code)
COMMENT 'Per-segment counts; listed + permit are BLOCKED (zero counts on real data). See docs/data-contract-module0.md §3.6.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

CREATE TABLE IF NOT EXISTS mip.gold.segment_population_prior (
  segment_code    STRING    NOT NULL COMMENT 'Matches segment_population.segment_code.',
  state           STRING    NOT NULL COMMENT 'Matches segment_population.state.',
  snapshot_date   DATE      NOT NULL COMMENT 'Daily snapshot date.',
  count           INT       NOT NULL COMMENT 'Count on snapshot_date.',
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
  synced_at         TIMESTAMP NOT NULL COMMENT 'Last sync run that touched this row.'
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
  fips_5                       STRING    NOT NULL COMMENT '5-char FIPS: 2-char state + 3-char county.',
  state                        STRING    NOT NULL COMMENT '2-char USPS state code.',
  county_name                  STRING             COMMENT 'Human county name; NULL until crosswalk seed lands.',
  addressable_borrowers        INT       NOT NULL COMMENT 'Population count for this county.',
  in_the_money_borrowers       INT       NOT NULL COMMENT 'COUNT where in_the_money = TRUE.',
  high_opportunity_borrowers   INT       NOT NULL COMMENT 'COUNT where opportunity_score >= 75.',
  avg_opportunity_score        INT       NOT NULL COMMENT 'AVG(opportunity_score) rounded to int.',
  top_segment_code             STRING             COMMENT 'Dominant segment_code by count.',
  snapshot_date                DATE      NOT NULL COMMENT 'Refresh date; daily grain.',
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
  state                     STRING    NOT NULL COMMENT '2-char USPS state code. PK part.',
  county_fips_5             STRING             COMMENT '5-char county FIPS (nullable). PK part when present.',
  zip                       STRING    NOT NULL COMMENT '5-digit ZIP. PK part.',
  addressable_borrowers     INT       NOT NULL COMMENT 'Population count for this ZIP.',
  avg_opportunity_score     INT       NOT NULL COMMENT 'AVG(opportunity_score) rounded to int.',
  top_segment_code          STRING             COMMENT 'Dominant segment_code by count.',
  sample_borrower_id        STRING             COMMENT 'Stable-ranked top borrower_id in the ZIP.',
  snapshot_date             DATE      NOT NULL COMMENT 'Refresh date; daily grain.',
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
  state                    STRING    NOT NULL COMMENT '2-char USPS state code.',
  top_segment_code         STRING    NOT NULL COMMENT 'Dominant SegmentCode; "none" when empty.',
  top_segment_share_pct    INT       NOT NULL COMMENT 'Share of state population in the top segment, 0..100.',
  snapshot_date            DATE      NOT NULL COMMENT 'Refresh date; daily grain.'
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
  ltv                       INT       NOT NULL COMMENT '0..100.',
  related_property_count    INT       NOT NULL COMMENT 'From gold.property_owner_bridge.',
  is_owner_occupied         BOOLEAN   NOT NULL COMMENT 'owner_occupancy_code = "O".',
  is_absentee               BOOLEAN   NOT NULL COMMENT 'From silver.property_master.',
  is_corporate_owner        BOOLEAN   NOT NULL COMMENT 'From silver.property_master.',
  has_permit                BOOLEAN   NOT NULL COMMENT 'BLOCKED: FALSE until Cotality Building Permits lands.',
  listed_for_sale           BOOLEAN   NOT NULL COMMENT 'BLOCKED: FALSE until Cotality MLS Listings lands.',
  is_investor               BOOLEAN   NOT NULL COMMENT 'Derived: multi-property OR corporate OR absentee.',
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'Current servicer is a tenant-lender alias in ref.lender_dictionary.',
  is_former_customer        BOOLEAN   NOT NULL COMMENT 'Historical tenant-lender relationship with no current tenant lien.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'Current servicer is known and not a tenant-lender alias.',
  has_first_party_relationship BOOLEAN NOT NULL COMMENT 'TRUE when optional first-party feeds resolve to this borrower.',
  first_party_relationship_depth INT   NOT NULL COMMENT 'Bounded count of resolved first-party feed categories.',
  first_party_recent_interactions INT  NOT NULL COMMENT 'Recent interaction count from the first-party engagement feed.',
  first_party_recent_application BOOLEAN NOT NULL COMMENT 'TRUE when a recent first-party LOS/application event exists.',
  first_party_synthetic_demo     BOOLEAN NOT NULL COMMENT 'TRUE only for rows touched by the Summit demo_synthetic first-party seed.',
  current_lender_ref        STRING             COMMENT 'Public-demo-safe current-servicer reference.',
  second_pos_amount         BIGINT             COMMENT 'For "equity" segment predicate.',
  first_pos_loan_type       STRING             COMMENT 'For fit sub-score.',
  owner_name_hash           STRING    NOT NULL COMMENT 'sha2 hash from silver; internal only, router strips.',
  min_spread_bps_applied    INT       NOT NULL COMMENT 'Threshold this refresh.',
  min_equity_pct_applied    INT       NOT NULL COMMENT 'Threshold this refresh.',
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
