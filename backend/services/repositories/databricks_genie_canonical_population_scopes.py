"""Population, geography, and cohort-count scope classifiers for Genie questions."""

from __future__ import annotations

import re

from backend.services.repositories.databricks_genie_canonical_scopes import (
    _US_STATE_FILTERS,
    CanonicalEquityThresholdScope,
    CanonicalListedCountScope,
    CanonicalNegativeEquityScope,
    _canonical_itm_state_scope,
    _has_count_intent,
    _has_equity_share_result_intent,
    _has_global_coverage_scope,
    _has_itm_intent,
    _has_rank_intent,
    _has_share_intent,
    _has_strong_rank_intent,
    _has_unsupported_geo_scope,
    _normalized_question,
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
    # "call/contact N borrowers — which segments, states, offers?" is the
    # same capacity-allocation ask as "spend N outreach touches"; both route
    # to the state-segment-offer strategy board.
    spend_terms = ("spend", "allocate", "prioritize", "focus", "deploy", "call", "contact", "reach")
    touch_terms = (
        "outreach touch",
        "outreach touches",
        "touches",
        "contacts",
        "campaign",
        "borrowers",
        "leads",
        "calls",
        "people",
    )
    strategy_terms = ("strategy", "where should", "which state", "which segment")
    has_touch_count = bool(re.search(r"\b\d{2,7}\b", q)) or "10k" in q
    return (
        any(term in q for term in spend_terms)
        and any(term in q for term in touch_terms)
        and (has_touch_count or any(term in q for term in strategy_terms))
        and any(term in q for term in (*strategy_terms, "offer", "offers", "touches", "campaign"))
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
