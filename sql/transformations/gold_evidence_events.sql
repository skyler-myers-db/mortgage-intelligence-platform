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
--   - 'permit'  : NEVER emitted until a true Cotality Building Permits
--                 source table is present. HELOC propensity is a model
--                 signal and must not be described as a filed permit.
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
--   0  listing
--   1  rate_spread
--   2  equity
--   3  market_trend
--   4  heloc_propensity
--   5  refi_propensity
--   6  loan_type_fit
--   7  competitor_lien
--   8  multi_property
--   9  absentee_mailing
--  10  corporate_owner
--  11  recent_refi
--  12  recent_payoff
--  13  recent_sale
--  14  foreclosure_stage
--  15  product_type          (explainability-only; excluded from evidence sub-score)
--  16  origination_channel   (explainability-only; excluded from evidence sub-score)
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
--   mip.silver.listing_activity
--   mip.silver.heloc_propensity
--   mip.silver.refi_propensity
--   mip.gold.property_owner_bridge
--   mip.first_party.loan_applications
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_evidence_events.sql; the
-- column list order matches the final SELECT projection 1:1.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.evidence_events
CLUSTER BY (clip)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH market AS (
  SELECT
    rate_fraction AS market_rate_fraction,
    observation_week AS market_observation_week,
    vintage_ts AS market_vintage_ts
  FROM mip.silver.market_rates_weekly
  WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE
  LIMIT 1
),
refresh_anchor AS (
  SELECT refresh_at
  FROM mip.ref.refresh_run_state
  ORDER BY captured_at DESC
  LIMIT 1
),
rules AS (
  -- Governed conforming loan limit for the product_type explainability row.
  -- Same fallback default as gold_borrower_360's rules CTE.
  SELECT
    CAST(COALESCE(MAX(CASE WHEN key = 'mip_conforming_loan_limit_usd' THEN value END), 806500.0) AS BIGINT) AS conforming_loan_limit_usd
  FROM mip.ref.offer_rules_config
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
equity_inputs AS (
  SELECT
    lc.clip,
    lc.avm_value,
    lc.avm_confidence,
    lc.avm_as_of_date,
    lc.ingest_ts,
    lc.situs_state,
    CAST(CASE
      WHEN lc.first_pos_amount IS NOT NULL AND lc.first_pos_amount > 0 THEN
        mip.gold.fn_estimated_upb(
          lc.first_pos_amount,
          CASE
            WHEN lc.first_pos_rate IS NULL THEN NULL
            WHEN lc.first_pos_rate < 0.01 THEN NULL
            WHEN lc.first_pos_rate > 0.15 THEN 0.15
            ELSE lc.first_pos_rate
          END,
          CASE
            WHEN lc.first_pos_date IS NULL THEN NULL
            ELSE CAST(FLOOR(months_between(DATE(ra.refresh_at), lc.first_pos_date)) AS INT)
          END
        )
        + COALESCE(lc.second_pos_amount, 0)
      ELSE COALESCE(lc.total_open_lien_balance, 0)
    END AS BIGINT) AS estimated_current_lien_balance
  FROM mip.silver.lien_current AS lc
  CROSS JOIN refresh_anchor AS ra
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
             GREATEST(0, COALESCE(lc.avm_value, 0) - COALESCE(lc.estimated_current_lien_balance, 0)) / 1000.0
           ) AS STRING), 'K')                        AS signal_value,
    'AVM-backed equity estimate using amortized UPB.' AS display_text,
    LEAST(1.0, GREATEST(0.0,
      CASE
        WHEN lc.avm_confidence IS NULL THEN 0.85
        WHEN lc.avm_confidence > 1     THEN lc.avm_confidence / 100.0
        ELSE lc.avm_confidence
      END
    ))                                               AS confidence,
    CAST(COALESCE(lc.avm_as_of_date, DATE(lc.ingest_ts)) AS STRING) AS `timestamp`,
    2                                                AS signal_rank
  FROM equity_inputs AS lc
  WHERE lc.avm_value IS NOT NULL
    AND lc.avm_value > 0
    AND (COALESCE(lc.avm_value, 0) - COALESCE(lc.estimated_current_lien_balance, 0)) > 0
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
-- 4. listing: current active/under-contract MLS listing per CLIP.
listing_rows AS (
  SELECT
    la.clip,
    'MLS Listings'                                   AS source_product,
    'mip.silver.listing_activity'                    AS source_table,
    'listing'                                        AS signal_type,
    COALESCE(la.listing_status_description, la.listing_status_category, 'active listing') AS signal_value,
    CONCAT(
      'Current MLS status is ',
      COALESCE(la.listing_status_description, la.listing_status_category, 'active'),
      CASE
        WHEN la.plausible_listing_price IS NOT NULL
          THEN CONCAT(' at $', CAST(la.plausible_listing_price AS STRING), ' list price')
        ELSE ''
      END,
      CASE
        WHEN la.days_on_market IS NOT NULL THEN CONCAT(' after ', CAST(la.days_on_market AS STRING), ' days on market')
        ELSE ''
      END,
      '.'
    )                                                AS display_text,
    0.94                                             AS confidence,
    CAST(COALESCE(la.listing_status_date, la.listing_date, DATE(la.source_updated_at), DATE(la.ingest_ts)) AS STRING) AS `timestamp`,
    0                                                AS signal_rank
  FROM (
    SELECT
      src.*,
      CASE
        WHEN src.listing_price IS NULL THEN NULL
        WHEN src.listing_price < 25000 THEN NULL
        WHEN lc.avm_value IS NOT NULL AND lc.avm_value > 0
          AND (src.listing_price < lc.avm_value * 0.15 OR src.listing_price > lc.avm_value * 5.0) THEN NULL
        ELSE src.listing_price
      END AS plausible_listing_price,
      ROW_NUMBER() OVER (
        PARTITION BY src.clip
        ORDER BY
          COALESCE(src.listing_status_date, src.listing_date, DATE(src.source_updated_at), DATE(src.ingest_ts)) DESC,
          src.listing_record_id
      ) AS rn
    FROM mip.silver.listing_activity AS src
    LEFT JOIN mip.silver.lien_current AS lc
      ON lc.clip = src.clip
    WHERE src.is_active_listing = TRUE
      AND src.clip IS NOT NULL
  ) la
  WHERE la.rn = 1
),
-- 5. heloc_propensity: high Cotality HELOC propensity model score.
heloc_propensity_rows AS (
  SELECT
    hp.clip,
    'HELOC Propensity'                               AS source_product,
    'mip.silver.heloc_propensity'                    AS source_table,
    'heloc_propensity'                               AS signal_type,
    CONCAT(CAST(hp.heloc_propensity_score AS STRING), '/999') AS signal_value,
    CONCAT(
      'Cotality HELOC propensity score is ',
      CAST(hp.heloc_propensity_score AS STRING),
      ' out of 999.'
    )                                                AS display_text,
    LEAST(0.95, GREATEST(0.65, hp.heloc_propensity_score / 1000.0)) AS confidence,
    CAST(COALESCE(hp.heloc_propensity_run_date, DATE(hp.source_updated_at), DATE(hp.ingest_ts)) AS STRING) AS `timestamp`,
    4                                                AS signal_rank
  FROM mip.silver.heloc_propensity AS hp
  WHERE hp.clip IS NOT NULL
    AND hp.heloc_propensity_score >= 700
),
-- 6. refi_propensity: high Cotality refinance propensity model score.
refi_propensity_rows AS (
  SELECT
    rp.clip,
    'Refi Propensity'                                AS source_product,
    'mip.silver.refi_propensity'                     AS source_table,
    'refi_propensity'                                AS signal_type,
    CONCAT(CAST(rp.refi_propensity_score AS STRING), '/999') AS signal_value,
    CONCAT(
      'Cotality refinance propensity score is ',
      CAST(rp.refi_propensity_score AS STRING),
      ' out of 999.'
    )                                                AS display_text,
    LEAST(0.95, GREATEST(0.65, rp.refi_propensity_score / 1000.0)) AS confidence,
    CAST(COALESCE(rp.refi_propensity_run_date, DATE(rp.source_updated_at), DATE(rp.ingest_ts)) AS STRING) AS `timestamp`,
    5                                                AS signal_rank
  FROM mip.silver.refi_propensity AS rp
  WHERE rp.clip IS NOT NULL
    AND rp.refi_propensity_score >= 700
),
-- 7. loan_type_fit: compliance-visible explanation for the fit sub-score's
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
    6                                                AS signal_rank
  FROM mip.silver.lien_current AS lc
  WHERE COALESCE(lc.owner_occupancy_code, '') = 'O'
    AND lc.first_pos_loan_type IN ('CONV','FHA','VA')
    AND lc.situs_state IS NOT NULL
),
-- 8. competitor_lien: current servicer known and not a tenant-lender alias.
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
    7                                                AS signal_rank
  FROM mip.silver.lien_current AS lc
  LEFT JOIN mip.ref.lender_dictionary AS lr
    ON UPPER(TRIM(lr.raw_key)) = UPPER(TRIM(lc.first_pos_lender_current))
  WHERE lc.first_pos_lender_current IS NOT NULL
    AND NOT COALESCE(NOT lr.is_competitor, FALSE)
    AND lc.situs_state IS NOT NULL
),
-- 9. multi_property: Owner-Link rollup says >= 2 related properties.
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
    8                                                AS signal_rank
  FROM mip.silver.property_master AS pm
  JOIN mip.gold.property_owner_bridge AS pob
    ON pob.owner_link_id = pm.owner_link_id
  WHERE pob.related_property_count >= 2
    AND pm.situs_state IS NOT NULL
),
-- 10. absentee_mailing: mailing state != situs state.
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
    9                                                AS signal_rank
  FROM mip.silver.property_master AS pm
  WHERE pm.is_absentee = TRUE
    AND pm.situs_state IS NOT NULL
),
-- 11. corporate_owner: ownership flagged corporate.
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
    10                                               AS signal_rank
  FROM mip.silver.property_master AS pm
  WHERE pm.owner_is_corporate = TRUE
    AND pm.situs_state IS NOT NULL
),
-- 12. recent_refi: last refi event per CLIP in the last 365 days.
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
    11                                               AS signal_rank
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
-- 13. recent_payoff: last payoff / release event per CLIP in last 365 days.
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
    12                                               AS signal_rank
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
-- 14. recent_sale: last transfer per CLIP in last 365 days.
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
    13                                               AS signal_rank
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
-- 15. foreclosure_stage: current distress snapshot from property_master.
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
    14                                               AS signal_rank
  FROM mip.silver.property_master AS pm
  WHERE pm.foreclosure_stage_code IS NOT NULL
    AND pm.situs_state IS NOT NULL
),
-- 16. product_type: explainability row for the derived loan product-type
--     dimension (conventional / jumbo / fha / va / other). Excluded from the
--     evidence sub-score (like loan_type_fit) so adding rationale does not
--     retune opportunity scores.
product_type_rows AS (
  SELECT
    lc.clip,
    'Voluntary Lien'                                 AS source_product,
    'mip.silver.lien_current'                        AS source_table,
    'product_type'                                   AS signal_type,
    CONCAT(
      mip.gold.fn_loan_product_type(lc.first_pos_loan_type, lc.first_pos_amount, r.conforming_loan_limit_usd),
      ' (source code ', UPPER(TRIM(lc.first_pos_loan_type)), ')'
    )                                                AS signal_value,
    'First-lien product type derived from the Cotality loan type code and original amount vs. the governed conforming loan limit.' AS display_text,
    0.89                                             AS confidence,
    CAST(lc.ingest_ts AS STRING)                     AS `timestamp`,
    15                                               AS signal_rank
  FROM mip.silver.lien_current AS lc
  CROSS JOIN rules AS r
  WHERE lc.first_pos_loan_type IS NOT NULL
    AND LENGTH(TRIM(lc.first_pos_loan_type)) > 0
    AND lc.situs_state IS NOT NULL
),
-- 17. origination_channel: LOS channel of the most recent FUNDED first-party
--     application, cited from the governed mip.first_party feed. Only
--     borrowers with a real funded application emit this row -- "unknown"
--     never fabricates evidence. Explainability-only (excluded from the
--     evidence sub-score). borrower_id derivation matches gold_borrower_360 /
--     demo_first_party_feeds exactly.
origination_channel_rows AS (
  SELECT
    lc.clip,
    'First-Party LOS'                                AS source_product,
    'mip.first_party.loan_applications'              AS source_table,
    'origination_channel'                            AS signal_type,
    fa.latest_funded_channel                         AS signal_value,
    'Origination channel recorded on the most recent funded loan application in the first-party LOS feed.' AS display_text,
    0.89                                             AS confidence,
    CAST(fa.latest_funded_at AS STRING)              AS `timestamp`,
    16                                               AS signal_rank
  FROM mip.silver.lien_current AS lc
  JOIN (
    SELECT
      borrower_id,
      -- Blank/whitespace-only channels count as NULL (unknown) -- matches
      -- gold_borrower_360 so evidence never cites an empty-string channel.
      MAX_BY(LOWER(TRIM(application_channel)), application_at)
        FILTER (WHERE application_status = 'funded'
                AND NULLIF(TRIM(application_channel), '') IS NOT NULL) AS latest_funded_channel,
      MAX(application_at)
        FILTER (WHERE application_status = 'funded'
                AND NULLIF(TRIM(application_channel), '') IS NOT NULL) AS latest_funded_at
    FROM mip.first_party.loan_applications
    GROUP BY borrower_id
  ) AS fa
    ON fa.borrower_id = CONCAT('B-', LPAD(UPPER(CONV(CAST(ABS(XXHASH64(lc.clip)) AS STRING), 10, 36)), 13, '0'))
  WHERE fa.latest_funded_channel IS NOT NULL
    AND lc.situs_state IS NOT NULL
),
unioned AS (
  SELECT * FROM rate_spread_rows      UNION ALL
  SELECT * FROM equity_rows           UNION ALL
  SELECT * FROM market_trend_rows     UNION ALL
  SELECT * FROM listing_rows          UNION ALL
  SELECT * FROM heloc_propensity_rows UNION ALL
  SELECT * FROM refi_propensity_rows  UNION ALL
  SELECT * FROM loan_type_fit_rows    UNION ALL
  SELECT * FROM competitor_lien_rows  UNION ALL
  SELECT * FROM multi_property_rows   UNION ALL
  SELECT * FROM absentee_rows         UNION ALL
  SELECT * FROM corporate_owner_rows  UNION ALL
  SELECT * FROM recent_refi_rows      UNION ALL
  SELECT * FROM recent_payoff_rows    UNION ALL
  SELECT * FROM recent_sale_rows      UNION ALL
  SELECT * FROM foreclosure_stage_rows UNION ALL
  SELECT * FROM product_type_rows     UNION ALL
  SELECT * FROM origination_channel_rows
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

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.evidence_events.clip IS 'Cotality CLIP. Not in Pydantic EvidenceEvent (router strips); used for join / filter.';
COMMENT ON COLUMN mip.gold.evidence_events.evidence_id IS 'Deterministic: "ev-" || substr(sha2(clip || signal_type || timestamp, 256), 1, 12). Stable across refreshes so Borrower360.evidence_ids stays consistent.';
COMMENT ON COLUMN mip.gold.evidence_events.source_product IS 'Human label: Voluntary Lien / AVM / Owner Link / Property / Mortgage Domain / Owner Transfer / Market Rates / MLS Listings / HELOC Propensity / Refi Propensity / First-Party LOS.';
COMMENT ON COLUMN mip.gold.evidence_events.source_table IS 'Real UC path. Shown verbatim in EvidenceDrawer -- must be a resolvable mip.silver.*, mip.gold.*, or mip.first_party.* path.';
COMMENT ON COLUMN mip.gold.evidence_events.signal_type IS 'Controlled vocab: listing / rate_spread / equity / market_trend / heloc_propensity / refi_propensity / loan_type_fit / product_type / origination_channel / competitor_lien / multi_property / absentee_mailing / corporate_owner / foreclosure_stage / recent_refi / recent_payoff / recent_sale. product_type and origination_channel are explainability-only (excluded from the evidence sub-score). BLOCKED vocab permit is NEVER emitted without a true permit source.';
COMMENT ON COLUMN mip.gold.evidence_events.signal_value IS 'Human-readable value: "+88 bps", "$285K", "3 properties", "competitor refi".';
COMMENT ON COLUMN mip.gold.evidence_events.display_text IS 'One-sentence deterministic template per signal_type. No PII.';
COMMENT ON COLUMN mip.gold.evidence_events.confidence IS '0..1. Per-signal: AVM uses upstream confidence_score_mktg; count-based rows 0.85-0.92 (see header).';
COMMENT ON COLUMN mip.gold.evidence_events.`timestamp` IS 'ISO-8601 STRING (matches Pydantic EvidenceEvent.timestamp: str).';
COMMENT ON COLUMN mip.gold.evidence_events.signal_rank IS 'Deterministic priority order for Borrower360.evidence_ids: listing=0, rate_spread=1, equity=2, market_trend=3, etc. Smaller = higher priority. Gold-only.';
