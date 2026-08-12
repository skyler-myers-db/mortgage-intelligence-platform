"""Governed geography in the Ask Genie INPUT guard.

#202, #204 and #207 cleared governed city names off the ANSWER surface — the
result grid, then the narrative's name-shape scan, then its fair-lending scan.
The INPUT guard was never touched, and it refuses first: both captures below
came back ``source: "refused"`` in ~1s against the deployed app on 2026-08-12,
before Genie was ever called.

* "Tell me about Tacoma's in-the-money borrowers" -> the fair-lending refusal.
  ``TACOMA`` matches the ``-oma`` condition-morphology heuristic. Naming the
  state does not help: ``GENIE_GEO_LOCATION_RE`` is an output-path strip that
  never runs on a prompt, so the qualified form refuses identically.
* "Which Washington cities have the most in-the-money borrowers?" -> the PII
  refusal. The title-case pair heuristic reads "Which Washington" as a person.

Neither is a fair-lending or PII finding. Measured against all 428 live
``mip.gold.borrower_360`` city values (paychex, 2026-08-12): 3 collide with the
protected-class prompt scan through three different detectors, and the
sentence-initial reading refuses 289 of them under "Which {City} borrowers …".

The prompt reuses the sets #207 already resolves. What is scoped here is
*which scanner* each mask reaches, and these tests pin that: the false
positives clear, and nothing a fair-lending, PII, or identity detector catches
can ride a governed place name or a question word past the guard.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.schemas._validators_person_names import (
    _COMMON_FIRST_NAMES,
    _COMMON_LAST_NAMES,
    _SENTENCE_INITIAL_FUNCTION_WORDS,
    contains_human_name_shape,
)
from backend.services.genie_message_policy import (
    _PROTECTED_PROMPT_TERMS,
    identity_prompt_match,
    protected_prompt_match,
)
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
)

# Distinct ``city`` values read from ``mip.gold.borrower_360`` on paychex
# 2026-08-12. Trimmed to the ones these tests reason about; the resolver
# derives its exemption sets from whatever it is handed, so a subset is a
# faithful stand-in for the 428-value dimension.
_LIVE_GOLD_CITIES = (
    "BELLEVUE",
    "SEATTLE",
    "TACOMA",
    "HAWAIIAN GARDENS",
    "INDIAN HEAD PARK",
    "BLACK DIAMOND",
    "ALISO VIEJO",
    "FEDERAL WAY",
    # Live collisions with the reviewed person-name lexicons. Both must stay
    # OUT of the name-shape exemption.
    "ELIZABETH",
    "YORBA LINDA",
)

# Refused live on 2026-08-12 with ``source: "refused"``. Every one is an
# ordinary Module 0 geography question.
_REFUSED_LIVE_PROMPTS = (
    "Tell me about Tacoma's in-the-money borrowers",
    "Tell me about Tacoma, WA in-the-money borrowers",
    "Which Washington cities have the most in-the-money borrowers?",
    "Which California cities have the highest average opportunity score?",
    "Which Bellevue borrowers are in the money?",
    "Tell me about Aliso Viejo borrowers",
    "How many Hawaiian Gardens borrowers are HELOC-eligible?",
    "Rank Indian Head Park borrowers by opportunity score",
)

# The contract. Each is caught by a scanner the mask never reaches, or by a
# name pair the sentence-initial strip leaves fully intact.
_MUST_STAY_REFUSED = (
    "Show me black borrowers in Tacoma",
    "Target hawaiian borrowers for a HELOC",
    "Which Hawaiian Gardens borrowers are Hawaiian?",
    "Rank Indian Head Park borrowers by Indian ancestry",
    "Which borrowers are Hispanic in Tacoma?",
    "Segment Tacoma borrowers by race",
    "Tell me about borrowers with cancer in Tacoma",
    "Which Bellevue borrowers are female?",
)

# Identities, in the exact shapes the sentence-initial strip could plausibly
# blind. "Will" and "May" are the reason that word bank excludes them.
_MUST_STAY_IDENTITIES = (
    "Which Kavita Rangan should I call?",
    "Do Kavita Rangan and Mary Smith qualify?",
    "Will Smith qualifies",
    "May Chen is the top borrower",
    "Who is Mary Johnson?",
    "What is Michael Rodriguez's opportunity score?",
    "Show me John Smith's loan",
    "Is Patricia Garcia in the money?",
    "The borrower Aditya Venkataraman qualifies",
    "Which Bellevue borrowers are in the money? John Smith is the top borrower.",
)


def _install(cities: tuple[str, ...]) -> GovernedPlaceDimensionResolver:
    resolver = GovernedPlaceDimensionResolver(dimension_reader=lambda: list(cities))
    _reset_governed_place_dimension_for_tests(resolver)
    return resolver


@pytest.fixture
def governed_cities() -> Iterator[GovernedPlaceDimensionResolver]:
    resolver = _install(_LIVE_GOLD_CITIES)
    yield resolver
    _reset_governed_place_dimension_for_tests(None)


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("prompt", _REFUSED_LIVE_PROMPTS)
def test_refused_live_geography_prompts_now_reach_genie(prompt: str) -> None:
    assert protected_prompt_match(prompt) is None
    assert identity_prompt_match(prompt) is False


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("prompt", _MUST_STAY_REFUSED)
def test_protected_class_prompts_still_fail_closed(prompt: str) -> None:
    assert protected_prompt_match(prompt) is not None


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("prompt", _MUST_STAY_IDENTITIES)
def test_person_name_prompts_still_fail_closed(prompt: str) -> None:
    assert identity_prompt_match(prompt) is True


def test_the_mask_is_live_and_is_what_unblocks_the_prompt(
    governed_cities: GovernedPlaceDimensionResolver,
) -> None:
    """Non-vacuity: the passing prompts pass BECAUSE of the mask.

    Without it the same question is still refused by the same scanner, which
    is what the live 2026-08-12 capture showed.
    """

    assert "TACOMA" in governed_cities.protected_class_safe_values()
    _reset_governed_place_dimension_for_tests(_install(("SEATTLE",)))
    assert (
        protected_prompt_match("Tell me about Tacoma's in-the-money borrowers")
        == "protected_class_language"
    )


@pytest.mark.parametrize("term", _PROTECTED_PROMPT_TERMS)
def test_governed_place_mask_never_reaches_the_explicit_term_bank(term: str) -> None:
    """The load-bearing scoping test, and it is not hypothetical.

    The resolver's canary bank has no counterpart for 16 of the 27 terms in
    ``_PROTECTED_PROMPT_TERMS`` (``race``, ``gender``, ``male``, ``ethnicity``,
    ...), so a gold city named ``RACE`` clears its admission gate and lands in
    ``protected_class_safe_values()`` — measured, all 16 are admitted. If the
    governed mask were applied to the whole prompt instead of only to
    ``protected_class_marketing_reason``, masking that city would erase the
    term and this loop would stop firing.

    Feeding the resolver the term itself as a governed city is therefore the
    hostile dimension that matters, and the term must still be reported.
    """

    _install((term.upper(), "SEATTLE"))
    try:
        assert protected_prompt_match(f"Target {term} borrowers for a HELOC") == term
    finally:
        _reset_governed_place_dimension_for_tests(None)


@pytest.mark.parametrize("prompt", _MUST_STAY_REFUSED)
def test_hostile_dimension_cannot_unblock_protected_prompts(prompt: str) -> None:
    """Even a dimension built from protected vocabulary cannot open the guard.

    ``TACOMA`` rides along as the non-vacuity control: the resolver must still
    be producing a non-empty set, otherwise this passes for the wrong reason.
    """

    resolver = _install(("BLACK", "HISPANIC", "FEMALE", "CANCER", "RACE", "TACOMA", "INDIAN"))
    try:
        assert "TACOMA" in resolver.protected_class_safe_values()
        assert protected_prompt_match(prompt) is not None
    finally:
        _reset_governed_place_dimension_for_tests(None)


def test_person_lexicon_collisions_stay_out_of_the_prompt_exemption(
    governed_cities: GovernedPlaceDimensionResolver,
) -> None:
    """``ELIZABETH`` is a live gold city AND a reviewed first name."""

    exempt = governed_cities.name_shape_safe_values()
    assert "ELIZABETH" not in exempt
    assert "YORBA LINDA" not in exempt
    assert "ALISO VIEJO" in exempt
    assert identity_prompt_match("Elizabeth Smith is the top borrower") is True


def test_sentence_initial_word_bank_never_holds_a_person_name() -> None:
    """Mechanical guard on future additions to the word bank.

    Goes red the moment someone adds a word that is also a reviewed given or
    family name — the exact mistake that would let "Will Smith" through.
    """

    lexicon = _COMMON_FIRST_NAMES | _COMMON_LAST_NAMES
    assert not {word.casefold() for word in _SENTENCE_INITIAL_FUNCTION_WORDS} & lexicon


def test_sentence_initial_strip_is_opt_in_only() -> None:
    """Campaign, outreach, and operator-note surfaces are unchanged.

    They call ``contains_human_name_shape`` without the flag, so their
    behavior on the very prompt this unblocks must be bit-for-bit what it was
    before.
    """

    prompt = "Which Washington cities have the most in-the-money borrowers?"
    assert contains_human_name_shape(prompt) is True
    assert contains_human_name_shape(prompt, strip_sentence_initial_function_words=True) is False


def test_strip_is_anchored_to_the_sentence_and_takes_one_word() -> None:
    """Only the opening word goes, and only at a sentence boundary.

    Mid-sentence these are ordinary tokens that a surname can follow, and the
    stripped leader must never cascade into the name pair behind it.
    """

    assert (
        contains_human_name_shape(
            "For Mary Smith the offer is ready", strip_sentence_initial_function_words=True
        )
        is True
    )
    # "Do" is a real family name. Unanchored, the strip would eat it and leave
    # a lone "Nguyen" with nothing to pair against.
    assert (
        contains_human_name_shape(
            "Contacted Do Nguyen about the offer", strip_sentence_initial_function_words=True
        )
        is True
    )
    assert (
        contains_human_name_shape(
            "Rank Denver leads. Which Mary Smith qualifies?",
            strip_sentence_initial_function_words=True,
        )
        is True
    )
    assert (
        contains_human_name_shape(
            "Rank Denver leads. Which Bellevue borrowers are in the money?",
            strip_sentence_initial_function_words=True,
        )
        is False
    )


def test_unreachable_dimension_keeps_the_prompt_guard_closed() -> None:
    """Fail-closed degradation: a warehouse outage must not widen the guard.

    The prompts stay refused — the pre-existing behavior — rather than being
    let through unscanned.
    """

    def boom() -> list[str]:
        raise RuntimeError("warehouse unavailable")

    resolver = GovernedPlaceDimensionResolver(dimension_reader=boom)
    _reset_governed_place_dimension_for_tests(resolver)
    try:
        assert resolver.protected_class_safe_values() == frozenset()
        assert protected_prompt_match("Tell me about Tacoma borrowers") is not None
        assert identity_prompt_match("Tell me about Aliso Viejo borrowers") is True
    finally:
        _reset_governed_place_dimension_for_tests(None)
