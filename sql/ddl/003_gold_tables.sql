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
--   2. gold.borrower_360              (depends on gold.property_owner_bridge)
--   3. gold.evidence_events           (CLIP-keyed; depends on borrower_360's CLIP set)
--   4. gold.lead_scores               (depends on borrower_360 + evidence_events)
--   5. gold.lead_population           (depends on borrower_360 + lead_scores)
--   6. gold.segment_population        (depends on borrower_360 + lead_scores)
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
  state                     STRING    NOT NULL COMMENT 'Situs state (6-state footprint).',
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
  is_current_customer       BOOLEAN   NOT NULL COMMENT 'Lender name contains Summit.',
  is_competitor_lien        BOOLEAN   NOT NULL COMMENT 'Current lender != Summit.',
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
-- 4. mip.gold.lead_scores
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
  is_competitor_lien       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
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
-- 5. mip.gold.lead_population
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
  rate_spread_bps           INT       NOT NULL COMMENT 'bps.',
  opportunity_score         INT       NOT NULL COMMENT '0..100.',
  confidence                INT       NOT NULL COMMENT '0..100.',
  recommended_offer         STRING    NOT NULL COMMENT 'Human label.',
  why_now                   STRING    NOT NULL COMMENT 'Deterministic template.',
  evidence_ids              ARRAY<STRING> NOT NULL COMMENT 'Evidence ids (ordered).',
  approval_status           STRING    NOT NULL COMMENT 'Default "pending".',
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
-- 6. mip.gold.segment_population (+ segment_population_prior)
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
  borrower_id       STRING    NOT NULL COMMENT 'Synthetic B-##### id; matches borrower_360.borrower_id.',
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
  state                 STRING             COMMENT '2-char state; always in the 6-state footprint.',
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
