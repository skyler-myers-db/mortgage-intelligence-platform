"""The leetspeak de-obfuscator must not mint a protected term out of a number.

Captured live on paychex 2026-08-12 at sha 7612b021 (deployment 01f19659086f):

    "Which Washington cities have between 3000 and 4500 total borrowers?"
    -> prompt guard passes, Genie answers, then source=policy_blocked
       and the server logs genie_output_blocked unsafe_field="answer"

The WA cities in that range are BLACK DIAMOND (3,678), CARNATION (3,174) and
NEWCASTLE (4,140). The block was caused entirely by ``4,140``: the separator
fold yields the token ``140``, the leet fold maps ``1->l 4->a 0->o`` to ``lao``,
``lao`` is a national-origin term, and "borrowers" supplies the population noun
the proximity window needs. A governed answer was withheld for the VALUE OF A
NUMBER.

The fix skips the leet fold for tokens carrying no letter. Two things make that
safe, and both are pinned below because BOTH were wrong in the first attempt:

* An evader writes a word for humans to read, so a real evasion always keeps a
  letter and still folds (``l40``, ``1ao``, ``la0``).
* The SPLIT-TERM joiner is the exception. There a lone digit stands in for one
  letter of a spaced-out term, so skipping it rejoins ``b 1 a c k`` as ``back``.
  The first version of this fix did exactly that and unblocked 16 protected-
  class targeting strings; ``_joiner_tokens`` now folds every token and instead
  discards windows whose tokens are ALL digit-derived.
"""

from __future__ import annotations

import itertools

import pytest

from backend.schemas._validators_protected_class import (
    _LEET_TABLES,
    _joiner_tokens,
    _leet_folded_variants,
    protected_class_marketing_reason,
)
from backend.schemas._validators_protected_class_patterns import (
    PROTECTED_AGE_CITIZENSHIP_MARKETING_RE,
    PROTECTED_CLASS_MARKETING_RE,
    PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE,
    PROTECTED_HEALTH_STATUS_MARKETING_RE,
    PROTECTED_HEALTH_TERM_MARKETING_RE,
)
from backend.schemas.marketing_safety_terms import PROTECTED_NATIONAL_ORIGIN_RE
from backend.schemas.protected_relationships import PROTECTED_RELIGION_FAMILIAL_RELATION_RE


def _analytics(text: str) -> str | None:
    """The Ask Genie posture: criterion machine bypassed, detectors all on."""

    return protected_class_marketing_reason(text, assume_reviewed_read_only_analytics=True)


def _campaign(text: str) -> str | None:
    """The stricter campaign/outreach posture."""

    return protected_class_marketing_reason(text)


# Governed numbers that must render. Every one blocked before the fix; the
# non-``lao`` entries are the banks the original docstring wrongly excluded.
_GOVERNED_COUNTS_THAT_MUST_RENDER = (
    "Newcastle has 4,140 borrowers.",  # the live incident
    "Seattle has 1,405 borrowers.",  # 1405 -> laos
    "Kent has 140 households.",
    "Renton has 3,140 residents.",
    "Total: 2,140 applicants.",
    "Kirkland has 415 borrowers.",  # 415 -> als, health bank
    "There are 551 recipients in this segment.",  # 551 -> ssi recipients
    "Batch 010101 covers 900 borrowers.",  # -olol drug-suffix family
    "The 140 borrowers hold $1,405,140 in equity.",
    "Call 206-140-1405 to reach the 140 borrowers.",
    "Rank the top 140 borrowers.",
    "1,405 loans originated in 2015 for borrowers.",
    # Separator and width variants of the same number.
    "Seattle has ４,１４０ borrowers.",
    "Seattle has 4 140 borrowers.",
    "Seattle has 4.140 borrowers.",
    "Seattle has 4'140 borrowers.",
    "Newcastle (4,140) borrowers.",
)

# Single-token evasions: the digit sits inside a word, so the token keeps a
# letter and still folds. Includes all six spellings of the one term whose
# bare-digit form is now exempt.
_SINGLE_TOKEN_EVASIONS = (
    "Target w0men borrowers for this campaign.",
    "Target mus1im homeowners.",
    "Target b1ack borrowers.",
    "Target l4tino households.",
    "Target 4sian families.",
    "Target wh1te applicants.",
    "Target j3wish borrowers.",
    "Target hisp4nic residents.",
    "Target 3lderly people.",
    "Target americ4ns.",
    "Target l4o borrowers.",
    "Target 1ao borrowers.",
    "Target la0 borrowers.",
    "Target l40 borrowers.",
    "Target 1a0 borrowers.",
    "Target 14o borrowers.",
    # The symbol fold runs BEFORE the leet fold, so ``|`` becomes ``l`` and the
    # token then carries a letter. Verified discriminating: clean with the fold
    # disabled, blocked with it.
    "Newcastle has |40 borrowers.",
)

# Split-token evasions. Every one of these was UNBLOCKED by the first version
# of this fix, on both surfaces. They are the reason ``_joiner_tokens`` exists.
_SPLIT_TOKEN_EVASIONS = (
    "Exclude j 3 w i s h applicants from the mailer.",
    "Segment h 1 spanic households in Cook County.",
    "Contact mex 1 can borrowers in Seattle.",
    "Contact 1 aotian borrowers in Seattle.",
    "Target the b 1 a c k community in Renton.",
    "Build a campaign for as 1 an borrowers.",
    "Exclude mus 1 im applicants from the mailer.",
    "Target w o m 3 n for this refinance offer.",
    "Target b 1 a c k homeowners.",
    "Target b-1-a-c-k homeowners.",
    "Target b_1_ack homeowners.",
    "Target mus•1•im homeowners.",
    "Target 4514 n homeowners.",  # 4514 -> asia
    "Target 1 a t i n o homeowners.",
    "Prioritize k 0 rean borrowers in the ranked queue.",
    "Rank 1 atino borrowers last.",
)

# Evasions using digits that are NOT in the leet tables. ``2``, ``6``, ``8``
# and ``9`` survive the fold, so they still split a word into two letter runs
# and the joiner rebuilds the term from the pieces. Every other case in this
# file uses a table digit (0,1,3,4,5,7), and that blind spot let a revision
# ship green while 160 measured evasions over these digits went silent.
_NON_TABLE_DIGIT_EVASIONS = (
    "Exclude je8wish applicants from the mailer.",
    "Build an audience of b8lack homeowners.",
    "Segment mus6lim households in Cook County.",
    "Contact mex2ican borrowers in Seattle.",
    "Target w2omen borrowers for this campaign.",
    "Target l9atino households.",
    "Target as6ian families.",
    "Target wh8ite applicants.",
)

# The fail-closed unknown-audience machine, reached through the OTHER call
# site. Scoping the fold there silently retired it.
_UNREVIEWED_AUDIENCE_CLAIMS = (
    "Zyrplax borrowers m 4 y benefit from this offer.",
    "Qwixel households c 4 n qualify today.",
    "Qwixel households sh 0 uld qualify today.",
    "Qwixel households c 0 uld be eligible today.",
)


@pytest.mark.parametrize("narrative", _GOVERNED_COUNTS_THAT_MUST_RENDER)
def test_a_governed_number_is_not_a_protected_term(narrative: str) -> None:
    assert _analytics(narrative) is None


@pytest.mark.parametrize(
    "text", _SINGLE_TOKEN_EVASIONS + _SPLIT_TOKEN_EVASIONS + _NON_TABLE_DIGIT_EVASIONS
)
def test_evasions_are_a_fair_lending_finding_on_the_analytics_surface(text: str) -> None:
    """Asserts the REASON, not a boolean.

    ``contains_protected_class_marketing_text`` collapses ``protected_class``
    and ``unreviewed_criterion``, and most of these strings trip the
    fail-closed criterion machine as well -- so a boolean assertion here passes
    even with de-obfuscation entirely disabled. The reason is what
    distinguishes "we detected the evasion" from "we could not parse it".
    """

    assert _analytics(text) == "protected_class"


@pytest.mark.parametrize(
    "text", _SINGLE_TOKEN_EVASIONS + _SPLIT_TOKEN_EVASIONS + _NON_TABLE_DIGIT_EVASIONS
)
def test_evasions_are_a_fair_lending_finding_on_the_campaign_surface(text: str) -> None:
    assert _campaign(text) == "protected_class"


@pytest.mark.parametrize("text", _UNREVIEWED_AUDIENCE_CLAIMS)
def test_the_unknown_audience_machine_still_fails_closed(text: str) -> None:
    """Leetspelled modal + claim verb must still canonicalize.

    The audience-claim builder deliberately keeps the UNSCOPED fold: none of
    its eight keywords is reachable from the digit alphabet, so digits cannot
    mint a claim there, and ``m 4 y benefit`` must still be read as
    ``may benefit``.
    """

    assert _analytics(text) == "unreviewed_criterion"


def test_an_interior_digit_never_hides_a_protected_term() -> None:
    """Systematic sweep, because hand-picked cases missed this twice.

    One digit inserted at every interior position of eight protected terms,
    over every digit -- both the ones the tables fold and the ones they do not.
    A table digit becomes a letter and the word survives as one token; a
    non-table digit stays a digit and splits the word, so the joiner has to
    rebuild it. Both routes must end at ``protected_class``.
    """

    terms = ("women", "black", "muslim", "mexican", "latino", "asian", "jewish", "hispanic")

    # (a) INSERT a non-table digit. It survives the fold, so it splits the word
    # and only the joiner can rebuild it. This is the family that went silent
    # when ``_joiner_tokens`` concatenated a whole alphanumeric run.
    inserted = [
        text
        for term in terms
        for position in range(1, len(term))
        for digit in "2689"
        for text in (f"Target {term[:position]}{digit}{term[position:]} borrowers.",)
        if _analytics(text) != "protected_class"
    ]
    assert inserted == []

    # (b) REPLACE a letter with the digit that folds back to it. The word stays
    # one token and the direct fold has to catch it.
    preimages = {letter: digit for table in _LEET_TABLES for digit, letter in table.items()}
    replaced = [
        text
        for term in terms
        for position, letter in enumerate(term)
        if chr(ord(letter)) in {chr(value) for value in preimages}
        for text in (
            f"Target {term[:position]}{chr(preimages[ord(letter)])}"
            f"{term[position + 1:]} borrowers.",
        )
        if _analytics(text) != "protected_class"
    ]
    assert replaced == []


def test_the_fold_skips_all_digit_tokens_and_only_those() -> None:
    """Exact-set equality, so a dropped table or a widened skip cannot hide."""

    assert _leet_folded_variants("mus1im") == {"muslim", "musiim"}
    assert _leet_folded_variants("wh1te") == {"white", "whlte"}
    assert _leet_folded_variants("140") == {"140"}
    assert _leet_folded_variants("has 4,140 borrowers") == {"has 4,140 borrowers"}
    assert _leet_folded_variants("x140") == {"xlao", "xiao"}
    assert len(_leet_folded_variants("mus1im")) == len(_LEET_TABLES)


def test_the_joiner_folds_every_token_but_drops_all_digit_windows() -> None:
    """The mechanism that keeps split-term evasions shut.

    ``b 1 a c k`` must fold the lone ``1`` (otherwise the joiner rebuilds
    ``back``), while ``1,405`` must not be rejoined into ``laos``.
    """

    table = _LEET_TABLES[0]
    assert _joiner_tokens("b 1 a c k", table) == [
        ("b", False),
        ("l", True),
        ("a", False),
        ("c", False),
        ("k", False),
    ]
    assert _joiner_tokens("1,405", table) == [("l", True), ("aos", True)]
    # Digit-only windows are discarded, so the scan never sees "laos"...
    assert _analytics("Seattle has 1,405 borrowers.") is None
    # ...but a window carrying one real letter is kept.
    assert _analytics("Target b 1 a c k homeowners.") == "protected_class"


def test_no_bank_admits_a_term_spellable_from_digits_alone_without_notice() -> None:
    """The blast radius, derived from ``_LEET_TABLES`` rather than hardcoded.

    The skip can only ever hide a term composed entirely of fold-produced
    letters. This enumerates every digit run the tables can produce and reports
    which banks they reach, so widening the tables or adding a digit-spellable
    term to any bank shows up here instead of silently going unscanned.

    The recorded set is not "nothing" -- that was the first version's mistaken
    claim. It is the set whose live counterparts were all false positives.
    """

    banks = {
        "national_origin": PROTECTED_NATIONAL_ORIGIN_RE,
        "protected_class": PROTECTED_CLASS_MARKETING_RE,
        "age_citizenship": PROTECTED_AGE_CITIZENSHIP_MARKETING_RE,
        "contextual_trait": PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE,
        "religion_familial": PROTECTED_RELIGION_FAMILIAL_RELATION_RE,
        "health_status": PROTECTED_HEALTH_STATUS_MARKETING_RE,
        "health_term": PROTECTED_HEALTH_TERM_MARKETING_RE,
    }
    digits = sorted({chr(key) for table in _LEET_TABLES for key in table})
    reached: set[str] = set()
    for length in range(1, 5):
        for combo in itertools.product(digits, repeat=length):
            run = "".join(combo)
            for table in _LEET_TABLES:
                folded = run.translate(table)
                for bank, pattern in banks.items():
                    if pattern.search(folded):
                        reached.add(bank)
    assert reached == {"national_origin", "health_term"}, (
        "a bank not previously reachable from digits alone now is; the skip "
        f"would stop scanning it. reached={sorted(reached)}"
    )


def test_the_live_incident_answer_renders_through_the_real_guard() -> None:
    """End-to-end on the actual surface, with the governed city dimension.

    The raw detector still refuses this sentence -- the UPPERCASE city names
    trip the protected-class scan, which is what the governed place dimension
    exists to mask. Pinning it at the guard is the only assertion that matches
    what a user sees.
    """

    from backend.services.genie_message_policy import genie_visible_text_unsafe
    from backend.services.genie_place_dimension import (
        GovernedPlaceDimensionResolver,
        _reset_governed_place_dimension_for_tests,
    )

    live_cities = ("SEATTLE", "BLACK DIAMOND", "CARNATION", "NEWCASTLE", "TACOMA")
    _reset_governed_place_dimension_for_tests(
        GovernedPlaceDimensionResolver(dimension_reader=lambda: list(live_cities))
    )
    try:
        assert (
            genie_visible_text_unsafe(
                "The Washington cities with between 3,000 and 4,500 total borrowers are "
                "BLACK DIAMOND (3,678), CARNATION (3,174), and NEWCASTLE (4,140)."
            )
            is False
        )
        # ...and a protected term beside the same numbers still blocks.
        assert (
            genie_visible_text_unsafe(
                "Target black borrowers in NEWCASTLE (4,140)."
            )
            is True
        )
    finally:
        _reset_governed_place_dimension_for_tests(None)
