"""The reviewed audience-decision lead-in is a closed vocabulary.

Pre-existing fail-open in ``marketing_selection_criteria``, measured on main
b1f58bcf (2026-09-08): ``_REVIEWED_AUDIENCE_DECISION_PATTERNS[0..2]`` opened
with ``(?:[a-z][a-z'-]*|<count>){1,10}`` -- any word -- so an unreviewed
premodifier rode in front of the population noun whenever the criterion
BEHIND the noun was reviewed. "Who are the top zyrplax borrowers by
opportunity score?" and "Who are the top borrowers in the zyrplax cohort by
opportunity score?" both reached Genie; ``_contains_unreviewed_audience_
decision`` returned False at the pattern full-match before the bound-
population capture or the population-directive tail could read the clause,
and ``audience_admission_criterion`` returned None, so no other grammar owned
it. Banked terms refused through the direct detectors, so the hole was
exactly the unbanked-token class the criterion machine exists to catch.

The lead-in is now the closed grammar in ``marketing_selection_lead_in``:
directive prefix, one opening frame, an optional bridge noun, determiners,
the count, the reviewed premodifiers and a screened governed place. The
same closure exposed a second, older hole the differential then measured:
the criterion machine reads the capital-I confusable variant of the prompt,
so a governed place spelled with a capital I ("Illinois", "Indian Head
Park") arrived at the scope screen as "lllinois" and refused -- in the scope
tail on main already, and in the new prenominal slot until
:func:`unfold_capital_i` taught the screen the fold's inverse.

Differential, base (main 7dc6c29a) vs fix, 6,139 questions -- 4,434
literals from 84 guard-family test modules, 191 template expansions and a
1,522-probe lead-in mutant battery -- at four surfaces (prompt guard,
criterion machine, audience decision, marketing boundary). Every change is
classified in the PR description: every loss carries an unreviewed or
ungoverned token in the lead-in, every gain is a capital-I fold image of a
governed place, zero reason shifts.

Every refusal below asserts the EXACT reason string: ``is not None`` passes
through a silent reclassification.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.schemas.marketing_selection_criteria import (
    _REVIEWED_AUDIENCE_DECISION_PATTERNS,
    _contains_unreviewed_audience_decision,
)
from backend.schemas.marketing_selection_vocabulary import reviewed_fullmatch
from backend.schemas.marketing_text_normalization import unfold_capital_i
from backend.services.genie_message_policy import protected_prompt_match
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
)

# Verbatim from the report; both reached Genie on main.
_REPORTED_LEAKS = (
    "Who are the top zyrplax borrowers by opportunity score?",
    "Who are the top borrowers in the zyrplax cohort by opportunity score?",
)


@pytest.mark.parametrize("question", _REPORTED_LEAKS)
def test_the_reported_leaks_refuse_on_the_criterion_reason(question: str) -> None:
    assert protected_prompt_match(question) == "unreviewed_criterion", question


@pytest.mark.parametrize("clause", [question.rstrip("?").lower() for question in _REPORTED_LEAKS])
def test_the_criterion_machine_owns_the_refusal(clause: str) -> None:
    """Red on main: pattern [0] full-matched the clause and the machine said False."""

    assert not any(
        reviewed_fullmatch(pattern, clause) for pattern in _REVIEWED_AUDIENCE_DECISION_PATTERNS
    ), clause
    assert _contains_unreviewed_audience_decision(clause) is True, clause


def test_the_count_free_twin_stays_red() -> None:
    """The report's control: no reviewed tail to ride, refused before and after."""

    assert protected_prompt_match("Rank the top zyrplax borrowers") == "unreviewed_criterion"


# One shape per reviewed criterion body the three patterns take (with/by,
# whose, that-have, conditional, by-whether) and per opening frame family
# (interrogative, command, read-only verb, first person).
#
# Deliberately absent: a destination-tailed admission ("Admit the {pre}
# homeowners to the cohort if ..."). That family is owned by the admission
# grammar, whose action slot is intentionally OPEN and absorbs "admit the
# zyrplax" as the verb phrase -- it answers on main and still does; a
# pre-existing hole of that grammar, not of this lead-in, and not widened or
# narrowed here.
_SHAPES = (
    "Who are the top {pre}borrowers by opportunity score?",
    "Rank the {pre}borrowers with a rate spread.",
    "Rank {pre}borrowers with a rate spread.",
    "Rank the top 50 {pre}borrowers with a rate spread.",
    "Show me the top {pre}borrowers with the highest rate spread.",
    "List the {pre}homeowners with high equity.",
    "Give me the {pre}leads based on opportunity score.",
    "Find the {pre}borrowers whose rate spread is documented.",
    "Rank the {pre}borrowers that have a rate spread.",
    "Select the {pre}borrowers if they have high equity.",
    "Tier the {pre}homeowners by whether they carry high equity.",
    "I want to see the {pre}borrowers with a rate spread.",
)
# Unknown, non-protected vocabulary: what the open lead-in leaked. The
# title-case pair is the "Zyrplax Heights" control for the place slot -- a
# place SHAPE is not a governed place.
_UNBANKED_PREMODIFIERS = (
    "zyrplax ",
    "left-handed ",
    "twentyish ",
    "several ",
    "wealthy ",
    "remaining ",
    "Zyrplax Heights ",
)


@pytest.mark.parametrize("pre", _UNBANKED_PREMODIFIERS)
@pytest.mark.parametrize("shape", _SHAPES)
def test_an_unbanked_premodifier_fails_closed_on_every_shape(shape: str, pre: str) -> None:
    question = shape.format(pre=pre)
    assert protected_prompt_match(question) == "unreviewed_criterion", question


# The closed premodifier set: the admission grammar's adjectives, a governed
# product intent (hyphenated and space-joined -- the machine reads the
# hyphen-deleted variant), a reviewed mortgage attribute, a US state.
_REVIEWED_PREMODIFIERS = (
    "",
    "eligible ",
    "marketing-eligible ",
    "marketing eligible ",
    "highest-scoring ",
    "current ",
    "prospective ",
    "qualified ",
    "reviewed ",
    "in-the-money ",
    "listed-for-sale ",
    "heloc ",
    "investor ",
    "high equity ",
    "high-equity ",
    "Texas ",
)


@pytest.mark.parametrize("pre", _REVIEWED_PREMODIFIERS)
@pytest.mark.parametrize("shape", _SHAPES)
def test_a_reviewed_premodifier_answers_on_every_shape(shape: str, pre: str) -> None:
    question = shape.format(pre=pre)
    assert protected_prompt_match(question) is None, question


# The bridge: the population the criterion binds sits behind a first
# population noun and a preposition. The premodifier under test is the one on
# the BOUND noun. (The ``eligible cohort`` spelling refuses on main and here
# alike, through a detector that is not this machine, so it is not a control.)
_BRIDGE_SHAPE = "Who are the top borrowers in the {pre}cohort by opportunity score?"


@pytest.mark.parametrize("pre", ("", "current ", "in-the-money ", "high equity ", "Texas "))
def test_a_reviewed_premodifier_on_the_bridged_noun_answers(pre: str) -> None:
    question = _BRIDGE_SHAPE.format(pre=pre)
    assert protected_prompt_match(question) is None, question


@pytest.mark.parametrize("pre", _UNBANKED_PREMODIFIERS)
def test_an_unbanked_premodifier_on_the_bridged_noun_fails_closed(pre: str) -> None:
    question = _BRIDGE_SHAPE.format(pre=pre)
    assert protected_prompt_match(question) == "unreviewed_criterion", question


# The severity bound the leak always had: a PROTECTED pre-nominal is owned by
# the term and proxy banks, on their own reasons, before and after.
@pytest.mark.parametrize(
    ("pre", "reason"),
    (
        ("eczema ", "protected_class_language"),
        ("diabetic ", "protected_class_language"),
        ("elderly ", "protected_class_language"),
        ("hispanic ", "hispanic"),
        ("female ", "female"),
    ),
)
def test_a_banked_premodifier_keeps_its_own_reason(pre: str, reason: str) -> None:
    question = f"Who are the top {pre}borrowers by opportunity score?"
    assert protected_prompt_match(question) == reason, question


# Pinned by the report as must-not-regress, plus the lead-ins the corpus
# attested that are neither a determiner nor a premodifier.
@pytest.mark.parametrize(
    "question",
    (
        "Rank the top 50 borrowers with a rate spread",
        "Who are the top 20 eligible borrowers ranked by opportunity score",
        "Show me the top 10 borrowers by lead score in Illinois.",
        "Identify the top 1,000 borrowers with a competitor lien.",
        "Could you please choose leads according to high equity for this offer.",
        "Kindly proceed to choose leads according to current mortgage rates for this campaign.",
        "Rank our segments by average rate spread.",
        "Break down listed-for-sale borrowers by loan product and average current rate.",
        "Only include those with low LTV for portfolio review.",
        "Build a HELOC cohort of households with high equity.",
        "Build a campaign for borrowers with next best offer",
        "Reserve the best treatment for recipients with high equity.",
        "Give priority to recipients carrying high equity.",
        "This offer gives priority to homeowners carrying high equity.",
        "Transfer applicants to the audience based on current mortgage rates.",
        "Who are the top borrowers by opportunity score across the current coverage?",
        "Rank the top borrower candidates with a rate spread.",
        "Let's rank the borrowers by rate spread.",
        "Can you show me the borrowers with a rate spread?",
    ),
)
def test_every_attested_lead_in_still_answers(question: str) -> None:
    assert protected_prompt_match(question) is None, question


# --- The closed shape, not a general class ---------------------------------
#
# The invariant the open token could never satisfy: an unknown token admitted
# at NO position. Insert it at every whitespace boundary of a clause each
# pattern admits, and no pattern may admit the mutant -- not in the lead-in
# and not in the criterion body, which was always closed.
_ADMITTED_CLAUSES = (
    "rank the top 50 borrowers with a rate spread",
    "who are the top borrowers by opportunity score",
    "could you please choose the eligible leads according to high equity for this offer",
    "build a heloc cohort of households with high equity",
    "this offer gives priority to homeowners carrying high equity",
    "find the current borrowers whose rate spread is documented",
    "admit homeowners to the cohort if they have strong equity",
)


@pytest.mark.parametrize("clause", _ADMITTED_CLAUSES)
def test_no_position_in_an_admitted_clause_takes_an_unknown_token(clause: str) -> None:
    patterns = _REVIEWED_AUDIENCE_DECISION_PATTERNS
    assert any(reviewed_fullmatch(p, clause) for p in patterns), clause  # non-vacuous
    words = clause.split()
    for index in range(len(words) + 1):
        mutant = " ".join([*words[:index], "zyrplax", *words[index:]])
        assert not any(reviewed_fullmatch(p, mutant) for p in patterns), mutant


# --- The place slot ---------------------------------------------------------


@pytest.fixture
def governed_cities() -> Iterator[None]:
    resolver = GovernedPlaceDimensionResolver(
        dimension_reader=lambda: ["YORBA LINDA", "INDIAN HEAD PARK"]
    )
    _reset_governed_place_dimension_for_tests(resolver)
    yield
    _reset_governed_place_dimension_for_tests(None)


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize(
    "question",
    (
        "Rank Yorba Linda borrowers by opportunity score",
        # The slot must not steal the parse: ``re`` will not re-parse after a
        # successful fullmatch, so the place repetition is lazy and every
        # place token excludes the closed vocabulary by lookahead. A reviewed
        # adjective or a product intent on either side of the place stays
        # outside the capture.
        "Rank Yorba Linda eligible borrowers by opportunity score",
        "Rank the eligible Yorba Linda borrowers by opportunity score",
        "Rank the Yorba Linda heloc borrowers with a rate spread.",
        "Rank the heloc Yorba Linda borrowers with a rate spread.",
        "Who are the top borrowers in the Yorba Linda cohort by opportunity score?",
    ),
)
def test_a_governed_city_premodifier_answers(question: str) -> None:
    assert protected_prompt_match(question) is None, question


def test_at_most_one_place_and_a_second_capture_cannot_hide_the_first() -> None:
    """A place group inside a repetition would keep only its last iteration."""

    assert protected_prompt_match("Rank Zyrplax Heights Texas borrowers by opportunity score") == (
        "unreviewed_criterion"
    )
    assert protected_prompt_match("Rank Texas Zyrplax Heights borrowers by opportunity score") == (
        "unreviewed_criterion"
    )


@pytest.mark.parametrize(
    "question",
    (
        # Segment intersections the product models: closed intents and
        # reviewed attributes stack in front of the population noun.
        "Rank the in-the-money heloc borrowers with a rate spread.",
        "Rank the high-equity investor borrowers with a rate spread.",
        "Rank the in-the-money high equity borrowers with a rate spread.",
        "Rank the Texas heloc borrowers with a rate spread.",
    ),
)
def test_closed_cohort_premodifiers_stack(question: str) -> None:
    assert protected_prompt_match(question) is None, question


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize(
    "question",
    (
        "Rank Zyrplax Heights borrowers by opportunity score",
        # Metro formants that are not gold cities, counties or states. They
        # rode the open lead-in, never a geography exemption.
        "Rank Inland Empire leads by opportunity score",
        "Rank High Desert leads by opportunity score",
    ),
)
def test_a_place_shape_that_is_not_a_governed_place_refuses(question: str) -> None:
    assert protected_prompt_match(question) == "unreviewed_criterion", question


def test_a_state_premodifier_needs_no_dimension() -> None:
    assert _reset_governed_place_dimension_for_tests(None) is None
    assert protected_prompt_match("Rank Texas borrowers by opportunity score") is None
    assert protected_prompt_match("Rank the top 50 Texas borrowers with a rate spread.") is None


# A bare USPS code is a closed literal, not a screened capture: the screen
# refuses a bare ``ms`` on purpose (multiple sclerosis or Mississippi), which
# is right for a scope tail and would make ``MS`` differ from ``TX`` here. The
# health reading stays with the term bank's carriers. ``IN`` and ``OR`` are the
# codes that collide with the function words the place token excludes; ``IN``
# and ``IL`` also arrive through the capital-I fold.
@pytest.mark.parametrize("code", ("MS", "TX", "IN", "OR", "IL", "LA", "ME"))
def test_a_bare_state_code_premodifier_answers_like_any_other(code: str) -> None:
    assert protected_prompt_match(f"Rank {code} borrowers by opportunity score") is None, code
    assert protected_prompt_match(f"Show me {code} borrowers with high equity.") is None, code


def test_the_state_code_slot_does_not_disarm_the_term_bank() -> None:
    assert protected_prompt_match("Show me MS patients with high equity.") == (
        "protected_class_language"
    )
    # A two-letter token that is no USPS code is an unknown premodifier again.
    # (``US`` is not the control: "Rank US borrowers" parses as the verb's
    # object "rank us", which names nobody.)
    assert protected_prompt_match("Rank ZZ borrowers by opportunity score") == "unreviewed_criterion"


# --- The capital-I fold -----------------------------------------------------
#
# ``ascii_confusable_folds`` rewrites every ``I`` as ``l`` and the criterion
# machine reads that variant. A closed lead-in word that starts with a capital
# I in the prompt arrives as "ldentify"; a governed place as "lllinois".


@pytest.mark.parametrize(
    "question",
    (
        "Identify the top 50 borrowers with a competitor lien.",
        "Include borrowers with a rate spread in the campaign.",
        "I want to see the top 50 borrowers with a rate spread.",
        "Rank the In-The-Money borrowers with a rate spread.",
        "Rank Investor borrowers with a rate spread.",
        "Rank Illinois borrowers by opportunity score",
        "Rank ILLINOIS borrowers by opportunity score",
        "Rank Idaho borrowers by opportunity score",
    ),
)
def test_a_capital_i_lead_in_answers_through_its_fold_image(question: str) -> None:
    assert protected_prompt_match(question) is None, question


@pytest.mark.parametrize(
    "question",
    (
        "Rank borrowers with a rate spread in Illinois.",
        "Rank borrowers with a rate spread in ILLINOIS.",
        "Rank borrowers with a rate spread in Idaho.",
        "Who are the top borrowers by opportunity score in Iowa?",
    ),
)
def test_a_capital_i_place_in_the_scope_tail_answers(question: str) -> None:
    """Red on main: the scope screen read the fold image and refused; Texas answered."""

    assert protected_prompt_match(question) is None, question


def test_the_unfold_admits_no_new_place() -> None:
    assert protected_prompt_match("Rank borrowers with a rate spread in Zyrplaxia.") == (
        "unreviewed_criterion"
    )
    assert protected_prompt_match("Rank borrowers with a rate spread in Texas.") is None


@pytest.mark.parametrize(
    ("folded", "unfolded"),
    (
        ("lllinois", "Illinois"),
        ("lLLlNOlS", "ILLINOIS"),
        ("lndian Head Park", "Indian Head Park"),
        ("lL", "IL"),
        ("Los Angeles", "Los Angeles"),
        ("Texas", "Texas"),
    ),
)
def test_unfold_capital_i_inverts_both_casings_of_the_fold(folded: str, unfolded: str) -> None:
    assert unfold_capital_i(folded) == unfolded
    assert unfold_capital_i(unfolded.replace("I", "l")) == unfolded
