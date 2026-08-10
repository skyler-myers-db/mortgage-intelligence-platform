"""The governed space's own deep-analysis plan must survive the guards.

Captured live 2026-08-10 against the paychex space for the user's question
("Analyze the full dataset of eligible borrowers ... why each borrower is an
especially good candidate ... best curated offer"). The space planned seven
sub-questions; the criterion machine dropped three as ``unreviewed_criterion``
— none named a protected class — which put the plan under the deep floor of
five, so ``run_planned_sweep`` returned ``None`` and the user silently got a
single-turn answer instead of the deep decomposition.
"""

from __future__ import annotations

import pytest

from backend.services.genie_message_policy import protected_prompt_match
from backend.services.repositories.databricks_genie_sweep import (
    _MIN_PLANNED_DEEP,
    _planned_question_guard_hit,
)

# Verbatim from the live planning turn.
LIVE_PLAN = (
    "Who are the top 20 eligible borrowers ranked by opportunity score, and "
    "what are their rate spread, equity percentage, key triggers, and "
    "recommended offer?",
    "How do the top 20 borrowers compare to the full eligible population in "
    "terms of average opportunity score, rate spread, and equity percentage?",
    "What is the distribution of recommended offers among the top 20 "
    "borrowers, and what key signals (rate spread, equity, listing, investor "
    "status) drive each offer type?",
    "What is the geographic concentration (by state and ZIP) of the top 20 "
    "borrowers compared to the overall eligible population?",
    "How do segment memberships (e.g., in-the-money, equity, investor, "
    "retention) and listing status co-occur among the top 20 borrowers versus "
    "the broader eligible group?",
    "What proportion of the top 20 borrowers have competitor liens or are "
    "listed for sale, and how does this compare to the full eligible "
    "population?",
    "What is the percentile rank of the top 20 borrowers for opportunity "
    "score, rate spread, and equity percentage within the entire eligible "
    "borrower pool?",
)

NEWLY_REVIEWED = (
    LIVE_PLAN[0],  # ranked shortlist + its governed signal columns
    LIVE_PLAN[6],  # percentile placement within the population
)


@pytest.mark.parametrize("question", NEWLY_REVIEWED)
def test_live_planned_questions_clear_the_guards(question: str) -> None:
    assert _planned_question_guard_hit(question) is None


def test_live_plan_keeps_enough_sections_to_run_the_deep_sweep() -> None:
    kept = [q for q in LIVE_PLAN if _planned_question_guard_hit(q) is None]
    assert len(kept) >= _MIN_PLANNED_DEEP


@pytest.mark.parametrize(
    "question",
    [
        # Protected-class terms riding the newly reviewed shapes.
        "Who are the top 20 hispanic borrowers ranked by opportunity score?",
        "Who are the top 20 female borrowers ranked by opportunity score, "
        "and what are their equity percentage?",
        "Who are the top 20 borrowers ranked by race?",
        "What is the percentile rank of the top 20 borrowers for religion "
        "within the entire eligible borrower pool?",
        # Unknown criteria riding the same shapes.
        "Who are the top 20 borrowers ranked by zyrplax score, and what are "
        "their zyrplax percentage?",
        "What is the percentile rank of the top 20 borrowers for zyrplax "
        "within the entire eligible borrower pool?",
    ],
)
def test_reviewed_shapes_stay_closed(question: str) -> None:
    assert protected_prompt_match(question) is not None


LIVE_PERSONA_PROBES = (
    # Marketing leader: "a deep, comprehensive read" is the same ask as "a deep
    # analysis"; the depth nouns had no business phrasings, so this ran as a
    # single turn. Live persona probe 2026-08-10.
    "Give me a deep, comprehensive read on our offer mix. For every recommended "
    "offer, what signals drive it, how large is the addressable cohort, how do "
    "those cohorts differ on equity and rate spread, and where should we "
    "concentrate spend geographically and why?",
)


@pytest.mark.parametrize("question", LIVE_PERSONA_PROBES)
def test_persona_depth_phrasings_route_to_the_deep_sweep(question: str) -> None:
    from backend.services.repositories.databricks_genie_sweep import (
        is_deep_analysis_request,
    )

    assert is_deep_analysis_request(question)


@pytest.mark.parametrize(
    "question",
    [
        # The plainest sales ask: a ranked cohort with NO criterion at all.
        "Rank the top opportunities",
        "Rank the top borrowers",
        "Show the top leads",
    ],
)
def test_bare_ranked_cohort_is_reviewed(question: str) -> None:
    assert protected_prompt_match(question) is None


@pytest.mark.parametrize(
    "question",
    [
        "Rank the top zyrplax borrowers",
        "Rank the top hispanic borrowers",
        "Rank the top borrowers with eczema",
        "Show me the top christian leads",
        "list the top female candidates",
    ],
)
def test_bare_ranked_cohort_shape_stays_closed(question: str) -> None:
    assert protected_prompt_match(question) is not None


@pytest.mark.parametrize(
    "phrasing",
    [
        # Investigative asks — live persona probe 2026-08-10 ran an "in-depth
        # investigation" question as a single 26s turn because the depth nouns
        # covered analysis/review/dive/study but no investigative vocabulary.
        "Do an in-depth investigation of the portfolio",
        "Do a deep investigation of the portfolio",
        "Do a thorough investigation of the portfolio",
        "Give me a comprehensive examination of the portfolio",
        "Run a full audit of the portfolio",
        "Do a deep exploration of the portfolio",
    ],
)
def test_investigative_phrasings_route_to_the_deep_sweep(phrasing: str) -> None:
    from backend.services.repositories.databricks_genie_sweep import (
        is_deep_analysis_request,
    )

    assert is_deep_analysis_request(phrasing)


@pytest.mark.parametrize(
    "question",
    [
        "How many in-the-money borrowers are in TX?",
        "Show borrowers by state.",
        "What is the average equity percentage?",
        "How many leads do we have?",
    ],
)
def test_single_part_asks_stay_on_the_single_turn_path(question: str) -> None:
    from backend.services.repositories.databricks_genie_sweep import (
        is_deep_analysis_request,
    )

    assert not is_deep_analysis_request(question)
