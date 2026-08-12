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
3. the separator fold collapsed ``4,140`` to two space-delimited tokens, so
   ``lao`` stood at a word boundary; and
4. the national-origin detector needs only a population noun within 120
   characters, which ``borrowers`` supplied.

Every comma group of ``140`` did it -- 140, 2,140, 3,140, 4,140, 5,140 -- and
``1,405`` minted ``laos``. Of the 122 national-origin terms, ``lao`` is the
only one composable purely from the fold alphabet ``{o,l,i,e,a,s,t}``, so this
was a single collision rather than a systemic hole; the sweep at the bottom of
this file re-derives that over every bank the scanner consults.

The fix is in :func:`in_word_leet_folds`: a digit run that touches no ASCII
letter is a number, not an evasion, and cannot mint a protected term. The
de-obfuscation itself is untouched, which is what ``_LEET_EVASIONS`` exists to
prove -- every one of those still refuses, and they are the reason the fold is
in the scanner at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import product

import pytest

from backend.schemas._validators_protected_class import protected_class_marketing_reason
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


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        # All-digit runs are left alone, whatever their separators.
        ("4,140", {"4,140"}),
        ("140", {"140"}),
        ("1,405 borrowers", {"1,405 borrowers"}),
        ("3,000 and 4,500", {"3,000 and 4,500"}),
        # A run touching a letter folds, and ``1`` yields both readings.
        ("w0men", {"women"}),
        ("mus1im", {"musiim", "muslim"}),
        ("140k", {"laok", "iaok"}),
        ("k140", {"klao", "kiao"}),
    ),
)
def test_in_word_leet_folds_contract(value: str, expected: set[str]) -> None:
    assert in_word_leet_folds(value) == expected


def test_no_bare_number_can_mint_a_protected_term() -> None:
    """Re-derive the scope claim instead of trusting it.

    Sweeps every foldable digit token up to length four past the scanner, in
    the narrative shape that triggered the report -- so the search is over
    numbers the product can actually print, not over the letters they used to
    become. ``140`` and ``1405`` are both in here, so this is red before the
    fix and green after it, across every bank the scanner consults rather than
    just the national-origin one the report traced.

    Four is the useful bound, not a budget compromise: a comma group is at
    most three digits, the leading group at most three more, and a run longer
    than that only occurs in an unformatted number, which has no separator to
    give the folded text a word boundary. After the fix the length stops
    mattering at all -- an all-digit run is never folded, whatever its size.
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
