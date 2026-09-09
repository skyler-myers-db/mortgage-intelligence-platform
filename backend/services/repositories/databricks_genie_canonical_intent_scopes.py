"""Top-borrower, specific-intent, segment-performance, and metric scope
classifiers for Genie questions."""

from __future__ import annotations

import re

from backend.services.repositories.databricks_genie_canonical_population_scopes import (
    _canonical_listed_purchase_scope,
)
from backend.services.repositories.databricks_genie_canonical_scopes import (
    _canonical_itm_state_scope,
    _has_global_coverage_scope,
    _has_itm_intent,
    _has_rank_intent,
    _normalized_question,
    _retention_competitor_lien_list_question,
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


_ALL_SEGMENTS_SCOPE_TERMS = (
    "across all segments",
    "across segments",
    "across every segment",
    "all segments",
    "every segment",
    "across the portfolio",
    "entire portfolio",
    "whole portfolio",
    "portfolio-wide",
    "portfolio wide",
)

# "Why" language that asks for per-borrower rationale, not just a ranking.
_TOP_BORROWER_WHY_TERMS = (
    "why",
    "what makes",
    "reason",
    "rationale",
    "explain",
    "driver",
    "justif",
)


def _has_all_segments_scope(q: str) -> bool:
    return any(term in q for term in _ALL_SEGMENTS_SCOPE_TERMS)


def _canonical_top_borrowers_global_scope(question: str) -> bool:
    q = _normalized_question(question)
    if _retention_competitor_lien_list_question(question):
        return False
    if not (_has_global_coverage_scope(q) or _has_all_segments_scope(q)):
        return False
    if _canonical_itm_state_scope(question) is not None:
        return False
    if _specific_top_borrower_intent(q) is not None:
        return False
    return (
        _has_rank_intent(q)
        and any(
            term in q
            for term in ("borrower", "borrowers", "lead", "leads", "candidate", "candidates")
        )
        and (
            any(
                term in q
                for term in (
                    "lead score",
                    "opportunity score",
                    "score",
                    "offer",
                    "any offer",
                    "candidate",
                    "candidates",
                )
            )
            or "best" in q
        )
    )


def _canonical_top_borrowers_all_segments_scope(question: str) -> bool:
    """Global candidate ranking that also asks WHY each borrower is strong.

    Matches "top borrower candidates across all segments — what makes each one
    a good candidate and which offer should we make?"-family questions. The
    per-intent and per-state scopes stay in charge of their narrower shapes.
    """
    q = _normalized_question(question)
    if not _canonical_top_borrowers_global_scope(question):
        return False
    if _canonical_listed_purchase_scope(question):
        return False
    return any(term in q for term in _TOP_BORROWER_WHY_TERMS)


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


# Columns `mip.semantics.segment_performance_metric_view` actually carries, in
# the words a user reaches for. The view is one row per segment with
# approval_rate, outreach_rate, count and avg_score, so it answers the whole
# "compare our segments" family from a single statement.
_SEGMENT_PERFORMANCE_METRIC_TERMS = (
    "approval",
    "approve",
    "convert",
    "conversion",
    "outreach",
    "opportunity score",
    "lead score",
    # NOT "avg score": ``_normalized_question`` rewrites "avg" to "average"
    # before this list is consulted, so that term could never match.
    "average score",
    "perform",
    "response rate",
    "win rate",
    # The view carries `count AS segment_borrowers`, so size comparisons are
    # answerable from the same statement.
    "size",
    "sizes",
    "how many borrowers",
    "borrower count",
    "largest",
    "smallest",
)
# Metrics the view does NOT carry. Naming one means the question belongs to a
# different statement (rate spread has its own) or to Genie. Standing aside is
# the whole point: answering a rate-spread question with approval-rate columns
# would be a confident wrong answer, which is worse than no rescue.
_SEGMENT_PERFORMANCE_FOREIGN_TERMS = (
    "rate spread",
    "spread",
    "equity",
    "ltv",
    "balance",
    "income",
    "delinquen",
    "days on market",
    "permit",
    "listing",
)
_SEGMENT_COMPARISON_TERMS = (
    "which",
    # NOT "what": it is the most common English question word, not a
    # comparison. With it in the list, "What is the average opportunity score
    # for the retention segment?" -- a question about ONE segment -- was served
    # the whole-portfolio ranking of all six.
    "highest",
    "lowest",
    "best",
    "worst",
    "top",
    "rank",
    "compare",
    "comparison",
    "versus",
    " vs ",
    "better",
    "across",
)

# The statement this predicate serves is ONE ROW PER SEGMENT, nationally
# (`WHERE state = '_ALL'`), at the current snapshot. It therefore cannot answer
# a question that varies along any other axis, and the failure is silent: the
# answer looks authoritative and is about a different population.
#
# Adversarial review 2026-08-11 measured all three, each of which turned a
# refusal into a confidently wrong answer:
#   * "Which segment performs best in California?"        -> national figures
#   * "Show me the top 10 borrowers by lead score in each segment" -> 6 rows
#   * "Which segment converted best last quarter?"        -> today's snapshot
_SEGMENT_PERFORMANCE_OFF_AXIS_TERMS = (
    # Geography — the view HAS a state dimension, but this statement pins
    # `_ALL`. Standing aside is honest; scoping it is a separate change.
    " in ca",
    " in tx",
    " in il",
    " in fl",
    " in wa",
    " in co",
    "state",
    "states",
    "city",
    "cities",
    "zip",
    "county",
    "market",
    "markets",
    "msa",
    "metro",
    "region",
    "california",
    "texas",
    "illinois",
    "florida",
    "washington",
    "colorado",
    # Grain — one row per SEGMENT, never per borrower.
    "borrower id",
    "borrowers by",
    "top 10 borrowers",
    "top 20 borrowers",
    "top 5 borrowers",
    "each borrower",
    "individual borrower",
    "list borrowers",
    "show me borrowers",
    "which borrowers",
    # Time — the view is the current snapshot, with no period selector.
    "last quarter",
    "last month",
    "last year",
    "this quarter",
    "this month",
    "year over year",
    "yoy",
    "trend",
    "over time",
    "since",
    "january",
    "february",
    "march",
    "april",
    " may ",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


def _canonical_segment_performance_rescue_scope(question: str) -> bool:
    """Comparative "how do our segments stack up" questions, for RESCUE ONLY.

    Deliberately broader than ``_canonical_segment_approval_rate_scope`` and
    deliberately NOT wired into ``direct_canonical_response``. The direct path
    PREEMPTS the live Genie turn, and the live-first doctrine reserves that for
    narrow count prompts -- overlaying a good live turn is prohibited. This
    predicate runs only after Genie has already failed to return trusted SQL,
    where the alternative is not a live answer but a refusal.

    Measured live on paychex 2026-08-11: "Which segment converts best: HELOC,
    cash-out, or retention?" returned `sql_query: null` from Genie, so the app
    refused -- while `_CANONICAL_SEGMENT_APPROVAL_RATE_SQL` had the answer the
    whole time. The old matcher required the literal words "approval rate"; the
    user said "converts best".

    Strict on the METRIC, loose on the PHRASING: the view's columns are a
    closed set, so a question naming a metric it does not carry gets no rescue
    rather than a confidently wrong one.
    """

    q = _normalized_question(question)
    if "segment" not in q:
        return False
    if any(term in q for term in _SEGMENT_PERFORMANCE_FOREIGN_TERMS):
        return False
    # Metric is only one of four axes. The statement is national, per-segment
    # and current, so a question that moves along geography, grain or time is
    # asking something this rescue cannot answer.
    if any(term in f" {q} " for term in _SEGMENT_PERFORMANCE_OFF_AXIS_TERMS):
        return False
    return any(term in q for term in _SEGMENT_PERFORMANCE_METRIC_TERMS) and any(
        term in q for term in _SEGMENT_COMPARISON_TERMS
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
