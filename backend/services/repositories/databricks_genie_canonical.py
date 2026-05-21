"""Canonical SQL and question-scope helpers for Databricks Genie answers."""
from __future__ import annotations

import re

from backend.services.databricks_sql_helpers import qualify

_BORROWER_360 = qualify("gold", "borrower_360")
_EVIDENCE_EVENTS = qualify("gold", "evidence_events")

_CANONICAL_ITM_COUNT_SQL = f"""
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE in_the_money = TRUE
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

_CANONICAL_HELOC_TOP_ZIPS_SQL = f"""
SELECT zip
     , state
     , COUNT(*) AS heloc_eligible_borrowers
     , CAST(ROUND(AVG(equity_pct), 1) AS DOUBLE) AS avg_equity_pct
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
     , MAX(refreshed_at) AS refreshed_at
FROM {_BORROWER_360}
WHERE equity_pct >= 35
  AND zip IS NOT NULL
  AND TRIM(zip) <> ''
GROUP BY zip, state
ORDER BY heloc_eligible_borrowers DESC, avg_equity_pct DESC, zip ASC
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

_US_STATE_FILTERS: tuple[tuple[str, str], ...] = (
    ("alabama", "AL"), ("alaska", "AK"), ("arizona", "AZ"), ("arkansas", "AR"),
    ("california", "CA"), ("colorado", "CO"), ("connecticut", "CT"), ("delaware", "DE"),
    ("florida", "FL"), ("georgia", "GA"), ("hawaii", "HI"), ("idaho", "ID"),
    ("illinois", "IL"), ("indiana", "IN"), ("iowa", "IA"), ("kansas", "KS"),
    ("kentucky", "KY"), ("louisiana", "LA"), ("maine", "ME"), ("maryland", "MD"),
    ("massachusetts", "MA"), ("michigan", "MI"), ("minnesota", "MN"),
    ("mississippi", "MS"), ("missouri", "MO"), ("montana", "MT"), ("nebraska", "NE"),
    ("nevada", "NV"), ("new hampshire", "NH"), ("new jersey", "NJ"),
    ("new mexico", "NM"), ("new york", "NY"), ("north carolina", "NC"),
    ("north dakota", "ND"), ("ohio", "OH"), ("oklahoma", "OK"), ("oregon", "OR"),
    ("pennsylvania", "PA"), ("rhode island", "RI"), ("south carolina", "SC"),
    ("south dakota", "SD"), ("tennessee", "TN"), ("texas", "TX"), ("utah", "UT"),
    ("vermont", "VT"), ("virginia", "VA"), ("washington", "WA"),
    ("west virginia", "WV"), ("wisconsin", "WI"), ("wyoming", "WY"),
)
_AMBIGUOUS_STATE_CODES: frozenset[str] = frozenset({"HI", "ID", "IN", "ME", "OH", "OK", "OR"})


def _ambiguous_state_code_match_is_contextual(
    question: str, match: re.Match[str]
) -> bool:
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
    blocked_geo_terms = {"state", "states", "zip", "zips", "msa", "market", "markets", "county"}
    if any(term in city.split() for term in blocked_geo_terms):
        return None
    state_names = {name for name, _code in _US_STATE_FILTERS}
    state_codes = {code.lower() for _name, code in _US_STATE_FILTERS}
    if city in state_names or city.lower() in state_codes:
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
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
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
        and any(term in q for term in ("borrower", "lead", "candidate"))
    )


def _canonical_heloc_zip_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    if any(term in q for term in ("permit", "permits", "listing", "listings", "mls")):
        return False
    zip_terms = ("zip", "zips", "zipcode", "zipcodes", "zip code", "zip codes", "postal")
    rank_terms = ("top", "most", "highest", "rank", "ranked", "which", "show", "list", "by zip")
    heloc_terms = ("heloc", "home equity", "equity line")
    equity_terms = ("equity", "eligible", "eligibility", "candidate", "borrower", "lead")
    return (
        any(term in q for term in heloc_terms)
        and any(term in q for term in zip_terms)
        and any(term in q for term in rank_terms)
        and any(term in q for term in equity_terms)
    )


def _canonical_cash_out_state_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    cash_out_terms = ("cash-out", "cash out", "cashout")
    rank_terms = ("top", "most", "highest", "rank", "ranked", "which", "show")
    return (
        any(term in q for term in cash_out_terms)
        and "state" in q
        and any(term in q for term in rank_terms)
    )
