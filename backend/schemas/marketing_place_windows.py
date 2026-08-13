"""A governed place beside a number is prose, not a split protected term.

Geography drill-down is a hero surface, so per-state counts are the product's
most common sentence shape. The leetspeak fold turns a count's digits into
letters, the scanner's rejoin step splices them onto the neighbouring token,
and the result can wear a governed term's spelling. ``LA 20 leads``
reconstructs ``lao`` and a governed Genie answer became a fair-lending
finding -- a regression against 7612b021 found in adversarial signoff.

Derived over the closed minted alphabet (digits ``013457`` fold to
``{o,l,i,e,a,s,t}``, one table applied uniformly) crossed with all USPS codes
and every bank, the joins that spell a governed term are::

    LA + 0    -> lao       MA + 13   -> male      3  + ID   -> eid
    LA + 05   -> laos      MA + 135  -> males     70 + MS   -> toms
    AL + 5    -> als       stat + IN -> statin    4 + ID + 5 -> aids
                                                  5 + WI + 55 -> swiss

Of those, this module exempts ``lao`` and ``laos`` and nothing else. The rest
are held back by the narrowings below -- which is the point: the exemption is
sized to the regression, not to the shape.

Why it has to be vocabulary. Match-time provenance cannot drop these, because
the span is only PARTLY minted: ``L`` and ``A`` are letters the author typed,
so ``ScanPair.backed`` reports the span as source-backed and is right to. Nor
can any rule keyed on the window's SHAPE, because the shape is identical to a
real evasion -- ``LA``+``o`` and ``lao``+``tian`` are both two tokens, one
wholly typed and one wholly minted. Only vocabulary separates them.

Four narrowings, each measured closing a real bypass:

1. exactly ONE typed token. A state code in prose is a single token; letters
   gathered from SEPARATED typed tokens are the evasion signature. A draft
   that joined them wherever they fell opened 250 evasions on the review
   corpus -- ``4 5 i 4 n 5`` (``asians``) hands back ``i``+``n`` = ``IN``,
   ``m 3 n`` (``men``) hands back ``MN``, ``7 h 4 i`` (``thai``) ``HI``.
2. the typed token at an EDGE, so the digits run along one side of the place
   rather than around it. Minted-on-both-sides is a term the code was spliced
   into: ``5 WI 55`` -> ``swiss``, ``4 ID 5`` -> ``aids``. Three bypasses.
3. something minted. A fully typed ``LA os`` earns no exemption.
4. no DIRECT bank may claim the window. Those banks match the joined blob
   without needing a population noun, so they were already refusing this
   shape in base: ``MA 13`` -> ``male`` stays a false positive, but a
   PRE-EXISTING one, and buying it here would cost six bypasses on a bank
   that never carried the regression.

``somali`` looks like family 2 but is unreachable: each fold table is applied
uniformly, so ``11`` folds to ``ll`` or ``ii`` and never to ``li``.
"""

from __future__ import annotations

from backend.schemas._validators_protected_class_patterns import (
    PROTECTED_AGE_CITIZENSHIP_MARKETING_RE,
    PROTECTED_CLASS_MARKETING_RE,
    PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE,
    PROTECTED_HEALTH_GOVERNANCE_INTENT_RE,
    PROTECTED_HEALTH_STATUS_MARKETING_RE,
    PROTECTED_HEALTH_TERM_MARKETING_RE,
)
from backend.schemas.marketing_scan_provenance import ScanPair
from backend.schemas.protected_relationships import PROTECTED_RELIGION_FAMILIAL_RELATION_RE
from backend.schemas.usps import USPS_STATE_CODES

# USPS Publication 28 primary street suffixes, closed and standard. Used only
# to recognise a house number in ``<number> <Street> <Suffix>`` position.
STREET_SUFFIXES: frozenset[str] = frozenset(
    {
        # fmt: off
        "alley", "avenue", "boulevard", "circle", "court", "crossing", "drive",
        "expressway", "freeway", "gardens", "grove", "heights", "highway",
        "lane", "loop", "parkway", "place", "plaza", "point", "ridge", "road",
        "route", "row", "run", "square", "street", "terrace", "trail", "turnpike",
        "walk", "way",
        "ave", "blvd", "cir", "ct", "dr", "hwy", "ln", "pkwy", "pl", "rd",
        "sq", "st", "ter", "trl",
        # fmt: on
    }
)

# The banks that match the joined scan blob WITHOUT needing a population noun
# nearby. A window one of these claims must never be dropped; see narrowing 4.
_DIRECT_BANKS = (
    PROTECTED_CLASS_MARKETING_RE,
    PROTECTED_AGE_CITIZENSHIP_MARKETING_RE,
    PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE,
    PROTECTED_RELIGION_FAMILIAL_RELATION_RE,
    PROTECTED_HEALTH_TERM_MARKETING_RE,
    PROTECTED_HEALTH_STATUS_MARKETING_RE,
    PROTECTED_HEALTH_GOVERNANCE_INTENT_RE,
)


def hits_direct_bank(candidate: str) -> bool:
    """True when a direct (non-context-gated) bank recognises these letters.

    Asked the same three ways the scanner's minted-run classifier asks,
    because several banks only match their term next to a population noun.
    """

    return any(
        pattern.search(probe)
        for probe in (candidate, f"{candidate} borrowers", f"{candidate} recipients")
        for pattern in _DIRECT_BANKS
    )


def window_is_place_beside_a_number(tokens: list[ScanPair], start: int, stop: int) -> bool:
    """True when ``tokens[start:stop]`` is a governed place next to a number.

    The state-code arm takes the typed token straight from the closed USPS
    set. The street-address arm exists because a house number folds the same
    way (``1407 Ian Court`` -> ``laot``+``Ian`` -> ``laotian``) and no
    vocabulary separates the typed ``Ian`` from the typed ``tian`` of a real
    evasion -- both are ordinary letter runs. What separates them is POSITION:
    a house number is followed by a street name and then a USPS suffix. So
    that arm fires only on ``<minted number> <Street> <Suffix>``, with the
    street name capitalized as an address renders it, which leaves
    ``140 tian record`` and ``140 tian homeowners`` refusing.

    Residual, accepted knowingly: ``140 Tian Court`` renders. Reaching it
    costs the evader an address frame and a capital letter, and the result no
    longer reads as an audience description to whoever would act on it -- a
    worse trade than the plain spelling, which still refuses.
    """

    window = tokens[start:stop]
    typed: list[ScanPair] = []
    minted: list[ScanPair] = []
    for token in window:
        # ``strict`` is safe: ScanPair's constructor guarantees equal lengths.
        pair_chars = zip(token.real, token.shadow, strict=True)
        backed = sum(1 for real, shadow in pair_chars if real == shadow)
        if backed == len(token.real):
            typed.append(token)
        elif backed == 0:
            minted.append(token)
        else:
            # A token mixing typed and minted letters (``l4o``) is a split
            # evasion, not a place.
            return False
    if not minted or len(typed) != 1:  # narrowings 1 and 3
        return False
    if typed[0] is not window[0] and typed[0] is not window[-1]:  # narrowing 2
        return False
    if not (
        typed[0].real.upper() in USPS_STATE_CODES
        or (
            typed[0] is window[-1]
            and not typed[0].real.islower()
            and stop < len(tokens)
            and tokens[stop].real.casefold() in STREET_SUFFIXES
        )
    ):
        return False
    # Narrowing 4 last, because it is the expensive one.
    return not hits_direct_bank("".join(token.real for token in window))
