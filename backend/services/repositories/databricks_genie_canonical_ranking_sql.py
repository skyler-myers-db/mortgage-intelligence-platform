"""Canonical borrower-ranking SQL: the per-intent shapes, the shared
economics-rich SELECT, and the generated dicts that replace them."""

from __future__ import annotations

from backend.services.eligibility import eligible_sql_predicate
from backend.services.repositories.databricks_genie_canonical_sql import (
    _B_ELIGIBLE,
    _BORROWER_360,
    _ELIGIBLE,
    _LEAD_POPULATION,
)

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

# Driver-rich variant of the global top-borrowers ranking for "top candidates
# across all segments + what makes each one strong + which offer" questions.
# Carries the raw economics (rate, balance, home value), the behavioral
# signals, and the run-specific policy thresholds so the analyst brief can
# interpret every number in plain language, plus a SQL-computed ``why_now``
# driver summary for the table view — all grounded in gold columns, never
# model prose.
_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL = f"""
SELECT borrower_id
     , display_name
     , city
     , state
     , zip
     , array_join(segment_codes, ', ') AS segments
     , opportunity_score
     , rate_spread_bps
     , equity_pct
     , equity_estimate
     , current_rate
     , current_lien_balance
     , avm_value
     , in_the_money
     , listed_for_sale
     , listing_status_category
     , related_property_count
     , heloc_propensity_score
     , has_heloc_propensity_trigger
     , is_current_customer
     , min_spread_bps_applied
     , min_equity_pct_applied
     , heloc_equity_min_applied
     , cashout_equity_min_applied
     , concat_ws(' | ',
         CASE
           WHEN in_the_money = TRUE AND rate_spread_bps IS NOT NULL
           THEN concat('In the money: +', CAST(CAST(ROUND(rate_spread_bps, 0) AS BIGINT) AS STRING), ' bps rate spread')
         END,
         CASE
           WHEN equity_pct IS NOT NULL AND equity_pct >= 35
           THEN concat('Strong equity: ', CAST(CAST(ROUND(equity_pct, 0) AS BIGINT) AS STRING), '%')
         END,
         CASE WHEN listed_for_sale = TRUE THEN 'Listed for sale' END,
         CASE
           WHEN related_property_count IS NOT NULL AND related_property_count >= 2
           THEN concat('Investor: ', CAST(related_property_count AS STRING), ' related properties')
         END,
         CASE WHEN array_contains(segment_codes, 'retention') THEN 'Retention / recapture risk' END,
         CASE WHEN has_heloc_propensity_trigger = TRUE THEN 'HELOC propensity trigger' END
       ) AS why_now
     , recommended_offer_code
     , recommended_offer
     , refreshed_at
FROM {_BORROWER_360}
WHERE {_ELIGIBLE}
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, rate_spread_bps DESC NULLS LAST, borrower_id ASC
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

# ---------------------------------------------------------------------------
# Teaching-analyst ranking SQL (2026-08-06). Every borrower-level ranking
# shape shares one economics-rich SELECT so the analyst brief can interpret
# each candidate in dollars, rates, and behavioral signals with the
# run-specific policy thresholds. The generated dicts below REPLACE the
# per-intent constants above at import time; the legacy constants remain only
# as documentation of each shape's cohort predicate and ordering.
# ---------------------------------------------------------------------------
_RANKING_SELECT_COLUMNS = """b.borrower_id
     , b.display_name
     , b.city
     , b.state
     , b.zip
     , array_join(b.segment_codes, ', ') AS segments
     , b.opportunity_score
     , b.rate_spread_bps
     , b.equity_pct
     , b.equity_estimate
     , b.current_rate
     , b.current_lien_balance
     , b.avm_value
     , b.in_the_money
     , b.listed_for_sale
     , b.listing_status_category
     , b.related_property_count
     , b.heloc_propensity_score
     , b.has_heloc_propensity_trigger
     , b.is_current_customer
     , b.min_spread_bps_applied
     , b.min_equity_pct_applied
     , b.heloc_equity_min_applied
     , b.cashout_equity_min_applied
     , b.recommended_offer_code
     , b.recommended_offer
     , b.refreshed_at"""


def _borrower_ranking_sql(where: str, order: str, *, state_scoped: bool) -> str:
    scope = "b.state = :state\n  AND " if state_scoped else ""
    return (
        f"SELECT {_RANKING_SELECT_COLUMNS}\n"
        f"FROM {_BORROWER_360} AS b\n"
        f"WHERE {scope}{where}\n"
        f"  AND {_B_ELIGIBLE}\n"
        "  AND b.consent_status = 'opt_in'\n"
        f"ORDER BY {order}\n"
        "LIMIT 10"
    )


_INTENT_RANKING_SPECS: dict[str, tuple[str, str]] = {
    "refi": (
        "b.in_the_money = TRUE",
        "b.opportunity_score DESC, b.rate_spread_bps DESC, b.borrower_id ASC",
    ),
    "cash_out": (
        "b.recommended_offer_code = 'cash_out'",
        "b.equity_estimate DESC, b.opportunity_score DESC, b.borrower_id ASC",
    ),
    "heloc": (
        "(b.recommended_offer_code IN ('heloc', 'refi_plus_heloc')"
        " OR b.has_heloc_propensity_trigger = TRUE"
        " OR array_contains(b.segment_codes, 'permit'))",
        "b.heloc_propensity_score DESC NULLS LAST, b.equity_estimate DESC, "
        "b.opportunity_score DESC, b.borrower_id ASC",
    ),
    "listed": (
        "b.listed_for_sale = TRUE",
        "b.opportunity_score DESC, b.borrower_id ASC",
    ),
    "investor": (
        "(array_contains(b.segment_codes, 'investor') OR b.is_investor = TRUE)",
        "b.related_property_count DESC NULLS LAST, b.opportunity_score DESC, b.borrower_id ASC",
    ),
    "retention": (
        "array_contains(b.segment_codes, 'retention')",
        "b.opportunity_score DESC, b.rate_spread_bps DESC, b.borrower_id ASC",
    ),
}

_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL = {
    intent: _borrower_ranking_sql(where, order, state_scoped=True)
    for intent, (where, order) in _INTENT_RANKING_SPECS.items()
}
_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL = {
    intent: _borrower_ranking_sql(where, order, state_scoped=False)
    for intent, (where, order) in _INTENT_RANKING_SPECS.items()
}
_CANONICAL_LISTED_PURCHASE_TOP_SQL = _borrower_ranking_sql(
    _INTENT_RANKING_SPECS["listed"][0],
    _INTENT_RANKING_SPECS["listed"][1],
    state_scoped=False,
)

# Lead-Queue rankings keep mip.gold.lead_population as the source of the
# ranked cohort (its rank/score are the operational truth) and join
# borrower_360 for the economics the analyst brief interprets.
_LEAD_QUEUE_RANKING_SQL_TEMPLATE = (
    f"SELECT {_RANKING_SELECT_COLUMNS}\n"
    "     , lp.rank_overall\n"
    f"FROM {_LEAD_POPULATION} AS lp\n"
    f"JOIN {_BORROWER_360} AS b ON b.borrower_id = lp.borrower_id\n"
    "WHERE {scope}\n"
    "ORDER BY lp.opportunity_score DESC, lp.rank_overall ASC, lp.borrower_id ASC\n"
    "LIMIT 10"
)
_CANONICAL_TOP_BORROWERS_BY_STATE_SQL = _LEAD_QUEUE_RANKING_SQL_TEMPLATE.format(
    scope="lp.state = :state"
)
_CANONICAL_TOP_BORROWERS_GLOBAL_SQL = _LEAD_QUEUE_RANKING_SQL_TEMPLATE.format(
    scope=eligible_sql_predicate("lp")
)
