-- =============================================================================
-- silver_owner_transfer_events.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE that populates
--            `mip_demo.silver.owner_transfer_events` from
--            `cotality_mortgage_data.corelogic.entrada_eval_owner_transfer_
--            domain_v1`, filtered to the 6-state footprint via
--            `deed_situs_state_static`.
--
-- Grain:     One row per sale/transfer event (~24M in share).
-- PK (MERGE key): transfer_txn_id (composite transaction ID from share).
-- Geography filter: WHERE deed_situs_state_static IN ('IL','CA','FL','TX','WA','CO').
-- Slice:     module0-real-data-slice2.
-- Data contract: docs/data-contract-module0.md §2.4.
--
-- PII posture:
--   - `buyer_1_full_name` / `seller_*_full_name` are NEVER projected. Gold
--     investor-segment counts + cash-purchase flags do not need names.
--   - `buyer_1_identifier` is an opaque Cotality id; safe to retain.
--   - `buyer_mailing_state` is retained; street is never projected.
--
-- PK column name:
--   The share uses a composite transaction id; we alias to
--   `transfer_txn_id`. If the share exposes a more specific column name
--   (e.g. `sale_composite_transaction_id`) on a future refresh, the
--   aliasing here is the single place to update. Until then we read
--   `owner_transfer_composite_transaction_id` as documented in the data-contract §2.4.
--
-- Indicator coercion:
--   All *_indicator columns are BIGINT 1/0 (share probe) -> CAST AS BOOLEAN.
--
-- Date-type coercion:
--   `sale_derived_date` is BIGINT yyyyMMdd -> TO_DATE(...).
--
-- Idempotency: MERGE on transfer_txn_id.
-- =============================================================================

MERGE INTO mip_demo.silver.owner_transfer_events AS t
USING (
  SELECT
    owner_transfer_composite_transaction_id                                         AS transfer_txn_id,
    clip,
    deed_situs_state_static                                          AS situs_state,
    TRY_TO_DATE(CAST(NULLIF(sale_derived_date, 0) AS STRING), 'yyyyMMdd')
      AS sale_date,
    CAST(sale_amount AS BIGINT)                                      AS sale_amount,
    sale_type_code,
    CAST(COALESCE(cash_purchase_indicator,      0) AS BOOLEAN)       AS is_cash_purchase,
    CAST(COALESCE(investor_purchase_indicator,  0) AS BOOLEAN)       AS is_investor_purchase,
    CAST(COALESCE(foreclosure_reo_indicator,    0) AS BOOLEAN)       AS is_reo,
    CAST(COALESCE(short_sale_indicator,         0) AS BOOLEAN)       AS is_short_sale,
    CAST(COALESCE(new_construction_indicator,   0) AS BOOLEAN)       AS is_new_construction,
    CAST(COALESCE(resale_indicator,             0) AS BOOLEAN)       AS is_resale,
    CAST(COALESCE(interfamily_related_indicator, 0) AS BOOLEAN)      AS is_interfamily,
    CAST(COALESCE(buyer_1_corporate_indicator,  0) AS BOOLEAN)       AS buyer_is_corporate,
    buyer_1_identifier                                               AS buyer_identifier,
    buyer_mailing_state,
    CURRENT_TIMESTAMP()                                              AS ingest_ts,
    CAST(:batch_id AS STRING)                                        AS _meta_batch_id
  FROM cotality_mortgage_data.corelogic.entrada_eval_owner_transfer_domain_v1
  WHERE deed_situs_state_static IN ('IL','CA','FL','TX','WA','CO')
    AND owner_transfer_composite_transaction_id IS NOT NULL
    AND clip IS NOT NULL
) AS s
  ON t.transfer_txn_id = s.transfer_txn_id
WHEN MATCHED THEN UPDATE SET
  clip                 = s.clip,
  situs_state          = s.situs_state,
  sale_date            = s.sale_date,
  sale_amount          = s.sale_amount,
  sale_type_code       = s.sale_type_code,
  is_cash_purchase     = s.is_cash_purchase,
  is_investor_purchase = s.is_investor_purchase,
  is_reo               = s.is_reo,
  is_short_sale        = s.is_short_sale,
  is_new_construction  = s.is_new_construction,
  is_resale            = s.is_resale,
  is_interfamily       = s.is_interfamily,
  buyer_is_corporate   = s.buyer_is_corporate,
  buyer_identifier     = s.buyer_identifier,
  buyer_mailing_state  = s.buyer_mailing_state,
  ingest_ts            = s.ingest_ts,
  _meta_batch_id       = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  transfer_txn_id, clip, situs_state, sale_date, sale_amount, sale_type_code,
  is_cash_purchase, is_investor_purchase, is_reo, is_short_sale,
  is_new_construction, is_resale, is_interfamily,
  buyer_is_corporate, buyer_identifier, buyer_mailing_state,
  ingest_ts, _meta_batch_id
) VALUES (
  s.transfer_txn_id, s.clip, s.situs_state, s.sale_date, s.sale_amount, s.sale_type_code,
  s.is_cash_purchase, s.is_investor_purchase, s.is_reo, s.is_short_sale,
  s.is_new_construction, s.is_resale, s.is_interfamily,
  s.buyer_is_corporate, s.buyer_identifier, s.buyer_mailing_state,
  s.ingest_ts, s._meta_batch_id
);
