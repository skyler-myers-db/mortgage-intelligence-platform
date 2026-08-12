"""The protected-class scanner withheld answers based on the VALUE of a number.

Captured live on paychex 2026-08-12 at sha ``7612b021`` (deployment
``01f19659086f``). "Which Washington cities have between 3000 and 4500 total
borrowers?" passed the prompt guard and then came back ``source=policy_blocked``
with ``genie_output_blocked unsafe_field="answer"``. The three cities in range
are BLACK DIAMOND (3,678), CARNATION (3,174) and NEWCASTLE (4,140), and the
block was caused solely by ``4,140``:

1. the leetspeak fold applied ``str.maketrans("013457", "oleast")`` to the
   whole string, so the digit run ``140`` became ``lao``;
2. ``lao`` is a member of the governed national-origin bank;
3. the comma already bounds that run as a word -- the separator fold is NOT
   what exposed it, ``a,lao`` matches on its own; and
4. the national-origin detector needs only a population noun within 120
   characters, which ``borrowers`` supplied.

Every comma group of ``140`` did it -- 140, 2,140, 3,140, 4,140, 5,140 -- and
``1,405`` minted ``laos``. Of the 122 national-origin terms, ``lao`` is the
only one composable purely from the fold alphabet ``{o,l,i,e,a,s,t}``, so this
was a single collision rather than a systemic hole; the sweep at the bottom of
this file re-derives that over every bank the scanner consults.

The fix is in :func:`in_word_leet_folds`: an isolated digit run keeps its
digits when -- and only when -- its folded SPELLING is itself a governed term.
``lao`` and ``laos`` are that entire reserved set.

The first attempt keyed on POSITION instead, withholding the fold from every
digit run that touched no letter. It fixed the report and opened a hole:
``mus 1 im`` is also a digit run touching no letter, its ``1`` folds to a
harmless ``l``, and it exists only so the scanner's rejoin step can splice
``muslim`` back together. Adversarial review measured 123 protected terms
going fully open on the Ask Genie posture, which has no unknown-criterion
backstop. ``_LEET_EVASIONS`` and ``_SPLIT_WORD_LEET_EVASIONS`` are the two
halves of the corpus that keep both directions honest.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import product

import pytest

from backend.schemas._validators_protected_class import (
    _mints_governed_term,
    protected_class_marketing_reason,
)
from backend.schemas.marketing_text_normalization import in_word_leet_folds
from backend.services.genie_message_policy import genie_visible_text_unsafe
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
)

# The live gold ``city`` values this file reasons about, including the three
# the captured question returns and the two whose own names trip a different
# detector (served by ``protected_class_safe_values`` -- see
# ``test_genie_city_protected_class_guard``).
_LIVE_GOLD_CITIES = (
    "SEATTLE",
    "TACOMA",
    "KENT",
    "RENTON",
    "BLACK DIAMOND",
    "CARNATION",
    "NEWCASTLE",
)

# The reported class: a comma-grouped count next to a population noun. Each is
# withheld the moment the fold stops being in-word.
_NUMERIC_NARRATIVES = (
    "NEWCASTLE has 4,140 borrowers.",
    "Newcastle, WA has 4,140 borrowers.",
    "Seattle has 4,140 borrowers.",
    "Kent has 140 households.",
    "Kent has 1,405 residents.",
    "There are 2,140 applicants in Kent.",
    "Renton has 3,140 borrowers and Kent has 5,140 borrowers.",
    "Renton has 1,405 borrowers.",
)

# The same counts with no population noun, and the same counts unformatted.
# These rendered even before the fix -- the collision needed BOTH the comma
# (to make a word boundary) and the noun (to satisfy the 120-char window) --
# so they pin that the fix did not have to reach them.
_ALREADY_RENDERING = (
    "Seattle has 4,140 loans.",
    "Seattle has 4140 borrowers.",
)

# Why the fold exists. Every one is an in-word digit substitution, every one
# must keep refusing, and together they are the mutation check on the fix:
# they stay green only because ``in_word_leet_folds`` still folds digit runs
# that touch a letter.
_LEET_EVASIONS = (
    "W0men homeowners may benefit from this mortgage review.",
    "Mus1im homeowners may benefit from this mortgage review.",
    "B1ack homeowners may benefit from this mortgage review.",
    "L4tino homeowners may benefit from this mortgage review.",
    "As14n homeowners may benefit from this mortgage review.",
    "W0m3n homeowners may benefit from this mortgage review.",
    "L4o borrowers are concentrated in Kent.",
    "We should target l4otian borrowers.",
    # Word-edge runs. The rule admits a run touching a letter on EITHER side,
    # so a substitution that opens or closes the word is still folded; only
    # requiring letters on BOTH sides would let these two through.
    "Target 4sian homeowners for this campaign.",
    "Prioritize latin0 homeowners for this campaign.",
)

# A digit substitution PLUS the split-word evasion the rejoin step was
# written for. The digit then touches no letter, so a POSITION-keyed rule
# leaves it alone and the halves never rejoin. All six rendered on the Ask
# Genie posture under that rule; the reserved-spelling rule folds them,
# because ``l``/``a``/``e`` are not governed terms. Adversarial review caught
# this before release (2026-08-12).
_SPLIT_WORD_LEET_EVASIONS = (
    "Target mus 1 im homeowners for this campaign.",
    "Target mus.1.im homeowners for this campaign.",
    "Target b 1 ack homeowners for this campaign.",
    "Target 4 sian homeowners for this campaign.",
    "Target as 14 n homeowners for this campaign.",
    "Prioritize latin 0 homeowners for this campaign.",
)

# The digits both reviewed tables translate; the other four never folded.
_FOLDABLE_DIGITS = "013457"


@pytest.fixture
def governed_cities() -> Iterator[None]:
    _reset_governed_place_dimension_for_tests(
        GovernedPlaceDimensionResolver(dimension_reader=lambda: list(_LIVE_GOLD_CITIES))
    )
    yield
    _reset_governed_place_dimension_for_tests(None)


@pytest.mark.parametrize("narrative", _NUMERIC_NARRATIVES)
def test_a_count_cannot_be_read_as_a_protected_term(narrative: str) -> None:
    """The reported defect, at the scanner that made the decision.

    Goes red if the leet fold is applied to a digit run that touches no
    letter -- that is, if ``in_word_leet_folds`` reverts to translating the
    whole string.
    """

    assert (
        protected_class_marketing_reason(narrative, assume_reviewed_read_only_analytics=True)
        is None
    )


@pytest.mark.parametrize("narrative", _ALREADY_RENDERING)
def test_counts_that_never_collided_are_unchanged(narrative: str) -> None:
    assert (
        protected_class_marketing_reason(narrative, assume_reviewed_read_only_analytics=True)
        is None
    )


@pytest.mark.usefixtures("governed_cities")
def test_the_captured_live_answer_renders() -> None:
    """The withheld turn, through the surface that withheld it.

    ``genie_visible_text_unsafe`` is the call behind ``unsafe_field="answer"``,
    so this is the block reproduced end to end rather than at the scanner.

    The text is the answer the local backend rendered against live paychex UC
    and the governed Genie space on 2026-08-12 with this fix applied --
    ``source=genie``, ``row_count=3``, ``trusted=True``, over
    ``mip.gold.borrower_360``. The same turn on the sha in the module
    docstring came back ``source=policy_blocked``.
    """

    answer = (
        "The governed query against mip.gold.borrower_360 returned 3 rows, shown in "
        "full in the table. The first row reads city: NEWCASTLE; total borrowers: "
        "4,140; refreshed at: 2026-08-09T16:56:59.870Z. Every value comes straight "
        "from the returned rows. Genie's draft narrative was withheld: it contained "
        "numbers the app could not verify against the returned rows.\n\n"
        "Source: mip.gold.borrower_360"
    )

    assert genie_visible_text_unsafe(answer) is False


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("narrative", _NUMERIC_NARRATIVES)
def test_numeric_narratives_render_through_the_genie_surface(narrative: str) -> None:
    assert genie_visible_text_unsafe(narrative) is False


@pytest.mark.parametrize("evasion", _LEET_EVASIONS)
def test_in_word_leet_evasions_still_refuse(evasion: str) -> None:
    """The anti-weakening pin.

    This is the whole reason the fix is scoped to all-digit runs instead of
    dropping the fold: these must stay red-on-render forever.
    """

    assert (
        protected_class_marketing_reason(evasion, assume_reviewed_read_only_analytics=True)
        == "protected_class"
    )


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("evasion", _LEET_EVASIONS)
def test_in_word_leet_evasions_stay_withheld_at_the_genie_surface(evasion: str) -> None:
    assert genie_visible_text_unsafe(evasion) is True


@pytest.mark.parametrize("evasion", _SPLIT_WORD_LEET_EVASIONS)
def test_split_word_leet_evasions_refuse_as_a_fair_lending_finding(evasion: str) -> None:
    """The regression the in-word rule introduced, pinned on BOTH postures.

    The analytics posture is the one that mattered: without it these read as
    ``unreviewed_criterion`` on the campaign surface, which is still a refusal
    and would have hidden the hole. Ask Genie bypasses that state machine, so
    only ``protected_class`` keeps them out of a rendered answer.
    """

    assert protected_class_marketing_reason(evasion) == "protected_class"
    assert (
        protected_class_marketing_reason(evasion, assume_reviewed_read_only_analytics=True)
        == "protected_class"
    )


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("evasion", _SPLIT_WORD_LEET_EVASIONS)
def test_split_word_leet_evasions_stay_withheld_at_the_genie_surface(evasion: str) -> None:
    assert genie_visible_text_unsafe(evasion) is True


def test_only_a_number_that_spells_a_governed_term_is_withheld() -> None:
    """The reserved-spelling rule itself, and its blast radius.

    ``lao`` and ``laos`` are the whole reserved set, so those numbers keep
    their digits; every other fold still happens, which is what leaves ``1``
    in ``mus 1 im`` free to become the ``l`` that rejoins ``muslim``. A rule
    keyed on position instead of spelling cannot separate those two.
    """

    def fold(value: str) -> set[str]:
        return in_word_leet_folds(value, mints_governed_term=_mints_governed_term)

    assert _mints_governed_term("lao") is True
    assert _mints_governed_term("laos") is True
    for harmless in ("l", "i", "e", "a", "s", "o", "t"):
        assert _mints_governed_term(harmless) is False, harmless

    # Reserved: the governed spelling keeps its digits, whole literal and per
    # group. The other reading of ``1`` is kept only because it is provably
    # harmless -- ``iao`` and ``i,aos`` are not governed terms.
    assert "lao" not in fold("140")
    assert "140" in fold("140")
    assert "a,lao" not in fold("4,140")
    assert "4,140" in fold("4,140")
    assert "l,aos" not in fold("1,405")
    assert "1,405" in fold("1,405")
    # Not reserved: folds exactly as before, so the rejoin still works.
    assert "mus l im" in fold("mus 1 im")
    assert fold("3") == {"e"}
    assert fold("w0men") == {"women"}


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        # Reserved SPELLINGS keep their digits -- but only for the table that
        # spells the governed term. ``1`` also reads as ``i``, and ``iao`` is
        # not governed, so that reading still folds and is harmless.
        ("140", {"140", "iao"}),
        ("4,140", {"4,140", "a,iao"}),
        ("1,405 borrowers", {"1,405 borrowers", "i,aos borrowers"}),
        # Nothing governed here, so an ordinary number folds as it always did.
        ("3,000 and 4,500", {"e,ooo and a,soo"}),
        # A run touching a letter folds regardless.
        ("w0men", {"women"}),
        ("mus1im", {"musiim", "muslim"}),
        ("140k", {"laok", "iaok"}),
        ("k140", {"klao", "kiao"}),
    ),
)
def test_in_word_leet_folds_contract(value: str, expected: set[str]) -> None:
    assert in_word_leet_folds(value, mints_governed_term=_mints_governed_term) == expected


def test_no_bare_number_survives_as_a_governed_term() -> None:
    """The property, exhaustively, at the function that owns it.

    Not "a number is never rewritten" -- it usually is, and must be, so the
    rejoin step keeps working. The property is narrower and is the one that
    matters: whatever a bare number folds to, it is never a governed term.
    """

    for length in range(1, 5):
        for combination in product(_FOLDABLE_DIGITS, repeat=length):
            digits = "".join(combination)
            for shape in (digits, f"{digits} ", f" {digits}", f"4,{digits}"):
                for folded in in_word_leet_folds(
                    shape, mints_governed_term=_mints_governed_term
                ):
                    for token in folded.replace(",", " ").split():
                        assert not _mints_governed_term(token), (shape, folded, token)


def test_no_bare_number_can_mint_a_protected_term() -> None:
    """The same property where it is actually decided, across every bank.

    Sweeps foldable digit tokens through the scanner in the narrative shape
    that triggered the report, so the search is over numbers the product can
    print rather than over the letters they used to become. ``140`` and
    ``1405`` are both here, which is what makes this red before the fix.

    It cannot fail while the fold is the identity on bare numbers -- the test
    above proves that separately -- so treat this as the integration half:
    it is what notices if some LATER stage of the pipeline starts minting
    letters from digits again, the way ``letter_backed_leet_windows`` could
    have without its provenance filter.
    """

    blocked = [
        digits
        for length in range(1, 5)
        for combination in product(_FOLDABLE_DIGITS, repeat=length)
        if protected_class_marketing_reason(
            f"Kent has 4,{(digits := ''.join(combination))} borrowers.",
            assume_reviewed_read_only_analytics=True,
        )
        is not None
    ]

    assert blocked == [], f"these counts are still withheld: {sorted(set(blocked))[:10]}"
