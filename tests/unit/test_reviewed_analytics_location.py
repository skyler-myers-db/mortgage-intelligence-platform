"""The location slot of the reviewed-analytics shapes, and what may enter it.

Matching one of these shapes sets ``reviewed_analytics``, which silences
``PROTECTED_HEALTH_TERM_MARKETING_RE`` and takes the unknown-criterion state
machine out of the decision for the whole clause. The slot is therefore a kill
switch with a hole in the middle, and these tests pin both halves of the deal:
every governed US place reaches it, and nothing else does.

Three defects are pinned here, all found on one 814-string differential
against 7612b021:

1. A city or county grain refused outright -- the reported gap.
2. Eleven states and DC refused, because b6c38f74 closed the slot to
   ``_validators_person_names.US_STATE_NAMES``, which calls itself "the federal
   list" but deliberately carries only the ONE-word states.
3. The open ``[A-Z][A-Za-z' -]{2,40}`` slot survived on the five OTHER shapes
   that carry a location, so "Chart borrowers by segment in dialysis centers"
   was still ALLOWED after the ranked ask was closed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.schemas._validators_protected_class import protected_class_marketing_reason
from backend.schemas.marketing_selection_reviewed_places import (
    COUNTY_NAME_EXCLUSIONS,
    US_STATE_CODES,
    US_STATE_NAMES_FEDERAL,
    _collides_with_governed_term,
    admitted_county_names,
    governed_analytics_cities,
    is_governed_analytics_location,
    register_governed_analytics_cities,
    shipped_county_names,
)

# The live gold ``city`` dimension's shape: upper-case, and carrying the four
# values the governed-place work has repeatedly found to collide with a
# detector (PR #206/#207).
GOLD_CITY_SAMPLE: tuple[str, ...] = (
    "SEATTLE",
    "CHICAGO",
    "HIGHLANDS RANCH",
    "BELLEVUE",
    "MEDINA",
    "ELIZABETH",
    "YORBA LINDA",
    "WINSTON-SALEM",
    "TACOMA",
    "HAWAIIAN GARDENS",
    "INDIAN HEAD PARK",
    "BLACK DIAMOND",
)
# Of that sample, the four a detector governs. Admitting any of them would hand
# the health/criterion kill switch to a term the banks exist to catch.
GOLD_CITY_COLLISIONS: tuple[str, ...] = (
    "TACOMA",
    "HAWAIIAN GARDENS",
    "INDIAN HEAD PARK",
    "BLACK DIAMOND",
)

RANKED = "Show me the top 20 borrowers with the highest lead scores in {loc}."
# Every reviewed shape that carries the shared location slot. A check wired
# into one of them and not the others is how the open slot survived b6c38f74.
LOCATION_CARRYING_SHAPES: tuple[str, ...] = (
    RANKED,
    "Rank the top cash-out candidates in {loc} and explain why each one qualifies",
    "Rank the top cash-out candidates in {loc}",
    "Show customers with an in-the-money refi in {loc}",
    "Chart borrowers by segment in {loc}",
    "What is the average lead score for borrowers by segment in {loc}",
)


@pytest.fixture(autouse=True)
def _isolated_city_registry() -> Iterator[None]:
    """The registry is process-wide state; never leak it between tests."""

    register_governed_analytics_cities(())
    yield
    register_governed_analytics_cities(())


def _refused(question: str) -> bool:
    return protected_class_marketing_reason(question) is not None


# --------------------------------------------------------------- the reports


@pytest.mark.parametrize(
    "question",
    (
        "Show me the top 20 borrowers with the highest lead scores in Seattle.",
        "Show me the top 20 borrowers with the highest lead scores in King County.",
        "Show me the top 20 borrowers with the highest lead scores in Highlands Ranch.",
    ),
)
def test_the_reported_city_and_county_asks_reach_the_shape(question: str) -> None:
    """The gap as reported: ranked analytics scoped below the state grain."""

    register_governed_analytics_cities(GOLD_CITY_SAMPLE)
    assert _refused(question) is False


@pytest.mark.parametrize(
    "state",
    ("New York", "North Carolina", "District of Columbia", "New Jersey", "West Virginia"),
)
def test_multi_word_states_reach_the_governed_slot(state: str) -> None:
    """b6c38f74 closed the slot to a list that has no two-word states in it."""

    assert _refused(RANKED.format(loc=state)) is False


def test_every_federal_state_reaches_the_slot() -> None:
    assert len(US_STATE_NAMES_FEDERAL) == 51
    refused = [name for name in US_STATE_NAMES_FEDERAL if _refused(RANKED.format(loc=name))]
    assert refused == []


def test_every_usps_code_reaches_the_slot_except_ms() -> None:
    """``ms`` is the one code that is also a governed term (multiple sclerosis)."""

    assert len(US_STATE_CODES) == 51
    refused = {code for code in US_STATE_CODES if _refused(RANKED.format(loc=code))}
    assert refused == {"MS"}
    assert _refused(RANKED.format(loc="ms")) is True
    # Mississippi stays reachable by name, and as a parenthetical after it.
    assert _refused(RANKED.format(loc="Mississippi")) is False
    assert _refused(RANKED.format(loc="the state of Mississippi (MS)")) is False


# --------------------------------------------------- nothing else gets in


# ``eczema`` is the load-bearing entry: it is NOT in the health-term bank, it
# is caught only by the criterion state machine that a shape match switches
# off. A slot screened with the banks alone lets it through.
GOVERNED_TAILS: tuple[str, ...] = (
    "dialysis centers",
    "cancer wards",
    "hospice care",
    "HIV clinics",
    "schizophrenia",
    "eczema",
    "methadone clinics",
    "nursing homes",
    "assisted living facilities",
    "Chinatown",
    "immigrant communities",
    "predominantly Black neighborhoods",
    "Section 8 housing",
    "single mothers",
    "wheelchair users",
    "zyrplax",
)


@pytest.mark.parametrize("shape", LOCATION_CARRYING_SHAPES)
@pytest.mark.parametrize("tail", GOVERNED_TAILS)
def test_no_governed_term_rides_the_location_slot_on_any_shape(shape: str, tail: str) -> None:
    """One slot, six shapes. The open spelling survived on five of them."""

    register_governed_analytics_cities(GOLD_CITY_SAMPLE)
    assert _refused(shape.format(loc=tail)) is True


@pytest.mark.parametrize("shape", LOCATION_CARRYING_SHAPES)
def test_a_governed_place_reaches_every_shape_that_carries_the_slot(shape: str) -> None:
    """The control for the sweep above: the same slot, a governed value."""

    register_governed_analytics_cities(GOLD_CITY_SAMPLE)
    assert _refused(shape.format(loc="Illinois")) is False
    assert _refused(shape.format(loc="King County")) is False
    assert _refused(shape.format(loc="Seattle")) is False


# ------------------------------------------------------- the city vocabulary


def test_a_city_refuses_until_the_governed_dimension_is_published() -> None:
    """Fail closed. An unresolvable dimension costs the grain, never the guard."""

    assert governed_analytics_cities() == frozenset()
    assert _refused(RANKED.format(loc="Seattle")) is True
    register_governed_analytics_cities(GOLD_CITY_SAMPLE)
    assert _refused(RANKED.format(loc="Seattle")) is False
    # A degraded resolve publishes empty, and the grain withdraws again.
    register_governed_analytics_cities(())
    assert _refused(RANKED.format(loc="Seattle")) is True


@pytest.mark.parametrize("city", GOLD_CITY_COLLISIONS)
def test_a_governed_city_that_collides_is_not_admitted(city: str) -> None:
    """Recognising a place is not authority to hand it the kill switch."""

    register_governed_analytics_cities(GOLD_CITY_SAMPLE)
    assert city.lower() not in governed_analytics_cities()
    assert _refused(RANKED.format(loc=city.title())) is True


def test_the_city_gate_admits_the_rest_of_the_sample() -> None:
    """Count what the filter removes: exactly the four, not the sample."""

    admitted = register_governed_analytics_cities(GOLD_CITY_SAMPLE)
    assert admitted == len(GOLD_CITY_SAMPLE) - len(GOLD_CITY_COLLISIONS)


@pytest.mark.parametrize(
    "junk",
    (
        "SEATTLE",  # a bare string IS iterable -- 7 single characters
        object(),  # not iterable at all
        None,  # what a degraded resolver hands over
        42,
        ("a b c d e f",),  # more tokens than any governed place value
        ("",),
        ("   ",),
    ),
)
def test_registration_is_fail_closed_on_junk(junk: object) -> None:
    """Called across a layer boundary by a resolver that degrades, so every
    one of these is a real input and none may raise into a dimension load."""

    assert register_governed_analytics_cities(junk) == 0
    assert governed_analytics_cities() == frozenset()


def test_publishing_the_dimension_invalidates_the_guard_memo() -> None:
    """The prompt guard memoizes by text; a pre-warm refusal must not stick."""

    question = RANKED.format(loc="Chicago")
    assert _refused(question) is True
    register_governed_analytics_cities(GOLD_CITY_SAMPLE)
    assert _refused(question) is False


# ----------------------------------------------------- the county vocabulary


def test_the_shipped_county_artifact_is_the_national_list() -> None:
    names = shipped_county_names()
    assert 1_500 <= len(names) <= 4_096
    assert {"King", "Cook", "Broward", "Orange"} <= names


def test_admitted_counties_are_the_shipped_names_minus_the_collisions() -> None:
    shipped = {name.lower() for name in shipped_county_names()}
    admitted = admitted_county_names()
    assert shipped - COUNTY_NAME_EXCLUSIONS <= admitted
    assert admitted & COUNTY_NAME_EXCLUSIONS == frozenset()
    # The only additions are period-stripped spellings of shipped names.
    extra = admitted - shipped
    assert extra
    assert all(name.replace(" ", "") in {s.replace(".", "").replace(" ", "") for s in shipped}
               for name in extra)


def test_abbreviated_counties_carry_a_spelling_that_survives_clause_splitting() -> None:
    """"... in St. Louis County." is cut in half before any shape is matched.

    Clause splitting on ``[.!?;:\\n]+`` happens first, so the abbreviated form
    cannot reach a shape at all. The period-stripped spelling is what makes the
    county reachable; the abbreviated one stays refused and is a documented
    segmentation residual, not something this slot can fix.
    """

    assert "st louis" in admitted_county_names()
    assert _refused(RANKED.format(loc="St Louis County")) is False
    assert _refused(RANKED.format(loc="St. Louis County")) is True


def test_committed_county_exclusions_are_all_real_collisions() -> None:
    """The cheap direction of ``tools/screen_county_place_vocabulary.py``.

    Re-deriving the whole set costs ~40s, so the full sweep lives in the tool.
    This runs the same gate over the 13 committed names, which is what catches
    an exclusion that stops being justified when a bank changes.
    """

    shipped = {name.lower(): name for name in shipped_county_names()}
    for excluded in sorted(COUNTY_NAME_EXCLUSIONS):
        assert excluded in shipped, f"{excluded!r} is not a shipped county name"
        assert _collides_with_governed_term(shipped[excluded]) is True


@pytest.mark.parametrize(
    ("county", "reason"),
    (
        ("White", "race"),
        ("Young", "age"),
        ("Box Elder", "age"),
        ("Canadian", "national origin"),
        ("Indian River", "national origin"),
        ("Christian", "religion"),
        # Only ``_contains_protected_class_proxy_pair`` catches this one, and
        # it is a function, not a pattern -- the name that proved a gate built
        # from a hand-listed set of banks is not the guard.
        ("Falls Church", "religion, via the proxy-pair detector"),
        ("Deaf Smith", "disability"),
        ("Sonoma", "-oma condition morphology"),
    ),
)
def test_a_county_carrying_a_governed_term_is_refused(county: str, reason: str) -> None:
    assert _refused(RANKED.format(loc=f"{county} County")) is True, reason


def test_oklahoma_is_a_state_even_though_it_is_an_excluded_county() -> None:
    """The federal list is checked first and is its own closed vocabulary.

    ``Oklahoma`` trips the ``-oma`` morphology, so it is excluded as a COUNTY
    name. Losing the state to the same heuristic would be a false refusal on a
    governed geography the product routes by.
    """

    assert "oklahoma" in COUNTY_NAME_EXCLUSIONS
    assert _refused(RANKED.format(loc="Oklahoma")) is False
    assert _refused(RANKED.format(loc="Oklahoma County")) is True


# ------------------------------------------------------------ normalization


@pytest.mark.parametrize(
    "location",
    (
        "Illinois",
        "illinois",
        "ILLINOIS",
        "the state of California (CA)",
        "King County",
        "King",
        "King County, WA",
        "  King   County  ",
        "WA",
        "wa",
    ),
)
def test_governed_spellings_resolve(location: str) -> None:
    assert is_governed_analytics_location(location) is True


@pytest.mark.parametrize(
    "location",
    (
        "",
        "   ",
        "dialysis centers",
        "eczema",
        "ms",
        "MS",
        "Wakanda",
        "eczema, WA",
        "Seattle",  # nothing registered in this test
        "King County of Washington and also eczema",
    ),
)
def test_ungoverned_spellings_do_not_resolve(location: str) -> None:
    assert is_governed_analytics_location(location) is False


def test_no_location_named_is_not_a_location_failure() -> None:
    """``None`` means the clause named no place; that is the shape's business."""

    assert is_governed_analytics_location(None) is True
    assert _refused("Show me the top 20 borrowers with the highest lead scores.") is False
