"""A word beside an abbreviation's letter is prose, not a split protected term.

Sibling of ``test_place_beside_a_number_windows``: same failure, different
accident. The rejoin step splices adjacent letter runs so a split evasion
reconstructs, and an ordinary abbreviation parks a lone letter next to a whole
word for it to splice onto -- ``Bank of America, N.A.`` -> ``american``,
``country reads India n/a`` -> ``indian``, ``the borrower's Audi`` ->
``saudi``. All three spell a CONTEXT-GATED term, which needs only a
population noun nearby, and a sentence about borrowers always has one.

The reported defect was the ``India n/a`` family. What the reproduction found
is that these windows were ALREADY judged not evasion-shaped, and that the
judgement decided nothing: withholding the origin-sentence assist leaves the
window in the scan blob, and the base 120-character blob window reads
whichever other windows sorted alphabetically alongside. ``indian`` sorts
beside text carrying ``borrowers`` and refused; ``american`` did not and
rendered -- the same input class, opposite verdicts, chosen by sort order. So
the exemption is at EMISSION. ``window_is_place_beside_a_number`` reached that
conclusion first, for the same reason, and this follows it.

Two axes narrow it, each with counter-cases below:

* the lone letter must be GLUED to its neighbour by punctuation with no
  space, and the far side of that punctuation must be another lone letter
  (``N.A.``, ``n/a``) or an elided possessive (``'s``). A split evasion keeps
  its letters apart with whitespace to stay readable as audience copy, so
  ``america n homeowners`` is untouched -- and so is ``america n.
  homeowners``, where the far side is a whole word;
* no DIRECT bank may claim the window, the sibling's narrowing 4: those match
  without a population noun, were never decided by sort luck, and dropping a
  window they claim would buy a bypass on a bank that never had the defect.
"""

from __future__ import annotations

import re

import pytest

from backend.schemas._validators_protected_class import protected_class_marketing_reason
from backend.schemas.marketing_abbreviation_windows import (
    window_is_word_beside_an_abbreviated_letter,
)
from backend.schemas.marketing_scan_provenance import SHADOW_SENTINEL, ScanPair

_LETTER_RUN_RE = re.compile(r"[A-Za-z]+")

# The reported defect, plus the servicer and possessive prose the same window
# shape was convicting. Every entry was refused as ``protected_class`` on
# 52f962d9 -- the ``India`` family on both postures, the rest wherever blob
# sort order obliged.
_ABBREVIATION_MUST_RENDER = (
    "The India n/a rows in the delinquency extract were excluded before the "
    "borrowers were scored.",
    "The India n/a rows in the servicing extract were dropped before the "
    "ranked borrower list was built.",
    "The India n/a rows in the nightly refresh were discarded, so no "
    "borrowers were scored from them.",
    "Rows whose country reads India n/a in the vendor extract were dropped "
    "before the borrower list was scored.",
    "The India n/a borrowers were dropped before scoring.",
    "Filter out the India n/a rows before we score the borrowers.",
    "Exclude the India n/a rows from the origination extract.",
    "Score every Bank of America, N.A. loan in the ranked lead queue.",
    "Filter the lead queue to Bank of America, N.A. first liens.",
    "Exclude Bank of America, N.A. loans from this segment.",
    "Select the Bank of America, N.A. borrowers we could recapture.",
    "Contact the Bank of America, N.A. servicing desk about these liens.",
    "Build a campaign for Bank of America, N.A. recapture candidates.",
    "Score the borrower's Audi as a titled asset on the application.",
    "Exclude the borrower's Audi from the collateral list.",
)

# One counter-case per narrowing axis. Every entry refuses on the tree WITHOUT
# the exemption too -- these pin an invariant, so they are green on both sides
# of the change and must stay green.
_EXEMPTION_MUST_NOT_REACH = (
    # whitespace keeps the letters apart: no frame, so no exemption
    "Target s audi homeowners for this campaign.",
    # a direct bank claims the window, however the frame is spelled
    "Target b/l ack homeowners for this campaign.",
    "Target b'l ack homeowners for this campaign.",
    "Target wome n/a homeowners for this campaign.",
    # The elision arm DOES drop this window -- ``Lao``+``s`` joins into the
    # governed ``laos`` -- and it costs nothing, because the possessive
    # leaves ``Lao`` a complete token that the bank matches on its own. The
    # arm can only lose a term whose word half is ungoverned, which is the
    # ``India``+``n`` case it exists for.
    "Target the Lao's homeowners for this campaign.",
    "Target Lao's homeowners for this campaign.",
)

# The coverage floor. Unchanged by this work; re-pinned so a later edit to the
# frame cannot quietly cost them.
_FLOOR_MUST_REFUSE = (
    "Build a campaign audience of 140-tian homeowners.",
    "Build a campaign audience of 140tian homeowners.",
    "Build a campaign audience of chines 3 homeowners.",
    "Target mus 1 im homeowners for this campaign.",
    "Target v i e t n a m e s e homeowners for a HELOC campaign.",
    "We should target l4otian borrowers.",
    "L4o borrowers are concentrated in Kent.",
)

_FLOOR_MUST_RENDER = (
    "Which of our borrowers have a Bank of America, N.A. first lien we could recapture?",
    "Bank of America, N.A. holds 4,210 first liens across the ranked borrower list.",
    "Bank of America N A is the servicer of record for these borrowers.",
    "The India n/a rows were dropped before scoring.",
    "NEWCASTLE has 4,140 borrowers.",
    "Kent has 415 borrowers.",
)


@pytest.mark.parametrize("narrative", _ABBREVIATION_MUST_RENDER)
def test_an_abbreviation_beside_a_word_is_not_a_finding(narrative: str) -> None:
    """The reported defect. Red on the tree that shipped it."""

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
    assert (
        protected_class_marketing_reason(narrative, assume_reviewed_read_only_analytics=True)
        is None
    )


def _window(source: str, start: int, stop: int, *, minted: tuple[int, ...] = ()) -> bool:
    """Ask the predicate about ``source``'s letter runs ``[start:stop]``.

    ``minted`` names token indices the leet fold wrote out of digits, so a
    test can build the split-evasion provenance without a fold recipe.
    """

    spans = list(_LETTER_RUN_RE.finditer(source))
    tokens = [
        ScanPair(
            span.group(),
            SHADOW_SENTINEL * len(span.group()) if index in minted else span.group(),
        )
        for index, span in enumerate(spans)
    ]
    return window_is_word_beside_an_abbreviated_letter(source, spans, tokens, start, stop)


@pytest.mark.parametrize(
    ("source", "start", "stop"),
    (
        # the three measured accidents, each reached from the word side
        ("Bank of America, N.A. holds the lien.", 2, 4),
        ("country reads India n/a in the extract", 2, 4),
        ("the borrower's Audi is titled", 2, 4),
        # the symmetric arm: the word on the FAR side of the initialism, whose
        # lone letter is glued by the punctuation on its left (``A.`` here)
        ("Bank of America, N.A. holds the lien.", 4, 6),
    ),
)
def test_the_frame_recognizes_the_measured_accidents(source: str, start: int, stop: int) -> None:
    assert _window(source, start, stop) is True


def test_two_lone_letters_are_not_a_word_beside_a_letter() -> None:
    """``N``+``A`` is the initialism itself, not a word it was spliced onto.

    The exemption exists for a WHOLE WORD wearing an extra letter; a pair of
    initials has no word in it, so it stays on the ordinary path and whatever
    it spells is decided there.
    """

    assert _window("Bank of America, N.A. holds the lien.", 3, 5) is False


@pytest.mark.parametrize(
    ("source", "start", "stop", "why"),
    (
        ("target america n homeowners", 1, 3, "whitespace on both sides of the letter"),
        ("target america n. homeowners", 1, 3, "the far side of the period is a word"),
        ("target chin ese homeowners", 1, 3, "no lone letter at all"),
        ("target b/l ack homeowners", 1, 3, "a direct bank claims black"),
        ("target b l ack homeowners", 1, 3, "two lone letters, and no glue"),
    ),
)
def test_the_frame_stays_shut(source: str, start: int, stop: int, why: str) -> None:
    assert _window(source, start, stop) is False, why


def test_a_minted_letter_is_never_an_abbreviation() -> None:
    """A digit the fold rewrote is the split-evasion signature, not prose.

    ``140.tian`` wears the same punctuation-glued shape as ``N.A.``; what
    separates them is that its letters were never typed.
    """

    assert _window("audience of lao.tian homeowners", 2, 4, minted=(2,)) is False
    assert _window("audience of lao.tian homeowners", 2, 4) is False, "not a lone letter either"
