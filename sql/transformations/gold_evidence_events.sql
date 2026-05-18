-- =============================================================================
-- gold_evidence_events.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.evidence_events` as a UNION of all
--            LIVE (non-BLOCKED) signal sources, per CLIP.
--
-- Grain:     One row per (clip, signal_type, timestamp). evidence_id is a
--            deterministic 12-hex suffix of sha2 over those three.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild is the
--            default refresh posture; clusters on clip per DDL.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.4.
--
-- BLOCKED signal types (data-contract §9):
--   - 'permit'  : NEVER emitted until the Cotality Building Permits Delta
--                 Share lands.
--   - 'listing' : NEVER emitted until the Cotality MLS/Listings Delta Share
--                 lands.
--
-- Confidence values (data-contract §3.4):
--   - AVM-backed (equity): from silver confidence_score_mktg.
--     Share emits 0..1 OR 0..100 depending on batch; we detect scale by
--     dividing by 100 when value > 1, else by 1 (clip to [0, 1] at the end).
--   - rate_spread, market_trend       : 0.92 (deterministic).
--   - Owner-Link derived signals      : 0.85 (deterministic).
--   - Recent-event signals + FC stage : 0.89 (deterministic).
--
-- Signal priority (signal_rank), smaller = higher priority:
--   1  rate_spread
--   2  equity
--   3  market_trend
--   4  loan_type_fit
--   5  competitor_lien
--   6  multi_property
--   7  absentee_mailing
--   8  corporate_owner
--   9  recent_refi
--  10  recent_payoff
--  11  recent_sale
--  12  foreclosure_stage
--
-- display_text is deterministic per signal_type and interpolates only
-- numeric values, never PII. ISO-8601 timestamp strings come from upstream
-- dates cast to STRING (Pydantic declares `timestamp: str`).
--
-- Real UC path in source_table (EvidenceDrawer renders this verbatim):
--   mip.silver.lien_current
--   mip.silver.property_master
--   mip.silver.mortgage_events
--   mip.silver.owner_transfer_events
--   mip.silver.market_rates_weekly
--   mip.gold.property_owner_bridge
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.evidence_events AS
WITH market AS (
  SELECT
    rate_fraction AS market_rate_fraction,
    observation_week AS market_observation_week,
    vintage_ts AS market_vintage_ts
  FROM mip.silver.market_rates_weekly
  WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE
  LIMIT 1
),
borrower_spine AS (
  -- Match gold.borrower_360's silver lien spine without reading gold output.
  -- This keeps evidence_events buildable before borrower_360 in the refresh DAG.
  SELECT DISTINCT clip
  FROM mip.silver.lien_current
  WHERE situs_state IS NOT NULL
    AND clip IS NOT NULL
),
rate_spread_inputs AS (
  SELECT
    lc.clip,
    lc.ingest_ts,
    lc.situs_state,
    CASE
      WHEN lc.first_pos_rate IS NULL THEN NULL
      WHEN lc.first_pos_rate < 0.01 THEN NULL
      WHEN lc.first_pos_rate > 0.15 THEN 0.15
      ELSE lc.first_pos_rate
    END AS first_pos_rate
  FROM mip.silver.lien_current AS lc
),
-- 1. rate_spread (per CLIP, requires a borrower with a 1st-pos rate).
rate_spread_rows AS (
  SELECT
    lc.clip,
    'Voluntary Lien + Market Rates'                  AS source_product,
    'mip.silver.lien_current'                        AS source_table,
    'rate_spread'                                    AS signal_type,
    CONCAT(
      CASE WHEN mip.gold.fn_rate_spread(lc.first_pos_rate, m.market_rate_fraction) >= 0
           THEN '+' ELSE '' END,
      CAST(mip.gold.fn_rate_spread(lc.first_pos_rate, m.market_rate_fraction) AS STRING),
      ' bps'
    )                                                AS signal_value,
    CONCAT('Current lien rate is ',
           CAST(mip.gold.fn_rate_spread(lc.first_pos_rate, m.market_rate_fraction) AS STRING),
           ' bps vs. par.')                          AS display_text,
    0.92                                             AS confidence,
    CAST(lc.ingest_ts AS STRING)                     AS `timestamp`,
    1                                                AS signal_rank
  FROM rate_spread_inputs AS lc
  CROSS JOIN market AS m
  WHERE lc.first_pos_rate IS NOT NULL
    AND lc.situs_state IS NOT NULL
),
-- 2. equity (per CLIP with meaningful equity). Confidence comes from AVM
--    confidence where available; clipped to [0, 1].
equity_rows AS (
  SELECT
    lc.clip,
    'AVM'                                            AS source_product,
    'mip.silver.lien_current'                   AS source_table,
    'equity'                                         AS signal_type,
    CONCAT('$',
           CAST(ROUND(
             GREATEST(0, COALESCE(lc.avm_value, 0) - COALESCE(lc.total_open_lien_balance, 0)) / 1000.0
           ) AS STRING), 'K')                        AS signal_value,
    'AVM-backed equity estimate.'                    AS display_text,
    LEAST(1.0, GREATEST(0.0,
      CASE
        WHEN lc.avm_confidence IS NULL THEN 0.85
        WHEN lc.avm_confidence > 1     THEN lc.avm_confidence / 100.0
        ELSE lc.avm_confidence
      END
    ))                                               AS confidence,
    CAST(COALESCE(lc.avm_as_of_date, DATE(lc.ingest_ts)) AS STRING) AS `timestamp`,
    2                                                AS signal_rank
  FROM mip.silver.lien_current AS lc
  WHERE lc.avm_value IS NOT NULL
    AND lc.avm_value > 0
    AND (COALESCE(lc.avm_value, 0) - COALESCE(lc.total_open_lien_balance, 0)) > 0
    AND lc.situs_state IS NOT NULL
),
-- 3. market_trend: one evidence row per CLIP describing latest par rate.
market_trend_rows AS (
  SELECT
    lc.clip,
    'Market Rates'                                   AS source_product,
    'mip.silver.market_rates_weekly'            AS source_table,
    'market_trend'                                   AS signal_type,
    CONCAT(CAST(ROUND(m.market_rate_fraction * 100, 2) AS STRING), '% par') AS signal_value,
    CONCAT(
      'Latest MORTGAGE30US market rate (FRED observation week ',
      CAST(m.market_observation_week AS STRING),
      ').'
    )                                                AS display_text,
    0.92                                             AS confidence,
    CAST(COALESCE(m.market_observation_week, DATE(m.market_vintage_ts)) AS STRING) AS `timestamp`,
    3                                                AS signal_rank
  FROM mip.silver.lien_current AS lc
  CROSS JOIN market AS m
  WHERE lc.situs_state IS NOT NULL
),
-- 4. loan_type_fit: compliance-visible explanation for the fit sub-score's
--    symmetric CONV/FHA/VA owner-occupant boost. This row is intentionally
--    excluded from the evidence sub-score in borrower_360/lead_scores so adding
--    rationale does not retune opportunity scores.
loan_type_fit_rows AS (
  SELECT
    lc.clip,
    'Voluntary Lien'                                 AS source_product,
    'mip.silver.lien_current'                        AS source_table,
    'loan_type_fit'                                  AS signal_type,
    CONCAT(COALESCE(lc.first_pos_loan_type, 'unknown'), ' owner-occupied fit') AS signal_value,
    'Owner-occupied CONV/FHA/VA loan type receives symmetric product-fit treatment.' AS display_text,
    0.89                                             AS confidence,
    CAST(lc.ingest_ts AS STRING)                     AS `timestamp`,
    4                                                AS signal_rank
  FROM mip.silver.lien_current AS lc
  WHERE COALESCE(lc.owner_occupancy_code, '') = 'O'
    AND lc.first_pos_loan_type IN ('CONV','FHA','VA')
    AND lc.situs_state IS NOT NULL
),
-- 5. competitor_lien: current servicer known and not a tenant-lender alias.
competitor_lien_rows AS (
  SELECT
    lc.clip,
    'Voluntary Lien'                                 AS source_product,
    'mip.silver.lien_current'                   AS source_table,
    'competitor_lien'                                AS signal_type,
    'competitor servicer'                            AS signal_value,
    'Current servicer is not the lender of record.'  AS display_text,
    0.89                                             AS confidence,
    CAST(lc.ingest_ts AS STRING)                     AS `timestamp`,
    5                                                AS signal_rank
  FROM mip.silver.lien_current AS lc
  LEFT JOIN mip.ref.lender_dictionary AS lr
    ON UPPER(TRIM(lr.raw_key)) = UPPER(TRIM(lc.first_pos_lender_current))
  WHERE lc.first_pos_lender_current IS NOT NULL
    AND NOT COALESCE(NOT lr.is_competitor, FALSE)
    AND lc.situs_state IS NOT NULL
),
-- 5. multi_property: Owner-Link rollup says >= 2 related properties.
multi_property_rows AS (
  SELECT
    pm.clip,
    'Owner Link'                                     AS source_product,
    'mip.gold.property_owner_bridge'            AS source_table,
    'multi_property'                                 AS signal_type,
    CONCAT(CAST(pob.related_property_count AS STRING), ' properties') AS signal_value,
    'Owner Link identifies related properties under the same entity.' AS display_text,
    0.85                                             AS confidence,
    CAST(pob.refreshed_at AS STRING)                 AS `timestamp`,
    6                                                AS signal_rank
  FROM mip.silver.property_master AS pm
  JOIN mip.gold.property_owner_bridge AS pob
    ON pob.owner_link_id = pm.owner_link_id
  WHERE pob.related_property_count >= 2
    AND pm.situs_state IS NOT NULL
),
-- 6. absentee_mailing: mailing state != situs state.
absentee_rows AS (
  SELECT
    pm.clip,
    'Property'                                       AS source_product,
    'mip.silver.property_master'                AS source_table,
    'absentee_mailing'                               AS signal_type,
    CONCAT('mails to ', COALESCE(pm.mailing_state, 'unknown'))        AS signal_value,
    'Owner mailing address is out of state from situs.'               AS display_text,
    0.85                                             AS confidence,
    CAST(pm.ingest_ts AS STRING)                     AS `timestamp`,
    7                                                AS signal_rank
  FROM mip.silver.property_master AS pm
  WHERE pm.is_absentee = TRUE
    AND pm.situs_state IS NOT NULL
),
-- 7. corporate_owner: ownership flagged corporate.
corporate_owner_rows AS (
  SELECT
    pm.clip,
    'Property'                                       AS source_product,
    'mip.silver.property_master'                AS source_table,
    'corporate_owner'                                AS signal_type,
    'corporate ownership'                            AS signal_value,
    'Owner of record is a corporate entity.'         AS display_text,
    0.85                                             AS confidence,
    CAST(pm.ingest_ts AS STRING)                     AS `timestamp`,
    8                                                AS signal_rank
  FROM mip.silver.property_master AS pm
  WHERE pm.owner_is_corporate = TRUE
    AND pm.situs_state IS NOT NULL
),
-- 8. recent_refi: last refi event per CLIP in the last 365 days.
recent_refi_rows AS (
  SELECT
    me.clip,
    'Mortgage Domain'                                AS source_product,
    'mip.silver.mortgage_events'                AS source_table,
    'recent_refi'                                    AS signal_type,
    CAST(me.event_date AS STRING)                    AS signal_value,
    'Refi event recorded within the last 12 months.' AS display_text,
    0.89                                             AS confidence,
    CAST(me.event_date AS STRING)                    AS `timestamp`,
    9                                                AS signal_rank
  FROM (
    SELECT
      clip, event_date,
      ROW_NUMBER() OVER (PARTITION BY clip ORDER BY event_date DESC) AS rn
    FROM mip.silver.mortgage_events
    WHERE is_refinance = TRUE
      AND event_date IS NOT NULL
      AND event_date >= DATE_SUB(CURRENT_DATE(), 365)
      AND situs_state IS NOT NULL
  ) me
  WHERE me.rn = 1
),
-- 9. recent_payoff: last payoff / release event per CLIP in last 365 days.
recent_payoff_rows AS (
  SELECT
    me.clip,
    'Mortgage Domain'                                AS source_product,
    'mip.silver.mortgage_events'                AS source_table,
    'recent_payoff'                                  AS signal_type,
    CAST(me.release_date AS STRING)                  AS signal_value,
    'Mortgage release recorded within the last 12 months.' AS display_text,
    0.89                                             AS confidence,
    CAST(me.release_date AS STRING)                  AS `timestamp`,
    10                                               AS signal_rank
  FROM (
    SELECT
      clip, release_date,
      ROW_NUMBER() OVER (PARTITION BY clip ORDER BY release_date DESC) AS rn
    FROM mip.silver.mortgage_events
    WHERE release_date IS NOT NULL
      AND release_date >= DATE_SUB(CURRENT_DATE(), 365)
      AND situs_state IS NOT NULL
  ) me
  WHERE me.rn = 1
),
-- 10. recent_sale: last transfer per CLIP in last 365 days.
recent_sale_rows AS (
  SELECT
    ot.clip,
    'Owner Transfer'                                 AS source_product,
    'mip.silver.owner_transfer_events'          AS source_table,
    'recent_sale'                                    AS signal_type,
    CAST(ot.sale_date AS STRING)                     AS signal_value,
    'Transfer of ownership recorded within the last 12 months.' AS display_text,
    0.89                                             AS confidence,
    CAST(ot.sale_date AS STRING)                     AS `timestamp`,
    11                                               AS signal_rank
  FROM (
    SELECT
      clip, sale_date,
      ROW_NUMBER() OVER (PARTITION BY clip ORDER BY sale_date DESC) AS rn
    FROM mip.silver.owner_transfer_events
    WHERE sale_date IS NOT NULL
      AND sale_date >= DATE_SUB(CURRENT_DATE(), 365)
      AND situs_state IS NOT NULL
  ) ot
  WHERE ot.rn = 1
),
-- 11. foreclosure_stage: current distress snapshot from property_master.
foreclosure_stage_rows AS (
  SELECT
    pm.clip,
    'Property'                                       AS source_product,
    'mip.silver.property_master'                AS source_table,
    'foreclosure_stage'                              AS signal_type,
    COALESCE(pm.foreclosure_stage_code, 'unknown')   AS signal_value,
    'Foreclosure stage snapshot from property master.' AS display_text,
    0.89                                             AS confidence,
    CAST(COALESCE(pm.last_foreclosure_date, DATE(pm.ingest_ts)) AS STRING) AS `timestamp`,
    12                                               AS signal_rank
  FROM mip.silver.property_master AS pm
  WHERE pm.foreclosure_stage_code IS NOT NULL
    AND pm.situs_state IS NOT NULL
),
unioned AS (
  SELECT * FROM rate_spread_rows      UNION ALL
  SELECT * FROM equity_rows           UNION ALL
  SELECT * FROM market_trend_rows     UNION ALL
  SELECT * FROM loan_type_fit_rows    UNION ALL
  SELECT * FROM competitor_lien_rows  UNION ALL
  SELECT * FROM multi_property_rows   UNION ALL
  SELECT * FROM absentee_rows         UNION ALL
  SELECT * FROM corporate_owner_rows  UNION ALL
  SELECT * FROM recent_refi_rows      UNION ALL
  SELECT * FROM recent_payoff_rows    UNION ALL
  SELECT * FROM recent_sale_rows      UNION ALL
  SELECT * FROM foreclosure_stage_rows
)
SELECT
  u.clip,
  CONCAT('ev-', SUBSTR(sha2(CONCAT(u.clip, '|', u.signal_type, '|', u.`timestamp`), 256), 1, 12)) AS evidence_id,
  u.source_product,
  u.source_table,
  u.signal_type,
  u.signal_value,
  u.display_text,
  u.confidence,
  u.`timestamp`,
  u.signal_rank
FROM unioned AS u
JOIN borrower_spine AS bs
  ON bs.clip = u.clip;
