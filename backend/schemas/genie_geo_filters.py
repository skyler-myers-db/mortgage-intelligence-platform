"""Canonical shape for the `(city, state)` cohort filter.

One definition, seven readers. A city-grain Genie answer is the one geography
the Lead Queue could not replay, and both of the shapes it degraded into were
measured live on paychex gold 2026-08-11 against an answer stating
Chicago = 523,010 cash-out candidates:

    rows [{city}]         -> no geography at all: 3,474,216 opened, 6.6x
    rows [{city, state}]  -> the STATE substituted for the city, which is
                             worse because it looks deliberate: 1,181,043, 2.3x

Both were merely DISCLOSED (``city_grain_unreplayable``). This module is the
key that makes the cohort exact instead.

Why a PAIR and not a name
-------------------------
``mip.gold.borrower_360.city`` carries 428 distinct names but 433 distinct
``(city, state)`` pairs (measured 2026-08-12, 5,085,969 of 5,156,184 rows
non-null). Five names span two states, and the minority side is tiny:

    CYPRESS          CA 14,630 / TX      1   -> 14,631x wrong on the TX side
    MIDLOTHIAN       IL  6,211 / TX     31
    SUNNYVALE        TX  3,833 / CA      1
    UNIVERSITY PARK  TX     39 / IL      6
    HIGHLAND PARK    TX     10 / IL      3

A name-only filter is therefore not "slightly broad"; on the minority side it
is wrong by four orders of magnitude. Carrying the state also preserves file
pruning -- ``borrower_360`` is ``CLUSTER BY (state, clip)`` -- so the pair is
cheaper to read than the name alone would be.

Why `~` and why plural-only
---------------------------
``~`` is RFC-3986 *unreserved*, so ``urlencode`` leaves it literal and a shared
link reads ``cities=CHICAGO~IL`` rather than ``CHICAGO%7EIL``. No city in gold
contains it (measured: the only non-``[A-Z]`` characters in the column are
space and hyphen). There is exactly ONE key, ``cities``, always plural: the
singular/plural pairs elsewhere in this vocabulary (``zip``/``zips``,
``state``/``states``) each need a second entry in every closed set they pass
through, and a key accepted by one vocabulary and rejected by another is how
PR #191 shipped a write-then-500.

Why the county key is not the model
-----------------------------------
``county_fips_5`` is NULL on every gold row since audit C2, so the county
filter matches nothing. City is modelled on ``state``/``zips`` -- vocabularies
that are alive -- and never on the county path.
"""

from __future__ import annotations

import re
from typing import Final

# The one key. Plural, always: see the module docstring.
GENIE_CITY_FILTER_KEY: Final[str] = "cities"

# The pair separator. Unreserved in RFC-3986 and absent from every gold city.
CITY_STATE_SEPARATOR: Final[str] = "~"

# ``CITY~ST``. The city side is deliberately tighter than "any text":
# uppercase letters, spaces, hyphens and periods only. Measured live, gold
# holds nothing but ``[A-Z]``, space and hyphen (one hyphenated value,
# ``UNION HILL-NOVELTY HILL, WA``); the period is admitted for the ``ST. LOUIS``
# shape so a legitimate future refresh is not rejected. An apostrophe is NOT
# admitted -- no gold row needs one, and the narrower the class the smaller the
# surface that reaches a bound parameter. Anything outside this shape fails
# closed to the existing ``city_grain_unreplayable`` disclosure rather than
# widening to the state.
#
# 48 characters is ~2x the observed maximum (23, ``UNION HILL-NOVELTY HILL``).
MAX_CITY_NAME_LEN: Final[int] = 48
CITY_STATE_PAIR_RE: Final[re.Pattern[str]] = re.compile(
    rf"^(?P<city>[A-Z][A-Z .-]{{0,{MAX_CITY_NAME_LEN - 1}}})"
    rf"{re.escape(CITY_STATE_SEPARATOR)}"
    r"(?P<state>[A-Z]{2})$"
)

# 433 pairs exist in gold today. 500 matches ``_MAX_ACTION_FILTER_VALUES`` (the
# ZIP cap) and comfortably covers "every city in the footprint" while still
# bounding a malformed answer.
MAX_CITY_FILTER_VALUES: Final[int] = 500


def format_city_state_pair(city: str, state: str) -> str:
    """Render one reviewed ``CITY~ST`` token, or ``""`` when the pair is unusable.

    Fails closed on purpose. A caller that gets ``""`` must emit NO city filter
    and keep the ``city_grain_unreplayable`` disclosure -- it must never fall
    through to the state, which is the 2.3x silent substitution this replaces.
    """

    city_text = " ".join(str(city or "").strip().upper().split())
    state_text = str(state or "").strip().upper()
    if not city_text or not state_text:
        return ""
    candidate = f"{city_text}{CITY_STATE_SEPARATOR}{state_text}"
    return candidate if CITY_STATE_PAIR_RE.fullmatch(candidate) else ""


def parse_city_state_pair(value: object) -> tuple[str, str] | None:
    """Split a ``CITY~ST`` token into ``(city, state)``, or None if malformed.

    Normalizes the way ``format_city_state_pair`` does (upper, collapse runs of
    whitespace) so a hand-typed ``?cities=fort  lauderdale~fl`` round-trips to
    the same pair the writer emitted.
    """

    text = str(value or "").strip()
    if not text:
        return None
    city_raw, separator, state_raw = text.partition(CITY_STATE_SEPARATOR)
    if not separator:
        return None
    normalized = format_city_state_pair(city_raw, state_raw)
    if not normalized:
        return None
    city, _, state = normalized.partition(CITY_STATE_SEPARATOR)
    return city, state


def normalise_city_state_pairs(values: object) -> list[str]:
    """Return the deduplicated, reviewed ``CITY~ST`` tokens in ``values``.

    Silently drops malformed entries (fail closed, per the module docstring)
    and caps the result. Order is preserved so a URL round-trips stably.
    """

    if isinstance(values, str) or not isinstance(values, list | tuple):
        return []
    out: list[str] = []
    for value in values:
        pair = parse_city_state_pair(value)
        if pair is None:
            continue
        token = f"{pair[0]}{CITY_STATE_SEPARATOR}{pair[1]}"
        if token not in out:
            out.append(token)
    return out[:MAX_CITY_FILTER_VALUES]
