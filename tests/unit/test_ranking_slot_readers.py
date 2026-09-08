"""Two ranking slots the criterion machine never read are read, fail-closed.

Both measured on main (7dc6c29a, 2026-09-08) at ``protected_prompt_match``
while the planner-line false positives were closed
(``test_planned_deep_analysis_cross_clause_bindings``); neither was opened by
that change, and each is exactly as reachable as before it.

1. **A hyphen-attached formation participle's LEFT half.** The bound-population
   capture matches ``ranked`` after the hyphen with an EMPTY criterion, and the
   empty span reviews as reviewed, so "the zyrplax-ranked borrowers" answered
   while "the top-ranked zyrplax borrowers" refused: only the span BETWEEN the
   participle and the population noun was ever judged.

2. **A weighing or ranking adverbial with no population noun inside it.**
   "which segments should be prioritized when balancing zyrplax?" and "Which
   segments have the highest opportunity when ranked by rosacea?" answered,
   while the same sentences refused once a population noun appeared later in
   the list -- accidentally, through the bound capture.

Both readers are refusal-adders on a closed allow side: a literal alternation
of legitimate left halves, and the union of the governed ranking vocabularies
the analytics shapes already read, judged WHOLE. Every refusal below asserts
the EXACT reason string: ``is not None`` passes through a silent
reclassification, and a banked term must keep its fair-lending reason rather
than fall to the unknown-criterion net.
"""

from __future__ import annotations

import pytest

from backend.services.genie_message_policy import protected_prompt_match

# ---------------------------------------------------------------------------
# Slot 1: the left half of ``<X>-<participle> <population>``.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        # The four measured carriers, verbatim.
        (
            "What is the recommended-offer mix among the zyrplax-ranked borrowers?",
            "unreviewed_criterion",
        ),
        (
            "What is the recommended-offer mix among the rosacea-ranked borrowers?",
            "unreviewed_criterion",
        ),
        (
            "What is the recommended-offer mix among the credit score-ranked borrowers?",
            "unreviewed_criterion",
        ),
        (
            "What is the recommended-offer mix among the left-handedness-ranked borrowers?",
            "unreviewed_criterion",
        ),
        # The population-noun compound the planner fix made transparent keeps
        # the left half judged: exactly as reachable as the bare noun, no wider.
        (
            "What is the recommended-offer mix among the zyrplax-ranked borrower cohort?",
            "unreviewed_criterion",
        ),
        # Every formation participle the capture reads, not only ``ranked``.
        ("Show me the zyrplax-selected borrowers.", "unreviewed_criterion"),
        ("Show me the rosacea-picked leads.", "unreviewed_criterion"),
        ("Show me the zyrplax-sorted customer segment.", "unreviewed_criterion"),
        ("Show me the zyrplax-prioritized lead audience.", "unreviewed_criterion"),
        # A multi-word left half is judged WHOLE up to the closed function-word
        # boundary: a reviewed tail cannot launder an unreviewed head.
        (
            "What is the recommended-offer mix among the zyrplax opportunity "
            "score-ranked borrowers?",
            "unreviewed_criterion",
        ),
        # A bare ``score`` is not a measure the product models (only
        # opportunity/lead score are), so it fails closed like "Rank borrowers
        # by score" already does.
        (
            "What is the recommended-offer mix among the score-ranked borrowers?",
            "unreviewed_criterion",
        ),
        # A banked term in the slot keeps its fair-lending reason.
        (
            "What is the recommended-offer mix among the hispanic-ranked borrowers?",
            "hispanic",
        ),
        # The already-refused sibling stays refused for the reason it always had.
        (
            "What is the recommended-offer mix among the top-ranked zyrplax borrowers?",
            "unreviewed_criterion",
        ),
    ],
)
def test_a_hyphen_attached_participle_left_half_is_judged(question: str, reason: str) -> None:
    assert protected_prompt_match(question) == reason


@pytest.mark.parametrize(
    "question",
    [
        # The closed allow list: degree/ordinal, productive prefix, recency,
        # the ranking agent, a count.
        "What is the recommended-offer mix among the top-ranked borrowers?",
        "What is the recommended-offer mix among the top-ranked borrower cohort?",
        "What is the recommended-offer mix among the highest-ranked borrowers?",
        "What is the recommended-offer mix among the best-ranked borrowers?",
        "What is the recommended-offer mix among the lowest-ranked borrowers?",
        "What is the recommended-offer mix among the bottom-ranked borrowers?",
        "What is the recommended-offer mix among the first-ranked borrowers?",
        "What is the recommended-offer mix among the last-selected borrowers?",
        "What is the recommended-offer mix among the pre-selected borrowers?",
        "What is the recommended-offer mix among the re-ranked borrowers?",
        "What is the recommended-offer mix among the hand-picked borrowers?",
        "What is the recommended-offer mix among the self-selected borrowers?",
        "What is the recommended-offer mix among the newly-added borrowers?",
        "What is the recommended-offer mix among the recently-added borrowers?",
        "What is the recommended-offer mix among the previously-selected borrowers?",
        "What is the recommended-offer mix among the model-ranked borrowers?",
        # The allow list is read on the token touching the hyphen, so a degree
        # adverb in front of it does not lengthen the judged span.
        "What is the recommended-offer mix among the very top-ranked borrowers?",
        # A reviewed ranking object as the left half, in every spelling the
        # de-obfuscation fold hands the machine (#228 doctrine).
        "What is the recommended-offer mix among the opportunity score-ranked borrowers?",
        "What is the recommended-offer mix among the average opportunity score-ranked "
        "borrowers?",
        "What is the recommended-offer mix among the rate spread-ranked borrowers?",
        "What is the recommended-offer mix among the rate-spread-ranked borrowers?",
        "What is the recommended-offer mix among the home-equity-ranked borrowers?",
        "What is the recommended-offer mix among the equity-ranked borrowers?",
        # The repo's own prompt copy.
        "Explain the scoring behind the highest-ranked borrowers.",
    ],
)
def test_a_legitimate_left_half_answers(question: str) -> None:
    assert protected_prompt_match(question) is None


# ---------------------------------------------------------------------------
# Slot 2: ``... when ranked by <X>`` / ``... when balancing <X>``.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        # The two measured carriers, verbatim.
        ("which segments should be prioritized when balancing zyrplax?", "unreviewed_criterion"),
        (
            "Which segments have the highest opportunity when ranked by rosacea?",
            "unreviewed_criterion",
        ),
        # Every weighing verb the allow shape reads, and the optimisation idioms.
        ("which segments should be prioritized when weighing zyrplax?", "unreviewed_criterion"),
        ("which segments should be prioritized when trading off zyrplax?", "unreviewed_criterion"),
        ("which segments should be prioritized when considering zyrplax?", "unreviewed_criterion"),
        (
            "which segments should be prioritized when optimizing for zyrplax?",
            "unreviewed_criterion",
        ),
        # Ranking participles and connectors beyond ``ranked by``.
        ("Which cohorts have the most upside when sorted by rosacea?", "unreviewed_criterion"),
        (
            "Which segments have the highest opportunity when ordered by zyrplax?",
            "unreviewed_criterion",
        ),
        (
            "Which segments have the highest opportunity when scored on zyrplax?",
            "unreviewed_criterion",
        ),
        (
            "Which segments have the highest opportunity when ranked according to zyrplax?",
            "unreviewed_criterion",
        ),
        (
            "Which segments have the highest opportunity when ranked based on zyrplax?",
            "unreviewed_criterion",
        ),
        # No subordinator: the participle is still not touching the population
        # noun, so the immediate-outcome branch never saw it.
        ("Which segments have the highest opportunity ranked by rosacea?", "unreviewed_criterion"),
        # The subject-first phrasing of the same decision.
        ("How should we prioritize segments when balancing zyrplax?", "unreviewed_criterion"),
        # The object is judged WHOLE: a reviewed conjunct cannot carry an
        # unreviewed one, in either position and in a comma list.
        (
            "which segments should be prioritized when balancing rate spread and zyrplax?",
            "unreviewed_criterion",
        ),
        (
            "which segments should be prioritized when balancing zyrplax and rate spread?",
            "unreviewed_criterion",
        ),
        (
            "which segments should be prioritized first when balancing borrower volume, "
            "zyrplax, and equity?",
            "unreviewed_criterion",
        ),
        (
            "Which segments have the highest opportunity when ranked by rate spread, "
            "home equity, and rosacea?",
            "unreviewed_criterion",
        ),
        # A coordinated group-size question stops the object; the object in
        # front of it is still judged.
        (
            "Which segments have the highest opportunity when ranked by rosacea, and how "
            "large is each segment?",
            "unreviewed_criterion",
        ),
        # A banked term in the slot keeps its fair-lending reason.
        ("Which segments have the highest opportunity when ranked by race?", "race"),
        (
            "which segments should be prioritized when balancing female borrower volume?",
            "female",
        ),
    ],
)
def test_a_ranking_adverbial_object_is_judged(question: str, reason: str) -> None:
    assert protected_prompt_match(question) == reason


@pytest.mark.parametrize(
    "question",
    [
        # The governed ranking vocabularies, alone and as a reviewed list.
        "Which segments have the highest opportunity when ranked by rate spread?",
        "Which segments have the highest opportunity when ranked by opportunity score?",
        "Which segments have the highest opportunity when ranked by average opportunity score?",
        "Which segments have the highest opportunity when ranked by home equity?",
        "Which segments have the highest opportunity when ranked by equity?",
        "Which segments have the highest opportunity when ranked by ltv?",
        # A population noun HEADING a measure ("ranked by borrower count",
        # "ranked by lead score") is a pre-existing bound-capture false positive
        # on main and is not this reader's to fix; the bare group-size noun and
        # the listing dimension are.
        "Which segments have the highest opportunity when sorted by count?",
        "Which segments have the highest opportunity when ranked by listing time on market?",
        "Which segments have the highest opportunity when sorted by state?",
        "Which segments have the highest opportunity when ranked by rate spread and home "
        "equity?",
        "which segments should be prioritized when balancing rate spread and home equity?",
        "which segments should be prioritized when balancing equity?",
        "which segments should be prioritized when balancing borrower volume?",
        "which segments should be prioritized first when balancing borrower volume, average "
        "opportunity score, and recommended-offer concentration?",
        "which segments should be prioritized first when balancing borrower volume, average "
        "opportunity score, and recommended offer concentration?",
        # A scope, a coordinated group-size question and a CTA behind the object.
        "Which segments have the highest opportunity when ranked by rate spread in Texas?",
        "Which segments have the highest opportunity when ranked by rate spread across the "
        "current coverage?",
        "Which segments have the highest opportunity when ranked by average opportunity "
        "score, and how large is each segment?",
        "Which segments have the highest opportunity when ranked by average opportunity "
        "score, and how many borrowers are in each segment?",
        # The scope alone as the consideration.
        "which segments should be prioritized when considering the current coverage?",
        # The live planner line the cross-clause fix restored stays restored.
        "Across the current coverage, which states and segments should be prioritized "
        "first when balancing borrower volume, average opportunity score, and "
        "recommended-offer concentration?",
        "Which segments have the highest opportunity within the current marketable "
        "population when ranked by average opportunity score, and how large is each "
        "segment?",
    ],
)
def test_a_reviewed_ranking_object_answers(question: str) -> None:
    assert protected_prompt_match(question) is None
