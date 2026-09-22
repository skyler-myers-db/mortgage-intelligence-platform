"""Catalog-qualified asset names and the canonical count, share, distribution,
and geography SQL for Genie's trusted answers."""

from __future__ import annotations

from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD

# S1.4: canonical fail-closed contactability predicates (single interface).
_ELIGIBLE = eligible_sql_predicate()
_B_ELIGIBLE = eligible_sql_predicate("b")
_BORROWER_360 = qualify("gold", "borrower_360")
_EVIDENCE_EVENTS = qualify("gold", "evidence_events")
_FUNNEL_SNAPSHOT_DAILY = qualify("gold", "funnel_snapshot_daily")
_LEAD_POPULATION = qualify("gold", "lead_population")
_LOCKIN_COHORT = qualify("gold", "lockin_cohort")
_SEGMENT_POPULATION = qualify("gold", "segment_population")
_SEGMENT_PERFORMANCE_METRIC_VIEW = qualify("semantics", "segment_performance_metric_view")

_CANONICAL_ITM_COUNT_SQL = f"""
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE in_the_money = TRUE
""".strip()

_CANONICAL_ITM_COUNT_AVG_SPREAD_SQL = f"""
SELECT COUNT(*) AS in_the_money_borrowers
     , CAST(ROUND(AVG(rate_spread_bps), 1) AS DOUBLE) AS avg_rate_spread_bps
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE in_the_money = TRUE
""".strip()

_CANONICAL_HELOC_COUNT_SQL = f"""
SELECT COUNT(*) AS equity_capacity_borrowers
     , CAST(ROUND(AVG(equity_pct), 1) AS DOUBLE) AS avg_equity_pct
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE equity_pct >= 35
""".strip()

_CANONICAL_EQUITY_THRESHOLD_COUNT_SQL = f"""
SELECT CAST(COUNT_IF(equity_pct >= :min_equity_pct) AS BIGINT)
         AS equity_capacity_borrowers
     , CAST(COUNT(*) AS BIGINT) AS total_borrowers
     , CAST(ROUND(
         100.0 * COUNT_IF(equity_pct >= :min_equity_pct) / NULLIF(COUNT(*), 0)
       , 2) AS DOUBLE) AS borrower_share_pct
     , CAST(ROUND(AVG(CASE WHEN equity_pct >= :min_equity_pct THEN equity_pct END), 1)
         AS DOUBLE) AS avg_equity_pct
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
""".strip()

_CANONICAL_EQUITY_THRESHOLD_STRICT_COUNT_SQL = f"""
SELECT CAST(COUNT_IF(equity_pct > :min_equity_pct) AS BIGINT)
         AS equity_capacity_borrowers
     , CAST(COUNT(*) AS BIGINT) AS total_borrowers
     , CAST(ROUND(
         100.0 * COUNT_IF(equity_pct > :min_equity_pct) / NULLIF(COUNT(*), 0)
       , 2) AS DOUBLE) AS borrower_share_pct
     , CAST(ROUND(AVG(CASE WHEN equity_pct > :min_equity_pct THEN equity_pct END), 1)
         AS DOUBLE) AS avg_equity_pct
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
""".strip()

_CANONICAL_NEGATIVE_EQUITY_COUNT_SQL = f"""
SELECT CAST(COUNT_IF(ltv > 100) AS BIGINT) AS underwater_borrowers
     , CAST(COUNT(*) AS BIGINT) AS total_borrowers
     , CAST(ROUND(
         100.0 * COUNT_IF(ltv > 100) / NULLIF(COUNT(*), 0)
       , 2) AS DOUBLE) AS borrower_share_pct
     , CAST(ROUND(PERCENTILE_APPROX(CASE WHEN ltv > 100 THEN ltv END, 0.5), 1)
         AS DOUBLE) AS median_underwater_ltv_pct
     , CAST(COUNT_IF(ltv > 500) AS BIGINT) AS high_ltv_tail_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
""".strip()

_CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL = f"""
WITH banded AS (
  SELECT CASE
           WHEN equity_pct IS NULL THEN 'Unknown'
           WHEN ltv > 100 THEN 'Underwater (LTV > 100)'
           WHEN equity_pct < 15 THEN '0-14%'
           WHEN equity_pct < 35 THEN '15-34%'
           WHEN equity_pct < 50 THEN '35-49%'
           WHEN equity_pct < 75 THEN '50-74%'
           ELSE '75%+'
         END AS equity_band
       , CASE
           WHEN equity_pct IS NULL THEN 99
           WHEN ltv > 100 THEN 0
           WHEN equity_pct < 15 THEN 1
           WHEN equity_pct < 35 THEN 2
           WHEN equity_pct < 50 THEN 3
           WHEN equity_pct < 75 THEN 4
           ELSE 5
         END AS sort_order
       , equity_pct
       , refreshed_at
  FROM {_BORROWER_360}
)
SELECT equity_band
     , CAST(COUNT(*) AS BIGINT) AS borrowers
     , CAST(ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS DOUBLE)
         AS borrower_share_pct
     , CAST(ROUND(AVG(equity_pct), 1) AS DOUBLE) AS avg_equity_pct
     , MAX(refreshed_at) AS refreshed_at
FROM banded
GROUP BY equity_band, sort_order
ORDER BY sort_order
""".strip()

_CANONICAL_ADDRESSABLE_MARKET_SQL = f"""
SELECT COUNT(*) AS marketable_population
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE {_ELIGIBLE}
  AND is_owner_occupied = TRUE
  AND current_lien_balance > 0
  AND COALESCE(second_pos_amount, 0) = 0
  AND equity_pct >= 15
""".strip()

_CANONICAL_RANKED_LEAD_POPULATION_SQL = f"""
SELECT COUNT(*) AS ranked_leads
     , MAX(refreshed_at) AS refreshed_at
FROM {_LEAD_POPULATION}
WHERE {_ELIGIBLE}
""".strip()

_CANONICAL_ITM_COUNT_BY_STATE_SQL = f"""
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE in_the_money = TRUE
  AND state = :state
""".strip()

_CANONICAL_ITM_COUNT_BY_CITY_SQL = f"""
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE in_the_money = TRUE
  AND LOWER(city) = LOWER(:city)
""".strip()

_CANONICAL_ITM_TOP_ZIPS_SQL = f"""
SELECT zip
     , state
     , COUNT(*) AS in_the_money_borrowers
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE in_the_money = TRUE
  AND zip IS NOT NULL
  AND TRIM(zip) <> ''
GROUP BY zip, state
ORDER BY in_the_money_borrowers DESC, avg_score DESC, zip ASC
LIMIT 10
""".strip()

_CANONICAL_ITM_TOP_LEAD_QUEUE_ZIPS_SQL = f"""
SELECT zip
     , state
     , COUNT(*) AS in_the_money_leads
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_LEAD_POPULATION}
WHERE array_contains(segment_codes, 'itm')
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
  AND zip IS NOT NULL
  AND TRIM(zip) <> ''
GROUP BY zip, state
ORDER BY in_the_money_leads DESC, avg_score DESC, zip ASC
LIMIT 10
""".strip()

_CANONICAL_ITM_BY_STATE_SQL = f"""
WITH broad AS (
  SELECT state
       , COUNT(*) AS in_the_money_borrowers
       , CAST(ROUND(AVG(rate_spread_bps), 1) AS DOUBLE) AS avg_rate_spread_bps
       , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
       , MAX(refreshed_at) AS refreshed_at
  FROM {_BORROWER_360}
  WHERE in_the_money = TRUE
    AND state IS NOT NULL
    AND TRIM(state) <> ''
  GROUP BY state
),
lead_queue AS (
  SELECT state
       , COUNT(*) AS lead_queue_borrowers
  FROM {_BORROWER_360}
  WHERE array_contains(segment_codes, 'itm')
    AND {_ELIGIBLE}
    AND state IS NOT NULL
    AND TRIM(state) <> ''
  GROUP BY state
)
SELECT b.state
     , b.in_the_money_borrowers
     , COALESCE(l.lead_queue_borrowers, 0) AS lead_queue_borrowers
     , b.avg_rate_spread_bps
     , b.avg_score
     , b.refreshed_at
FROM broad b
LEFT JOIN lead_queue l ON l.state = b.state
ORDER BY b.in_the_money_borrowers DESC, b.avg_score DESC, b.state ASC
LIMIT 20
""".strip()

_CANONICAL_HELOC_TOP_ZIPS_SQL = f"""
SELECT zip
     , state
     , COUNT(*) AS equity_capacity_borrowers
     , CAST(ROUND(AVG(equity_pct), 1) AS DOUBLE) AS avg_equity_pct
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE equity_pct >= 35
  AND zip IS NOT NULL
  AND TRIM(zip) <> ''
GROUP BY zip, state
ORDER BY equity_capacity_borrowers DESC, avg_equity_pct DESC, zip ASC
LIMIT 5
""".strip()

_CANONICAL_CASH_OUT_TOP_STATE_SQL = f"""
SELECT state
     , COUNT(*) AS cash_out_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE recommended_offer_code = 'cash_out'
GROUP BY state
ORDER BY cash_out_borrowers DESC, state ASC
LIMIT 1
""".strip()

_CANONICAL_LISTED_COUNT_SQL = f"""
SELECT CAST(COUNT(*) AS BIGINT) AS listed_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE listed_for_sale = TRUE
""".strip()

_CANONICAL_LISTED_COUNT_BY_STATE_SQL = f"""
SELECT CAST(COUNT(*) AS BIGINT) AS listed_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE listed_for_sale = TRUE
  AND state = :state
""".strip()

_CANONICAL_INVESTOR_COUNT_SQL = f"""
SELECT CAST(COUNT(*) AS BIGINT) AS investor_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE array_contains(segment_codes, 'investor')
""".strip()

_CANONICAL_ITM_SHARE_SQL = f"""
SELECT CAST(COUNT_IF(in_the_money = TRUE) AS BIGINT) AS in_the_money_borrowers
     , CAST(COUNT(*) AS BIGINT) AS total_borrowers
     , CAST(ROUND(
         100.0 * COUNT_IF(in_the_money = TRUE) / NULLIF(COUNT(*), 0)
       , 2) AS DOUBLE) AS borrower_share_pct
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
""".strip()

_CANONICAL_REFI_EQUITY_SIGNAL_COMPARE_SQL = f"""
SELECT CAST(COUNT(*) AS BIGINT) AS marketable_borrowers
     , CAST(COUNT_IF(recommended_offer_code IN ('refi', 'refi_plus_heloc')) AS BIGINT)
         AS refinance_candidates
     , CAST(COUNT_IF(recommended_offer_code IN ('heloc', 'cash_out', 'refi_plus_heloc')) AS BIGINT)
         AS home_equity_candidates
     , CAST(COUNT_IF(recommended_offer_code = 'refi_plus_heloc') AS BIGINT)
         AS refi_plus_home_equity_candidates
     , CAST(ROUND(AVG(
         CASE WHEN recommended_offer_code IN ('refi', 'refi_plus_heloc')
              THEN rate_spread_bps END
       ), 1) AS DOUBLE) AS avg_refi_rate_spread_bps
     , CAST(ROUND(AVG(
         CASE WHEN recommended_offer_code IN ('heloc', 'cash_out', 'refi_plus_heloc')
              THEN equity_pct END
       ), 1) AS DOUBLE) AS avg_home_equity_pct
     , CAST(ROUND(AVG(
         CASE WHEN recommended_offer_code IN ('heloc', 'cash_out', 'refi_plus_heloc')
              THEN heloc_propensity_score END
       ), 1) AS DOUBLE) AS avg_heloc_propensity_score
     , CAST(COUNT_IF(has_refi_propensity_trigger = TRUE) AS BIGINT) AS refi_propensity_triggers
     , CAST(COUNT_IF(has_heloc_propensity_trigger = TRUE) AS BIGINT) AS heloc_propensity_triggers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE {_ELIGIBLE}
  AND consent_status = 'opt_in'
""".strip()

_CANONICAL_REFI_DRIVER_SQL = f"""
SELECT e.signal_type
     , CAST(COUNT(DISTINCT b.borrower_id) AS BIGINT) AS borrowers
     , CAST(ROUND(AVG(e.confidence), 3) AS DOUBLE) AS avg_confidence
     , MAX(to_timestamp(e.`timestamp`)) AS latest_evidence_at
FROM {_BORROWER_360} AS b
JOIN {_EVIDENCE_EVENTS} AS e
  ON e.clip = b.clip
WHERE {_B_ELIGIBLE}
  AND b.consent_status = 'opt_in'
  AND b.recommended_offer_code IN ('refi', 'refi_plus_heloc')
  AND e.signal_type IN (
    'rate_spread',
    'equity',
    'market_trend',
    'refi_propensity',
    'heloc_propensity',
    'recent_refi',
    'recent_payoff'
  )
GROUP BY e.signal_type
ORDER BY borrowers DESC, avg_confidence DESC, signal_type ASC
LIMIT 8
""".strip()

_CANONICAL_ITM_TOP_TIER_COMPARE_SQL = f"""
SELECT CAST(COUNT(*) AS BIGINT) AS marketable_borrowers
     , CAST(COUNT_IF(in_the_money = TRUE) AS BIGINT) AS in_the_money_borrowers
     , CAST(COUNT_IF(opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD}) AS BIGINT) AS top_tier_borrowers
     , CAST(COUNT_IF(in_the_money = TRUE AND opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD}) AS BIGINT)
         AS overlap_borrowers
     , CAST(ROUND(AVG(CASE WHEN in_the_money = TRUE THEN rate_spread_bps END), 1) AS DOUBLE)
         AS avg_in_the_money_rate_spread_bps
     , CAST(ROUND(AVG(CASE WHEN opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD} THEN opportunity_score END), 1) AS DOUBLE)
         AS avg_top_tier_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE {_ELIGIBLE}
  AND consent_status = 'opt_in'
""".strip()

_CANONICAL_STRATEGY_BOARD_SQL = f"""
WITH exploded_segments AS (
  SELECT state
       , segment_code
       , borrower_id
       , opportunity_score
       , recommended_offer_code
       , recommended_offer
       , refreshed_at
  FROM {_BORROWER_360}
  LATERAL VIEW explode(segment_codes) seg AS segment_code
  WHERE {_ELIGIBLE}
    AND consent_status = 'opt_in'
    AND state IS NOT NULL
    AND TRIM(state) <> ''
    AND segment_code IN ('itm', 'equity', 'investor', 'retention')
    AND recommended_offer_code <> 'nurture'
),
segment_geo AS (
  SELECT state
       , segment_code
       , COUNT(DISTINCT borrower_id) AS marketable_borrowers
       , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
       , MAX(refreshed_at) AS refreshed_at
  FROM exploded_segments
  GROUP BY state, segment_code
),
offer_mix AS (
  SELECT state
       , segment_code
       , recommended_offer_code
       , recommended_offer
       , COUNT(DISTINCT borrower_id) AS offer_borrowers
       , ROW_NUMBER() OVER (
           PARTITION BY state, segment_code
           ORDER BY COUNT(DISTINCT borrower_id) DESC, recommended_offer_code ASC
         ) AS offer_rank
  FROM exploded_segments
  GROUP BY state, segment_code, recommended_offer_code, recommended_offer
)
SELECT sg.state
     , sg.segment_code
     , sg.marketable_borrowers
     , sg.avg_score
     , om.recommended_offer_code AS leading_offer_code
     , om.recommended_offer AS leading_recommended_offer
     , om.offer_borrowers AS leading_offer_borrowers
     , sg.refreshed_at
FROM segment_geo AS sg
LEFT JOIN offer_mix AS om
  ON sg.state = om.state
 AND sg.segment_code = om.segment_code
 AND om.offer_rank = 1
WHERE sg.marketable_borrowers > 0
ORDER BY sg.avg_score DESC, sg.marketable_borrowers DESC, sg.state ASC, sg.segment_code ASC
LIMIT 12
""".strip()
