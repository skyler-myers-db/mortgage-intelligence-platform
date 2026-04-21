-- =============================================================================
-- silver_lien_current.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE that populates `mip.silver.lien_current`
--            from `cotality_mortgage_data.corelogic.entrada_eval_voluntary_
--            lien_status_marketing_v2`, filtered to the 6-state footprint.
--            THE SPINE of Module 0.
--
-- Grain:     One row per CLIP (1:1 with source).
-- PK (MERGE key): clip.
-- Geography filter: WHERE situs_state IN ('IL','CA','FL','TX','WA','CO').
-- Slice:     module0-real-data-slice2.
-- Data contract: docs/data-contract-module0.md §2.1.
--
-- PII posture:
--   - `owner_1_full_name` from this share IS NEVER landed here. The hashed
--     owner_name_hash column is a property_master-layer column; gold joins
--     lien_current to property_master on CLIP and picks up owner_name_hash
--     from there. This keeps the silver PII surface in exactly one table.
--   - `situs_street_address` is NEVER projected.
--
-- Rate contract:
--   - Share `first_position_mortgage_interest_rate` is DOUBLE in PERCENT form
--     (e.g. 6.40 == 6.40%). We divide by 100 to get fractional form, then
--     coerce rates <= 0 to NULL (defends fn_rate_spread against unit-confusion).
--   - `second_position_mortgage_interest_rate` gets identical treatment.
--
-- Date-type coercion (share probe, Apr 2026):
--   - `first_position_mortgage_date` and `purchase_recording_date` are BIGINT
--     in yyyyMMdd form. Converted via TO_DATE(CAST(x AS STRING), 'yyyyMMdd'),
--     with NULLIF(x, 0) guard to skip the 0 sentinel.
--   - `value_as_of_date_mktg` is DATE-ish in the share; TRY_TO_DATE tolerates
--     either STRING or BIGINT forms.
--
-- Idempotency: MERGE on clip.
-- =============================================================================

MERGE INTO mip.silver.lien_current AS t
USING (
  SELECT
    clip,
    situs_state,
    -- Data contract §2.1 says situs_zip_code is 5-digit; share frequently
    -- emits ZIP+4. Truncate at silver so gold/api stay contract-clean
    -- (slice13 Wave-2 fix).
    SUBSTR(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 1, 5)
                                                                    AS situs_zip_code,
    owner_occupancy_code,
    CAST(total_number_of_open_mortgage_liens AS INT)                 AS total_open_liens,
    CAST(total_amount_of_open_mortgage_liens AS BIGINT)              AS total_open_lien_balance,
    CAST(estimated_value_mktg        AS BIGINT)                      AS avm_value,
    CAST(estimated_value_high_mktg   AS BIGINT)                      AS avm_value_high,
    CAST(estimated_value_low_mktg    AS BIGINT)                      AS avm_value_low,
    CAST(confidence_score_mktg       AS DOUBLE)                      AS avm_confidence,
    TRY_TO_DATE(CAST(value_as_of_date_mktg AS STRING))               AS avm_as_of_date,
    CAST(estimated_equity AS BIGINT)                                 AS estimated_equity,
    CAST(estimated_combined_ltv_loan_to_value AS DOUBLE)             AS estimated_cltv,
    CAST(purchase_amount AS BIGINT)                                  AS purchase_amount,
    TRY_TO_DATE(CAST(NULLIF(purchase_recording_date, 0) AS STRING), 'yyyyMMdd')
      AS purchase_date,
    CAST(purchase_combined_ltv_loan_to_value AS DOUBLE)              AS purchase_cltv,
    TRY_TO_DATE(CAST(NULLIF(first_position_mortgage_date, 0) AS STRING), 'yyyyMMdd')
      AS first_pos_date,
    CAST(first_position_mortgage_amount AS BIGINT)                   AS first_pos_amount,
    -- Share rate is DOUBLE in percent form (6.40 == 6.40%). Convert to
    -- fractional; coerce rates <= 0 to NULL per data-contract §2.1.
    CASE
      WHEN first_position_mortgage_interest_rate IS NULL THEN NULL
      WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) <= 0 THEN NULL
      ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
    END                                                              AS first_pos_rate,
    first_position_mortgage_interest_rate_type_code                  AS first_pos_rate_type,
    CAST(first_position_mortgage_term AS INT)                        AS first_pos_term_months,
    first_position_mortgage_loan_type_code                           AS first_pos_loan_type,
    first_position_mortgage_purpose_code                             AS first_pos_purpose,
    CAST(first_position_mortgage_ltv_loan_to_value AS DOUBLE)        AS first_pos_ltv,
    first_position_lender_company_name                               AS first_pos_lender_original,
    first_position_currently_assigned_lender_company_name            AS first_pos_lender_current,
    CAST(second_position_mortgage_amount AS BIGINT)                  AS second_pos_amount,
    CASE
      WHEN second_position_mortgage_interest_rate IS NULL THEN NULL
      WHEN CAST(second_position_mortgage_interest_rate AS DOUBLE) <= 0 THEN NULL
      ELSE CAST(second_position_mortgage_interest_rate AS DOUBLE) / 100.0
    END                                                              AS second_pos_rate,
    second_position_mortgage_purpose_code                            AS second_pos_purpose,
    second_position_lender_company_name                              AS second_pos_lender,
    CURRENT_TIMESTAMP()                                              AS ingest_ts,
    CAST(:batch_id AS STRING)                                        AS _meta_batch_id
  FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
  WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
    AND clip IS NOT NULL
) AS s
  ON t.clip = s.clip
WHEN MATCHED THEN UPDATE SET
  situs_state               = s.situs_state,
  situs_zip_code            = s.situs_zip_code,
  owner_occupancy_code      = s.owner_occupancy_code,
  total_open_liens          = s.total_open_liens,
  total_open_lien_balance   = s.total_open_lien_balance,
  avm_value                 = s.avm_value,
  avm_value_high            = s.avm_value_high,
  avm_value_low             = s.avm_value_low,
  avm_confidence            = s.avm_confidence,
  avm_as_of_date            = s.avm_as_of_date,
  estimated_equity          = s.estimated_equity,
  estimated_cltv            = s.estimated_cltv,
  purchase_amount           = s.purchase_amount,
  purchase_date             = s.purchase_date,
  purchase_cltv             = s.purchase_cltv,
  first_pos_date            = s.first_pos_date,
  first_pos_amount          = s.first_pos_amount,
  first_pos_rate            = s.first_pos_rate,
  first_pos_rate_type       = s.first_pos_rate_type,
  first_pos_term_months     = s.first_pos_term_months,
  first_pos_loan_type       = s.first_pos_loan_type,
  first_pos_purpose         = s.first_pos_purpose,
  first_pos_ltv             = s.first_pos_ltv,
  first_pos_lender_original = s.first_pos_lender_original,
  first_pos_lender_current  = s.first_pos_lender_current,
  second_pos_amount         = s.second_pos_amount,
  second_pos_rate           = s.second_pos_rate,
  second_pos_purpose        = s.second_pos_purpose,
  second_pos_lender         = s.second_pos_lender,
  ingest_ts                 = s.ingest_ts,
  _meta_batch_id            = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  clip, situs_state, situs_zip_code, owner_occupancy_code,
  total_open_liens, total_open_lien_balance,
  avm_value, avm_value_high, avm_value_low, avm_confidence, avm_as_of_date,
  estimated_equity, estimated_cltv,
  purchase_amount, purchase_date, purchase_cltv,
  first_pos_date, first_pos_amount, first_pos_rate, first_pos_rate_type,
  first_pos_term_months, first_pos_loan_type, first_pos_purpose, first_pos_ltv,
  first_pos_lender_original, first_pos_lender_current,
  second_pos_amount, second_pos_rate, second_pos_purpose, second_pos_lender,
  ingest_ts, _meta_batch_id
) VALUES (
  s.clip, s.situs_state, s.situs_zip_code, s.owner_occupancy_code,
  s.total_open_liens, s.total_open_lien_balance,
  s.avm_value, s.avm_value_high, s.avm_value_low, s.avm_confidence, s.avm_as_of_date,
  s.estimated_equity, s.estimated_cltv,
  s.purchase_amount, s.purchase_date, s.purchase_cltv,
  s.first_pos_date, s.first_pos_amount, s.first_pos_rate, s.first_pos_rate_type,
  s.first_pos_term_months, s.first_pos_loan_type, s.first_pos_purpose, s.first_pos_ltv,
  s.first_pos_lender_original, s.first_pos_lender_current,
  s.second_pos_amount, s.second_pos_rate, s.second_pos_purpose, s.second_pos_lender,
  s.ingest_ts, s._meta_batch_id
);
