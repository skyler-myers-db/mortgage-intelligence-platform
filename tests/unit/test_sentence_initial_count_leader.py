"""The answer surface withheld a narrative because it opened with a count.

``_SENTENCE_INITIAL_FUNCTION_WORDS`` is the bank of words that, sitting at the
start of a sentence in front of a known place, are orthography rather than an
identity — "Which Washington cities ..." is not a person named "Which
Washington". Before 2026-08-12 the bank carried interrogatives, auxiliaries,
determiners, prepositions and sentence-initial adverbs, but no cardinal number
words, so ``_sentence_initial_place_strip_pattern`` left the leader in place
and ``_TITLECASE_HUMAN_NAME_RE`` read "Three Washington" as a person::

    tail = ' Washington cities have between 3,000 and 4,500 total borrowers.'
    genie_visible_text_unsafe('Three'   + tail)   # True  -> WITHHELD
    genie_visible_text_unsafe('The'     + tail)   # False -> renders
    genie_visible_text_unsafe('Several' + tail)   # False -> renders

That is the shape a COUNT ANSWER takes, and the live capture confirms it is
where the loss lands. Run against live paychex UC and the governed Genie space
on 2026-08-12 at sha ``993169dc``, "Which Washington cities have between 3,000
and 4,500 total borrowers?" produced this draft from Genie itself::

    You want to see the cities in Washington state that have between 3,000
    and 4,500 borrowers in total.

    Three Washington cities have between 3,000 and 4,500 total borrowers.
    These cities are:
    - **NEWCASTLE**: 4,140 borrowers
    ...

and ``_answer_text_contains_pii`` returned True on it, so the app replaced
Genie's prose with "Genie's draft narrative was withheld: the output safety
guard flagged its wording." Scanned line by line against the live 428-value
city dimension, the count-led sentence flipped to rendering with this fix and
stayed withheld without it — :data:`_COUNT_LED_NARRATIVES` opens with that
exact sentence.

The same draft carries a SECOND, unrelated block on ``4,140`` (the leetspeak
fold minting ``lao``), which is fixed separately in "fix(guards): stop a
comma-grouped number from minting a protected term". This fix is what clears
the count-led sentence; that one clears the count itself, and only both
together render the captured draft. Neither causes the other — the cardinal
gap predates it.

``Only three ...`` and ``Just three ...`` already rendered, because the leader
is then a listed function word — the gap was arbitrary, not a line someone
drew.

The fix adds the cardinal row (plus ``More``/``Fewer``, the comparative half
of the already-banked ``Many``/``Most``/``Few``). Scope is grammatical: a word
belongs only if it can stand IMMEDIATELY before "{Place} {noun}". The measured
sweep that produced this file flagged ``Nearly``, ``Roughly``,
``Approximately``, ``Dozens``, ``Half``, ``First`` and ``Hundred`` too, but
only against strings no one writes ("Nearly Washington cities"); their real
forms need an intervening "of", numeral or comma and already rendered.
:data:`_NEVER_NEEDED_ADDING` re-derives that instead of asserting it, so the
scope claim is checked rather than trusted.

Safety is inherited, not re-argued. The strip still fires only at a sentence
start and only in front of a governed place, and the #208 rule — no banked
word may be attested in FIRST-name position — is what makes a bank entry safe
at all. :func:`test_the_count_bank_holds_no_person_name` pins that
mechanically for the added row, and the anti-weakening cases below pin the
behavior that matters: a real name pair anywhere in a count-led sentence still
refuses, because the strip removes one leading word and consumes no lookahead.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.schemas._validators_person_names import (
    _COMMON_FIRST_NAMES,
    _COMMON_LAST_NAMES,
    _SENTENCE_INITIAL_FUNCTION_WORDS,
    US_STATE_NAMES,
    contains_human_name_shape,
)
from backend.services.genie_message_policy import (
    genie_visible_text_unsafe,
    identity_prompt_match,
)
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
)

# Live ``mip.gold.borrower_360`` city values (paychex, 2026-08-12). The three
# the captured question returns, plus ``MEDINA``/``ELIZABETH``/``YORBA LINDA``
# — the place-name/surname collisions #208 exists for.
_LIVE_GOLD_CITIES = (
    "SEATTLE",
    "TACOMA",
    "KENT",
    "RENTON",
    "NEWCASTLE",
    "BLACK DIAMOND",
    "CARNATION",
    "MEDINA",
    "ELIZABETH",
    "YORBA LINDA",
)

# The cardinal row, kept as data so the sweep below can put each word through
# the real surface one at a time. That is not a restatement of the bank: the
# bank becomes a single regex alternation, so ``Six``/``Sixty``,
# ``Seven``/``Seventy`` and ``Nine``/``Nineteen``/``Ninety`` all match only
# because the engine backtracks past the shorter branch. Asserting membership
# would never have caught an ordering mistake there.
_CARDINAL_LEADERS = (
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)

# The comparative half of the ``Many``/``Most``/``Few`` row already banked.
# Held separately because it is the one part of this fix that is not a
# cardinal, and a reviewer should be able to see that boundary without
# reading the whole tuple.
_COMPARATIVE_LEADERS = ("More", "Fewer")

_COUNT_LEADERS = _CARDINAL_LEADERS + _COMPARATIVE_LEADERS

# The reported class, in the phrasings a count answer actually takes.
_COUNT_LED_NARRATIVES = (
    "Three Washington cities have between 3,000 and 4,500 total borrowers.",
    "One Washington city clears the 4,000-borrower mark.",
    "Twelve Washington cities carry in-the-money borrowers.",
    "Ninety Washington cities appear in the refreshed coverage.",
    "Zero Washington cities meet that equity threshold.",
    "More Washington cities qualify this quarter than last.",
    "Fewer Washington cities qualify than last quarter.",
    "Four Tacoma borrowers are in the money this month.",
    "Six Kent borrowers hold high equity.",
    # Genie writes paragraphs, so the leader usually follows a sentence
    # terminator or a newline rather than opening the string.
    "The governed query returned 3 rows. Three Washington cities have "
    "between 3,000 and 4,500 total borrowers.",
    "Coverage refreshed. Ten Seattle borrowers moved in the money.",
    "Summary:\nFive Washington cities clear the threshold.",
)

# Already rendering before the fix, and still rendering after it. The first
# two are why the gap reads as arbitrary; the rest are the count shapes the
# pair heuristic never reached — a hyphen and a numeral are not whitespace,
# and a lowercase second word is not a title-case pair.
_ALREADY_RENDERING = (
    "Only three Washington cities have high equity.",
    "Just three Washington cities have high equity.",
    "Twenty-three Washington cities have high equity.",
    "Twenty three Washington cities have high equity.",
    "3 Washington cities have high equity.",
    "In total, three Washington cities have high equity.",
)

# Words the sweep flagged that this fix deliberately leaves out, paired with
# the form they are actually written in. Each must already render — that is
# the evidence they were probe artifacts and not live losses.
_NEVER_NEEDED_ADDING = (
    ("Nearly", "Nearly half of Washington cities have high equity."),
    ("Roughly", "Roughly 40 Washington cities have high equity."),
    ("Approximately", "Approximately 3,000 Washington borrowers are in the money."),
    ("Dozens", "Dozens of Washington cities have high equity."),
    ("First", "First, Washington cities lead on equity."),
    ("Hundred", "About a hundred Washington cities appear in coverage."),
)

# The anti-weakening set: everything that must keep refusing. Widening a bank
# that feeds a name-shape strip is exactly the change that quietly blinds the
# detector, so these are the reason the fix is scoped the way it is.
_MUST_STILL_REFUSE = (
    # A real pair elsewhere in a count-led sentence. The strip removes ONE
    # leading word; it does not license the rest of the sentence.
    "Three Washington cities were reviewed by Robert Johnson.",
    "Six Kent borrowers were flagged. Maria Garcia approved the wave.",
    "Three cities were named. Elizabeth Smith is the top borrower.",
    # The place half of the gate. A capitalized non-place after a cardinal is
    # still a pair, so the strip never fires on it.
    "Three Nguyen borrowers are in Kent.",
    "Four Whitfield borrowers hold high equity.",
    # The sentence-initial half of the gate.
    "The report says Three Washington cities qualify.",
    # #208's first-position exclusions, untouched by this change. ``Do`` is a
    # family name in exactly the position the strip consumes, and ``MEDINA``
    # is a live gold city that is also a surname.
    "Do Medina qualifies for a HELOC?",
    "Do Nguyen qualifies for a HELOC?",
    "Elizabeth Smith is the top borrower",
)


@pytest.fixture
def governed_cities() -> Iterator[None]:
    _reset_governed_place_dimension_for_tests(
        GovernedPlaceDimensionResolver(dimension_reader=lambda: list(_LIVE_GOLD_CITIES))
    )
    yield
    _reset_governed_place_dimension_for_tests(None)


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("narrative", _COUNT_LED_NARRATIVES)
def test_count_led_narratives_render(narrative: str) -> None:
    """The reported defect, at the surface that withheld it.

    ``genie_visible_text_unsafe`` is the call behind ``unsafe_field="answer"``.
    Goes red the moment the cardinal row leaves the bank.
    """

    assert genie_visible_text_unsafe(narrative) is False


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("narrative", _COUNT_LED_NARRATIVES)
def test_count_led_prompts_reach_genie(narrative: str) -> None:
    """The same bank feeds the INPUT guard, which refuses before Genie runs.

    A user can type a count-led sentence too ("Three Washington cities showed
    up last week — which were they?"), and the prompt guard would have refused
    it in ~1s without ever calling Genie.
    """

    assert identity_prompt_match(narrative) is False


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("leader", _COUNT_LEADERS)
def test_every_added_leader_renders_through_the_real_surface(leader: str) -> None:
    """Each added word, individually, through the policy that blocked it.

    See :data:`_CARDINAL_LEADERS` for why this is a per-word sweep and not a
    membership assertion: it is the check that would catch an ordering or
    escaping mistake in :func:`_sentence_initial_place_strip_pattern`.
    """

    assert genie_visible_text_unsafe(f"{leader} Washington cities were reviewed.") is False


@pytest.mark.usefixtures("governed_cities")
def test_the_captured_live_draft_prose_renders() -> None:
    """Genie's own words from the captured turn, not a reconstruction.

    Verbatim from the 2026-08-12 paychex capture in the module docstring, up
    to the row bullets. The bullets are excluded on purpose: ``4,140`` is
    withheld by the separate leetspeak-fold defect, so asserting the whole
    draft here would pin a second fix that is not in this change and would go
    red for a reason this test has nothing to say about.
    """

    draft = (
        "You want to see the cities in Washington state that have between "
        "3,000 and 4,500 borrowers in total.\n\n"
        "Three Washington cities have between 3,000 and 4,500 total "
        "borrowers. These cities are:"
    )

    assert genie_visible_text_unsafe(draft) is False


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("narrative", _ALREADY_RENDERING)
def test_count_shapes_that_never_collided_are_unchanged(narrative: str) -> None:
    assert genie_visible_text_unsafe(narrative) is False


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize(("word", "real_form"), _NEVER_NEEDED_ADDING)
def test_words_left_out_of_the_bank_never_needed_adding(word: str, real_form: str) -> None:
    """Re-derive the scope boundary rather than asserting it.

    Each of these blocks when jammed straight in front of a place, which is
    how a naive sweep finds them — and none of them can grammatically sit
    there. The form they are really written in already renders, so adding them
    would have widened a name-shape strip to buy no live turn.
    """

    assert word not in _SENTENCE_INITIAL_FUNCTION_WORDS
    assert genie_visible_text_unsafe(real_form) is False


@pytest.mark.usefixtures("governed_cities")
@pytest.mark.parametrize("text", _MUST_STILL_REFUSE)
def test_a_count_leader_does_not_blind_the_name_scan(text: str) -> None:
    """The anti-weakening pin, on both surfaces.

    This is the whole reason the addition is gated on sentence-initial
    position AND a known place rather than dropping the leader wherever it
    appears.
    """

    assert genie_visible_text_unsafe(text) is True
    assert identity_prompt_match(text) is True


def test_the_count_bank_holds_no_person_name() -> None:
    """#208's rule, applied to the row this fix adds.

    A banked word is safe only if it is never attested in FIRST-name position:
    the strip consumes exactly that slot, and the place half of the gate
    cannot save a surname that is also a place. ``Do``, ``An``, ``No``,
    ``Will`` and ``May`` are excluded for that reason and must stay excluded.
    """

    added = {word.casefold() for word in _COUNT_LEADERS}
    assert added <= {word.casefold() for word in _SENTENCE_INITIAL_FUNCTION_WORDS}
    assert not added & (_COMMON_FIRST_NAMES | _COMMON_LAST_NAMES)
    assert not added & {"do", "an", "no", "will", "may", "grace", "mark"}


def test_the_bank_carries_no_duplicates() -> None:
    """A duplicated entry is a silently doubled alternation branch."""

    banked = [word.casefold() for word in _SENTENCE_INITIAL_FUNCTION_WORDS]
    assert len(banked) == len(set(banked))


@pytest.mark.parametrize("leader", _COUNT_LEADERS)
def test_the_strip_stays_opt_in_only(leader: str) -> None:
    """Campaign, outreach and operator-note surfaces are bit-for-bit unchanged.

    They call ``contains_human_name_shape`` without place terms, so the strip
    is never built for them and this fix cannot reach them.
    """

    narrative = f"{leader} Washington cities were reviewed."
    assert contains_human_name_shape(narrative) is True
    assert (
        contains_human_name_shape(narrative, sentence_initial_place_terms=US_STATE_NAMES) is False
    )
