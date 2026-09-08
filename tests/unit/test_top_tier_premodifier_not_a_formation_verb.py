"""``top-tier`` is a cohort adjective, not the audience-formation verb ``tier``.

Measured 2026-09-08 at ``protected_prompt_match``. ``_AUDIENCE_FORMATION_ACTION_
FRAGMENT`` lists ``tier(?:s|ed|ing)?`` and both of its embeddings open on ``\\b``,
which holds between the hyphen and the ``t`` of "top-tier". So "How do top-tier
opportunities compare against the rest of the outreach-ready population?" parsed
as the verb ``tier`` binding the criterion "opportunities compare against the
rest of the outreach-ready" to a "population" audience, and the pre-population
reviewer refused it as ``unreviewed_criterion`` -- while the tier-free twin "How
do top opportunities compare ..." was answered. The governed space's own planner
writes the same adjective ("how do top-tier opportunities (opportunity_score >=
75) compare with the full outreach-ready population"), so each such line cost a
deep sweep one section.

The fix narrows the TERM, the same move as the MS/Mississippi carrier fix: a
fixed-width negative lookbehind ``(?<!\\btop[-\\s])`` in front of the ``tier``
alternative, in the shared fragment and in the selection-context marker's own
copy of the verb list. ``\\b`` inside the lookbehind keeps the shape closed to
the WORD ``top``: "laptop-tier the borrowers" and "stop tier the borrowers"
still read as the verb, and the clause-initial command fragment is untouched.

Differential over the 5,018 guard-family test literals plus the battery below
(base vs fix, three surfaces): every changed verdict is refused -> allowed and
carries "top-tier"/"top tier"; the pure-analytics shapes had no criterion
vocabulary at all, and every changed literal carrying the unbanked control
token has a tier-free twin base already answers, so its refusal was the
misparse itself and not a net. Banked vocabulary in the same frame (eczema,
christian, hispanic) keeps its direct-detector verdict on both sides. Those
twins are pinned below as PARITY, never as an allowed verdict: if the bare
unreviewed-premodifier class is ever closed, both sides refuse together and the
parity assertions stay green.

Every refusal below asserts the EXACT reason string.
"""

from __future__ import annotations

import pytest

from backend.schemas._validators_protected_class_patterns import (
    PROTECTED_HEALTH_SELECTION_CONTEXT_RE,
)
from backend.schemas.marketing_selection_criteria import (
    _AUDIENCE_FORMATION_ACTION_RE,
    _AUDIENCE_FORMATION_BOUND_POPULATION_RE,
)
from backend.services.genie_message_policy import protected_prompt_match
from backend.services.repositories.databricks_genie_sweep import _planned_question_guard_hit

RESIDUAL_2026_09_08 = (
    "How do top-tier opportunities compare against the rest of the outreach-ready "
    "population?"
)
# Verbatim from the live planning turn the sibling capture pinned.
PLANNED_TOP_TIER_COMPARISON = (
    "In the highest-volume states, how do top-tier opportunities "
    "(opportunity_score >= 75) compare with the full outreach-ready population "
    "on average opportunity score, average rate spread, and average equity "
    "percentage?"
)


@pytest.mark.parametrize(
    "question",
    [
        RESIDUAL_2026_09_08,
        PLANNED_TOP_TIER_COMPARISON,
        # The planner's bare clause, and the space / capitalized spellings.
        "top-tier opportunities (opportunity_score >= 75) compare with the full "
        "outreach-ready population",
        "How do top tier opportunities compare against the rest of the outreach-ready "
        "population?",
        "How do Top-Tier opportunities compare against the rest of the outreach-ready "
        "population?",
        "How do TOP-TIER opportunities compare against the rest of the outreach-ready "
        "population?",
        # Any population noun within the 80-char capture, any comparison frame.
        "How do top-tier borrowers compare with the whole population?",
        "How do top-tier borrowers compare with the whole population on rate spread?",
        "How does the top-tier segment compare with the population?",
        "Compare the top-tier cohort against the full population.",
        "What share of the top-tier borrowers are in the retention segment?",
        "Which counties have the most top-tier opportunities relative to the "
        "addressable population?",
        "How do top-tier opportunities compare against the rest of the outreach-ready "
        "population, and which segments do they fall into?",
        # A co-reference clause behind the comparison: the marker's own copy of
        # the verb list no longer reads the adjective as a selection context.
        "How do top-tier opportunities compare with the population? What do they "
        "look like?",
    ],
)
def test_top_tier_analytics_clear_the_prompt_guard(question: str) -> None:
    assert protected_prompt_match(question) is None


@pytest.mark.parametrize("question", [RESIDUAL_2026_09_08, PLANNED_TOP_TIER_COMPARISON])
def test_the_planner_screen_keeps_the_top_tier_line(question: str) -> None:
    assert _planned_question_guard_hit(question) is None


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        # The verb reading survives everywhere but directly behind ``top``.
        ("Tier the borrowers with eczema", "protected_class_language"),
        ("Tier the borrowers with eczema. Then contact them.", "protected_class_language"),
        ("Tier borrowers by zyrplax", "unreviewed_criterion"),
        ("Tier top borrowers by zyrplax", "unreviewed_criterion"),
        ("Please tier the zyrplax borrowers", "unreviewed_criterion"),
        ("We tier borrowers by zyrplax into the population", "unreviewed_criterion"),
        ("Tiering borrowers by zyrplax population", "unreviewed_criterion"),
        ("tiered borrowers by zyrplax population", "unreviewed_criterion"),
        ("Tier the borrowers by zyrplax. They may qualify.", "unreviewed_criterion"),
        # Banked vocabulary in the top-tier frame belongs to the direct
        # detectors, which never consulted the verb reading.
        ("top-tier borrowers with eczema", "protected_class_language"),
        ("Rank the top tier borrowers with eczema", "protected_class_language"),
        (
            "How do top-tier opportunities with eczema compare against the rest of the "
            "population?",
            "protected_class_language",
        ),
        (
            "How do top-tier opportunities compare against the rest of the population "
            "with eczema?",
            "protected_class_language",
        ),
        (
            "How do top-tier opportunities (eczema >= 75) compare with the full "
            "outreach-ready population?",
            "protected_class_language",
        ),
        (
            "How do top-tier opportunities compare with the full christian population "
            "on average opportunity score?",
            "protected_class_language",
        ),
        (
            "How do top-tier borrowers compare with the population? They have eczema.",
            "protected_class_language",
        ),
        (
            "How do top-tier opportunities compare against the rest of the hispanic "
            "population?",
            "hispanic",
        ),
        # An unreviewed criterion behind a connector still reaches the
        # population-directive tail ("top-tier" is an ordinary lead-in).
        ("top-tier borrowers with zyrplax", "unreviewed_criterion"),
        ("top tier borrowers with zyrplax", "unreviewed_criterion"),
        ("top-tier borrowers by zyrplax. They may qualify.", "unreviewed_criterion"),
        # A real formation command in front keeps every owner it had.
        ("Rank the top-tier zyrplax borrowers", "unreviewed_criterion"),
        ("Add the top-tier zyrplax borrowers to the campaign", "unreviewed_criterion"),
        ("Select top-tier borrowers by zyrplax for the population", "unreviewed_criterion"),
        (
            "How do top-tier opportunities compare with the population? Prioritize those "
            "with zyrplax.",
            "unreviewed_criterion",
        ),
        (
            "How do top-tier borrowers compare with the population? Those with zyrplax "
            "are eligible.",
            "unreviewed_criterion",
        ),
    ],
)
def test_tier_as_a_verb_and_every_other_net_still_refuse(question: str, reason: str) -> None:
    assert protected_prompt_match(question) == reason


@pytest.mark.parametrize(
    ("compound", "twin"),
    [
        # Bare and read-verb noun phrases: no formation structure on either side.
        ("top tier zyrplax borrowers", "top zyrplax borrowers"),
        ("top-tier zyrplax borrowers", "top zyrplax borrowers"),
        ("List top-tier zyrplax borrowers", "List top zyrplax borrowers"),
        ("Show top tier zyrplax borrowers", "Show top zyrplax borrowers"),
        (
            "Which top-tier zyrplax borrowers should we contact?",
            "Which top zyrplax borrowers should we contact?",
        ),
        # The comparison frame with the control token on either side of it.
        (
            "How do top-tier zyrplax opportunities compare against the rest of the "
            "outreach-ready population?",
            "How do top zyrplax opportunities compare against the rest of the "
            "outreach-ready population?",
        ),
        (
            "How do top-tier opportunities compare against the rest of the rosacea "
            "population?",
            "How do top opportunities compare against the rest of the rosacea "
            "population?",
        ),
        # The sibling capture's attack literal for its top-band shape: refused
        # before this change by the misparse alone, so it now shares the verdict
        # of the twin base already answers.
        (
            "In the highest-volume states, how do top-tier zyrplax opportunities compare "
            "with the full outreach-ready population on average opportunity score?",
            "In the highest-volume states, how do top zyrplax opportunities compare "
            "with the full outreach-ready population on average opportunity score?",
        ),
        # The selection-context marker: a co-reference clause behind the
        # comparison is scanned exactly as it is behind the tier-free twin.
        (
            "How do top-tier borrowers compare with the population? They have zyrplax.",
            "How do top borrowers compare with the population? They have zyrplax.",
        ),
    ],
)
def test_a_tier_compound_shares_its_tier_free_twins_verdict(compound: str, twin: str) -> None:
    assert protected_prompt_match(compound) == protected_prompt_match(twin)


@pytest.mark.parametrize(
    "text",
    [
        "top-tier the borrowers",
        "top tier the borrowers",
        "Top-Tier the borrowers",
        "top\ttier the borrowers",
        "on top tier the borrowers",
        "top-tiered the borrowers",
    ],
)
def test_the_lookbehind_covers_the_hyphen_and_whitespace_spellings(text: str) -> None:
    assert _AUDIENCE_FORMATION_ACTION_RE.search(text) is None
    assert _AUDIENCE_FORMATION_BOUND_POPULATION_RE.search(text) is None
    assert PROTECTED_HEALTH_SELECTION_CONTEXT_RE.search(f"{text} population") is None


@pytest.mark.parametrize(
    "text",
    [
        "tier the borrowers",
        "tiered the borrowers",
        "tiering the borrowers",
        # ``\\b`` inside the lookbehind: only the WORD ``top`` disarms the verb.
        "laptop-tier the borrowers",
        "stop tier the borrowers",
    ],
)
def test_the_lookbehind_is_closed_to_the_word_top(text: str) -> None:
    assert _AUDIENCE_FORMATION_ACTION_RE.search(text) is not None
    assert _AUDIENCE_FORMATION_BOUND_POPULATION_RE.search(text) is not None
    assert PROTECTED_HEALTH_SELECTION_CONTEXT_RE.search(f"{text} population") is not None
