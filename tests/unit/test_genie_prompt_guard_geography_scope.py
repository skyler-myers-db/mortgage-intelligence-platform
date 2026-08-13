"""A geography scope narrows a reviewed attribute; it is not a second criterion.

Measured on b3007754 over ``Show borrowers with {attribute}{ scope}.``, nine
reviewed attributes x seven scopes: **49 of 63 refused** as
``unreviewed_criterion``. Every attribute passed bare and refused with "in
Texas", "in Washington", "in Seattle", "in ZIP 98404", "across Colorado" and
"in Cook County". ``home equity`` was the single outlier, and only because it
has a bare alternative that matches by a different route. ``fixed-rate loans``
predates every recent vocabulary change, so this is not fallout from one --
it is the shape of the capture itself: ``_CRITERION_TAIL`` takes the criterion
WHOLE (``[^.!?;:]{1,120}``) and then requires it to match the reviewed
fragment, and "a rate spread in Texas" is not "a rate spread".

Geography drill-down is a hero surface (CLAUDE.md), so this was the largest
false-positive class left in the grammar.

The place slot is the whole risk. Accepting an arbitrary title-case run behind
"in" would let a non-place tail ride a reviewed head, and the criterion state
machine is the only net that catches a health condition outside the enumerated
bank. So every test that proves a scope is ANSWERABLE has a twin here that
proves an ungoverned scope in the identical sentence still refuses.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from backend.schemas.marketing_selection_criteria import (
    _REVIEWED_AUDIENCE_DECISION_PATTERNS,
    _REVIEWED_PRENOMINAL_AUDIENCE_DIRECTIVE_RE,
    _contains_unreviewed_audience_decision,
    is_reviewed_read_only_analytics_text,
)
from backend.schemas.marketing_selection_reviewed_places import (
    US_STATE_NAMES_FEDERAL,
    admitted_county_names,
    governed_analytics_cities,
)
from backend.schemas.marketing_selection_vocabulary import (
    _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE,
    _SCOPE_GROUP_PREFIX,
    matches_reviewed_mortgage_attribute,
)
from backend.services.genie_message_policy import protected_prompt_match
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
)

# Live gold city values (paychex, 2026-08-12). ``TACOMA`` is here on purpose:
# it is a real governed city that the ``-oma`` condition morphology screens
# OUT, so it doubles as the control proving the admission gate runs.
_LIVE_CITY_SAMPLE = ("SEATTLE", "SPOKANE", "BELLEVUE", "PLANO", "AURORA", "TACOMA")


@pytest.fixture
def published_cities() -> Iterator[None]:
    """Publish a city dimension for the life of one test, then withdraw it.

    Injects a real resolver rather than calling the registry directly, because
    the registry is only half the contract: publishing happens on RESOLVE, and
    a resolve is what every guard call triggers. Registering by hand passes
    even if the services-to-schemas wiring is severed -- and worse, the first
    guard call would then resolve a degraded dimension and publish an empty
    set straight over the hand-registered one.

    The reset also withdraws the vocabulary: the registry is a module global in
    the schemas layer, so a test that leaves it populated hands its cities to
    every later test file.
    """

    _reset_governed_place_dimension_for_tests(
        GovernedPlaceDimensionResolver(dimension_reader=lambda: list(_LIVE_CITY_SAMPLE))
    )
    try:
        yield
    finally:
        _reset_governed_place_dimension_for_tests(None)


# Every reviewed attribute in the measured table, spelled as an operator writes
# it. ``fixed-rate loans`` and ``conventional loans`` are here because the
# outage was never specific to recently-added vocabulary.
_REVIEWED_ATTRIBUTES = (
    "a rate spread",
    "home equity",
    "helocs",
    "conventional loans",
    "fixed-rate loans",
    "an opportunity score",
    "a loan balance",
    "high LTV",
    "recommended offers",
    "the highest opportunity scores",
    "average home equity",
)
# Scopes that name a governed US place, one per grain and per preposition.
_GOVERNED_SCOPES = (
    " in Texas",
    " in New York",
    " in North Carolina",
    " in District of Columbia",
    " across Colorado",
    " within Ohio",
    " throughout Florida",
    " in the state of Washington",
    " in Cook County",
    " in Cook",
    " in ZIP 98404",
    " in zip code 30301",
    " in postal code 80202",
    " in this metro",
    " in the current coverage",
    " in our footprint",
)
# Text that is NOT a governed place. Each must refuse behind a REVIEWED
# attribute -- that is the fail-open this slot exists to prevent. ``in 98404``
# is here because a bare five-digit run is deliberately not a ZIP.
_UNGOVERNED_SCOPES = (
    " in Zyrplax",
    " in dialysis centers",
    " in hospice care",
    " in methadone clinics",
    " in nursing homes",
    " in Chinatown",
    " in the mosque",
    " in 98404",
    " in memory care",
    " in Section 8 housing",
)
# ``Select borrowers with {c}.`` is deliberately ABSENT, and its absence is
# pinned by ``test_a_formation_verb_still_refuses_a_scoped_attribute`` below:
# that shape refuses through a different detector, on main and on this branch
# alike, and closing it is a different change with its own control battery.
_SHAPES = (
    "Show borrowers with {c}.",
    "Show me the top borrowers with {c}.",
    "Show me the top 50 borrowers with {c}.",
    "Rank borrowers with {c}.",
    "Show me borrowers who have {c}.",
    "Add borrowers with {c} to the campaign.",
)


@pytest.mark.parametrize("attribute", _REVIEWED_ATTRIBUTES)
@pytest.mark.parametrize("scope", _GOVERNED_SCOPES)
def test_a_governed_scope_leaves_a_reviewed_attribute_answerable(
    scope: str, attribute: str
) -> None:
    """The 49-of-63 table, restored.

    Parametrized over the vocabulary AND the scope because the criterion is
    matched whole: an attribute that passes bare says nothing about the same
    attribute behind "in Texas", which is exactly how the outage went unseen.
    """

    question = f"Show borrowers with {attribute}{scope}."
    assert protected_prompt_match(question) is None, question


@pytest.mark.parametrize("shape", _SHAPES)
@pytest.mark.parametrize("scope", (" in Texas", " in Cook County", " in ZIP 98404"))
def test_a_governed_scope_answers_in_every_clause_shape(shape: str, scope: str) -> None:
    """Shapes, not just the matcher.

    The clause patterns in ``marketing_selection_criteria`` embed the reviewed
    LIST fragment DIRECTLY and never consult
    ``_REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE``. A fix proved only against the
    full matcher has left the prompt boundary broken twice (the article, #213),
    so the scope is asserted through the whole guard in each shape it reaches.
    """

    question = shape.format(c=f"a rate spread{scope}")
    assert protected_prompt_match(question) is None, question


@pytest.mark.parametrize("attribute", _REVIEWED_ATTRIBUTES)
@pytest.mark.parametrize("scope", _UNGOVERNED_SCOPES)
def test_an_ungoverned_scope_is_never_a_reviewed_criterion(scope: str, attribute: str) -> None:
    """The safety half, and the reason the slot is a vocabulary not a shape.

    A reviewed head must not carry a non-place tail. ``in dialysis centers``
    and ``in hospice care`` are the measured carriers that a bounded title-case
    shape admitted when this was tried without a gazetteer.

    Asserted on the reviewed VOCABULARY, which is the artifact this change
    edits, because neither layer above it can carry the property alone. A
    SECOND surface -- the reviewed read-only analytics shapes -- still has an
    OPEN ``[A-Z][A-Za-z' -]{2,40}`` location slot, and matching one of those
    shapes is a kill switch consulted BOTH by ``protected_class_marketing_reason``
    and, one layer down, inside ``_contains_unreviewed_audience_decision``
    itself. So for the attributes that reach an analytics shape, everything
    above the vocabulary short-circuits before this machine runs at all. That
    hole is on main today (measured: "Show borrowers with home equity in
    dialysis centers." is ALLOWED at 26b3ae56, byte-identical on this branch)
    and PR #224 is the change that closes it.
    """

    criterion = f"{attribute}{scope}"
    assert matches_reviewed_mortgage_attribute(criterion) is False, criterion


@pytest.mark.parametrize("attribute", _REVIEWED_ATTRIBUTES)
@pytest.mark.parametrize("scope", _UNGOVERNED_SCOPES)
def test_an_ungoverned_scope_is_refused_or_owned_by_the_analytics_slot(
    scope: str, attribute: str
) -> None:
    """End-to-end twin of the test above, honest about the surface it shares.

    Either the guard refuses the question, or it was admitted by a reviewed
    ANALYTICS shape -- a different slot, a different PR (#224), and the second
    disjunct is what stops this test from silently passing if the criterion
    machine ever starts admitting an ungoverned place.
    """

    question = f"Show borrowers with {attribute}{scope}."
    if protected_prompt_match(question) is not None:
        return
    assert is_reviewed_read_only_analytics_text(question), question


@pytest.mark.parametrize(
    "attribute",
    ("a credit score", "FICO", "household income", "citizenship", "eczema", "zyrplax"),
)
@pytest.mark.parametrize("scope", ("", " in Texas", " in Cook County", " in Seattle"))
def test_a_scope_never_admits_an_unreviewed_attribute(
    published_cities: None, scope: str, attribute: str
) -> None:
    """The fail-closed default is exactly as strong as before the scope existed.

    The attribute alternation is untouched, so an unreviewed attribute stays
    unreviewed with or without a geography scope. This is the acceptance bar
    the differential battery measures at scale; these are its named pins.
    """

    question = f"Show borrowers with {attribute}{scope}."
    assert protected_prompt_match(question) is not None, question


@pytest.mark.parametrize(
    "criterion",
    (
        "home equity in Texas and eczema",
        "a rate spread in Texas with a hijab",
        "home equity and eczema in Texas",
        "a rate spread in Texas and a credit score",
        "helocs in Cook County and race",
    ),
)
def test_a_scope_cannot_launder_an_unreviewed_conjunct(criterion: str) -> None:
    """A reviewed head plus a governed place must not carry an unreviewed one.

    The scope is an optional group inside the ANCHORED vocabulary, so anything
    the pair does not account for leaves text unmatched and the whole criterion
    unreviewed. Pinned directly because every widening of this fragment also
    widens the allow-lookahead in ``marketing_safety_terms``.

    On the vocabulary, for the reason
    ``test_an_ungoverned_scope_is_never_a_reviewed_criterion`` gives: the
    analytics slot shadows every layer above it for some of these.
    """

    assert matches_reviewed_mortgage_attribute(criterion) is False, criterion


def test_a_formation_verb_still_refuses_a_scoped_attribute() -> None:
    """ "Select borrowers with a rate spread in Texas." refuses, and not here.

    Unchanged by this branch and not this branch's to change. The refusal is
    ``protected_class_language``, not ``unreviewed_criterion``: the allow
    lookahead inside ``PROTECTED_HEALTH_GOVERNANCE_INTENT_RE`` requires the
    object of "with" to be a complete reviewed attribute followed immediately
    by a terminator, and " in Texas" is neither. Widening THAT lookahead was
    implemented, measured over a 99,408-prompt sweep and pulled -- it costs
    3,456 refusals carrying an unenumerated health condition -- and a
    membership screen cannot be expressed inside a negative lookahead, so the
    governed vocabulary this branch adds does not rescue it either.

    Recorded as a test rather than a comment so the day it changes, something
    says so.
    """

    assert protected_prompt_match("Select borrowers with a rate spread in Texas.") is not None
    # ... while the criterion machine, which this branch owns, is satisfied.
    assert (
        _contains_unreviewed_audience_decision("Select borrowers with a rate spread in Texas")
        is False
    )


@pytest.mark.parametrize(
    "question",
    (
        "Include high equity borrowers in a reviewed cohort.",
        "Shortlist high equity homeowners in a reviewed cohort.",
        "Include high equity borrowers in the campaign.",
        "Rank the high LTV borrowers in this segment.",
        "Add borrowers with home equity to the queue.",
        "Select high equity borrowers for the campaign.",
    ),
)
# Not in the list, and deliberately: "Add borrowers with home equity to the
# REVIEWED queue." refuses on main and on this branch alike. It was in an
# earlier draft of this test as an invented example, and the branch was
# briefly suspected before the baseline was checked. Every case above is
# verified answerable at 26b3ae56.
def test_the_scope_slot_does_not_steal_a_destination(question: str) -> None:
    """An optional slot in front of other optional slots changes which parse wins.

    Every one of these is answered on main. The open place shape matches "a
    reviewed cohort" happily, so the pattern FULLMATCHED with the scope group
    holding a destination, the membership screen rejected it, and the sentence
    refused -- ``re`` will not backtrack into another parse once the overall
    match has succeeded, so no screen can recover it. Three of these were
    caught by the full unit suite AFTER a 15,600-probe differential reported
    zero losses; the differential's shapes had no destination tail.

    Fixed in the fragment, by a lookahead over the closed destination noun set,
    because only the regex engine can decide which parse wins.
    """

    assert protected_prompt_match(question) is None, question


def test_the_capital_i_fold_does_not_hide_the_zip_keyword() -> None:
    """``ZIP`` reaches the grammar as ``ZlP`` on one scan pass.

    The guard scans a confusable fold alongside the text, and a capital ``I``
    folds to ``l``. With a plain ``zip`` literal, all 51 federal state names
    passed while every reviewed attribute refused with a ZIP scope -- the
    signature of a literal that does not survive the fold. Same convention the
    audience-formation grammar already uses for ``(?:insert|lnsert)``.
    """

    for question in (
        "Show borrowers with a rate spread in ZIP 98404.",
        "Show borrowers with home equity in ZIP 98404.",
        "Rank borrowers with an opportunity score in ZIP code 98404.",
    ):
        assert protected_prompt_match(question) is None, question


def test_every_federal_state_name_is_a_governed_scope() -> None:
    """All 51, because a partial state list has shipped here before.

    ``_validators_person_names.US_STATE_NAMES`` calls itself the federal list
    and deliberately carries only the ONE-word states; reusing it for a
    location tail silently dropped eleven states and DC for a week.
    """

    refused = [
        state
        for state in US_STATE_NAMES_FEDERAL
        if protected_prompt_match(f"Show borrowers with home equity in {state}.") is not None
    ]
    assert refused == [], refused


def test_a_city_scope_needs_a_published_dimension(published_cities: None) -> None:
    """Degradation costs the grain, never the guard.

    Cities are live Cotality coverage and cannot be committed, so they arrive
    by registration. With no dimension published a city question refuses --
    the same answer the guard gave before this slot existed -- and the fixture
    proves the published state is what changes it.
    """

    question = "Show borrowers with a rate spread in Seattle."
    assert protected_prompt_match(question) is None, question

    _reset_governed_place_dimension_for_tests(None)
    assert protected_prompt_match(question) is not None, question


def test_a_governed_city_that_carries_a_governed_term_is_still_refused(
    published_cities: None,
) -> None:
    """The non-exempt control. Recognising a place is not authority to admit it.

    ``TACOMA`` is a real live gold city and IS in the published sample, yet the
    admission gate screens it out on the ``-oma`` condition morphology -- five
    of the six sample values are admitted, and this is the sixth. Without a
    control like this, an exemption sweep measures only its own vocabulary
    echoing back.
    """

    assert protected_prompt_match("Show borrowers with a rate spread in Tacoma.") is not None
    assert (
        protected_prompt_match("Show borrowers with a rate spread in Indian River County.")
        is not None
    )
    # Asserted AFTER a guard call, because publishing happens on the first
    # resolve and a guard call is what triggers it.
    assert "tacoma" not in governed_analytics_cities()
    assert "seattle" in governed_analytics_cities()


def test_the_county_vocabulary_is_the_shipped_national_artifact() -> None:
    """Counties come from the map drill-down's own TopoJSON, screened once.

    An empty set here means the artifact moved and every county-grain question
    silently refuses, which is fail-closed but invisible.
    """

    counties = admitted_county_names()
    assert len(counties) > 1_500, len(counties)
    assert "cook" in counties
    assert "st louis" in counties  # period-stripped: clause splitting cuts "St."
    # Screened out on their own merits, and each for a different bank.
    assert "white" not in counties
    assert "christian" not in counties
    assert "deaf smith" not in counties


def test_every_scope_slot_in_every_compiled_pattern_is_screened() -> None:
    """A check wired into one call site is a check that is not wired in.

    The scope fragment is a SHAPE; membership is what makes it safe. This walks
    the compiled patterns and asserts each declares its scope groups under the
    prefix :func:`match_scopes_are_governed` keys on, so a slot added to a new
    pattern is screened by construction rather than by remembering.
    """

    patterns: tuple[re.Pattern[str], ...] = (
        _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE,
        _REVIEWED_PRENOMINAL_AUDIENCE_DIRECTIVE_RE,
        *_REVIEWED_AUDIENCE_DECISION_PATTERNS,
    )
    scoped = [p for p in patterns if any(g.startswith(_SCOPE_GROUP_PREFIX) for g in p.groupindex)]
    assert len(scoped) >= 6, [p.groupindex for p in patterns]
    for pattern in patterns:
        for group in pattern.groupindex:
            # Any group whose name mentions a scope must carry the screened
            # prefix; a near-miss name would silently opt out of the check.
            if "scope" in group:
                assert group.startswith(_SCOPE_GROUP_PREFIX), group
