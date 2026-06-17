"""Canonical SQL and question-scope helpers for Databricks Genie answers."""

from __future__ import annotations

import re

from backend.services.databricks_sql_helpers import qualify

_BORROWER_360 = qualify("gold", "borrower_360")
_EVIDENCE_EVENTS = qualify("gold", "evidence_events")
_LEAD_POPULATION = qualify("gold", "lead_population")
_SEGMENT_POPULATION = qualify("gold", "segment_population")

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
WHERE equity_pct > 35
""".strip()

_CANONICAL_ADDRESSABLE_MARKET_SQL = f"""
SELECT COUNT(*) AS eligible_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_LEAD_POPULATION}
WHERE marketing_eligible = TRUE
  AND consent_status = 'opt_in'
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
FROM {_LEAD_POPULATION}
WHERE array_contains(segment_codes, 'itm')
  AND marketing_eligible = TRUE
  AND consent_status = 'opt_in'
  AND zip IS NOT NULL
  AND TRIM(zip) <> ''
GROUP BY zip, state
ORDER BY in_the_money_borrowers DESC, avg_score DESC, zip ASC
LIMIT 10
""".strip()

_CANONICAL_ITM_BY_STATE_SQL = f"""
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
ORDER BY in_the_money_borrowers DESC, avg_score DESC, state ASC
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
  AND marketing_eligible = TRUE
  AND consent_status = 'opt_in'
ORDER BY opportunity_score DESC, borrower_id ASC
LIMIT 10
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
WHERE marketing_eligible = TRUE
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
WHERE b.marketing_eligible = TRUE
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
     , CAST(COUNT_IF(opportunity_score >= 75) AS BIGINT) AS top_tier_borrowers
     , CAST(COUNT_IF(in_the_money = TRUE AND opportunity_score >= 75) AS BIGINT)
         AS overlap_borrowers
     , CAST(ROUND(AVG(CASE WHEN in_the_money = TRUE THEN rate_spread_bps END), 1) AS DOUBLE)
         AS avg_in_the_money_rate_spread_bps
     , CAST(ROUND(AVG(CASE WHEN opportunity_score >= 75 THEN opportunity_score END), 1) AS DOUBLE)
         AS avg_top_tier_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE marketing_eligible = TRUE
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
  WHERE marketing_eligible = TRUE
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
        re.search(r"\b(which|show|list|find|who are|give me)\b", q)
        and re.search(r"\bborrowers?\b", q)
    )
    retention_scope = bool(
        re.search(
            r"\b(retention list|retention cohort|retention-risk|retention risk|recapture)\b",
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
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    if not any(phrase in q for phrase in ("in-the-money", "in the money")):
        return False
    if "borrower" not in q:
        return False
    if not any(term in q for term in ("how many", "count", "total number", "number of")):
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
        "list",
        "zip",
        "county",
        "msa",
        "market",
        "average",
        "avg",
        "mean",
    )
    if any(term in q for term in breakdown_terms):
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
        " in teh money": " in the money",
        " rn ": " right now ",
        "avg": "average",
    }
    for needle, replacement in replacements.items():
        q = q.replace(needle, replacement)
    return re.sub(r"\s+", " ", q).strip()


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
        "break down",
        "breakdown",
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
    has_itm = any(term in q for term in ("in-the-money", "in the money", "itm"))
    asks_count = any(term in q for term in ("how many", "count", "number of", "total"))
    asks_spread = (
        ("rate spread" in q or "spread" in q)
        and any(term in q for term in ("average", "avg", "mean"))
    )
    return has_itm and "borrower" in q and asks_count and asks_spread


def _canonical_heloc_count_scope(question: str) -> bool:
    q = _normalized_question(question)
    if not _has_global_coverage_scope(q) or _has_unsupported_geo_scope(question, q):
        return False
    has_equity_capacity = any(
        term in q
        for term in ("heloc", "home equity", "equity line", "modeled equity", "equity capacity")
    ) or "borrower" in q
    asks_count = any(term in q for term in ("how many", "count", "number of", "total"))
    has_equity_threshold = "35" in q and "equity" in q
    return has_equity_capacity and asks_count and has_equity_threshold


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


def _canonical_itm_state_breakdown_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    return (
        any(term in q for term in ("in-the-money", "in the money", "itm", "refi", "refinance"))
        and any(term in q for term in ("borrower", "lead", "candidate"))
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
    rank_terms = ("top", "rank", "ranked", "which", "show", "list", "first", "prioritize")
    return (
        any(term in q for term in listed_terms)
        and any(term in q for term in purchase_terms)
        and any(term in q for term in rank_terms)
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
    if not any(term in q for term in ("top", "highest", "rank", "ranked", "show", "list")):
        return None
    if not any(term in q for term in ("borrower", "borrowers", "lead", "leads")):
        return None
    if not any(term in q for term in ("lead score", "opportunity score", "score")):
        return None
    return _canonical_itm_state_scope(question)
