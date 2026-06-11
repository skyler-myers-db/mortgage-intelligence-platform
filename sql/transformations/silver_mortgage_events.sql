-- =============================================================================
-- silver_mortgage_events.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE that populates `mip.silver.mortgage_events`
--            from `cotality_mortgage_data.corelogic.entrada_eval_mortgage_
--            domain_v1`. Coverage follows whatever states are present in the
--            source share via the `deed_situs_state_static` column.
--
-- Grain:     One row per mortgage event.
-- PK (MERGE key): mortgage_txn_id (= mortgage_composite_transaction_id).
-- Geography contract: keep rows with a non-null deed_situs_state_static;
-- downstream geography rollups discover state/county/ZIP coverage from data.
-- Slice:     module0-real-data-slice2.
-- Data contract: docs/data-contract-module0.md §2.3.
--
-- PII posture:
--   - No borrower names projected. `borrower_1_identifier` is an opaque
--     Cotality id; safe to retain. Raw `borrower_1_full_name` is NEVER read.
--
-- Indicator coercion (share probe, Apr 2026):
--   - `refinance_loan_indicator`, `equity_loan_indicator`,
--     `reverse_mortgage_indicator` are BIGINT 1/0, NOT STRING 'Y'/'N'.
--     CAST(ind AS BOOLEAN) maps 1 -> true, 0 -> false, NULL -> NULL.
--
-- Date-type coercion:
--   - `mortgage_derived_date` is BIGINT yyyyMMdd -> TO_DATE(...).
--   - `mortgage_release_date` same treatment; NULLIF(x, 0) guards sentinel.
--
-- Idempotency: MERGE on mortgage_txn_id.
-- =============================================================================

MERGE INTO mip.silver.mortgage_events AS t
USING (
  SELECT
    mortgage_composite_transaction_id                                AS mortgage_txn_id,
    clip,
    deed_situs_state_static                                          AS situs_state,
    TRY_TO_DATE(CAST(NULLIF(mortgage_derived_date, 0) AS STRING), 'yyyyMMdd')
      AS event_date,
    YEAR(TRY_TO_DATE(CAST(NULLIF(mortgage_derived_date, 0) AS STRING), 'yyyyMMdd'))
      AS event_year,
    CAST(mortgage_amount AS BIGINT)                                  AS mortgage_amount,
    CAST(mortgage_interest_rate_cascade AS DOUBLE)                   AS rate_cascade,
    mortgage_purpose_code                                            AS purpose_code,
    mortgage_loan_type_code                                          AS loan_type_code,
    CAST(COALESCE(refinance_loan_indicator,   0) AS BOOLEAN)         AS is_refinance,
    CAST(COALESCE(equity_loan_indicator,      0) AS BOOLEAN)         AS is_equity_loan,
    CAST(COALESCE(reverse_mortgage_indicator, 0) AS BOOLEAN)         AS is_reverse_mortgage,
    lender_company_name                                              AS lender_name,
    TRY_TO_DATE(CAST(NULLIF(mortgage_release_date, 0) AS STRING), 'yyyyMMdd')
      AS release_date,
    mortgage_status_indicator                                        AS status_indicator,
    borrower_1_identifier                                            AS borrower_identifier,
    CURRENT_TIMESTAMP()                                              AS ingest_ts,
    CAST(:batch_id AS STRING)                                        AS _meta_batch_id
  FROM cotality_mortgage_data.corelogic.entrada_eval_mortgage_domain_v1
  WHERE deed_situs_state_static IS NOT NULL
    AND mortgage_composite_transaction_id IS NOT NULL
    AND clip IS NOT NULL
  -- dedup guard (audit P2-10): share refreshes occasionally land duplicate
  -- mortgage_txn_id; keep the newest by event_date (the event's own recording
  -- date), then release_date / mortgage_amount as deterministic tiebreaks.
  -- Without this a duplicate key makes the MERGE fail (multiple source rows
  -- match one target row). ingest_ts is a single run-wide CURRENT_TIMESTAMP(),
  -- so it cannot order rows within a refresh.
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY mortgage_txn_id
    ORDER BY event_date DESC, release_date DESC, mortgage_amount DESC
  ) = 1
) AS s
  ON t.mortgage_txn_id = s.mortgage_txn_id
WHEN MATCHED THEN UPDATE SET
  clip                = s.clip,
  situs_state         = s.situs_state,
  event_date          = s.event_date,
  event_year          = s.event_year,
  mortgage_amount     = s.mortgage_amount,
  rate_cascade        = s.rate_cascade,
  purpose_code        = s.purpose_code,
  loan_type_code      = s.loan_type_code,
  is_refinance        = s.is_refinance,
  is_equity_loan      = s.is_equity_loan,
  is_reverse_mortgage = s.is_reverse_mortgage,
  lender_name         = s.lender_name,
  release_date        = s.release_date,
  status_indicator    = s.status_indicator,
  borrower_identifier = s.borrower_identifier,
  ingest_ts           = s.ingest_ts,
  _meta_batch_id      = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  mortgage_txn_id, clip, situs_state, event_date, event_year,
  mortgage_amount, rate_cascade, purpose_code, loan_type_code,
  is_refinance, is_equity_loan, is_reverse_mortgage,
  lender_name, release_date, status_indicator, borrower_identifier,
  ingest_ts, _meta_batch_id
) VALUES (
  s.mortgage_txn_id, s.clip, s.situs_state, s.event_date, s.event_year,
  s.mortgage_amount, s.rate_cascade, s.purpose_code, s.loan_type_code,
  s.is_refinance, s.is_equity_loan, s.is_reverse_mortgage,
  s.lender_name, s.release_date, s.status_indicator, s.borrower_identifier,
  s.ingest_ts, s._meta_batch_id
);
