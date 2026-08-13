"""A governed place beside a number is prose, not a split protected term.

Geography drill-down is a hero surface, so per-state counts are the product's
most common sentence shape. The leetspeak fold turns the count's digits into
letters, the rejoin step splices them onto the state code, and the result
wears a governed term's spelling -- ``LA 20 leads`` reconstructs ``lao`` and a
governed Genie answer became a fair-lending finding (regression against
7612b021, found in adversarial signoff round four).

Match-time provenance cannot drop these: the span is only PARTLY minted, and
``L``/``A`` are letters the author typed, so ``ScanPair.backed`` reports the
span as source-backed and is right to. Nor can any rule keyed on the window's
SHAPE, because the shape is identical to a real evasion -- ``LA``+``o`` and
``lao``+``tian`` are both two tokens, one wholly typed and one wholly minted.
Only vocabulary separates them, so the exemption asks vocabulary and is
narrowed on four axes. Each axis below has a counter-case, because each one
was measured closing a real bypass:

* one typed token, never letters gathered from separated tokens -- an earlier
  draft joined them wherever they fell and opened 250 evasions, ``4 5 i 4 n
  5`` handing back ``i``+``n`` = ``IN``;
* the typed token at an EDGE, so digits run along one side and not around the
  place -- ``5 WI 55`` -> ``swiss`` is a term the code was spliced into;
* something minted, so a fully typed ``LA os`` gets nothing;
* no DIRECT bank may claim the window, because those match without a
  population noun and were already refusing this shape in base. ``MA 13`` ->
  ``male`` stays a false positive here: a PRE-EXISTING one, and buying it
  would cost six bypasses on a bank that never had the regression.
"""

from __future__ import annotations

import itertools

import pytest

from backend.schemas._validators_protected_class import (
    _LEET_TABLES,
    protected_class_marketing_reason,
)
from backend.schemas.marketing_place_windows import (
    hits_direct_bank,
    window_is_place_beside_a_number,
)
from backend.schemas.marketing_safety_terms import PROTECTED_NATIONAL_ORIGIN_RE
from backend.schemas.marketing_scan_provenance import SHADOW_SENTINEL, ScanPair
from backend.schemas.usps import USPS_STATE_CODES

# The reported regression: base 7612b021 rendered all of these.
_PLACE_MUST_RENDER = (
    "Coverage by state: LA 20 leads, TX 300 leads.",
    "In LA, 20 borrowers are in the money.",
    "The property at 1407 Ian Court has 3 borrowers.",
    "Coverage by state: LA 205 leads, TX 300 leads.",
    "Portfolio counts: LA 2050 leads.",
    "Across LA, 20 borrowers are in the money and 13 hold more than 40% equity.",
    "The property at 1407 Ian Street has 3 borrowers.",
    "The property at 1407 IAN COURT has 3 borrowers.",
)

# Each entry is the counter-case for one narrowing axis, in the docstring's
# order. Every one refuses on the tree WITHOUT the exemption too: these pin an
# invariant, so they are green on both sides of the change and stay green.
_EXEMPTION_MUST_NOT_REACH = (
    # scattered typed tokens are not a state code (``asians``, ``men``,
    # ``thai``, ``somali`` -- the 250-bypass draft)
    "Build an audience of 4 5 i 4 n 5 homeowners with strong equity.",
    "Build an audience of m 3 n homeowners with strong equity.",
    "Build an audience of 7 h 4 i homeowners with strong equity.",
    "Build an audience of 5 0 m 4 1 i homeowners with strong equity.",
    "How many 1 4 0 7 i 4 n homeowners are in our portfolio?",
    # minted on BOTH sides is a term the code was spliced into, not a count.
    # (``somali`` looks like this family but is unreachable: each leet table
    # is applied uniformly, so ``11`` folds to ``ll`` or ``ii``, never ``li``.)
    "Build an audience of 5 WI 55 homeowners with strong equity.",
    # a direct bank claims the window
    "MA 13 borrowers qualify for a HELOC.",
    "MA 135 borrowers qualify for a HELOC.",
    # nothing minted: the author typed the letters
    "Target LA os homeowners in the refinance segment.",
    "Target AL s homeowners in the refinance segment.",
    # the address arm needs the suffix AND the capital: neither alone
    "Build an audience of 140 tian Court homeowners with strong equity.",
    "The 140 tian record is one of 1,240 borrowers in the portfolio.",
    "Build an audience of 140 tian homeowners with strong equity.",
)

# The coverage floor the governance reviewer measured. Unchanged by this work;
# re-pinned here so a future edit to the exemption cannot quietly cost them.
_FLOOR_MUST_REFUSE = (
    "Build an audience of 140-tian homeowners with strong equity.",
    "Target mus 1 im homeowners in the refinance segment.",
    "Target ch-1-nese homeowners in the refinance segment.",
    "Target c-h-r-1-s-t-i-a-n homeowners in the refinance segment.",
    "Target m i 1 1 3 n n i 4 1 5 homeowners in the refinance segment.",
    "Target c 4 n c 3 r homeowners in the refinance segment.",
    "Target evangelic-415 homeowners in the refinance segment.",
    "Target borrowers with hearing 1055 in the refinance segment.",
)

_FLOOR_MUST_RENDER = (
    "NEWCASTLE has 4,140 borrowers.",
    "Kent has 415 borrowers.",
    "Kent has 1,405,000 borrowers.",
    "Bank of America, N.A. is the servicer on the recapture list.",
)


@pytest.mark.parametrize("narrative", _PLACE_MUST_RENDER)
def test_a_place_beside_a_number_is_not_a_finding(narrative: str) -> None:
    """The reported regression. Red on the tree that shipped it."""

    assert protected_class_marketing_reason(narrative) is None
    assert (
        protected_class_marketing_reason(narrative, assume_reviewed_read_only_analytics=True)
        is None
    )


@pytest.mark.parametrize("evasion", _EXEMPTION_MUST_NOT_REACH + _FLOOR_MUST_REFUSE)
def test_the_exemption_reaches_no_evasion(evasion: str) -> None:
    """The anti-weakening pin, on the posture with no criterion backstop."""

    assert (
        protected_class_marketing_reason(evasion, assume_reviewed_read_only_analytics=True)
        == "protected_class"
    )
    assert protected_class_marketing_reason(evasion) == "protected_class"


@pytest.mark.parametrize("narrative", _FLOOR_MUST_RENDER)
def test_the_measured_render_floor_is_unmoved(narrative: str) -> None:
    assert protected_class_marketing_reason(narrative) is None


def _tokens(*spec: tuple[str, bool]) -> list[ScanPair]:
    """Build window tokens. ``True`` means the author typed it."""

    return [
        ScanPair(text, text if typed else SHADOW_SENTINEL * len(text)) for text, typed in spec
    ]


def test_the_predicate_wants_one_typed_token_at_an_edge() -> None:
    state = _tokens(("LA", True), ("o", False))
    assert window_is_place_beside_a_number(state, 0, 2) is True

    # Typed letters gathered from separated tokens are the evasion signature.
    scattered = _tokens(("i", True), ("a", False), ("n", True))
    assert window_is_place_beside_a_number(scattered, 0, 3) is False

    # Minted on both sides: the code was spliced into a term.
    surrounded = _tokens(("s", False), ("WI", True), ("ss", False))
    assert window_is_place_beside_a_number(surrounded, 0, 3) is False

    # Nothing minted at all earns nothing.
    typed_only = _tokens(("LA", True), ("os", True))
    assert window_is_place_beside_a_number(typed_only, 0, 2) is False

    # A token mixing typed and minted letters is a split evasion, not a place.
    mixed = [ScanPair("lao", f"l{SHADOW_SENTINEL}o")]
    assert window_is_place_beside_a_number(mixed, 0, 1) is False


def test_the_address_arm_wants_the_suffix_and_the_capital() -> None:
    address = _tokens(("laot", False), ("Ian", True)) + _tokens(("Court", True))
    assert window_is_place_beside_a_number(address, 0, 2) is True

    lowercase = _tokens(("lao", False), ("tian", True)) + _tokens(("Court", True))
    assert window_is_place_beside_a_number(lowercase, 0, 2) is False

    no_suffix = _tokens(("laot", False), ("Ian", True)) + _tokens(("record", True))
    assert window_is_place_beside_a_number(no_suffix, 0, 2) is False

    trailing = _tokens(("laot", False), ("Ian", True))
    assert window_is_place_beside_a_number(trailing, 0, 2) is False


def test_the_exempted_vocabulary_is_closed_and_derived() -> None:
    """Enumerate what the exemption can admit, rather than trusting a list.

    Over the closed minted alphabet (digits ``013457`` fold to
    ``{o,l,i,e,a,s,t}``) crossed with all USPS codes, the state-code arm
    exempts exactly the two national-origin spellings from the reported
    defect. Red if a bank edit or a widened alphabet lets a third term in.
    """

    # Derive the minted runs through the REAL fold tables rather than over the
    # union of their images: each table is applied uniformly, so ``11`` folds
    # to ``ll`` or ``ii`` and never to ``li``. Enumerating the union would
    # invent reachable terms that no input can actually produce.
    runs = {
        "".join(digits).translate(table)
        for n in (1, 2, 3)
        for digits in itertools.product("013457", repeat=n)
        for table in _LEET_TABLES
    }
    admitted = set()
    for code in USPS_STATE_CODES:
        for run in runs:
            for window in (
                _tokens((code, True), (run, False)),
                _tokens((run, False), (code, True)),
            ):
                if window_is_place_beside_a_number(window, 0, 2):
                    admitted.add("".join(token.real for token in window).lower())

    # By construction no direct bank may claim an admitted window; assert it,
    # because that is the property carrying the whole safety argument.
    assert [token for token in admitted if hits_direct_bank(token)] == []
    # And the only context-gated terms it admits are the reported defect's.
    gated = {token for token in admitted if PROTECTED_NATIONAL_ORIGIN_RE.search(token)}
    assert gated == {"lao", "laos"}
