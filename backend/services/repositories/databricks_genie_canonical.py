"""Canonical SQL and question-scope helpers for Databricks Genie answers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD


@dataclass(frozen=True)
class CanonicalRetentionEligibilityFallback:
    sql_query: str
    rows: list[dict[str, Any]]
    answer: str
    metric_value: str
    suppress_actions: bool = True


@dataclass(frozen=True)
class CanonicalEquityThresholdScope:
    threshold_pct: float
    strict_greater: bool
    asks_share: bool


@dataclass(frozen=True)
class CanonicalNegativeEquityScope:
    asks_share: bool


@dataclass(frozen=True)
class CanonicalListedCountScope:
    state_name: str | None = None
    state_code: str | None = None


def _retention_eligibility_fallback_from_summary(
    summary_rows: list[dict[str, Any]] | None,
    *,
    state_name: str | None = None,
    state_code: str | None = None,
) -> CanonicalRetentionEligibilityFallback | None:
    if not summary_rows:
        return None

    summary = summary_rows[0]
    retention_count = int(summary.get("retention_segment_borrowers") or 0)
    marketing_count = int(summary.get("marketing_eligible_retention_borrowers") or 0)
    action_ready_count = int(summary.get("action_ready_retention_borrowers") or 0)

    if state_name and state_code:
        answer = (
            f"{state_name} ({state_code}) has {retention_count:,} borrowers in the "
            "Retention Risk segment, but none qualify for the action-ready best-retention "
            f"queue after marketing-eligibility and opt-in consent filters "
            f"({marketing_count:,} marketing-eligible; {action_ready_count:,} opt-in). "
            "Competitor-lien evidence questions use a separate evidence workflow and may "
            "return borrowers that are not action-ready for outreach."
        )
        sql_query = _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL
    else:
        answer = (
            f"The current coverage has {retention_count:,} borrowers in the Retention "
            "Risk segment, but none qualify for the action-ready best-retention queue "
            f"after marketing-eligibility and opt-in consent filters "
            f"({marketing_count:,} marketing-eligible; {action_ready_count:,} opt-in). "
            "Competitor-lien evidence questions use a separate evidence workflow and may "
            "return borrowers that are not action-ready for outreach."
        )
        sql_query = _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL

    return CanonicalRetentionEligibilityFallback(
        sql_query=sql_query,
        rows=summary_rows,
        answer=answer,
        metric_value=f"{action_ready_count:,}",
    )

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

_CANONICAL_LISTED_PURCHASE_TOP_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , first_pos_loan_type
     , current_rate
     , listing_status_category
     , refreshed_at
FROM {_BORROWER_360}
WHERE listed_for_sale = TRUE
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, borrower_id ASC
LIMIT 10
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

_CANONICAL_TOP_BORROWERS_BY_STATE_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , opportunity_score AS lead_score
     , recommended_offer_code
     , recommended_offer
     , rank_within_state
     , refreshed_at
FROM {_LEAD_POPULATION}
WHERE state = :state
ORDER BY opportunity_score DESC, rank_within_state ASC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_BORROWERS_GLOBAL_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , opportunity_score AS lead_score
     , recommended_offer_code
     , recommended_offer
     , rank_overall
     , refreshed_at
FROM {_LEAD_POPULATION}
WHERE {_ELIGIBLE}
ORDER BY opportunity_score DESC, rank_overall ASC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_REFI_BORROWERS_BY_STATE_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , rate_spread_bps
     , equity_pct
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE state = :state
  AND in_the_money = TRUE
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, rate_spread_bps DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_CASH_OUT_BORROWERS_BY_STATE_SQL = f"""
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
WHERE state = :state
  AND recommended_offer_code = 'cash_out'
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY equity_estimate DESC, opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_HELOC_BORROWERS_BY_STATE_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , equity_estimate
     , equity_pct
     , heloc_propensity_score
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE state = :state
  AND (
    recommended_offer_code IN ('heloc', 'refi_plus_heloc')
    OR has_heloc_propensity_trigger = TRUE
    OR array_contains(segment_codes, 'permit')
  )
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY heloc_propensity_score DESC NULLS LAST, equity_estimate DESC, opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_LISTED_BORROWERS_BY_STATE_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , listing_status_category
     , refreshed_at
FROM {_BORROWER_360}
WHERE state = :state
  AND listed_for_sale = TRUE
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_INVESTOR_BORROWERS_BY_STATE_SQL = f"""
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
WHERE state = :state
  AND (array_contains(segment_codes, 'investor') OR is_investor = TRUE)
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY related_property_count DESC NULLS LAST, opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_RETENTION_BORROWERS_BY_STATE_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , rate_spread_bps
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE state = :state
  AND array_contains(segment_codes, 'retention')
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, rate_spread_bps DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL = f"""
SELECT CAST(COUNT_IF(array_contains(segment_codes, 'retention')) AS BIGINT)
         AS retention_segment_borrowers
     , CAST(COUNT_IF(array_contains(segment_codes, 'retention') AND {_ELIGIBLE}) AS BIGINT)
         AS marketing_eligible_retention_borrowers
     , CAST(COUNT_IF(
         array_contains(segment_codes, 'retention')
         AND {_ELIGIBLE}
         AND consent_status = 'opt_in'
       ) AS BIGINT) AS action_ready_retention_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE state = :state
""".strip()

_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL = {
    "refi": _CANONICAL_TOP_REFI_BORROWERS_BY_STATE_SQL,
    "cash_out": _CANONICAL_TOP_CASH_OUT_BORROWERS_BY_STATE_SQL,
    "heloc": _CANONICAL_TOP_HELOC_BORROWERS_BY_STATE_SQL,
    "listed": _CANONICAL_TOP_LISTED_BORROWERS_BY_STATE_SQL,
    "investor": _CANONICAL_TOP_INVESTOR_BORROWERS_BY_STATE_SQL,
    "retention": _CANONICAL_TOP_RETENTION_BORROWERS_BY_STATE_SQL,
}

_CANONICAL_TOP_REFI_BORROWERS_GLOBAL_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , rate_spread_bps
     , equity_pct
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE in_the_money = TRUE
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, rate_spread_bps DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_CASH_OUT_BORROWERS_GLOBAL_SQL = f"""
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
WHERE recommended_offer_code = 'cash_out'
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY equity_estimate DESC, opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_HELOC_BORROWERS_GLOBAL_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , equity_estimate
     , equity_pct
     , heloc_propensity_score
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE (
    recommended_offer_code IN ('heloc', 'refi_plus_heloc')
    OR has_heloc_propensity_trigger = TRUE
    OR array_contains(segment_codes, 'permit')
  )
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY heloc_propensity_score DESC NULLS LAST, equity_estimate DESC, opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_LISTED_BORROWERS_GLOBAL_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , listing_status_category
     , refreshed_at
FROM {_BORROWER_360}
WHERE listed_for_sale = TRUE
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_INVESTOR_BORROWERS_GLOBAL_SQL = f"""
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
WHERE (array_contains(segment_codes, 'investor') OR is_investor = TRUE)
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY related_property_count DESC NULLS LAST, opportunity_score DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_TOP_RETENTION_BORROWERS_GLOBAL_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , rate_spread_bps
     , opportunity_score
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE array_contains(segment_codes, 'retention')
  AND {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, rate_spread_bps DESC, borrower_id ASC
LIMIT 10
""".strip()

_CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL = f"""
SELECT CAST(COUNT_IF(array_contains(segment_codes, 'retention')) AS BIGINT)
         AS retention_segment_borrowers
     , CAST(COUNT_IF(array_contains(segment_codes, 'retention') AND {_ELIGIBLE}) AS BIGINT)
         AS marketing_eligible_retention_borrowers
     , CAST(COUNT_IF(
         array_contains(segment_codes, 'retention')
         AND {_ELIGIBLE}
         AND consent_status = 'opt_in'
       ) AS BIGINT) AS action_ready_retention_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
""".strip()

_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL = {
    "refi": _CANONICAL_TOP_REFI_BORROWERS_GLOBAL_SQL,
    "cash_out": _CANONICAL_TOP_CASH_OUT_BORROWERS_GLOBAL_SQL,
    "heloc": _CANONICAL_TOP_HELOC_BORROWERS_GLOBAL_SQL,
    "listed": _CANONICAL_TOP_LISTED_BORROWERS_GLOBAL_SQL,
    "investor": _CANONICAL_TOP_INVESTOR_BORROWERS_GLOBAL_SQL,
    "retention": _CANONICAL_TOP_RETENTION_BORROWERS_GLOBAL_SQL,
}

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

_US_STATE_FILTERS: tuple[tuple[str, str], ...] = (
    ("alabama", "AL"),
    ("alaska", "AK"),
    ("arizona", "AZ"),
    ("arkansas", "AR"),
    ("california", "CA"),
    ("colorado", "CO"),
    ("connecticut", "CT"),
    ("delaware", "DE"),
    ("florida", "FL"),
    ("georgia", "GA"),
    ("hawaii", "HI"),
    ("idaho", "ID"),
    ("illinois", "IL"),
    ("indiana", "IN"),
    ("iowa", "IA"),
    ("kansas", "KS"),
    ("kentucky", "KY"),
    ("louisiana", "LA"),
    ("maine", "ME"),
    ("maryland", "MD"),
    ("massachusetts", "MA"),
    ("michigan", "MI"),
    ("minnesota", "MN"),
    ("mississippi", "MS"),
    ("missouri", "MO"),
    ("montana", "MT"),
    ("nebraska", "NE"),
    ("nevada", "NV"),
    ("new hampshire", "NH"),
    ("new jersey", "NJ"),
    ("new mexico", "NM"),
    ("new york", "NY"),
    ("north carolina", "NC"),
    ("north dakota", "ND"),
    ("ohio", "OH"),
    ("oklahoma", "OK"),
    ("oregon", "OR"),
    ("pennsylvania", "PA"),
    ("rhode island", "RI"),
    ("south carolina", "SC"),
    ("south dakota", "SD"),
    ("tennessee", "TN"),
    ("texas", "TX"),
    ("utah", "UT"),
    ("vermont", "VT"),
    ("virginia", "VA"),
    ("washington", "WA"),
    ("west virginia", "WV"),
    ("wisconsin", "WI"),
    ("wyoming", "WY"),
)
_AMBIGUOUS_STATE_CODES: frozenset[str] = frozenset({"HI", "ID", "IN", "ME", "OH", "OK", "OR"})


def _ambiguous_state_code_match_is_contextual(question: str, match: re.Match[str]) -> bool:
    before = question[: match.start()]
    after = question[match.end() :]
    has_geo_preface = bool(
        re.search(
            r"(?:^|[\s(,/;:-])(?:in|for|from|state|states|market|coverage|geography|geo)[:\s]+$",
            before,
            flags=re.IGNORECASE,
        )
    )
    if not has_geo_preface and not before.rstrip().endswith(("(", "[")):
        return False
    next_word = re.match(r"[\s,;:.-]+([A-Za-z]+)", after)
    if next_word is None:
        return True
    return next_word.group(1).lower() in {"is", "are", "has", "have", "with", "and"}


def _current_footprint_label() -> str:
    from backend.services.state_footprint import get_state_footprint_resolver

    codes = get_state_footprint_resolver().state_codes()
    return " / ".join(codes) if codes else "configured"


def _retention_competitor_lien_list_question(question: str) -> bool:
    q = question.lower()
    asks_for_rows = bool(
        re.search(r"\bborrowers?\b", q)
        and (
            re.search(r"\b(which|show|list|find|who are|give me)\b", q)
            or re.search(r"\bretention(?:[-\s]risk)?\s+borrowers?\b", q)
            or re.search(r"\bborrowers?\s+with\b", q)
        )
    )
    retention_scope = bool(
        re.search(
            r"\b(retention(?: list| cohort| borrowers?| leads?| candidates?)?|retention-risk|retention risk|recapture)\b",
            q,
        )
    )
    competitor_signal = "competitor lien" in q or "competitor-lien" in q
    return asks_for_rows and retention_scope and competitor_signal


def _retention_risk_question(question: str) -> bool:
    q = question.lower()
    if _retention_competitor_lien_list_question(question):
        return False
    has_customer_scope = bool(re.search(r"\b(current|summit|customer|customers)\b", q))
    has_retention_risk_phrase = bool(re.search(r"\bretention[-\s]?risk\b", q))
    has_risk_intent = bool(
        re.search(
            r"\b(retention|recapture|at risk|risk of going|going to a competitor|"
            r"shop(?:ping)?(?: a)? competitor|competitor recapture)\b",
            q,
        )
    )
    if has_retention_risk_phrase:
        return True
    return has_customer_scope and has_risk_intent


def _canonical_itm_state_scope(question: str) -> tuple[str, str] | None:
    q = question.lower()
    for name, code in _US_STATE_FILTERS:
        name_pattern = r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
        code_pattern = r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])"
        code_match = False
        exact_code_matches = tuple(re.finditer(code_pattern, question, flags=re.IGNORECASE))
        if exact_code_matches:
            code_match = code not in _AMBIGUOUS_STATE_CODES or any(
                _ambiguous_state_code_match_is_contextual(question, match)
                for match in exact_code_matches
            )
        if re.search(name_pattern, q) or code_match:
            return name.title(), code
    return None


def _canonical_in_the_money_count_scope(question: str) -> tuple[str, str] | None | bool:
    q = _normalized_question(question)
    if not _has_itm_intent(q):
        return False
    if "borrower" not in q:
        return False
    if not _has_count_intent(q):
        return False
    breakdown_terms = (
        " by ",
        "break down",
        "broken down",
        " by state",
        "by-state",
        "state by state",
        "top ",
        "rank",
        "zip",
        "county",
        "msa",
        "market",
        "average",
        "avg",
        "mean",
    )
    if any(term in q for term in breakdown_terms) or re.search(r"\blist\b", q):
        return None
    state_scope = _canonical_itm_state_scope(question)
    if state_scope is not None:
        return state_scope
    if re.search(
        r"\bborrowers?\b(?:\s+[a-z0-9-]+){0,6}\s+"
        r"(?:in|for|near|around|within)\s+(?!the\b|the-money\b)[a-z]",
        q,
    ):
        return None
    if re.search(r"\bin[- ]the[- ]money\s+in\s+[a-z]", q):
        return None
    return True


def _normalized_question(question: str) -> str:
    q = re.sub(r"[^a-z0-9\s%.-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    replacements = {
        "borower": "borrower",
        "borowers": "borrowers",
        "borrowr": "borrower",
        "borrowrs": "borrowers",
        " equty": " equity",
        " equiy": " equity",
        " equit ": " equity ",
        " in teh money": " in the money",
        " rn ": " right now ",
        "avg": "average",
    }
    for needle, replacement in replacements.items():
        q = q.replace(needle, replacement)
    return re.sub(r"\s+", " ", q).strip()


def _has_count_intent(q: str) -> bool:
    return bool(
        re.search(
            r"\b(how many|count|count of|number of|total|total number|size of|how big)\b",
            q,
        )
    )


def _has_share_intent(q: str) -> bool:
    return bool(re.search(r"\b(share|percent|percentage|ratio|what portion)\b", q))


def _has_strong_rank_intent(q: str) -> bool:
    return bool(
        re.search(
            r"\b(top|highest|rank|ranked|ranking|best|first|prioritize)\b",
            q,
        )
    )


def _has_equity_share_result_intent(q: str) -> bool:
    """Return True when the user asks for a share, not just a percent threshold."""

    return bool(
        re.search(
            r"\b(share|percentage|ratio|what portion|what percent|percent of borrowers|"
            r"percentage of borrowers)\b",
            q,
        )
    )


def _format_pct_threshold(value: float) -> str:
    return f"{value:g}"


def _has_rank_intent(q: str) -> bool:
    return bool(
        re.search(
            r"\b(top|highest|rank|ranked|ranking|show|list|best|first|prioritize)\b",
            q,
        )
    )


def _has_itm_intent(q: str) -> bool:
    return any(
        term in q
        for term in (
            "in-the-money",
            "in the money",
            "itm",
            "prime refi",
            "refi economic",
            "refinance economic",
            "refinance incentive",
            "refi incentive",
            "economic incentive",
            "rate incentive",
            "refinance opportunity",
            "refi opportunity",
        )
    )


def _has_global_coverage_scope(q: str) -> bool:
    return any(
        term in q
        for term in (
            "current cotality data coverage",
            "current cotality coverage",
            "current data coverage",
            "current coverage",
            "current refreshed coverage",
            "across coverage",
            "across the coverage",
            "currently",
            "overall",
            "national",
            "right now",
        )
    )


def _has_unsupported_geo_scope(question: str, q: str) -> bool:
    if _canonical_itm_state_scope(question) is not None:
        return True
    geo_terms = (
        "zip",
        "zips",
        "zipcode",
        "zip code",
        "postal",
        "county",
        "msa",
        "cbsa",
        "metro",
        "state by state",
        "by state",
    )
    if any(term in q for term in geo_terms):
        return True
    if re.search(
        r"\b(?:in|for|near|around|within)\s+(?:zip\s*)?\d{3,5}(?:-\d{4})?\b",
        q,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:in|for|near|around|within)\s+"
            r"(?!the\b|the-money\b|current\b|all\b|overall\b|national\b|coverage\b)"
            r"[a-z][a-z0-9 .-]{1,40}\b",
            q,
        )
    )


def _canonical_itm_count_avg_spread_scope(question: str) -> bool:
    q = _normalized_question(question)
    if not _has_global_coverage_scope(q) or _has_unsupported_geo_scope(question, q):
        return False
    has_itm = _has_itm_intent(q)
    asks_count = _has_count_intent(q)
    asks_spread = (
        ("rate spread" in q or "spread" in q)
        and any(term in q for term in ("average", "avg", "mean"))
    )
    return has_itm and "borrower" in q and asks_count and asks_spread


def _canonical_equity_threshold_scope(question: str) -> CanonicalEquityThresholdScope | None:
    q = _normalized_question(question)
    if _has_unsupported_geo_scope(question, q):
        return None
    equity_terms = (
        "home equity",
        "modeled equity",
        "equity pct",
        "equity percent",
        "equity percentage",
        "equity capacity",
        "high equity",
        "strong equity",
        "equity",
    )
    if not any(term in q for term in equity_terms):
        return None
    if not (_has_count_intent(q) or _has_share_intent(q)):
        return None
    if any(term in q for term in ("distribution", "histogram", "bucket", "band", "break down", "breakdown")):
        return None
    threshold: float = 35.0
    strict_greater = False
    threshold_match = re.search(
        r"\b(?P<op>at least|>=|over|more than|above|greater than|greater than or equal to)"
        r"\s*(?P<threshold>\d{1,3}(?:\.\d+)?)\s*(?:%|percent|percentage)?",
        q,
    )
    if threshold_match:
        threshold = float(threshold_match.group("threshold"))
        strict_greater = threshold_match.group("op") in {
            "over",
            "more than",
            "above",
            "greater than",
        }
    elif "high equity" not in q and "strong equity" not in q:
        return None
    if threshold < 0:
        return None
    return CanonicalEquityThresholdScope(
        threshold_pct=threshold,
        strict_greater=strict_greater,
        asks_share=_has_equity_share_result_intent(q) and not _has_count_intent(q),
    )


def _canonical_negative_equity_scope(question: str) -> CanonicalNegativeEquityScope | None:
    q = _normalized_question(question)
    if _has_unsupported_geo_scope(question, q):
        return None
    negative_terms = (
        "negative equity",
        "underwater",
        "equity below 0",
        "equity below zero",
        "below zero equity",
        "less than 0% equity",
        "less than 0 percent equity",
        "under 0% equity",
        "under 0 percent equity",
    )
    if not any(term in q for term in negative_terms):
        return None
    if not (_has_count_intent(q) or _has_share_intent(q) or "borrower" in q):
        return None
    return CanonicalNegativeEquityScope(
        asks_share=_has_equity_share_result_intent(q) and not _has_count_intent(q),
    )


def _canonical_heloc_count_scope(question: str) -> bool:
    q = _normalized_question(question)
    if not _has_global_coverage_scope(q) or _has_unsupported_geo_scope(question, q):
        return False
    has_equity_capacity = any(
        term in q
        for term in ("heloc", "home equity", "equity line", "modeled equity", "equity capacity")
    ) or "borrower" in q
    asks_count = _has_count_intent(q)
    has_equity_threshold = "35" in q and "equity" in q
    return has_equity_capacity and asks_count and has_equity_threshold


def _canonical_listed_count_scope(question: str) -> CanonicalListedCountScope | None:
    q = _normalized_question(question)
    listed_terms = (
        "listed for sale",
        "listed-for-sale",
        "listed borrower",
        "listed borrowers",
        "listing",
        "listings",
        "mls",
        "for sale",
    )
    if not any(term in q for term in listed_terms):
        return None
    if not _has_count_intent(q):
        return None
    if _has_strong_rank_intent(q):
        return None
    if any(term in q for term in ("loan product", "days on market", "current rate", "average rate")):
        return None
    state_scope = _canonical_itm_state_scope(question)
    if state_scope is not None:
        return CanonicalListedCountScope(
            state_name=state_scope[0],
            state_code=state_scope[1],
        )
    if any(term in q for term in ("zip", "zipcode", "zip code", "county", "msa", "cbsa", "metro")):
        return None
    return CanonicalListedCountScope()


def _canonical_investor_count_scope(question: str) -> bool:
    q = _normalized_question(question)
    if _has_unsupported_geo_scope(question, q):
        return False
    investor_terms = ("investor", "investors", "multi-property", "multi property")
    if not any(term in q for term in investor_terms):
        return False
    if not _has_count_intent(q):
        return False
    return not _has_strong_rank_intent(q)


def _canonical_itm_share_scope(question: str) -> bool:
    q = _normalized_question(question)
    if _has_unsupported_geo_scope(question, q):
        return False
    return _has_itm_intent(q) and "borrower" in q and _has_share_intent(q)


def _canonical_home_equity_distribution_scope(question: str) -> bool:
    q = _normalized_question(question)
    if _has_unsupported_geo_scope(question, q):
        return False
    equity_terms = (
        "home equity",
        "modeled equity",
        "equity pct",
        "equity percent",
        "equity percentage",
        "equity distribution",
    )
    distribution_terms = (
        "distribution",
        "histogram",
        "bucket",
        "buckets",
        "band",
        "bands",
        "break down",
        "breakdown",
        "by equity",
        "by home equity",
    )
    return (
        ("equity" in q or any(term in q for term in equity_terms))
        and any(term in q for term in distribution_terms)
        and (
            any(term in q for term in ("borrower", "borrowers", "coverage", "portfolio", "population", "show"))
            or any(term in q for term in ("modeled equity", "home equity", "equity band", "equity bands"))
        )
    )


def _canonical_addressable_market_scope(question: str) -> bool:
    q = _normalized_question(question)
    if not _has_global_coverage_scope(q) or _has_unsupported_geo_scope(question, q):
        return False
    product_terms = (
        "heloc",
        "home equity",
        "in-the-money",
        "in the money",
        "refi",
        "refinance",
        "cash-out",
        "cash out",
        "listed",
        "listing",
        "permit",
        "investor",
        "retention",
    )
    return (
        "borrower" in q
        and (
            "addressable market" in q
            or "market size" in q
            or "marketable population" in q
            or (
                "eligible borrower" in q
                and not any(term in q for term in product_terms)
            )
        )
    )


def _canonical_ranked_lead_population_scope(question: str) -> bool:
    q = _normalized_question(question)
    if _has_unsupported_geo_scope(question, q):
        return False
    count_terms = ("how many", "count", "number of", "size")
    ranked_terms = (
        "ranked lead population",
        "ranked leads",
        "lead queue",
        "action ready lead",
        "action-ready lead",
    )
    return any(term in q for term in ranked_terms) and any(term in q for term in count_terms)


def _canonical_itm_city_scope(question: str) -> str | None:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"[-]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if "in the money" not in q or "borrower" not in q:
        return None
    if not any(term in q for term in ("how many", "count", "total number", "number of")):
        return None
    city_start = q.rfind(" in ")
    if city_start <= q.find("in the money"):
        return None
    city = q[city_start + 4 :].strip()
    city = re.sub(r"\b(?:right now|currently|today|this week|this month)\b.*$", "", city)
    city = city.strip()
    if not city:
        return None
    if re.match(r"\d", city):
        return None
    blocked_geo_terms = {"state", "states", "zip", "zips", "msa", "market", "markets", "county"}
    if any(term in city.split() for term in blocked_geo_terms):
        return None
    state_names = {name for name, _code in _US_STATE_FILTERS}
    state_codes = {code.lower() for _name, code in _US_STATE_FILTERS}
    city_terms = set(city.split())
    if city in state_names or city.lower() in state_codes:
        return None
    if city_terms & state_names or city_terms & state_codes:
        return None
    return " ".join(part.capitalize() for part in city.split())


def _canonical_msa_score_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    score_terms = (
        "lead score",
        "opportunity score",
        "avg score",
        "average score",
        "mean score",
        "mean lead score",
    )
    geo_terms = ("msa", "cbsa", "market", "markets")
    top_terms = ("top five", "top 5", "five markets", "5 markets")
    return (
        "compare" in q
        and any(term in q for term in score_terms)
        and any(term in q for term in geo_terms)
        and any(term in q for term in top_terms)
    )


def _canonical_itm_zip_scope(question: str) -> bool:
    q = _normalized_question(question)
    zip_terms = ("zip", "zips", "zipcode", "zipcodes", "zip code", "zip codes", "postal")
    rank_terms = (
        "top",
        "most",
        "highest",
        "rank",
        "ranked",
        "which",
        "show",
        "list",
        "break down",
        "by zip",
    )
    refi_terms = ("in-the-money", "in the money", "itm", "refi", "refinance")
    return (
        any(term in q for term in zip_terms)
        and any(term in q for term in rank_terms)
        and any(term in q for term in refi_terms)
        and any(term in q for term in ("borrower", "lead", "candidate", "loan officer", "savings"))
    )


def _canonical_itm_lead_queue_zip_scope(question: str) -> bool:
    q = _normalized_question(question)
    if not _canonical_itm_zip_scope(question):
        return False
    return any(
        term in q
        for term in (
            "lead queue",
            "loan officer",
            "lo ",
            "work first",
            "leads",
            "lead ",
            "actionable",
            "ranked",
        )
    )


def _canonical_itm_state_breakdown_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        _has_itm_intent(q)
        and any(term in q for term in ("borrower", "lead", "candidate", "segment"))
        and "state" in q
        and any(
            term in q for term in ("break down", "breakdown", "by state", "state by state", "table")
        )
    )


def _canonical_heloc_zip_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    if any(term in q for term in ("permit", "permits", "listing", "listings", "mls")):
        return False
    zip_terms = ("zip", "zips", "zipcode", "zipcodes", "zip code", "zip codes", "postal")
    rank_terms = ("top", "most", "highest", "rank", "ranked", "which", "show", "list", "by zip")
    heloc_terms = ("heloc", "home equity", "equity line", "modeled equity", "equity capacity")
    equity_terms = ("equity", "eligible", "eligibility", "candidate", "borrower", "lead")
    return (
        any(term in q for term in heloc_terms)
        and any(term in q for term in zip_terms)
        and any(term in q for term in rank_terms)
        and any(term in q for term in equity_terms)
    )


def _canonical_cash_out_state_scope(question: str) -> bool:
    q = _normalized_question(question)
    cash_out_terms = ("cash-out", "cash out", "cashout")
    rank_terms = ("top", "most", "highest", "rank", "ranked", "which", "show")
    return (
        any(term in q for term in cash_out_terms)
        and "state" in q
        and any(term in q for term in rank_terms)
    )


def _canonical_listed_purchase_scope(question: str) -> bool:
    q = _normalized_question(question)
    listed_terms = ("listed for sale", "listing", "listings", "mls", "for-sale")
    purchase_terms = (
        "purchase",
        "purchase financing",
        "next home",
        "buy next",
        "homebuy",
        "financing help",
    )
    return (
        any(term in q for term in listed_terms)
        and any(term in q for term in purchase_terms)
        and _has_rank_intent(q)
    )


def _canonical_refi_equity_signal_compare_scope(question: str) -> bool:
    q = _normalized_question(question)
    refi_terms = ("refi", "refinance", "rate-and-term", "rate and term")
    equity_terms = (
        "home equity",
        "heloc",
        "equity line",
        "cash-out",
        "cash out",
        "equity outreach",
    )
    comparison_terms = (
        "compare",
        "choose",
        "choosing",
        "decide",
        "deciding",
        "between",
        "which signals",
        "what signals",
        "signals should",
    )
    return (
        any(term in q for term in refi_terms)
        and any(term in q for term in equity_terms)
        and any(term in q for term in comparison_terms)
    )


def _canonical_refi_driver_scope(question: str) -> bool:
    q = _normalized_question(question)
    refi_terms = ("refi", "refinance", "rate refinance", "rate-and-term")
    driver_terms = (
        "driver",
        "drivers",
        "signal",
        "signals",
        "strongest",
        "why",
        "rationale",
        "what is driving",
        "what drives",
    )
    return (
        any(term in q for term in refi_terms)
        and any(term in q for term in driver_terms)
        and any(term in q for term in ("opportunity", "candidate", "borrower", "outreach", "right now"))
    )


def _canonical_itm_top_tier_compare_scope(question: str) -> bool:
    q = _normalized_question(question)
    has_itm = any(term in q for term in ("in-the-money", "in the money", "itm"))
    has_top_tier = any(
        term in q
        for term in (
            "top tier",
            "top-tier",
            "opportunity score",
            "score 75",
            "75+",
            "high intent",
            "high-intent",
        )
    )
    compare_terms = ("versus", "vs", "difference", "different", "same", "compare", "mean")
    return has_itm and has_top_tier and any(term in q for term in compare_terms)


def _canonical_strategy_board_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    spend_terms = ("spend", "allocate", "prioritize", "focus", "deploy")
    touch_terms = ("outreach touch", "outreach touches", "touches", "contacts", "campaign")
    strategy_terms = ("strategy", "where should", "which state", "which segment")
    has_touch_count = "10000" in q or "10 000" in q or "10k" in q
    return (
        any(term in q for term in spend_terms)
        and any(term in q for term in touch_terms)
        and (has_touch_count or any(term in q for term in strategy_terms))
    )


def _canonical_investor_segment_by_state_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s/-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    investor_terms = (
        "investor",
        "multi property",
        "multi-property",
        "multi property segment",
        "multi-property segment",
    )
    state_terms = ("state", "by state", "broken down", "breakdown", "break down")
    return (
        any(term in q for term in investor_terms)
        and "segment" in q
        and any(term in q for term in state_terms)
    )


def _canonical_top_borrowers_state_scope(question: str) -> tuple[str, str] | None:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    if _retention_competitor_lien_list_question(question):
        return None
    if _specific_top_borrower_intent(q) is not None:
        return None
    if not _has_rank_intent(q):
        return None
    if not any(term in q for term in ("borrower", "borrowers", "lead", "leads")):
        return None
    if not (
        any(term in q for term in ("lead score", "opportunity score", "score", "offer", "any offer"))
        or "best" in q
    ):
        return None
    return _canonical_itm_state_scope(question)


def _canonical_top_borrowers_global_scope(question: str) -> bool:
    q = _normalized_question(question)
    if _retention_competitor_lien_list_question(question):
        return False
    if not _has_global_coverage_scope(q):
        return False
    if _canonical_itm_state_scope(question) is not None:
        return False
    if _specific_top_borrower_intent(q) is not None:
        return False
    return (
        _has_rank_intent(q)
        and any(term in q for term in ("borrower", "borrowers", "lead", "leads"))
        and (
            any(term in q for term in ("lead score", "opportunity score", "score", "offer", "any offer"))
            or "best" in q
        )
    )


def _specific_top_borrower_intent(q: str) -> str | None:
    """Return an explicit borrower intent that must not be answered generically."""
    intents = _specific_top_borrower_intents(q)
    return intents[0] if intents else None


def _specific_top_borrower_intents(q: str) -> list[str]:
    """Return explicit borrower intents in the deterministic ranking order."""
    intents: list[str] = []

    def add(intent: str, predicate: bool) -> None:
        if predicate and intent not in intents:
            intents.append(intent)

    if any(term in q for term in ("cash-out", "cash out", "cashout")):
        add("cash_out", True)
    add(
        "heloc",
        any(
            term in q
            for term in (
                "heloc",
                "home equity",
                "equity line",
                "equity-line",
                "equity-credit",
                "permit",
                "permits",
            )
        ),
    )
    add(
        "listed",
        any(
            term in q
            for term in ("listed for sale", "listed-for-sale", "listing", "listings", "mls", "for sale")
        )
        or bool(re.search(r"\blisted\s+(borrowers?|leads?|candidates?)\b", q)),
    )
    add(
        "investor",
        any(term in q for term in ("investor", "multi-property", "multi property", "related property")),
    )
    add(
        "retention",
        any(term in q for term in ("retention", "recapture", "current customer", "former customer")),
    )
    add(
        "refi",
        _has_itm_intent(q) or any(term in q for term in ("refi", "refinance")),
    )
    return intents


def _specific_top_borrower_intent_label(intent: str) -> str:
    return {
        "cash_out": "cash-out refinance",
        "heloc": "home-equity / HELOC",
        "listed": "listed-for-sale purchase",
        "investor": "Investor / Multi-Property",
        "retention": "retention-risk",
        "refi": "Prime Refi Candidate",
    }.get(intent, "specific-intent")


def _specific_top_borrower_sort_label(intent: str) -> str:
    return {
        "cash_out": "estimated equity, then opportunity score",
        "heloc": "HELOC propensity, estimated equity, then opportunity score",
        "listed": "opportunity score among active listing signals",
        "investor": "related-property count, then opportunity score",
        "retention": "opportunity score, then rate spread",
        "refi": "opportunity score, then rate-spread economics",
    }.get(intent, "the governed borrower ranking")


def _specific_top_borrower_intent_note(question: str, selected_intent: str) -> str:
    intents = _specific_top_borrower_intents(_normalized_question(question))
    other_intents = [intent for intent in intents if intent != selected_intent]
    if not other_intents:
        return ""
    labels = ", ".join(_specific_top_borrower_intent_label(intent) for intent in other_intents)
    return (
        f" I detected additional intent language ({labels}) and used "
        f"{_specific_top_borrower_intent_label(selected_intent)} as the primary ranking lens; "
        "ask for a combined segment if you want an intersection."
    )


def _canonical_specific_top_borrowers_state_scope(question: str) -> tuple[str, str, str] | None:
    q = _normalized_question(question)
    if _retention_competitor_lien_list_question(question):
        return None
    if _canonical_listed_purchase_scope(question):
        return None
    if not _has_rank_intent(q):
        return None
    if not any(term in q for term in ("borrower", "borrowers", "lead", "leads", "candidate", "candidates")):
        return None
    intent = _specific_top_borrower_intent(q)
    if intent is None:
        return None
    state_scope = _canonical_itm_state_scope(question)
    if state_scope is None:
        return None
    state_name, state_code = state_scope
    return intent, state_name, state_code


def _canonical_specific_top_borrowers_global_scope(question: str) -> str | None:
    q = _normalized_question(question)
    if _retention_competitor_lien_list_question(question):
        return None
    if _canonical_listed_purchase_scope(question):
        return None
    if not _has_rank_intent(q):
        return None
    if not any(term in q for term in ("borrower", "borrowers", "lead", "leads", "candidate", "candidates")):
        return None
    if _canonical_itm_state_scope(question) is not None:
        return None
    if any(term in q for term in ("which state", "what state", "by state", "state by state", "state has", "states have")):
        return None
    return _specific_top_borrower_intent(q)


def _canonical_top_cash_out_by_equity_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("cash-out", "cash out", "cashout"))
        and any(term in q for term in ("top", "show", "list", "rank"))
        and "equity" in q
        and any(term in q for term in ("borrower", "candidate", "lead"))
    )


def _canonical_investor_top_by_related_property_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("investor", "multi-property", "multi property"))
        and any(term in q for term in ("related property", "property count", "properties"))
        and any(term in q for term in ("top", "show", "list", "rank"))
        and any(term in q for term in ("borrower", "borrowers", "masked"))
    )


def _canonical_mean_rate_spread_by_segment_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("mean rate spread", "average rate spread", "avg rate spread"))
        and "segment" in q
    )


def _canonical_segment_approval_rate_scope(question: str) -> bool:
    q = _normalized_question(question)
    return "segment" in q and "approval rate" in q and any(
        term in q for term in ("highest", "top", "rank", "which", "show")
    )


def _canonical_mean_lead_score_by_state_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("mean lead score", "average lead score", "avg lead score"))
        and "state" in q
        and any(term in q for term in ("compare", "break down", "breakdown", "by state"))
    )


def _canonical_evidence_events_yesterday_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        "evidence" in q
        and "event" in q
        and "yesterday" in q
        and any(term in q for term in ("trigger type", "signal type", "grouped", "by trigger"))
    )


def _canonical_lead_score_weekly_distribution_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        "lead score" in q
        and any(term in q for term in ("distribution", "avg", "average", "mean"))
        and any(term in q for term in ("this week", "week"))
        and any(term in q for term in ("last week", "prior week", "previous week"))
    )


def _canonical_approval_trend_30d_scope(question: str) -> bool:
    q = _normalized_question(question)
    return "approval" in q and "trend" in q and any(
        term in q for term in ("30 days", "last 30", "thirty days")
    )


def _canonical_evidence_events_quarter_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        "evidence" in q
        and "event" in q
        and any(term in q for term in ("quarter", "qtd", "this q"))
        and any(term in q for term in ("trigger type", "signal type", "grouped", "by trigger"))
    )


def _canonical_itm_offer_mix_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("offer mix", "recommended offer", "next best offer", "nbo"))
        and any(term in q for term in ("in-the-money", "in the money", "itm"))
        and "segment" in q
    )


def _projected_monthly_savings_gap_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("projected monthly savings", "monthly savings"))
        and any(term in q for term in ("trusted asset", "asset", "column", "approved refi"))
    )


def _canonical_heloc_recommendation_borrowers_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        "borrower" in q
        and any(term in q for term in ("heloc recommendation", "got a heloc", "recommended heloc"))
    )


def _canonical_listed_by_product_rate_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("listed-for-sale", "listed for sale", "listing"))
        and any(term in q for term in ("loan product", "product"))
        and any(term in q for term in ("average current rate", "avg current rate", "current rate"))
        and any(term in q for term in ("break down", "breakdown", "by"))
    )


def _canonical_listed_days_on_market_by_state_scope(question: str) -> bool:
    q = _normalized_question(question)
    listed_terms = ("listed-for-sale", "listed for sale", "listing", "listings", "mls")
    days_terms = (
        "days on market",
        "day on market",
        "listing days",
        "market days",
        "dom",
    )
    state_terms = ("by state", "state", "states")
    ranking_terms = (
        "top",
        "leading",
        "lead",
        "highest",
        "largest",
        "most",
        "break down",
        "breakdown",
    )
    return (
        any(term in q for term in listed_terms)
        and any(term in q for term in days_terms)
        and any(term in q for term in state_terms)
        and (
            any(term in q for term in ("average", "avg", "mean"))
            or any(term in q for term in ranking_terms)
        )
    )


def _canonical_lockin_size_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("lock-in cohort", "lock in cohort", "sub-3", "sub 3"))
        and any(term in q for term in ("how big", "how many", "count", "size"))
    )


def _canonical_lockin_median_rate_scope(question: str) -> bool:
    q = _normalized_question(question)
    return any(term in q for term in ("lock-in cohort", "lock in cohort")) and any(
        term in q for term in ("median rate", "median interest rate")
    )


def _canonical_lockin_by_state_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("lock-in cohort", "lock in cohort"))
        and "state" in q
        and any(term in q for term in ("break down", "breakdown", "by state", "state by state"))
    )


def _canonical_top_cohorts_scope(question: str) -> bool:
    q = _normalized_question(question)
    return (
        any(term in q for term in ("top cohorts", "top cohort", "largest cohorts", "top segments"))
        and not any(term in q for term in ("borrower", "masked borrower", "lead score"))
    )
