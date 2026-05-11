-- =============================================================================
-- demo_first_party_feeds.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Populate the optional mip.first_party.* contracts with a governed
--            Summit Mortgage demo feed. This is NOT a runtime mock fallback:
--            it writes real Delta tables that the gold layer can join exactly
--            as it would join a customer's LOS, servicing, CRM, interaction,
--            and product-balance feeds.
--
-- Guardrail: tools/render_sql.py replaces {{mip_enable_demo_first_party_feeds}}
--            with TRUE/FALSE from MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS before this
--            file is uploaded to Databricks. When false, the tables are recreated
--            with the correct schema but zero rows, so a production/customer
--            deploy can keep first-party feeds empty until real ingestion is
--            connected.
--
-- PII posture:
--   - No names, emails, phones, SSNs, account numbers, or street addresses.
--   - customer/application/loan/campaign/interaction/product ids are SHA-256
--     hashes with a demo namespace.
--   - clip_ref is display-safe, not raw Cotality CLIP.
--   - borrower_id is the same synthetic CLIP-derived MIP id used by gold.
--
-- Grain:
--   - loan_applications: application-event grain.
--   - servicing_portfolio: one current/closed demo servicing row per borrower.
--   - crm_campaign_membership: most-recent campaign membership per borrower.
--   - customer_interactions: most-recent engagement event per borrower.
--   - product_balances: one banded product-balance relationship per borrower.
-- =============================================================================

CREATE OR REPLACE TEMP VIEW demo_first_party_base AS
WITH seed_enabled AS (
  SELECT {{mip_enable_demo_first_party_feeds}} AS enabled
),
anchor AS (
  SELECT
    CAST(refresh_at AS DATE) AS refresh_date,
    refresh_at AS refresh_ts
  FROM mip.ref.refresh_run_state
  ORDER BY captured_at DESC
  LIMIT 1
),
source_rows AS (
  SELECT
    lc.clip,
    CONCAT('B-', LPAD(UPPER(CONV(CAST(ABS(XXHASH64(lc.clip)) AS STRING), 10, 36)), 13, '0')) AS borrower_id,
    CONCAT('clip_ref_', SUBSTR(SHA2(CONCAT('clip|', CAST(lc.clip AS STRING)), 256), 1, 16)) AS clip_ref,
    SHA2(CONCAT('summit-demo-customer|', COALESCE(pm.owner_link_id, lc.clip)), 256) AS customer_key_hash,
    UPPER(TRIM(lc.situs_state)) AS state,
    CASE
      WHEN LENGTH(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', '')) = 5
        THEN REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', '')
      WHEN LENGTH(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', '')) = 4
       AND UPPER(TRIM(lc.situs_state)) IN ('CT','MA','ME','NH','NJ','RI','VT','PR','VI')
        THEN LPAD(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', ''), 5, '0')
      ELSE NULL
    END AS zip,
    CAST(COALESCE(lc.total_open_lien_balance, 0) AS DOUBLE) AS lien_balance,
    CAST(COALESCE(lc.first_pos_rate, 0.0630) AS DOUBLE) AS first_pos_rate,
    CAST(COALESCE(lc.second_pos_amount, 0) AS BIGINT) AS second_pos_amount,
    CAST(pmod(ABS(XXHASH64(lc.clip)), 100) AS INT) AS bucket,
    CAST(pmod(ABS(XXHASH64(CONCAT('variant|', lc.clip))), 12) AS INT) AS variant,
    a.refresh_date,
    a.refresh_ts
  FROM mip.silver.lien_current AS lc
  LEFT JOIN mip.silver.property_master AS pm
    ON pm.clip = lc.clip
  CROSS JOIN seed_enabled AS se
  CROSS JOIN anchor AS a
  WHERE se.enabled
    AND lc.clip IS NOT NULL
    AND lc.situs_state IS NOT NULL
)
SELECT *
FROM source_rows
WHERE zip IS NOT NULL;

CREATE OR REPLACE TABLE mip.first_party.loan_applications AS
SELECT
  SHA2(CONCAT('summit-demo-app|', clip), 256) AS application_id_hash,
  customer_key_hash,
  borrower_id,
  clip_ref,
  state,
  zip,
  CASE
    WHEN variant IN (0, 1, 2, 3) THEN 'funded'
    WHEN variant IN (4, 5)       THEN 'prequalified'
    WHEN variant IN (6, 7, 8)    THEN 'in_progress'
    WHEN variant = 9             THEN 'withdrawn'
    ELSE                              'declined'
  END AS application_status,
  CASE
    WHEN variant IN (0, 4, 8) THEN 'loan_officer'
    WHEN variant IN (1, 5, 9) THEN 'digital'
    WHEN variant IN (2, 6)    THEN 'branch'
    ELSE                           'call_center'
  END AS application_channel,
  CASE
    WHEN second_pos_amount > 0     THEN 'heloc'
    WHEN first_pos_rate >= 0.0700  THEN 'refinance'
    WHEN variant IN (3, 7, 11)     THEN 'purchase'
    ELSE                                'cash_out'
  END AS product_intent,
  CAST(date_add(refresh_date, -CAST(15 + bucket + variant AS INT)) AS TIMESTAMP) AS application_at,
  'summit_demo_los' AS source_system,
  'demo_synthetic' AS feed_mode,
  TRUE AS synthetic_demo,
  refresh_ts AS refreshed_at
FROM demo_first_party_base
WHERE bucket BETWEEN 8 AND 35;

CREATE OR REPLACE TABLE mip.first_party.servicing_portfolio AS
SELECT
  SHA2(CONCAT('summit-demo-servicing|', clip), 256) AS servicing_loan_id_hash,
  customer_key_hash,
  borrower_id,
  clip_ref,
  state,
  zip,
  CASE
    WHEN variant IN (0, 1, 2, 3, 4, 5) THEN 'first_mortgage'
    WHEN variant IN (6, 7, 8)          THEN 'heloc'
    ELSE                                    'home_equity_loan'
  END AS product_type,
  CAST(GREATEST(50000.0, lien_balance * (0.82 + (variant / 100.0))) AS DOUBLE) AS current_upb,
  CAST(first_pos_rate * 100 AS DOUBLE) AS note_rate_pct,
  CASE
    WHEN variant = 11 THEN '60_plus'
    WHEN variant = 10 THEN '30_59'
    ELSE                  'current'
  END AS delinquency_bucket,
  CASE
    WHEN bucket BETWEEN 0 AND 10 THEN 'active'
    WHEN bucket BETWEEN 11 AND 14 THEN 'paid_off'
    ELSE                              'transferred'
  END AS servicing_status,
  'summit_demo_servicing' AS source_system,
  'demo_synthetic' AS feed_mode,
  TRUE AS synthetic_demo,
  refresh_ts AS refreshed_at
FROM demo_first_party_base
WHERE bucket BETWEEN 0 AND 17;

CREATE OR REPLACE TABLE mip.first_party.crm_campaign_membership AS
SELECT
  SHA2(CONCAT('summit-demo-campaign-member|', clip), 256) AS campaign_member_id_hash,
  customer_key_hash,
  borrower_id,
  SHA2(CONCAT('summit-demo-campaign|', state, '|', CAST(variant % 4 AS STRING)), 256) AS campaign_key_hash,
  CASE
    WHEN variant IN (0, 1, 2, 3, 4) THEN 'email'
    WHEN variant IN (5, 6)          THEN 'call'
    WHEN variant IN (7, 8, 9)       THEN 'direct_mail'
    ELSE                                 'sms'
  END AS channel,
  CAST(date_add(refresh_date, -CAST(3 + variant + (bucket % 28) AS INT)) AS TIMESTAMP) AS last_touch_at,
  CASE
    WHEN variant = 11 THEN 'do_not_contact'
    WHEN variant = 10 THEN 'recent_contact_cap'
    ELSE                  NULL
  END AS suppression_reason,
  CASE
    WHEN variant = 11 THEN 'opt_out'
    ELSE                  'opt_in'
  END AS consent_status,
  'summit_demo_crm' AS source_system,
  'demo_synthetic' AS feed_mode,
  TRUE AS synthetic_demo,
  refresh_ts AS refreshed_at
FROM demo_first_party_base
WHERE bucket BETWEEN 0 AND 42;

CREATE OR REPLACE TABLE mip.first_party.customer_interactions AS
SELECT
  SHA2(CONCAT('summit-demo-interaction|', clip), 256) AS interaction_id_hash,
  customer_key_hash,
  borrower_id,
  CASE
    WHEN variant IN (0, 1, 2, 3) THEN 'call_center'
    WHEN variant IN (4, 5, 6)    THEN 'web'
    WHEN variant IN (7, 8)       THEN 'mobile'
    ELSE                              'branch'
  END AS interaction_channel,
  CASE
    WHEN variant IN (0, 4, 8) THEN 'rate_quote'
    WHEN variant IN (1, 5, 9) THEN 'payment_question'
    WHEN variant IN (2, 6)    THEN 'preapproval_interest'
    ELSE                           'servicing_support'
  END AS interaction_type,
  CASE
    WHEN variant IN (0, 2, 4, 6, 8) THEN 'positive'
    WHEN variant IN (1, 5, 9)       THEN 'neutral'
    ELSE                                 'no_answer'
  END AS outcome_code,
  CAST(date_add(refresh_date, -CAST(1 + variant + (bucket % 45) AS INT)) AS TIMESTAMP) AS interaction_at,
  'summit_demo_engagement' AS source_system,
  'demo_synthetic' AS feed_mode,
  TRUE AS synthetic_demo,
  refresh_ts AS refreshed_at
FROM demo_first_party_base
WHERE bucket BETWEEN 0 AND 55;

CREATE OR REPLACE TABLE mip.first_party.product_balances AS
SELECT
  SHA2(CONCAT('summit-demo-product|', clip), 256) AS product_balance_id_hash,
  customer_key_hash,
  borrower_id,
  CASE
    WHEN variant IN (0, 1, 2, 3) THEN 'deposit'
    WHEN variant IN (4, 5, 6)    THEN 'credit_card'
    WHEN variant IN (7, 8)       THEN 'wealth'
    ELSE                              'personal_loan'
  END AS product_family,
  CASE
    WHEN variant IN (0, 4, 8) THEN '25k_100k'
    WHEN variant IN (1, 5, 9) THEN '100k_250k'
    WHEN variant IN (2, 6)    THEN '250k_plus'
    ELSE                           'under_25k'
  END AS balance_band,
  CAST(12 + (bucket * 3) + variant AS INT) AS relationship_tenure_months,
  'summit_demo_core_banking' AS source_system,
  'demo_synthetic' AS feed_mode,
  TRUE AS synthetic_demo,
  refresh_ts AS refreshed_at
FROM demo_first_party_base
WHERE bucket BETWEEN 0 AND 37;
