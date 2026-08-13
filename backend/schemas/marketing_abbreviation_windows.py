"""A word beside an abbreviation's letter is prose, not a split protected term.

Sibling of ``marketing_place_windows``: same failure, different accident. The
scanner's rejoin step splices adjacent letter runs so a split evasion
reconstructs, and an ordinary abbreviation puts a lone letter next to a whole
word for it to splice onto::

    Bank of America, N.A.       America + N -> american
    country reads India n/a     India   + n -> indian
    the borrower's Audi         s       + Audi -> saudi

All three spell a CONTEXT-GATED term (national origin), so each needed only a
population noun somewhere nearby to become a fair-lending finding -- and a
sentence about borrowers always has one. Measured on 52f962d9: "Rank the
borrowers by Bank of America, N.A. first-lien balance." and "The India n/a
rows in the delinquency extract were excluded before the borrowers were
scored." were both refused as ``protected_class``.

Why the exemption has to be at EMISSION, not at the context test. These
windows were already judged not evasion-shaped, and the judgement already
withheld the origin-sentence assist from them. It changed nothing: the
scan blob is a sorted concatenation of parallel representations, a joined
window is a bare token inside it, and the base 120-character blob window
therefore reads whichever OTHER windows happen to sort alongside. ``indian``
sorts next to text carrying ``borrowers`` and refuses; ``american`` does not
and renders. Same input class, opposite verdicts, decided by alphabetical
luck -- so denying the assist without dropping the window denies nothing.
``window_is_place_beside_a_number`` reached the same conclusion first and
made the same choice for the same reason.

Why it has to be a FRAME, not a shape. ``America``+``N`` and a real
``america``+``n`` evasion are both a complete word beside one typed letter;
no vocabulary, capitalization or token count separates them. What separates
them is that the prose letter belongs to an abbreviation: it is glued to its
neighbour by punctuation with no space, and what sits on the other side of
that punctuation is another lone letter (``N.A.``, ``n/a``) or an elided
possessive (``'s``). A split evasion has to keep its letters apart with
whitespace to stay readable as targeting copy, and ``america n homeowners``
therefore still refuses, unchanged.

Residual, accepted knowingly and in the sibling's spirit: an evader who
writes ``Target america n/a homeowners for this campaign.`` supplies a real
initialism frame and the window is dropped. That sentence rendered on
52f962d9 too -- the shape was never carried by anything but sort luck, so
this is a residual the exemption makes deterministic, not one it opens. The
neighbouring shapes stay shut: ``Target america n homeowners ...`` keeps its
letters apart with whitespace and ``Target america n. homeowners ...`` puts a
whole word on the far side of the period, so neither arm ever opens, and both
were measured unchanged across this commit.

The direct banks are excluded from the exemption for the sibling's narrowing
4: they match the blob without needing a population noun, so they were never
decided by sort luck, and dropping a window they claim would buy a bypass on
a bank that never carried the defect.
"""

from __future__ import annotations

import re

from backend.schemas.marketing_place_windows import hits_direct_bank
from backend.schemas.marketing_scan_provenance import ScanPair

# The characters that glue an abbreviation's letter to its neighbour.
_INITIALISM_PUNCTUATION = frozenset({".", "/"})
_ELISION_PUNCTUATION = frozenset({"'", "‘", "’"})


def _is_lone_letter(span: re.Match[str]) -> bool:
    return span.end() - span.start() == 1


def _reads_as_initialism(source: str, spans: list[re.Match[str]], index: int) -> bool:
    """True when ``spans[index]`` is a lone letter glued to another lone letter.

    "Glued" means exactly one punctuation character and no whitespace, in
    either direction: ``N.A.`` reached from ``N``, and again from ``A``.
    """

    span = spans[index]
    if index + 1 < len(spans):
        gap = source[span.end() : spans[index + 1].start()]
        if gap in _INITIALISM_PUNCTUATION and _is_lone_letter(spans[index + 1]):
            return True
    if index > 0:
        gap = source[spans[index - 1].end() : span.start()]
        if gap in _INITIALISM_PUNCTUATION and _is_lone_letter(spans[index - 1]):
            return True
    # The abbreviation's own trailing period needs no branch of its own: what
    # proves ``N.A.`` an initialism is the OTHER letter across the punctuation,
    # and both directions are covered above.
    return False


def _reads_as_elision(source: str, span: re.Match[str]) -> bool:
    """True when ``span`` is a lone letter hanging off an apostrophe (``'s``)."""

    return span.start() > 0 and source[span.start() - 1] in _ELISION_PUNCTUATION


def window_is_word_beside_an_abbreviated_letter(
    source: str,
    token_spans: list[re.Match[str]],
    tokens: list[ScanPair],
    start: int,
    stop: int,
) -> bool:
    """True when ``tokens[start:stop]`` is a whole word plus an abbreviation letter.

    ``source`` is the leet-folded scan text the spans index into; ``tokens``
    are its letter runs as scan pairs, parallel to ``token_spans``.
    """

    if stop - start != 2:
        return False
    window = tokens[start:stop]
    # A minted character means the leet fold wrote one of these letters out of
    # a digit -- the split-evasion signature, and already evasion-shaped.
    if any(token.real != token.shadow for token in window):
        return False
    lone = [index for index in (start, start + 1) if _is_lone_letter(token_spans[index])]
    if len(lone) != 1:
        return False
    index = lone[0]
    if not (
        _reads_as_elision(source, token_spans[index])
        or _reads_as_initialism(source, token_spans, index)
    ):
        return False
    return not hits_direct_bank("".join(token.real for token in window))
