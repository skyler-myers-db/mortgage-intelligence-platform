"""Canonical scope dataclasses, question normalizers, intent predicates, and the
US state vocabulary they classify against."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.services.repositories.databricks_genie_canonical_ranking_sql import (
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL,
)


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
