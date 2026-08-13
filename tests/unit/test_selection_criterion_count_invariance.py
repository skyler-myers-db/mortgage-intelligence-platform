"""A count in a directive changed whether the audience guard ran at all.

Two grammars in ``marketing_selection_criteria`` matched the words before a
population noun with a letters-only class -- the reviewed-directive lead-in
``(?:[a-z][a-z'-]*\\s+){1,10}`` and the ``is_population_directive`` prefix
``[A-Za-z][A-Za-z' -]{0,100}``. A digit matched neither, so writing "the top
10 borrowers" instead of "the top borrowers" silently took BOTH grammars out
of the decision, in opposite and equally wrong directions:

* the reviewed directive stopped being recognized as reviewed, and
* the population directive stopped being recognized as a directive, so
  "Identify the top 10 borrowers with eczema." bound a health predicate to a
  population and was never refused.

Only SOME counts were affected, which is why one narrow case looked covered.
The leetspeak fold rewrote 1/0/3 into letters before these grammars ran, so
"top 10" and "top 13" arrived spelled "top lo" and "top le" and behaved
correctly, while "top 20" -- whose ``2`` has no letter reading -- was an open
hole. ``test_deep_analysis_question_family`` pinned "top 10" and therefore
passed on the fold, not on the grammar. Stopping the fold from firing on bare
numbers (see :func:`in_word_leet_folds`, added for an unrelated defect in the
same release) is what surfaced it.

The property these tests pin is COUNT-INVARIANCE: a directive must reach the
same decision with and without a count, because a count is orthography, not a
selection criterion. That is strictly stronger than pinning the two strings
that happened to be reported -- it is what makes "top 20", "best 25" and
"first 5" correct for the same reason as "top 10".

The property holds for a count written as ONE alphanumeric token, which is
every form the product actually produces, including the fused ones
(``top-10``, ``30-year``, ``2nd``, ``10k``, ``Q3``, ``203k``). It does NOT
hold for a count carrying a comma or a percent: ``top 1,000`` and ``top 10%``
sit outside both grammars, so they are neither recognized as reviewed nor
refused as a directive. That asymmetry predates this work and is unchanged by
it -- both forms behave identically at 7612b021 -- and closing it means
widening the refusing prefix too, which needs its own false-positive sweep.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.marketing_selection_criteria import (
    _contains_unreviewed_audience_decision,
)

# (with_count, without_count). Both members must reach the SAME decision.
_COUNT_PAIRS = (
    (
        "Identify the top 10 borrowers with eczema",
        "Identify the top borrowers with eczema",
    ),
    (
        "Identify the top 20 borrowers with eczema",
        "Identify the top borrowers with eczema",
    ),
    (
        "Rank the best 25 borrowers with diabetes",
        "Rank the best borrowers with diabetes",
    ),
    (
        "Pick the first 5 borrowers with asthma",
        "Pick the first borrowers with asthma",
    ),
    (
        "identify the top 10 borrowers with the highest potential",
        "identify the top borrowers with the highest potential",
    ),
    (
        "identify the top 100 borrowers with the highest potential",
        "identify the top borrowers with the highest potential",
    ),
    # Counts fused to a letter. A lead-in built from letters-OR-digits
    # alternatives cannot match these at all, so the reviewed directive stops
    # being recognized and refuses while its spaced form is allowed.
    (
        "identify the top-10 borrowers with the highest potential",
        "identify the top borrowers with the highest potential",
    ),
    (
        "Identify the top-10 borrowers with eczema",
        "Identify the top borrowers with eczema",
    ),
    (
        "Identify the top-25 borrowers with diabetes",
        "Identify the best borrowers with diabetes",
    ),
)

# Mortgage prose is full of tokens that fuse digits to letters. Every one of
# these refused at the count-invariance commit while its spaced or unnumbered
# form was allowed; ``30-year`` is the most common adjective in the domain and
# would have reached a lender demo. Found by adversarial review, 2026-08-12.
_MIXED_TOKEN_DIRECTIVES = (
    "Show me the top-10 borrowers with the highest potential",
    "Show me 30-year fixed borrowers with strong equity",
    "Show me 2nd lien borrowers with strong equity",
    "Show me the top 10k borrowers with strong equity",
    "Show me Q3 borrowers with strong equity",
    "Show me FHA 203k borrowers with strong equity",
)

# Health targeting behind a count. Every one is refused only because the
# population-directive prefix admits digits.
_MUST_REFUSE = (
    "Identify the top 10 borrowers with eczema",
    "Identify the top-10 borrowers with eczema",
    "Identify the top-25 borrowers with diabetes",
    "Identify the top 20 borrowers with eczema",
    "Rank the best 25 borrowers with diabetes",
    "Pick the first 5 borrowers with asthma",
)

# The product's own question. Refused if the reviewed lead-in stops admitting
# the count -- this is the half that keeps the fix from being a blunt
# "digits mean danger" rule.
_MUST_ALLOW = (
    "identify the top 10 borrowers with the highest potential",
    "identify the top 100 borrowers with the highest potential",
    "identify the top-10 borrowers with the highest potential",
    *_MIXED_TOKEN_DIRECTIVES,
)


def _prompt_refused(question: str) -> bool:
    try:
        GrowthAgentPromptRunRequest(prompt=question)
    except ValidationError:
        return True
    return False


@pytest.mark.parametrize(("with_count", "without_count"), _COUNT_PAIRS)
def test_a_count_does_not_change_the_decision(with_count: str, without_count: str) -> None:
    """The invariant. Red if either grammar stops admitting a count."""

    assert _contains_unreviewed_audience_decision(
        with_count
    ) == _contains_unreviewed_audience_decision(without_count)


@pytest.mark.parametrize("clause", _MUST_REFUSE)
def test_health_targeting_behind_a_count_still_refuses(clause: str) -> None:
    assert _contains_unreviewed_audience_decision(clause) is True


@pytest.mark.parametrize("clause", _MUST_ALLOW)
def test_the_reviewed_ranking_question_survives_a_count(clause: str) -> None:
    assert _contains_unreviewed_audience_decision(clause) is False


@pytest.mark.parametrize("clause", _MUST_REFUSE)
def test_health_targeting_behind_a_count_refuses_at_the_prompt_boundary(clause: str) -> None:
    """At the surface a user reaches, not just at the clause helper."""

    assert _prompt_refused(f"{clause}.") is True


@pytest.mark.parametrize("clause", _MUST_ALLOW)
def test_the_reviewed_ranking_question_passes_the_prompt_boundary(clause: str) -> None:
    assert _prompt_refused(f"{clause}.") is False


# The shipped ranked-ask family. "Show me the top 20 borrowers with the
# highest lead scores." is a published Genie Space sample question and the
# question of the 2026-08-12 live capture; base answered it only because the
# refusing branch was letters-only and N carrying a digit outside
# {0,1,3,4,5,7} slipped the leet fold. Count-invariance closed that accident
# for EVERY N, breaking the shipped question, until the anchored analytics
# shape restored the family on the allow side with a closed tail (signoff
# round two).
_RANKED_ASK = "Show me the top {n} borrowers with the highest lead scores."
_RANKED_ASK_COUNTS = (2, 6, 8, 9, 10, 20, 25, 26, 50, 100, 250, 1000)


@pytest.mark.parametrize("count", _RANKED_ASK_COUNTS)
def test_the_shipped_ranked_ask_passes_at_every_count(count: int) -> None:
    assert _prompt_refused(_RANKED_ASK.format(n=count)) is False


@pytest.mark.parametrize("count", (10, 20))
def test_the_ranked_ask_tail_stays_closed(count: int) -> None:
    """The separation the reverted article could not achieve: the closed tail
    admits governed signals only, so a health predicate at the same count
    still refuses."""

    assert _prompt_refused(f"Identify the top {count} borrowers with eczema.") is True
    assert (
        _prompt_refused(f"Show me the top {count} borrowers with the highest credit scores.")
        is True
    )
