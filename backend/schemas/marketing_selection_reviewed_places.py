"""The closed US place vocabulary the reviewed-analytics location slot admits.

Matching a reviewed analytics shape sets ``reviewed_analytics``, which is a
KILL SWITCH: it silences ``PROTECTED_HEALTH_TERM_MARKETING_RE`` and takes the
unknown-criterion state machine out of the decision for the whole clause. An
allow shape in a fail-closed system may therefore not contain an open slot,
which is what ``[A-Z][A-Za-z' -]{2,40}`` under IGNORECASE was -- 41 characters
of free text that let ``... in dialysis centers`` ride the shape.

Closing that slot to a vocabulary, rather than to a shape, is not a stylistic
choice. A bounded shape screened at match time was built and MEASURED here
before this module existed, and it fails in both directions at once: over a
20-term governed probe set and an 18-value governed place set, the best carrier
(``Show me borrowers in {loc}.``) missed ``eczema``, ``Chinatown``,
``methadone clinics`` and ``nursing homes`` -- ``eczema`` is not in the health
bank at all, it is caught only by the criterion machine the shape switches off
-- while refusing ``Tacoma``, ``Hawaiian Gardens`` and ``Indian Head Park``,
which are live gold cities. Membership in a screened vocabulary is what
separates a place from a governed term; the shape of the string cannot.

Three vocabularies, three lifetimes
-----------------------------------
* **States** -- the federal list. Fixed, and reproduced here in full.
* **Counties** -- the shipped ``us-counties.json`` the map drill-down already
  ships. National, public-domain, and repo-committed, so it screens once at
  import-time cost and never drifts with coverage.
* **Cities** -- the governed gold dimension, which is live Cotality coverage
  and cannot be committed (CLAUDE.md: the product follows the current refresh
  dynamically). It arrives by registration from the services layer, because
  ``backend/schemas`` may not import ``backend/services``. Until it does, the
  set is empty and a city-grain question refuses, which is the same answer the
  guard gives today -- degradation costs the grain, never the guard.

Every county and city value passes :func:`_collides_with_governed_term` before
it is admitted. That gate runs the REAL banks, not a copy of them: a private
regex copy is how a relaxation silently stops tracking the detector it claims
to mirror.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Final

# The federal list: 50 states plus the District of Columbia.
#
# NOT ``_validators_person_names.US_STATE_NAMES``. That tuple names itself "the
# federal list" but is a person-name-heuristic helper, and it deliberately
# carries only the ONE-word states -- "Two-word states already live in
# ``_REVIEWED_NON_PERSON_PHRASES``; the pair heuristic only ever misreads the
# ONE-word ones". Reusing it to close the analytics location tail silently
# dropped eleven states and DC out of the governed slot: "in New York", "in
# North Carolina" and "in District of Columbia" all refused, measured on the
# 814-string differential against 7612b021.
US_STATE_NAMES_FEDERAL: Final[tuple[str, ...]] = (
    # fmt: off
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin",
    "Wyoming",
    # fmt: on
)
US_STATE_CODES: Final[tuple[str, ...]] = (
    # fmt: off
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
    # fmt: on
)
# ``ms`` is the one USPS code that is also a governed term (multiple
# sclerosis) -- computed by sweeping all 676 pairs against the banks, not
# assumed. Mississippi stays reachable by name, and as "(MS)" after it.
_EXCLUDED_STATE_CODES: Final[frozenset[str]] = frozenset({"ms"})

_STATE_NAMES_LOWER: Final[frozenset[str]] = frozenset(
    name.lower() for name in US_STATE_NAMES_FEDERAL
)
_STATE_CODES_LOWER: Final[frozenset[str]] = frozenset(code.lower() for code in US_STATE_CODES)

# The map drill-down's own artifact. Public-domain us-atlas county TopoJSON,
# already a backend dependency for county display labels.
_COUNTY_TOPOJSON: Final[Path] = (
    Path(__file__).resolve().parents[2] / "frontend" / "public" / "us-counties.json"
)
# A county dimension that reads far larger than the ~1.8k distinct national
# names is not the shipped artifact. Admitting thousands of unscreened values
# on that evidence would be a guard rewrite by accident, so admit none.
_MAX_COUNTY_NAMES: Final[int] = 4_096
# Governed city values arrive from live gold. The same posture, one order up:
# the dimension measured 428 distinct values on 2026-08-12.
_MAX_CITY_VALUES: Final[int] = 8_192

# Shipped county names that carry a governed term. Committed rather than
# screened at runtime because the artifact is static: which names collide can
# only change when a DETECTOR BANK changes, and screening ~1.8k names costs
# ~5s -- unacceptable on a first guard call, trivial as a build step.
# Re-derive with ``tools/screen_county_place_vocabulary.py``.
#
# 13 of 1,833, and every one is explainable, which is the check that says the
# gate is measuring something rather than just returning: race (``white``,
# ``white pine``), age (``box elder``, ``young``), national origin
# (``canadian``, ``indian river``), religion (``christian``, ``falls church``,
# ``st. john the baptist``), disability (``deaf smith``), and the ``-oma``
# condition morphology (``coahoma``, ``oklahoma``, ``sonoma``) that also costs
# the prose surface every question naming Tacoma.
#
# Two names are worth knowing about. ``Oklahoma`` stays reachable as a STATE:
# the federal list is its own closed vocabulary and is checked first, so only
# the county grain is lost. ``Young`` is the one entry the gate excludes that
# the full guard does not flag in a ranked sentence -- "Young borrowers" trips
# age targeting, "... in Young County" does not -- and it stays excluded,
# because the conservative carrier is the right one for a slot that hands out
# a kill switch.
COUNTY_NAME_EXCLUSIONS: Final[frozenset[str]] = frozenset(
    {
        "box elder",
        "canadian",
        "christian",
        "coahoma",
        "deaf smith",
        "falls church",
        "indian river",
        "oklahoma",
        "sonoma",
        "st. john the baptist",
        "white",
        "white pine",
        "young",
    }
)

# At most four whitespace-separated tokens. "Prince George's County" is three;
# nothing in any of the three vocabularies is longer. The bound is what stops
# the capture swallowing a following clause in the compound shapes -- and if
# backtracking ever does settle on a wrong binding, membership rejects it and
# the clause fails closed, which is a false refusal and never a bypass.
_LOCATION_TOKEN = r"[A-Za-z][A-Za-z.'-]*"
REVIEWED_ANALYTIC_LOCATION_FRAGMENT: Final[str] = (
    r"(?:\s+(?:in|across)\s+(?:the\s+current\s+(?:coverage|portfolio)|"
    r"(?:the\s+state\s+of\s+)?"
    rf"(?P<loc>{_LOCATION_TOKEN}(?:\s+{_LOCATION_TOKEN}){{0,3}}"
    r"(?:\s*,\s*[A-Za-z]{2})?(?:\s*\([A-Za-z]{2}\))?)))?"
)

_TRAILING_STATE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<core>.*?)(?:\s*,\s*(?P<comma>[A-Za-z]{2})|\s*\((?P<paren>[A-Za-z]{2})\))$"
)


def _collides_with_governed_term(value: str) -> bool:
    """True when a place value carries a term a detector governs.

    Asks the REAL front door, not a hand-assembled list of banks. The first
    draft of this gate enumerated seven compiled patterns and was measured
    against the full guard over all 1,833 shipped county names: it agreed on
    every name but ``Falls Church``, which the guard refuses through
    ``_contains_protected_class_proxy_pair`` -- a detector that is a function,
    not a pattern, so no bank list contains it. That is the private-copy
    failure mode in miniature, caught only because the sweep had a control to
    disagree with. Delegating means a detector added to the guard is in this
    gate the same day.

    One probe, ``"<value> borrowers"``. It contains the bare value, so a
    direct-term hit is still found, and it supplies the population noun the
    windowed detectors need -- the national-origin bank is why ``Indian
    River`` and ``Hawaiian Gardens`` are excluded, and it does not fire on a
    bare value. ~2.8ms per probe.

    ``unreviewed_criterion`` is NOT a collision: it is the criterion state
    machine saying it does not recognise "Adams borrowers" as a complete
    reviewed selection, which is true of every place name and would exclude
    the entire vocabulary. Only a named protected-class verdict disqualifies.

    Imported lazily and deliberately: ``_validators_protected_class`` reaches
    this module through ``marketing_selection_criteria``, so a module-level
    import would close a cycle. The probe cannot recurse into the county set --
    ``admitted_county_names`` is set arithmetic over a committed exclusion
    list and calls nothing here -- and a bare ``"<value> borrowers"`` cannot
    fullmatch an anchored analytics shape.
    """

    from backend.schemas._validators_protected_class import protected_class_marketing_reason

    return protected_class_marketing_reason(f"{value} borrowers") == "protected_class"


@lru_cache(maxsize=1)
def shipped_county_names() -> frozenset[str]:
    """Distinct county names from the shipped national TopoJSON, unscreened.

    Empty when the artifact is missing or unreadable: no counties admitted, so
    a county question refuses. Fail-closed, like every other degradation on
    this path.
    """

    try:
        payload = json.loads(_COUNTY_TOPOJSON.read_text(encoding="utf-8"))
        geometries = payload["objects"]["counties"]["geometries"]
    except Exception:
        return frozenset()
    names = {
        " ".join(str(geometry["properties"]["name"]).split())
        for geometry in geometries
        if isinstance(geometry, dict) and (geometry.get("properties") or {}).get("name")
    }
    if not names or len(names) > _MAX_COUNTY_NAMES:
        return frozenset()
    return frozenset(name for name in names if name)


@lru_cache(maxsize=1)
def admitted_county_names() -> frozenset[str]:
    """Lower-cased shipped county names, minus the screened collisions.

    Carries a period-stripped spelling of the ~20 abbreviated names
    (``st. louis`` -> ``st louis``). Not cosmetic: the reviewed-analytics text
    is split into clauses on ``[.!?;:\\n]+`` before any shape is matched, so
    "... in St. Louis County." arrives as two clauses cut at the abbreviation
    and no shape can match either half. "St Louis County" is the spelling that
    survives that split, and the abbreviated-with-period form stays refused --
    a clause-segmentation residual that predates this slot and is not fixable
    inside it.

    Dropping a period cannot introduce a token, so the stripped spelling
    inherits its screen from the name it came from; no re-probe is needed.
    """

    admitted = frozenset(name.lower() for name in shipped_county_names()) - COUNTY_NAME_EXCLUSIONS
    return admitted | {
        " ".join(name.replace(".", " ").split()) for name in admitted if "." in name
    }


_registered_city_values: frozenset[str] = frozenset()
_registered_city_input: frozenset[str] | None = None


def register_governed_analytics_cities(values: object) -> int:
    """Publish the governed city dimension to the location slot.

    Dependency inversion, because ``backend/schemas`` may not import
    ``backend/services`` (``test_schemas_do_not_import_runtime_services``). The
    resolver that owns the live dimension calls this at warm-up; nothing here
    reaches for it, so the schemas layer keeps no runtime dependency.

    Returns the number of values admitted, so the caller can log what a refresh
    actually bought rather than assume. Passing an empty iterable clears the
    set, which is how a degraded resolve withdraws the city grain.

    Screening the 428 live values costs ~1.2s, so an unchanged dimension
    short-circuits on the raw input before a single probe runs. The resolver
    re-reads gold every 15 minutes and the values almost never move; without
    this, every TTL refresh would pay the full screen on whichever request
    happened to trigger it.
    """

    global _registered_city_values, _registered_city_input
    if isinstance(values, str | bytes):
        candidates: tuple[str, ...] = ()
    else:
        try:
            candidates = tuple(str(value) for value in values)  # type: ignore[union-attr]
        except TypeError:
            candidates = ()
    incoming = frozenset(candidates)
    if incoming == _registered_city_input:
        return len(_registered_city_values)
    admitted: set[str] = set()
    if len(candidates) <= _MAX_CITY_VALUES:
        for value in candidates:
            collapsed = " ".join(value.split())
            if not collapsed or len(collapsed.split()) > 4:
                continue
            if _collides_with_governed_term(collapsed):
                continue
            admitted.add(collapsed.lower())
    resolved = frozenset(admitted)
    _registered_city_input = incoming
    if resolved != _registered_city_values:
        _registered_city_values = resolved
        _invalidate_dependent_caches()
    return len(resolved)


def governed_analytics_cities() -> frozenset[str]:
    """The admitted governed city values. Empty until a resolver registers."""

    return _registered_city_values


def _invalidate_dependent_caches() -> None:
    """Drop the guard memos that could hold a pre-registration verdict.

    The prompt guard memoizes by text, so a question answered before warm-up
    would keep its refusal for the life of the process. Lazily imported for the
    same cycle reason as the probe in :func:`_collides_with_governed_term`.
    """

    from backend.schemas._validators_protected_class import (
        _protected_class_marketing_reason_cached,
    )

    _protected_class_marketing_reason_cached.cache_clear()


def is_governed_analytics_location(value: str | None) -> bool:
    """True when a captured location slot names a governed US place.

    ``None`` means the clause named no location, which is the shape's own
    business, not this vocabulary's.
    """

    if value is None:
        return True
    text = " ".join(str(value).split())
    # The shapes keep "the state of" outside the capture, but this is the
    # public contract for a location string and callers pass what they read.
    if text.lower().startswith("the state of "):
        text = text[len("the state of ") :].strip()
    if not text:
        return False
    trailing = _TRAILING_STATE_RE.fullmatch(text)
    if trailing is not None:
        code = (trailing.group("comma") or trailing.group("paren") or "").lower()
        core = trailing.group("core").strip()
        # A trailing two-letter token only disambiguates when it is a real
        # state code AND something precedes it. Anything else keeps the whole
        # string and lets membership decide.
        #
        # ``ms`` is NOT excluded here. The exclusion belongs to the bare-code
        # branch below, where nothing in the string says which reading is
        # meant; in "Mississippi (MS)" the state name is right there, and
        # dropping the parenthetical on that ground refused a spelling the
        # closed tail has always admitted.
        if core and code in _STATE_CODES_LOWER:
            text = core
    lowered = text.lower()
    if lowered in _STATE_NAMES_LOWER:
        return True
    if lowered in _STATE_CODES_LOWER:
        return lowered not in _EXCLUDED_STATE_CODES
    if lowered in governed_analytics_cities():
        return True
    counties = admitted_county_names()
    if lowered in counties:
        return True
    bare = lowered[: -len(" county")] if lowered.endswith(" county") else ""
    return bool(bare) and bare in counties
