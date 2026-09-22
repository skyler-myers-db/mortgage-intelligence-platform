"""Canonical metric, time-series, lock-in, retention, and MSA SQL for Genie's
trusted answers."""

from __future__ import annotations

from backend.services.repositories.databricks_genie_canonical_sql import (
    _BORROWER_360,
    _ELIGIBLE,
    _EVIDENCE_EVENTS,
    _FUNNEL_SNAPSHOT_DAILY,
    _LOCKIN_COHORT,
    _SEGMENT_PERFORMANCE_METRIC_VIEW,
    _SEGMENT_POPULATION,
)

_CANONICAL_TOP_CASH_OUT_BY_EQUITY_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , equity_estimate
     , equity_pct
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE recommended_offer_code IN ('cash_out', 'heloc', 'refi_plus_heloc')
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY equity_estimate DESC, opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_INVESTOR_TOP_BY_RELATED_PROPERTY_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , related_property_count
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE array_contains(segment_codes, 'investor')
  AND related_property_count >= 2
  AND {_ELIGIBLE}
ORDER BY related_property_count DESC, opportunity_score DESC, borrower_id ASC
LIMIT 20
""".strip()

_CANONICAL_MEAN_RATE_SPREAD_BY_SEGMENT_SQL = f"""
SELECT segment_code
     , COUNT(DISTINCT borrower_id) AS borrowers
     , CAST(ROUND(AVG(rate_spread_bps), 1) AS DOUBLE) AS avg_rate_spread_bps
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
LATERAL VIEW explode(segment_codes) seg AS segment_code
WHERE rate_spread_bps IS NOT NULL
GROUP BY segment_code
ORDER BY borrowers DESC, segment_code ASC
""".strip()

_CANONICAL_SEGMENT_APPROVAL_RATE_SQL = f"""
SELECT segment_code
     , name
     , count AS segment_borrowers
     , approval_rate
     , outreach_rate
     , avg_score
     , refreshed_at
FROM {_SEGMENT_PERFORMANCE_METRIC_VIEW}
WHERE state = '_ALL'
  AND count > 0
ORDER BY approval_rate DESC NULLS LAST, outreach_rate DESC NULLS LAST, count DESC, segment_code ASC
LIMIT 10
""".strip()

_CANONICAL_MEAN_LEAD_SCORE_BY_STATE_SQL = f"""
SELECT state
     , COUNT(*) AS borrowers
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_lead_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE state IS NOT NULL
  AND TRIM(state) <> ''
GROUP BY state
ORDER BY avg_lead_score DESC, borrowers DESC, state ASC
LIMIT 20
""".strip()

_CANONICAL_EVIDENCE_EVENTS_YESTERDAY_SQL = f"""
SELECT signal_type
     , COUNT(*) AS evidence_events
     , MAX(to_timestamp(`timestamp`)) AS latest_evidence_at
FROM {_EVIDENCE_EVENTS}
WHERE to_date(to_timestamp(`timestamp`)) = date_sub(current_date(), 1)
GROUP BY signal_type
ORDER BY evidence_events DESC, signal_type ASC
""".strip()

_CANONICAL_LEAD_SCORE_WEEKLY_DISTRIBUTION_SQL = f"""
SELECT date_trunc('WEEK', snapshot_date) AS week_start
     , COUNT(*) AS snapshot_rows
     , CAST(SUM(addressable_borrowers) AS BIGINT) AS addressable_borrowers
     , CAST(ROUND(AVG(avg_opportunity_score), 1) AS DOUBLE) AS avg_opportunity_score
     , CAST(SUM(high_opportunity_borrowers) AS BIGINT) AS high_opportunity_borrowers
     , MAX(snapshot_at) AS snapshot_at
FROM {_FUNNEL_SNAPSHOT_DAILY}
WHERE state = '_ALL'
  AND segment_code = '_ALL'
  AND snapshot_date >= date_sub(current_date(), 14)
GROUP BY date_trunc('WEEK', snapshot_date)
ORDER BY week_start DESC
LIMIT 2
""".strip()

_CANONICAL_APPROVAL_TREND_30D_SQL = f"""
SELECT snapshot_date
     , approved_borrowers AS approvals
     , actioned_borrowers
     , addressable_borrowers
     , snapshot_at
FROM {_FUNNEL_SNAPSHOT_DAILY}
WHERE state = '_ALL'
  AND segment_code = '_ALL'
  AND snapshot_date >= date_sub(current_date(), 30)
ORDER BY snapshot_date ASC
""".strip()

_CANONICAL_EVIDENCE_EVENTS_THIS_QUARTER_SQL = f"""
SELECT signal_type
     , COUNT(*) AS evidence_events
     , MAX(to_timestamp(`timestamp`)) AS latest_evidence_at
FROM {_EVIDENCE_EVENTS}
WHERE to_timestamp(`timestamp`) >= date_trunc('QUARTER', current_timestamp())
GROUP BY signal_type
ORDER BY evidence_events DESC, signal_type ASC
""".strip()

_CANONICAL_ITM_OFFER_MIX_SQL = f"""
SELECT recommended_offer_code
     , recommended_offer
     , COUNT(*) AS borrowers
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE array_contains(segment_codes, 'itm')
GROUP BY recommended_offer_code, recommended_offer
ORDER BY borrowers DESC, recommended_offer_code ASC
""".strip()

_CANONICAL_HELOC_RECOMMENDATION_BORROWERS_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , recommended_offer_code
     , recommended_offer
     , equity_estimate
     , equity_pct
     , heloc_propensity_score
     , opportunity_score
     , refreshed_at
FROM {_BORROWER_360}
WHERE recommended_offer_code IN ('heloc', 'refi_plus_heloc')
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, equity_estimate DESC, borrower_id ASC
LIMIT 50
""".strip()

_CANONICAL_LISTED_BY_PRODUCT_RATE_SQL = f"""
SELECT COALESCE(NULLIF(first_pos_loan_type, ''), 'Unknown') AS first_pos_loan_type
     , COUNT(*) AS listed_borrowers
     , CAST(ROUND(AVG(current_rate), 2) AS DOUBLE) AS avg_current_rate
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE listed_for_sale = TRUE
GROUP BY COALESCE(NULLIF(first_pos_loan_type, ''), 'Unknown')
ORDER BY listed_borrowers DESC, first_pos_loan_type ASC
""".strip()

_CANONICAL_LISTED_DAYS_ON_MARKET_BY_STATE_SQL = f"""
SELECT state
     , COUNT(*) AS listed_borrowers
     , CAST(ROUND(AVG(listing_days_on_market), 1) AS DOUBLE)
         AS avg_listing_days_on_market
     , CAST(ROUND(AVG(listing_price), 0) AS BIGINT) AS avg_listing_price
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE listed_for_sale = TRUE
  AND state IS NOT NULL
  AND TRIM(state) <> ''
GROUP BY state
ORDER BY listed_borrowers DESC, avg_listing_days_on_market ASC, state ASC
LIMIT 5
""".strip()

_CANONICAL_LOCKIN_COHORT_SIZE_SQL = f"""
SELECT COUNT(*) AS lockin_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_LOCKIN_COHORT}
""".strip()

_CANONICAL_LOCKIN_MEDIAN_RATE_SQL = f"""
SELECT CAST(ROUND(percentile_approx(origination_rate * 100, 0.5), 3) AS DOUBLE)
         AS median_rate_pct
     , COUNT(*) AS lockin_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_LOCKIN_COHORT}
""".strip()

_CANONICAL_LOCKIN_BY_STATE_SQL = f"""
SELECT state
     , COUNT(*) AS lockin_borrowers
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_LOCKIN_COHORT}
WHERE state IS NOT NULL
  AND TRIM(state) <> ''
GROUP BY state
ORDER BY lockin_borrowers DESC, state ASC
""".strip()

_CANONICAL_TOP_COHORTS_SQL = f"""
SELECT segment_code
     , name
     , count AS borrowers
     , avg_score
     , refreshed_at
FROM {_SEGMENT_POPULATION}
WHERE state = '_ALL'
  AND count > 0
ORDER BY count DESC, avg_score DESC, segment_code ASC
LIMIT 10
""".strip()

_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL = f"""
SELECT COUNT(*) AS retention_risk_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE is_current_customer = TRUE
  AND (
    array_contains(segment_codes, 'retention')
    OR recommended_offer_code = 'retention'
)
""".strip()

_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL = f"""
WITH matches AS (
  SELECT b.borrower_id
       , b.city
       , b.state
       , b.recommended_offer_code
       , b.opportunity_score
       , MAX(to_timestamp(e.`timestamp`)) AS latest_competitor_lien_at
  FROM {_BORROWER_360} AS b
  JOIN {_EVIDENCE_EVENTS} AS e
    ON e.clip = b.clip
  WHERE array_contains(b.segment_codes, 'retention')
    AND e.signal_type = 'competitor_lien'
    AND to_timestamp(e.`timestamp`) >= current_timestamp() - interval 30 days
  GROUP BY b.borrower_id
         , b.city
         , b.state
         , b.recommended_offer_code
         , b.opportunity_score
),
ranked AS (
  SELECT borrower_id
       , city
       , state
       , recommended_offer_code
       , opportunity_score
       , latest_competitor_lien_at
       , COUNT(*) OVER () AS total_matching_borrowers
  FROM matches
)
SELECT borrower_id
     , city
     , state
     , recommended_offer_code
     , opportunity_score
     , latest_competitor_lien_at
     , total_matching_borrowers
FROM ranked
ORDER BY latest_competitor_lien_at DESC
       , opportunity_score DESC
       , borrower_id ASC
LIMIT 50
""".strip()

_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL = f"""
WITH matches AS (
  SELECT b.borrower_id
       , b.city
       , b.state
       , b.recommended_offer_code
       , b.opportunity_score
       , MAX(to_timestamp(e.`timestamp`)) AS latest_competitor_lien_at
  FROM {_BORROWER_360} AS b
  JOIN {_EVIDENCE_EVENTS} AS e
    ON e.clip = b.clip
  WHERE b.state = :state
    AND array_contains(b.segment_codes, 'retention')
    AND e.signal_type = 'competitor_lien'
    AND to_timestamp(e.`timestamp`) >= current_timestamp() - interval 30 days
  GROUP BY b.borrower_id
         , b.city
         , b.state
         , b.recommended_offer_code
         , b.opportunity_score
),
ranked AS (
  SELECT borrower_id
       , city
       , state
       , recommended_offer_code
       , opportunity_score
       , latest_competitor_lien_at
       , COUNT(*) OVER () AS total_matching_borrowers
  FROM matches
)
SELECT borrower_id
     , city
     , state
     , recommended_offer_code
     , opportunity_score
     , latest_competitor_lien_at
     , total_matching_borrowers
FROM ranked
ORDER BY latest_competitor_lien_at DESC
       , opportunity_score DESC
       , borrower_id ASC
LIMIT 50
""".strip()

_CANONICAL_MSA_SCORE_SQL = f"""
WITH borrower_markets AS (
  SELECT situs_cbsa_code
       , COALESCE(NULLIF(city, ''), 'Unknown') AS city
       , state
       , opportunity_score
       , refreshed_at
  FROM {_BORROWER_360}
  WHERE situs_cbsa_code IS NOT NULL
    AND TRIM(situs_cbsa_code) <> ''
),
market_scores AS (
  SELECT situs_cbsa_code AS msa_cbsa_code
       , CAST(COUNT(*) AS BIGINT) AS borrowers
       , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
       , MAX(refreshed_at) AS refreshed_at
  FROM borrower_markets
  GROUP BY situs_cbsa_code
),
city_counts AS (
  SELECT situs_cbsa_code
       , city
       , state
       , COUNT(*) AS city_borrowers
  FROM borrower_markets
  GROUP BY situs_cbsa_code, city, state
),
city_ranked AS (
  SELECT situs_cbsa_code
       , city
       , state
       , city_borrowers
       , ROW_NUMBER() OVER (
           PARTITION BY situs_cbsa_code
           ORDER BY city_borrowers DESC, city ASC, state ASC
         ) AS rn
  FROM city_counts
)
SELECT CONCAT(cr.city, ', ', cr.state, ' (CBSA ', ms.msa_cbsa_code, ')') AS market
     , ms.msa_cbsa_code
     , ms.borrowers
     , ms.avg_score
     , ms.refreshed_at
FROM market_scores AS ms
LEFT JOIN city_ranked AS cr
  ON cr.situs_cbsa_code = ms.msa_cbsa_code
 AND cr.rn = 1
ORDER BY ms.borrowers DESC, ms.avg_score DESC, ms.msa_cbsa_code ASC
LIMIT 5
""".strip()

_CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL = f"""
SELECT segment_code
     , state
     , count AS investor_borrowers
     , avg_score
     , delta_vs_prior
     , refreshed_at
FROM {_SEGMENT_POPULATION}
WHERE segment_code = 'investor'
  AND state <> '_ALL'
  AND count > 0
ORDER BY count DESC, avg_score DESC, state ASC
LIMIT 20
""".strip()
