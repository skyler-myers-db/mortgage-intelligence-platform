-- =============================================================================
-- silver_lien_current.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip_demo.silver.lien_current`. THE SPINE of Module 0:
--            one row per CLIP carrying the current-state lien stack, rates,
--            AVM, equity, LTV, and servicer. Every gold lead-scoring join
--            starts here. 1:1 with `entrada_eval_voluntary_lien_status_
--            marketing_v2` on the CLIP key, filtered to the 6-state demo
--            footprint.
--
-- Data contract reference: docs/data-contract-module0.md §2.1.
-- Slice:     module0-real-data-slice2 (silver lift from the Cotality share).
-- Source:    cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_
--            status_marketing_v2 (5.16M rows, CLIP 1:1).
--
-- Geography filter (non-negotiable, CLAUDE.md + data-contract-module0 §2):
--            WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
--            -- Applied in sql/transformations/silver_lien_current.sql.
--            Single-metro demos are explicitly forbidden.
--
-- PII posture (NON-NEGOTIABLE, governance-real-data-review §1):
--            - `owner_1_full_name`       : NEVER landed in silver. The gold
--              `borrower_360.owner_name_hash` is sourced from
--              `silver.property_master.owner_name_hash` (hashed at silver
--              ingest of the property share), NOT re-hashed from this table.
--            - `situs_street_address`    : NEVER landed in silver.
--            - `first_position_lender_*` : real lender names are retained
--              because the retention/recapture segment reads them (see gold
--              §3.2 `is_current_customer`, `is_competitor_lien`). Gold
--              remaps real names to controlled `{Summit Mortgage, Competitor
--              A, Competitor B}` vocab via `ref.lender_dictionary` before
--              surfacing over `/api/*` (governance-real-data-review §1).
--
-- Rate contract (critical):
--            `first_pos_rate` is fractional (e.g. 0.0575 = 5.75%), matching
--            `sql/uc_functions/fn_rate_spread.sql`. We coerce rates <= 0 to
--            NULL to defend the UDF against unit-confusion drift in the
--            share. The share's `first_position_mortgage_interest_rate` is
--            DOUBLE in percent form (6.40 == 6.40%), so the transformation
--            file divides by 100 and enforces strict > 0.
--
-- Date-type note (from slice-2 probe of the share):
--            `first_position_mortgage_date`, `first_position_mortgage_
--            recording_date`, and `purchase_recording_date` are BIGINT in
--            yyyyMMdd form, NOT DATE. The transformation does:
--               TO_DATE(CAST(col AS STRING), 'yyyyMMdd')
--            with NULL-safe handling on 0 / malformed values.
--
-- Columns (data-contract §2.1 minus raw PII; minus raw street addresses;
-- minus `situs_cbsa_code` which is sourced from property_master join path,
-- carried here as a placeholder NULL for gold's convenience):
--
--            clip                           Property spine, PK.
--            situs_state                    6-state filter.
--            situs_zip_code                 5-digit STRING.
--            owner_occupancy_code           Occupancy code.
--            total_open_liens               Count of active mortgage liens.
--            total_open_lien_balance        Sum of open-lien balances.
--            avm_value, avm_value_high,
--            avm_value_low                  AVM point + bounds.
--            avm_confidence                 0..100 or 0..1 (scale-checked).
--            avm_as_of_date                 AVM vintage.
--            estimated_equity, estimated_cltv
--                                           Cotality-computed equity/CLTV.
--            purchase_amount, purchase_date,
--            purchase_cltv                  Last purchase tuple.
--            first_pos_date, first_pos_amount,
--            first_pos_rate, first_pos_rate_type,
--            first_pos_term_months, first_pos_loan_type,
--            first_pos_purpose, first_pos_ltv
--                                           1st-lien features.
--            first_pos_lender_original      Originating lender name.
--            first_pos_lender_current       Current servicer name (59% cov).
--            second_pos_amount, second_pos_rate,
--            second_pos_purpose, second_pos_lender
--                                           2nd-lien features (HELOC flag).
--            ingest_ts, _meta_batch_id      Audit metadata.
--
-- Clustering: Liquid cluster on (situs_state, clip) -- gold always state-
--            filters first and joins on clip; clustering here makes the
--            downstream 6-state scan cheap. 5M rows: partitioning by state
--            would create only 6 partitions (too coarse) and hurt per-CLIP
--            lookups; Delta + liquid is the right posture.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. Transformation file MERGE-keys
--            on `clip` only (CLIP is 1:1 per share).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip_demo.silver.lien_current (
  clip                       STRING    NOT NULL COMMENT 'Cotality mastered property ID. PK.',
  situs_state                STRING    NOT NULL COMMENT '2-char state code. Filter: IN (IL, CA, FL, TX, WA, CO).',
  situs_zip_code             STRING             COMMENT '5-digit situs ZIP (STRING preserves leading zeros).',
  owner_occupancy_code       STRING             COMMENT 'Owner-occupancy code: O/A/T/NULL.',
  total_open_liens           INT                COMMENT 'Count of active mortgage liens.',
  total_open_lien_balance    BIGINT             COMMENT 'Sum of open-lien balances, USD.',
  avm_value                  BIGINT             COMMENT 'Current AVM marketing value, USD.',
  avm_value_high             BIGINT             COMMENT 'AVM upper confidence bound, USD.',
  avm_value_low              BIGINT             COMMENT 'AVM lower confidence bound, USD.',
  avm_confidence             DOUBLE             COMMENT 'AVM confidence score (share emits 0..1 or 0..100; scale-checked on ingest).',
  avm_as_of_date             DATE               COMMENT 'AVM vintage.',
  estimated_equity           BIGINT             COMMENT 'Cotality-computed AVM - open-lien balance, USD.',
  estimated_cltv             DOUBLE             COMMENT 'Cotality combined LTV, 0..100.',
  purchase_amount            BIGINT             COMMENT 'Last purchase price, USD.',
  purchase_date              DATE               COMMENT 'Last deed recording date. Derived TO_DATE(CAST(yyyyMMdd_bigint AS STRING), ...).',
  purchase_cltv              DOUBLE             COMMENT 'Origination combined LTV.',
  first_pos_date             DATE               COMMENT '1st-lien origination date. BIGINT yyyyMMdd in source; converted here.',
  first_pos_amount           BIGINT             COMMENT '1st-lien original amount, USD.',
  first_pos_rate             DOUBLE             COMMENT 'Fractional rate (0.0575 = 5.75%). Rates <= 0 coerced to NULL.',
  first_pos_rate_type        STRING             COMMENT 'Rate type code: FIX/ARM/NULL.',
  first_pos_term_months      INT                COMMENT '1st-lien term in months.',
  first_pos_loan_type        STRING             COMMENT 'Loan type code: CONV/FHA/VA/etc.',
  first_pos_purpose          STRING             COMMENT 'Purpose code: PUR/REF/etc.',
  first_pos_ltv              DOUBLE             COMMENT '1st-lien LTV at origination, 0..100.',
  first_pos_lender_original  STRING             COMMENT 'Originating lender company name. Remapped to controlled vocab at gold.',
  first_pos_lender_current   STRING             COMMENT 'Current servicer company name. Remapped to controlled vocab at gold.',
  second_pos_amount          BIGINT             COMMENT '2nd-lien balance, USD. NULL/0 if none.',
  second_pos_rate            DOUBLE             COMMENT '2nd-lien fractional rate.',
  second_pos_purpose         STRING             COMMENT '2nd-lien purpose code (HELOC detection).',
  second_pos_lender          STRING             COMMENT '2nd-lien lender name. Remapped at gold.',
  ingest_ts                  TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp (CURRENT_TIMESTAMP at MERGE time).',
  _meta_batch_id             STRING             COMMENT 'Lakeflow pipeline run id for observability.'
)
USING DELTA
CLUSTER BY (situs_state, clip)
COMMENT 'CLIP-grain lien + rates snapshot lifted from cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2 filtered to IN (IL, CA, FL, TX, WA, CO). THE SPINE of Module 0 scoring. Raw owner names and situs street addresses never land here. See docs/data-contract-module0.md §2.1 and docs/governance-real-data-review.md §1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
