"""Five legitimate analytics phrasings refused as ``unreviewed_criterion``.

Each was reproduced offline at ``protected_prompt_match`` on main at b1f58bcf
(2026-09-08) and bisected to a minimal passing twin. Two producers were
involved. The audience-decision tail reads a population noun bound to a
criterion connector behind an open lead-in, and it fired on the verb in "lead
with", on "borrowers by opportunity score", and on "customers with retention
signals compare ...". The bound-population capture reads any formation verb
followed within 80 characters by a population noun, and it fired on the NOUN
"move" ("the best move for a lender given these borrowers") and on the
INTRANSITIVE "moved" ("the funnel moved ... across lead population").

Every fix is a closed shape on the allow side of the reviewed read-only
analytics grammar. Neither net was touched, so the controls below pin that
the same carriers with an unreviewed or health term still refuse, and the
layer test pins that each phrasing is admitted BY that grammar rather than by
a relaxed net.
"""

from __future__ import annotations

import pytest

from backend.schemas.marketing_selection_criteria import (
    is_reviewed_read_only_analytics_text,
)
from backend.services.genie_message_policy import protected_prompt_match

_REPORTED = (
    # 1. The idiom "lead with" read as the population noun ``lead`` + ``with``.
    "what offer should we lead with?",
    # 2. Ranked-by-score cohort with the per-row rationale tail.
    "Show the top 15 borrowers by opportunity score and explain why each one is a strong candidate.",
    # 3. Cohort-versus-book comparison on governed measures.
    "how do our current customers with retention signals compare with the rest of our customers on rate spread and equity?",
    # 4. The NOUN "move" read as the formation verb.
    "what is the best move for a lender given these borrowers will not rate-and-term refi",
    # 5. The INTRANSITIVE "moved" read as the formation verb, both tails.
    "Do a comprehensive review of how the funnel moved over the last 30 days across lead population, approvals, and outreach.",
    "Do a comprehensive review of how the funnel moved over the last 30 days, and which segments and states drove the change.",
)

_SIBLINGS = (
    "what offer should we lead with, and why?",
    "Which next-best offer should we recommend?",
    "Rank the top 15 borrowers by opportunity score and explain why each one is a strong candidate.",
    "Show the top borrowers ranked by lead score and rate spread and explain why each one qualifies.",
    # Same vocabulary gap as case 3: a segment SIGNAL is the product's own noun.
    "show customers with retention signals",
    "show customers with a retention signal",
    "how do our in-the-money borrowers compare to the rest of the book on equity?",
    "what's the right move for a lender when those customers won't refinance",
    "how has the funnel moved over the last 30 days across lead population, approvals, and outreach?",
    "Explain how our lead funnel shifted this month, and which states drove the change.",
)

_PASSING_TWINS = (
    "which offer should we recommend first and why?",
    "What are the top borrower candidates across all segments overall, what makes each one a strong candidate, and which offer should we make to each and why?",
    "how do current customers in the retention segment compare with the rest of the book on rate spread and equity?",
    "what is the best offer for a lender since it will not rate-and-term refinance",
    "Do a comprehensive review of how the funnel moved over the last 30 days",
    "Do a comprehensive review of how the funnel changed over the last 30 days, and which segments and states drove the change.",
)


@pytest.mark.parametrize("question", _REPORTED)
def test_reported_phrasings_are_answered(question: str) -> None:
    assert protected_prompt_match(question) is None, question


@pytest.mark.parametrize("question", _SIBLINGS)
def test_sibling_phrasings_share_the_closed_shape(question: str) -> None:
    assert protected_prompt_match(question) is None, question


@pytest.mark.parametrize("question", _PASSING_TWINS)
def test_bisection_twins_keep_answering(question: str) -> None:
    assert protected_prompt_match(question) is None, question


@pytest.mark.parametrize("question", _REPORTED + _SIBLINGS)
def test_each_phrasing_is_admitted_by_the_analytics_grammar(question: str) -> None:
    """The fix lives on the allow side: every phrasing is a closed read-only
    analytics clause. If this goes red while the verdict stays None, a net was
    relaxed instead."""

    assert is_reviewed_read_only_analytics_text(question), question


@pytest.mark.parametrize(
    "question",
    [
        # The standing unreviewed controls.
        "Rank the top borrowers with eczema",
        "Rank the top zyrplax borrowers",
        # Case 1 carriers: the offer question cannot tow a criterion.
        "what offer should we lead with for borrowers with eczema?",
        "what offer should we lead with for zyrplax borrowers?",
        "what should we lead with for the lead with zyrplax?",
        # Case 2 carriers: the ranking signal and the rationale tail are closed.
        "Show the top 15 borrowers by zyrplax and explain why each one is a strong candidate.",
        "Show the top 15 borrowers by eczema and explain why each one is a strong candidate.",
        "Show the top 15 borrowers by opportunity score and eczema and explain why each one is a strong candidate.",
        "Show the top 15 borrowers by opportunity score and explain why each one has eczema.",
        # Case 3 carriers: the with-clause, the baseline and the measure list.
        "how do our current customers with zyrplax signals compare with the rest of our customers on rate spread?",
        "how do our current customers with eczema compare with the rest of our customers on rate spread and equity?",
        "how do our current customers with retention signals compare with the rest of our customers on zyrplax?",
        "how do our current customers with retention signals compare with the rest of our customers on eczema?",
        "show customers with zyrplax signals",
        "show customers with eczema signals",
        # Case 4 carriers: the negated outcome must be a closed product intent.
        "what is the best move for a lender given these borrowers will not take zyrplax",
        "what is the best move for a lender given these zyrplax borrowers will not refinance",
        "what is the best move for a lender given these borrowers have eczema",
        # Case 5 carriers: stage list and driver dimensions are closed.
        "Do a comprehensive review of how the funnel moved over the last 30 days across eczema patients.",
        "Do a comprehensive review of how the funnel moved over the last 30 days across zyrplax leads.",
        "Do a comprehensive review of how the funnel moved over the last 30 days, and which zyrplax segments drove the change.",
        "Move the eczema borrowers over the last 30 days into the campaign.",
        "Move the zyrplax borrowers into the campaign.",
    ],
)
def test_unreviewed_and_health_carriers_still_refuse(question: str) -> None:
    assert protected_prompt_match(question) is not None, question
