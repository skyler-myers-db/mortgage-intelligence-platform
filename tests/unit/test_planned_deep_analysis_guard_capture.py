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
