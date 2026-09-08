"""Three of the governed space's own deep-analysis plan lines must survive the guards.

Captured 2026-09-08 by replaying ``_planning_prompt(question, deep=True)``
against the paychex space (``01f13d4968af1b249dc388fd5b18b195``) for a full-
analysis ask ("Do a full analysis of the existing marketable population. What
segments have the highest opportunity? ... what offers should we be making
exactly and why?") and for a Head-of-Growth "comprehensive analysis of our
addressable market". Three planned lines were refused by
``protected_prompt_match`` as ``unreviewed_criterion``. None names a
criterion outside the reviewed vocabulary; each drop costs the sweep one
section and pushes the plan toward the deep floor (``_MIN_PLANNED_DEEP``).

All three trip the same producer -- the bound-population capture in
``_contains_unreviewed_audience_decision`` and the pre-population reviewer it
shares with the immediate-outcome branch -- binding a criterion ACROSS a
clause boundary or a compound noun:

* ``ranked by average opportunity score, and how large is each segment``:
  the capture reads everything between ``ranked`` and the NEXT population
  noun (``segment``) as that population's criterion, so the coordinated
  group-size question rides inside the span.
* ``the top-ranked borrower cohort``: ``cohort`` is itself a population noun,
  so the optional criterion slot prefers the non-empty ``borrower`` over the
  empty parse. A population noun compounding a group noun is the same
  population reference, not a criterion. Measured: the ``top-`` half is not
  the defect ("the ranked borrower cohort" refused, "the top-ranked
  borrowers" answered).
* ``which states and segments should be prioritized first``: the immediate-
  outcome branch hands ``which states and`` to the pre-population reviewer,
  which reads the coordinated grouping dimension as a criterion. Behind it,
  invisible to the bisect (the ``when balancing`` clause passes ALONE), the
  same capture re-fires on ``prioritized first when balancing borrower
  volume``: ``first when balancing`` captured as the criterion of
  ``borrower``, a population noun that is really half of a measure.

Every fix is a closed shape on the allow side. The weighing list is admitted
only when EVERY measure in it is reviewed vocabulary, because "... balancing
borrower volume, zyrplax, and equity" refused before this change (by the
same accident) and keeps refusing after it.

Every refusal below asserts the EXACT reason string: ``is not None`` passes
through a silent reclassification.
"""

from __future__ import annotations

import pytest

from backend.services.genie_message_policy import protected_prompt_match
from backend.services.repositories.databricks_genie_sweep import _planned_question_guard_hit

# Verbatim from the live planning turns.
LIVE_PLAN_2026_09_08 = (
    "Which segments have the highest opportunity within the current marketable "
    "population when ranked by average opportunity score, and how large is each "
    "segment?",
    "What is the recommended-offer mix among the top-ranked borrower cohort, and "
    "how do the underlying signals differ across refinance, refinance plus HELOC, "
    "HELOC, retention, investor, and purchase-oriented recommendations?",
    "Across the current coverage, which states and segments should be prioritized "
    "first when balancing borrower volume, average opportunity score, and "
    "recommended-offer concentration?",
)


@pytest.mark.parametrize("question", LIVE_PLAN_2026_09_08)
def test_live_planned_questions_clear_the_guards(question: str) -> None:
    assert _planned_question_guard_hit(question) is None


@pytest.mark.parametrize(
    "question",
    [
        # The bisected minimal triggers, one per planned line.
        "Which segments have the highest opportunity when ranked by average "
        "opportunity score, and how large is each segment?",
        "What is the recommended-offer mix among the top-ranked borrower cohort?",
        "which states and segments should be prioritized first?",
        # The second defect behind the third line.
        "which segments should be prioritized first when balancing borrower volume?",
        "which segments should be prioritized first when balancing borrower volume, "
        "average opportunity score, and recommended-offer concentration?",
        # The measure list in either position around the population noun.
        "which segments should be prioritized when balancing average opportunity "
        "score and borrower volume?",
        # A count question is the same group-size tail as a size question.
        "Which segments have the highest opportunity when ranked by average "
        "opportunity score, and how many borrowers are in each segment?",
        # A reviewed list ranks exactly as one reviewed attribute does.
        "Which segments have the highest opportunity when ranked by rate spread and "
        "home equity, and how large is each segment?",
    ],
)
def test_bisected_minimal_twins_answer(question: str) -> None:
    assert protected_prompt_match(question) is None


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        # The group-size tail never admits an unreviewed or banked ranking.
        (
            "Which segments have the highest opportunity when ranked by rosacea, and "
            "how large is each segment?",
            "unreviewed_criterion",
        ),
        (
            "Which segments have the highest opportunity when ranked by zyrplax score, "
            "and how large is each segment?",
            "unreviewed_criterion",
        ),
        (
            "Which segments have the highest opportunity when ranked by rate spread and "
            "zyrplax, and how large is each segment?",
            "unreviewed_criterion",
        ),
        (
            "Which segments have the highest opportunity when ranked by race, and how "
            "large is each segment?",
            "race",
        ),
        (
            "Which segments have the highest opportunity when ranked by average "
            "opportunity score, and how large is each zyrplax segment?",
            "unreviewed_criterion",
        ),
        # A population-noun compound is transparent; whatever else rides in front
        # of, or between, the two nouns still has to be reviewed.
        (
            "What is the recommended-offer mix among the top-ranked zyrplax borrower "
            "cohort?",
            "unreviewed_criterion",
        ),
        (
            "What is the recommended-offer mix among the top-ranked borrower zyrplax "
            "cohort?",
            "unreviewed_criterion",
        ),
        (
            "What is the recommended-offer mix among the top-ranked hispanic borrower "
            "cohort?",
            "hispanic",
        ),
        # A coordinated grouping dimension is transparent; an unreviewed word on
        # either side of the conjunction is not.
        ("which states and zyrplax segments should be prioritized first?", "unreviewed_criterion"),
        ("which zyrplax states and segments should be prioritized first?", "unreviewed_criterion"),
        ("which states and hispanic segments should be prioritized first?", "hispanic"),
        # The weighing list is admitted whole or not at all.
        (
            "which segments should be prioritized first when balancing zyrplax borrower "
            "volume?",
            "unreviewed_criterion",
        ),
        (
            "which segments should be prioritized first when balancing borrower volume, "
            "zyrplax, and equity?",
            "unreviewed_criterion",
        ),
        (
            "which segments should be prioritized first when balancing borrower volume "
            "and zyrplax?",
            "unreviewed_criterion",
        ),
        (
            "which segments should be prioritized first when balancing female borrower "
            "volume?",
            "female",
        ),
        # The unreviewed controls every capture file keeps.
        ("Rank the top zyrplax borrowers", "unreviewed_criterion"),
        ("Rank the top borrowers with eczema", "protected_class_language"),
    ],
)
def test_cross_clause_shapes_stay_closed(question: str, reason: str) -> None:
    assert protected_prompt_match(question) == reason


@pytest.mark.parametrize(
    "question",
    [
        "What is the recommended-offer mix among the ranked borrower cohort?",
        "What is the recommended-offer mix among the ranked customer segment?",
        "What is the recommended-offer mix among the ranked homeowner population?",
        "What is the recommended-offer mix among the top-ranked lead audience?",
    ],
)
def test_a_population_noun_compound_is_not_a_criterion(question: str) -> None:
    assert protected_prompt_match(question) is None


@pytest.mark.parametrize(
    "question",
    [
        "which states, counties, and segments should be prioritized first?",
        "which markets or segments should be prioritized first?",
        "Across the current coverage, which states and segments should be prioritized "
        "first?",
    ],
)
def test_a_coordinated_grouping_dimension_is_not_a_criterion(question: str) -> None:
    assert protected_prompt_match(question) is None


@pytest.mark.parametrize(
    "spelling",
    ["recommended-offer concentration", "recommended offer concentration"],
)
def test_the_reviewed_offer_compound_answers_in_either_spelling(spelling: str) -> None:
    """#228 doctrine: a reviewed compound spells its own hyphen-fold image."""

    question = (
        "which segments should be prioritized first when balancing borrower volume "
        f"and {spelling}?"
    )
    assert protected_prompt_match(question) is None
