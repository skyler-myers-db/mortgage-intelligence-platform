"""A place the warehouse does not carry is still a place, not a person.

The sentence-initial strip could only recognise geography the gold dimension
actually contains, so any city outside the refreshed footprint read as an
identity. Measured 2026-08-12 over 720 prompts naming 18 real US cities outside
the six covered states (IL, CA, FL, TX, WA, CO): **466 refused, 64.7%, and 412
of those as PII** — "Which Boise borrowers are in the money?" answered with "I
do not return borrower names, street-level addresses ...". The control on
governed cities refused 28 of 160.

The honest answer for those questions is the geography router's "outside the
current refreshed data footprint", which is what they get once the guard stops
reading them as identities — exactly what "How many in-the-money borrowers are
in Oklahoma?" already returns.

The strip now also fires when a title-case run is followed by a GOVERNED
POPULATION NOUN, because nobody writes "Which <person> borrowers". Safe by the
same argument as the place half: only the leading function word is removed, so
a real name pair behind it still scans in full.
"""

from __future__ import annotations

import itertools

import pytest

from backend.services.genie_message_policy import identity_prompt_match
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
)

_GOLD_CITIES = ("SEATTLE", "BELLEVUE", "CHICAGO", "AURORA", "TACOMA", "ALISO VIEJO")

# Real US cities outside the six covered states. Deliberately NOT in the
# fixture dimension: the point is that the warehouse does not carry them.
_OUT_OF_COVERAGE = ("Boise", "Reno", "Fresno", "Tulsa", "Omaha", "Nashville", "Phoenix", "Memphis")
_SCOPED_TEMPLATES = (
    "{leader} {city} borrowers are in the money?",
    "{leader} {city} leads have the highest opportunity score?",
    "{leader} {city} cities lead on HELOC candidates?",
)
_LEADERS = ("Which", "What", "The", "Are", "Rank")

_OUT_OF_COVERAGE_SCOPES = tuple(
    template.format(leader=leader, city=city)
    for leader, city, template in itertools.product(_LEADERS, _OUT_OF_COVERAGE, _SCOPED_TEMPLATES)
)

# The scope reading must never rescue a real name pair.
_IDENTITIES_MUST_REFUSE = (
    "Which John Smith borrowers are in the money?",
    "Which Mary Smith borrowers are in the money?",
    "The Patricia Garcia leads look strong",
    "Which Kavita Rangan should I call?",
    "Do Nguyen qualifies for a HELOC?",
    "Do Medina qualifies for a HELOC?",
    "An Elizabeth qualifies for a HELOC?",
    "Elizabeth Smith is the top borrower",
    "Will Smith qualifies",
    "Who is Mary Johnson?",
)


@pytest.fixture(autouse=True)
def _governed_cities():
    resolver = GovernedPlaceDimensionResolver(dimension_reader=lambda: list(_GOLD_CITIES))
    _reset_governed_place_dimension_for_tests(resolver)
    yield resolver
    _reset_governed_place_dimension_for_tests(None)


@pytest.mark.parametrize("prompt", _OUT_OF_COVERAGE_SCOPES)
def test_a_place_outside_coverage_is_a_scope_not_an_identity(prompt: str) -> None:
    assert identity_prompt_match(prompt) is False


@pytest.mark.parametrize("prompt", _IDENTITIES_MUST_REFUSE)
def test_a_name_pair_before_a_population_noun_still_refuses(prompt: str) -> None:
    """The load-bearing half.

    Stripping the leader leaves the pair intact, so "Which John Smith
    borrowers" is still caught by the pair itself rather than by the leader.
    """

    assert identity_prompt_match(prompt) is True


def test_the_scope_reading_needs_an_actual_population_noun() -> None:
    """Without one, a title-case run is not treated as a scope.

    Goes red if the noun list is replaced by something permissive: the whole
    safety of this alternative is that the follower vocabulary is closed.
    """

    assert identity_prompt_match("Which Kavita Rangan should I call?") is True
    assert identity_prompt_match("Which Boise borrowers are in the money?") is False


def test_a_governed_city_still_clears_without_a_population_noun() -> None:
    """The place half of the gate is untouched by the new alternative."""

    assert identity_prompt_match("Which Bellevue is largest?") is False
    assert identity_prompt_match("Which Boise is largest?") is True


@pytest.mark.parametrize("leader", ("Do", "An", "No"))
def test_the_surname_leaders_still_do_not_strip_even_before_a_scope(leader: str) -> None:
    """The accepted cost of keeping "Do Nguyen" safe, pinned so it stays visible.

    ``Do``, ``An`` and ``No`` are surname-first Vietnamese and Korean family
    names and are excluded from the word bank entirely, so they never strip --
    not before a governed place, and not before a population noun either.
    "Do Boise borrowers are in the money?" therefore still refuses. That is the
    deliberate trade for "Do Nguyen qualifies for a HELOC?" keeping its pair,
    and it is documented rather than quietly absorbed.
    """

    assert identity_prompt_match(f"{leader} Boise borrowers are in the money?") is True
    assert identity_prompt_match(f"{leader} Bellevue borrowers are in the money?") is True
