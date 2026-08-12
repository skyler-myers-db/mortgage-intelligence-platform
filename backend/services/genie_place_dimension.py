"""Governed place-dimension values the output-policy scanner disagrees with.

The Genie result grid is scanned cell by cell by the same detectors that guard
model-authored outreach prose. Against a gold ``city`` column those detectors
produce pure false positives, and the block reaches the user as "The generated
response did not pass the governed output policy" with ``unsafe_field:
"table_rows"`` in the server log and nothing else to go on.

Measured on paychex 2026-08-12 against the 428 distinct ``city`` values in
``mip.gold.borrower_360``, exactly three collide:

* ``TACOMA`` — the tumor-suffix heuristic in the health-term bank matches the
  ``-oma`` tail. 17 borrowers.
* ``BLACK DIAMOND`` — matches ``black``. The place-name exemption in
  ``PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS`` already lists
  ``(?:white|black)\\s+(?:...|diamond)`` and DOES clear ``Black Diamond``, but
  the protected-class scan runs against a space-joined superset that includes
  the ASCII-confusable fold (capital ``I`` -> lowercase ``l``). The exemption
  erases ``BLACK DIAMOND`` from that superset and cannot erase the fold's
  ``BLACK DLAMOND``, so a bare ``\\bblack\\b`` survives. Title case has no
  capital ``I`` to fold, which is why ``Black Diamond`` passes and the
  uppercase gold value does not. 3,678 borrowers.
* ``HAWAIIAN GARDENS`` — matches ``hawaiian``. No exemption exists. 2 borrowers.

The ANSWER NARRATIVE has a second, larger collision, measured on paychex
2026-08-12 against the same 428 values: 51 of them trip the TITLE-CASE
person-name heuristic when Genie writes them into ordinary prose
(``Highlands Ranch``, ``Lone Tree``, ``Federal Way``, ``Mission Viejo``, ...).
The two closed vocabularies in ``_validators_person_names`` cover the toponym
formants they were built from (``Lake Forest``, ``El Paso``) and miss the rest.
This is not a fair-lending finding -- it is the identity heuristic reading a
governed place as a person -- and it blocks live: "List the top 10 cities in
Colorado for cash-out candidates" and "... in Washington for in-the-money
borrowers" both had their governed narrative withheld (captured 2026-08-12,
rows contained ``HIGHLANDS RANCH``/``LONE TREE`` and ``FEDERAL WAY``).

``name_shape_safe_values`` serves that second set, and it is deliberately NOT
the same set as ``conflicting_values``: it is scoped to the one detector that
produces the false positive, and any value sharing a token with the person-name
lexicons is excluded (see ``_collides_with_person_lexicon``).

There is a THIRD collision, and it is the one that was hidden rather than
absent. ``genie_message_policy`` used to delete every ``City, ST`` span from
the text before ANY detector saw it, so a governed city that trips a
fair-lending detector still rendered as long as Genie qualified it with a
state. That strip is now scoped to the person-name scan (which is all its
docstring ever claimed), and the four live values it was covering surface as
what they are -- ``TACOMA``, ``BLACK DIAMOND``, ``HAWAIIAN GARDENS``,
``INDIAN HEAD PARK``, measured against the same 428 values.
``protected_class_safe_values`` serves them.

That third set subtracts from a FAIR-LENDING detector, so it carries an
admission gate the other two do not need: a value is admitted only after
masking it is shown not to disarm any must-block probe
(``_disarms_a_protected_class_canary``). A gold city named ``BLACK DIAMOND``
is admitted; a gold city named ``BLACK`` is refused, and
``test_genie_city_protected_class_guard.py`` fails if the live dimension ever
contains one.

This module resolves all three sets from the live governed dimension rather
than pinning names. A static list would go stale the moment Cotality coverage
refreshes, and CLAUDE.md forbids pinning the product to a fixed geography.

Posture, in order:

1. Live distinct ``city`` values from the governed gold table, filtered to the
   values the structured-cell scan rejects, cached for
   ``_PLACE_DIMENSION_TTL_S``.
2. Last-known-good (stale cache) when the warehouse read fails.
3. Empty set. That is exactly today's behavior — the wide grid blocks — so the
   degraded path is fail-CLOSED, never a silent widening of the guard.

It never raises: a governed-output check must not turn a warehouse hiccup into
a failed request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from functools import lru_cache
from threading import Lock
from typing import NamedTuple

from backend.services.observability import emit

log = logging.getLogger(__name__)

# The dimension only changes when gold is rebuilt, so a longer TTL than the
# 300s geography footprint is both fresh enough and four times cheaper.
_PLACE_DIMENSION_TTL_S: float = 900.0
# Cache the degraded answer too, briefly. Without this every Genie response
# during a warehouse outage re-runs the read (and its retry/backoff) inside the
# output-policy check, turning a degraded dependency into a latency cliff on
# the surface that is supposed to survive it.
_PLACE_DIMENSION_FAILURE_TTL_S: float = 30.0
_PLACE_DIMENSION_CACHE_KEY: str = "mip.gold.borrower_360.city"

# Defensive bounds. The read is a governed DISTINCT over one low-cardinality
# column (428 values today); anything wildly outside that shape means the read
# returned something other than a city dimension, and the safe response is to
# exempt nothing rather than to trust it.
_MAX_DIMENSION_ROWS: int = 50_000
_MAX_CONFLICTING_VALUES: int = 256
# The prose set is legitimately larger (51 of 428 today) because the title-case
# person-name heuristic has no place-name vocabulary for most two-word cities.
# A set approaching the whole dimension still means the read returned something
# other than a city column, and exempting all of it would silently retire the
# name-shape heuristic on this surface.
_MAX_NAME_SHAPE_VALUES: int = 2_048
# The fair-lending set is the dangerous one, so its bound is the tight one.
# Four of the 428 live values qualify today (0.9%): TACOMA, BLACK DIAMOND,
# HAWAIIAN GARDENS, INDIAN HEAD PARK. 64 is ~16x that headroom at today's
# dimension size and still an order of magnitude below the dimension itself,
# so a read that returned something other than a city column cannot quietly
# subtract a large vocabulary from the fair-lending scanner.
_MAX_PROTECTED_CLASS_VALUES: int = 64


class ResolvedPlaceDimension(NamedTuple):
    """Every governed exemption set from a single dimension read."""

    conflicting: frozenset[str]
    """Normalized values the STRUCTURED-cell scan rejects (exact full-cell)."""
    name_shape_safe: frozenset[str]
    """Stored-casing values the PROSE person-name heuristic misreads."""
    protected_class_safe: frozenset[str]
    """Stored-casing values the PROSE fair-lending scan misreads."""
    all_values: frozenset[str]
    """Every governed city value, unfiltered. NOT an exemption set.

    The sentence-initial strip needs to recognise a place, not to stop scanning
    one: it only decides whether an opening ``Which``/``The`` is orthography.
    Nothing is masked from any detector on the strength of membership here.
    """


_EMPTY_DIMENSION = ResolvedPlaceDimension(frozenset(), frozenset(), frozenset(), frozenset())


def normalize_place_value(value: str) -> str:
    """Casefold and collapse whitespace for exact governed-value matching."""

    return " ".join(str(value).split()).casefold()


def _default_conflict_predicate(value: str) -> bool:
    """Does the structured-cell scan reject this governed dimension value?

    Imported lazily: the policy module resolves this dimension, so a module
    level import would close a cycle. ``genie_visible_text_unsafe`` never
    consults this resolver itself, so there is no recursion either way.
    """

    from backend.services.genie_message_policy import genie_visible_text_unsafe

    return genie_visible_text_unsafe(value, structured_value=True)


# The narrative probe. Genie renders a governed city into ordinary prose in
# both the stored casing ("FEDERAL WAY") and title case ("Federal Way"), so a
# value qualifies when EITHER rendering trips the name-shape heuristic in a
# sentence that is otherwise plain governed analytics.
_NARRATIVE_PROBE = "{city} leads with 17 in-the-money borrowers."


def _title_case(value: str) -> str:
    return " ".join(word.capitalize() for word in value.split())


def _default_name_shape_conflict_predicate(value: str) -> bool:
    """Does the title-case person-name heuristic reject this city IN PROSE?

    Deliberately narrower than the structured-cell predicate: it asks only
    whether ``contains_human_name_shape`` is the detector that fires. A value
    that a protected-class, health, or PII detector rejects is NOT eligible --
    masking it would not help (those scanners never see the mask) and claiming
    it as a name-shape false positive would be wrong.
    """

    from backend.schemas._validators_person_names import contains_human_name_shape

    return any(
        contains_human_name_shape(_NARRATIVE_PROBE.format(city=rendering))
        for rendering in (value, _title_case(value))
    )


# The fair-lending probe. The national-origin bank only fires when its term
# sits near a population or targeting noun, which a narrative always supplies
# and a bare value never does -- ``INDIAN HEAD PARK`` is invisible without one.
# One population noun is the whole carrier: the full narrative probe above
# finds exactly the same four live values and costs 8x more, because the
# protected-class scan builds joined-token windows over every token it is given.
_PROTECTED_CLASS_PROBE = "{city} borrowers"

# Prose that MUST stay blocked, used as a differential gate below rather than
# as a term list. Masking is whole-run anchored, so the only detection a
# governed value can ever cost the fair-lending scanner is the value itself --
# which makes "is this value safe to mask?" exactly "does masking it stop any
# of these from being caught?".
#
# The canaries are a CROSS-PRODUCT of protected term x audience noun, not one
# sentence per term, and that is what makes the gate structural rather than a
# list of today's names. The canary that catches a dangerous value is the one
# that CONTAINS the value's run: ``HAWAIIAN HOMEOWNERS`` is refused because
# "Target hawaiian homeowners for this campaign." stops blocking once the run
# is masked, while ``HAWAIIAN GARDENS`` is admitted because no canary contains
# it. Sweeping the nouns is therefore what separates a governed place from a
# protected audience, and it needs no "population noun" vocabulary kept in
# sync with the detectors.
#
# Singular and plural both, because the run has to match literally: without
# ``community`` the singular ``BLACK COMMUNITY`` slips past the plural canary.
_PROTECTED_CLASS_AUDIENCE_NOUNS: tuple[str, ...] = (
    "applicant",
    "applicants",
    "borrower",
    "borrowers",
    "communities",
    "community",
    "customer",
    "customers",
    "families",
    "family",
    "homeowner",
    "homeowners",
    "household",
    "households",
    "neighborhood",
    "neighborhoods",
    "people",
    "prospect",
    "prospects",
    "resident",
    "residents",
    "zip codes",
)
_PROTECTED_CLASS_CANARY_CARRIER = "Target {term} {noun} for this campaign."
# The national-origin half of the sweep is generated from the detector's own
# frozenset (see ``_canary_terms``) so the gate cannot drift behind the bank it
# guards. These name one term per protected-class family for the banks that do
# not expose an enumerable set.
_PROTECTED_CLASS_CANARY_TERMS: tuple[str, ...] = (
    # Race / ethnicity / colour.
    "black",
    "white",
    "asian",
    "arab",
    "hispanic",
    "latino",
    "biracial",
    "multiracial",
    "indigenous",
    "native american",
    "native hawaiian",
    "hawaiian",
    "pacific islander",
    "alaska native",
    "american indian",
    "african american",
    # Religion.
    "muslim",
    "jewish",
    "catholic",
    "hindu",
    "sikh",
    "buddhist",
    "evangelical",
    "mormon",
    # Sex, gender, orientation.
    "women",
    "men",
    "female",
    "transgender",
    "gay",
    "lesbian",
    "non-binary",
    # Familial and marital status.
    "pregnant",
    "single mothers",
    "families with children",
    "married",
    "divorced",
    "widowed",
    # Disability and age.
    "disabled",
    "blind",
    "deaf",
    "wheelchair users",
    "neurodivergent",
    "elderly",
    "senior citizens",
    # Health status.
    "melanoma",
    "sarcoma",
    "diabetes",
    "dementia",
    "epileptic",
    # Public assistance and military status.
    "welfare recipients",
    "veterans",
)
# Proxy shapes, which the term carrier cannot express: these are caught by the
# geographic-composition and proxy detectors rather than by a bare term.
_PROTECTED_CLASS_CANARY_SENTENCES: tuple[str, ...] = (
    "Target black neighborhoods for this campaign.",
    "Focus on the predominantly hispanic ZIP codes in the portfolio.",
    "Select the census tracts with the highest share of asian households.",
)


@lru_cache(maxsize=1)
def _canary_terms() -> tuple[str, ...]:
    """Protected-class terms the canary sweep is built from.

    The national-origin and named-health halves come from the detectors' own
    vocabularies so the gate cannot drift behind the banks it guards. The
    health half matters: ``cancer`` was absent from the hand-written list, so a
    gold city named ``CANCER`` cleared the gate and "Tell me about borrowers
    with cancer in <city>" lost its health finding (test audit, 2026-08-12).
    The prompt guard's explicit term bank is folded in for the same reason,
    and it matters MORE on the answer surface than on the prompt: the prompt
    scans that bank against unmasked text, but prose has no such bank, so a
    gold city named ``RACE`` would have disarmed a narrative finding with
    nothing left to catch it. Morphology is deliberately NOT folded in — that
    is the ``-oma`` collision the exemption exists to fix.
    """

    from backend.schemas.marketing_safety_terms import (
        _PROTECTED_NATIONAL_ORIGIN_TERMS,
        reviewed_named_health_terms,
    )
    from backend.services.genie_message_policy import _PROTECTED_PROMPT_TERMS

    return tuple(
        sorted(
            set(_PROTECTED_CLASS_CANARY_TERMS)
            | set(_PROTECTED_NATIONAL_ORIGIN_TERMS)
            | set(reviewed_named_health_terms())
            | set(_PROTECTED_PROMPT_TERMS)
        )
    )


@lru_cache(maxsize=1)
def _protected_class_canaries() -> tuple[str, ...]:
    """Every must-block probe the admission gate below runs a value against."""

    terms = _canary_terms()
    return (
        tuple(
            _PROTECTED_CLASS_CANARY_CARRIER.format(term=term, noun=noun)
            for term in terms
            for noun in _PROTECTED_CLASS_AUDIENCE_NOUNS
        )
        + _PROTECTED_CLASS_CANARY_SENTENCES
    )


@lru_cache(maxsize=512)
def protected_term_overlap_guards(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Contexts where erasing this value would damage a protected term.

    Masking is a whole-token-run erase, so a term can only be damaged where the
    two runs OVERLAP, and for token runs that is exactly two shapes: a suffix of
    the term equals a prefix of the value (``native`` + ``hawaiian gardens``),
    or a prefix of the term equals a suffix of the value. Full containment
    either way is not overlap — the resolver's admission gate already covers it,
    and guarding it here would stop the mask working at all.

    Returned as (left contexts, right contexts) for
    :func:`mask_governed_phrases`, so the value is erased everywhere EXCEPT
    beside the term it could damage.
    """

    value_tokens = value.split()
    if not value_tokens:
        return ((), ())
    folded_value = [token.casefold() for token in value_tokens]
    lefts: set[str] = set()
    rights: set[str] = set()
    for term in _canary_terms():
        term_tokens = term.split()
        folded_term = [token.casefold() for token in term_tokens]
        for overlap in range(1, min(len(term_tokens), len(value_tokens)) + 1):
            if len(term_tokens) <= overlap:
                continue  # containment, not overlap
            if folded_term[-overlap:] == folded_value[:overlap]:
                lefts.add(" ".join(term_tokens[:-overlap]))
            if folded_term[:overlap] == folded_value[-overlap:]:
                rights.add(" ".join(term_tokens[overlap:]))
    return (tuple(sorted(lefts)), tuple(sorted(rights)))


def governed_protected_class_mask_guards(
    values: Sequence[str],
) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Per-value guards for every governed place masked out of fair lending."""

    return tuple((value, *protected_term_overlap_guards(value)) for value in values)


def _boundary_overlap_canaries(value: str) -> tuple[str, ...]:
    """Canaries where the value's run OVERLAPS a protected term's run.

    The occurrence-scoped gate below skips any canary the value does not appear
    in, and an adversarial review on 2026-08-12 turned that skip into a
    laundering path: masking the run ``hawaiian gardens`` also erases the
    ``hawaiian`` of ``native hawaiian``, so "Which Native Hawaiian Gardens
    homeowners should we contact" stopped being refused — 216 such strings,
    built from the gate's OWN must-block term list, and both live values
    (``HAWAIIAN GARDENS``, ``INDIAN HEAD PARK``) were vehicles.

    Masking is a whole-token-run erase, so a protected term can only be damaged
    where the two runs overlap, and for token runs that is exactly two shapes:
    a suffix of the term equals a prefix of the value ("native" + "hawaiian
    gardens"), or a prefix of the term equals a suffix of the value ("little
    black" + "neighborhoods"). Full containment either way is already covered —
    the canary then literally contains the value, so the gate does not skip it.
    Splicing those two shapes into the same carriers closes the class rather
    than the two strings that exposed it.
    """

    value_tokens = value.split()
    if not value_tokens:
        return ()
    folded_value = [token.casefold() for token in value_tokens]
    spliced: set[str] = set()
    for term in _canary_terms():
        term_tokens = term.split()
        folded_term = [token.casefold() for token in term_tokens]
        for overlap in range(1, min(len(term_tokens), len(value_tokens)) + 1):
            if len(term_tokens) <= overlap:
                # The term is CONTAINED in the value, not overlapping it. The
                # splice would be the value itself ("Target Hawaiian Gardens
                # applicant ..."), which is place targeting, not protected-class
                # targeting -- masking it SHOULD stop blocking. Leaving this in
                # condemned every value the exemption exists for.
                continue
            if folded_term[-overlap:] == folded_value[:overlap]:
                spliced.add(" ".join(term_tokens[:-overlap] + value_tokens))
            if folded_term[:overlap] == folded_value[-overlap:]:
                spliced.add(" ".join(value_tokens + term_tokens[overlap:]))
    return tuple(
        _PROTECTED_CLASS_CANARY_CARRIER.format(term=phrase, noun=noun)
        for phrase in sorted(spliced)
        for noun in _PROTECTED_CLASS_AUDIENCE_NOUNS
    )


def _default_protected_class_conflict_predicate(value: str) -> bool:
    """Does the fair-lending scan reject this city IN PROSE?

    Scoped the same way as the name-shape predicate: it asks only whether
    ``contains_protected_class_marketing_text`` is the detector that fires.
    A value rejected by the PII, injection, or confidential scanners is not
    eligible -- masking it would not help, because those scanners are never
    handed the masked copy.
    """

    from backend.schemas._validators_protected_class import (
        contains_protected_class_marketing_text,
    )

    return any(
        contains_protected_class_marketing_text(
            _PROTECTED_CLASS_PROBE.format(city=rendering),
            assume_reviewed_read_only_analytics=True,
        )
        for rendering in (value, _title_case(value))
    )


def _disarms_a_protected_class_canary(value: str) -> bool:
    """True when masking this value would blind the fair-lending scanner.

    This is the gate that makes it safe to subtract governed geography from a
    fair-lending detector. ``BLACK DIAMOND`` and ``HAWAIIAN GARDENS`` pass it:
    masking a whole-token ``black diamond`` run leaves every canary untouched,
    so a standalone ``black`` is still caught. A gold city literally named
    ``BLACK`` fails it: masking it empties "Target black borrowers for this
    campaign." of the term that blocks it, and the value is refused.

    Uses the guard's own :func:`mask_governed_phrases`, not a re-implementation
    -- a gate that masked differently from the guard would prove nothing about
    the guard. Canaries the value does not occur in are skipped rather than
    re-scanned: masking cannot change text it does not match, and the scan is
    the expensive half. That skip is exactly why the fixed carrier set is not
    sufficient on its own; :func:`_boundary_overlap_canaries` supplies the
    probes where the value's run OVERLAPS a protected term's run instead of
    containing it.

    The term x audience-noun cross-product has dead corners -- the bank matches
    ``disabled`` only beside a person noun, so "Target disabled households ..."
    does not block while "Target disabled borrowers ..." does. A value landing
    in a dead corner is refused anyway, on a canary that could not have been
    disarmed. That over-refusal is deliberate: refusing costs a governed city
    name in one answer, admitting costs a fair-lending detector, and a corner
    the detector does not catch is a gap in the detector rather than evidence
    that the phrase is safe to erase. No live gold value is affected (the one
    live refusal, ``PACIFIC``, comes from a canary that does block and is not
    a candidate for the exemption in the first place).
    """

    from backend.schemas._validators_protected_class import (
        contains_protected_class_marketing_text,
    )
    from backend.schemas._validators_unsafe_text import mask_governed_phrases

    guards = ((value, *protected_term_overlap_guards(value)),)
    for canary in _protected_class_canaries() + _boundary_overlap_canaries(value):
        # The SAME guarded mask the consumer uses. A spliced overlap canary
        # therefore proves the guard holds ("Target native hawaiian gardens
        # borrowers ..." is left intact and still blocks) instead of condemning
        # the value; only a value the guard cannot save is refused.
        masked = mask_governed_phrases(canary, (value,), guards)
        if masked == canary:
            continue
        if not contains_protected_class_marketing_text(
            masked, assume_reviewed_read_only_analytics=True
        ):
            return True
    return False


def _collides_with_person_lexicon(value: str) -> bool:
    """True when masking this value could blind a real person-name detection.

    ``ELIZABETH`` is a live ``mip.gold.borrower_360`` city AND a reviewed
    first name; ``YORBA LINDA`` carries ``LINDA``. Blanking either token out of
    the name-shape scan would hide ``Elizabeth Smith``. The governed dimension
    is authoritative about geography, not about which strings are safe to stop
    scanning for identities, so any value sharing a token with the person
    lexicons is excluded and keeps scanning. ``test_genie_place_dimension.py``
    fails if this exclusion ever stops firing on a live gold value.
    """

    from backend.schemas._validators_person_names import shares_token_with_person_lexicon

    return shares_token_with_person_lexicon(value)


class GovernedPlaceDimensionResolver:
    """Resolve governed city values that the output-policy scanner rejects."""

    def __init__(
        self,
        *,
        ttl_s: float = _PLACE_DIMENSION_TTL_S,
        conflict_predicate: Callable[[str], bool] | None = None,
        name_shape_conflict_predicate: Callable[[str], bool] | None = None,
        protected_class_conflict_predicate: Callable[[str], bool] | None = None,
        dimension_reader: Callable[[], list[str]] | None = None,
    ) -> None:
        from backend.services.resilience import TTLCache

        self._cache: TTLCache = TTLCache(max_entries=4)
        self._ttl_s = ttl_s
        self._conflict_predicate = conflict_predicate or _default_conflict_predicate
        self._name_shape_conflict_predicate = (
            name_shape_conflict_predicate or _default_name_shape_conflict_predicate
        )
        self._protected_class_conflict_predicate = (
            protected_class_conflict_predicate or _default_protected_class_conflict_predicate
        )
        self._dimension_reader = dimension_reader or self._read_dimension
        self._load_lock = Lock()
        self._warned = False

    def _read_dimension(self) -> list[str]:
        """Read distinct governed city values. Raises on any dependency failure."""

        from backend.services.databricks_sql import get_sql_client
        from backend.services.databricks_sql_helpers import qualify

        rows = get_sql_client().execute(
            "SELECT DISTINCT city "
            f"FROM {qualify('gold', 'borrower_360')} "
            "WHERE city IS NOT NULL AND TRIM(city) <> '' "
            "ORDER BY city ASC"
        )
        return [str(row.get("city") or "") for row in (rows or [])[:_MAX_DIMENSION_ROWS]]

    def _load(self) -> ResolvedPlaceDimension:
        values = self._dimension_reader()
        # US states join the FAIR-LENDING candidate pool (and only that pool).
        # They are a closed federal list, not a coverage footprint, so naming
        # them pins no geography -- and they hit the same -oma morphology the
        # city dimension does: every question naming Oklahoma refused, 28 of
        # them, plus "Oklahoma City" and "Oklahoma County" (measured
        # 2026-08-12). They earn their exemption through the same admission
        # gate as a city; nothing is exempt because it is on this list.
        from backend.schemas._validators_person_names import US_STATE_NAMES

        protected_candidates = [*values, *US_STATE_NAMES]
        conflicting = {
            normalize_place_value(value)
            for value in values
            if value.strip() and self._conflict_predicate(value)
        }
        if len(conflicting) > _MAX_CONFLICTING_VALUES:
            # Not a city dimension, or the detectors changed shape. Exempting
            # hundreds of values on that evidence would be a guard rewrite by
            # accident, so exempt none and say so.
            emit(
                log,
                "genie_place_dimension_rejected",
                level=logging.WARNING,
                outcome="degraded",
                dimension_values=len(values),
                conflicting_values=len(conflicting),
                max_conflicting_values=_MAX_CONFLICTING_VALUES,
            )
            return _EMPTY_DIMENSION
        # One pass. The predicate runs the name-shape scan over two renderings
        # per value, so evaluating it twice (once for the set, once for the
        # excluded count) doubled the load cost for nothing.
        name_shape_safe: set[str] = set()
        excluded = 0
        for value in values:
            if not value.strip() or not self._name_shape_conflict_predicate(value):
                continue
            if _collides_with_person_lexicon(value):
                excluded += 1
                continue
            name_shape_safe.add(" ".join(value.split()))
        if len(name_shape_safe) > _MAX_NAME_SHAPE_VALUES:
            emit(
                log,
                "genie_place_dimension_name_shape_rejected",
                level=logging.WARNING,
                outcome="degraded",
                dimension_values=len(values),
                name_shape_values=len(name_shape_safe),
                max_name_shape_values=_MAX_NAME_SHAPE_VALUES,
            )
            name_shape_safe = set()
        protected_class_safe: set[str] = set()
        canary_excluded = 0
        for value in protected_candidates:
            if not value.strip() or not self._protected_class_conflict_predicate(value):
                continue
            if _disarms_a_protected_class_canary(value):
                canary_excluded += 1
                continue
            protected_class_safe.add(" ".join(value.split()))
        if len(protected_class_safe) > _MAX_PROTECTED_CLASS_VALUES:
            emit(
                log,
                "genie_place_dimension_protected_class_rejected",
                level=logging.WARNING,
                outcome="degraded",
                dimension_values=len(values),
                protected_class_values=len(protected_class_safe),
                max_protected_class_values=_MAX_PROTECTED_CLASS_VALUES,
            )
            protected_class_safe = set()
        emit(
            log,
            "genie_place_dimension_loaded",
            level=logging.INFO,
            dimension_values=len(values),
            conflicting_values=len(conflicting),
            name_shape_values=len(name_shape_safe),
            person_lexicon_excluded=excluded,
            protected_class_values=len(protected_class_safe),
            protected_class_canary_excluded=canary_excluded,
        )
        return ResolvedPlaceDimension(
            conflicting=frozenset(conflicting),
            name_shape_safe=frozenset(name_shape_safe),
            protected_class_safe=frozenset(protected_class_safe),
            all_values=frozenset(" ".join(value.split()) for value in values if value.strip()),
        )

    def _resolve(self) -> ResolvedPlaceDimension:
        """Load-or-degrade the whole dimension. Never raises."""

        cached: ResolvedPlaceDimension | None = self._cache.get(_PLACE_DIMENSION_CACHE_KEY)
        if cached is not None:
            return cached
        with self._load_lock:
            cached = self._cache.get(_PLACE_DIMENSION_CACHE_KEY)
            if cached is not None:
                return cached
            try:
                loaded = self._load()
            except Exception as exc:  # noqa: BLE001 — governed output must not 500
                if not self._warned:
                    emit(
                        log,
                        "genie_place_dimension_unavailable",
                        level=logging.WARNING,
                        dependency="warehouse",
                        outcome="degraded",
                        exc_type=type(exc).__name__,
                        exc_msg=str(exc)[:500],
                        posture="last_known_good_or_no_exemption",
                    )
                    self._warned = True
                stale = self._cache.get_stale(_PLACE_DIMENSION_CACHE_KEY)
                degraded = stale if stale is not None else _EMPTY_DIMENSION
                self._cache.set(
                    _PLACE_DIMENSION_CACHE_KEY, degraded, _PLACE_DIMENSION_FAILURE_TTL_S
                )
                return degraded
            self._warned = False
            self._cache.set(_PLACE_DIMENSION_CACHE_KEY, loaded, self._ttl_s)
            return loaded

    def conflicting_values(self) -> frozenset[str]:
        """Normalized governed city values the structured-cell scan rejects.

        Never raises. Returns an empty set when the dimension is unreachable
        and no last-known-good value is cached.
        """

        return self._resolve().conflicting

    def name_shape_safe_values(self) -> frozenset[str]:
        """Governed city values the PROSE person-name heuristic misreads.

        Whitespace-collapsed, in the stored casing; the consumer matches
        case-insensitively on whole tokens. Excludes any value sharing a token
        with the person-name lexicons. Same fail-closed degradation as
        ``conflicting_values``: unreachable dimension means exempt nothing.
        """

        return self._resolve().name_shape_safe

    def protected_class_safe_values(self) -> frozenset[str]:
        """Governed city values the PROSE fair-lending scan misreads.

        Whitespace-collapsed, in the stored casing; the consumer matches
        case-insensitively on whole tokens. Every value has been proven unable
        to disarm a must-block fair-lending probe (see
        :func:`_disarms_a_protected_class_canary`). Same fail-closed
        degradation as the other two sets: unreachable dimension means exempt
        nothing, so the answer is withheld rather than widened.
        """

        return self._resolve().protected_class_safe

    def known_place_values(self) -> frozenset[str]:
        """Every governed city value — recognition, never exemption.

        Consumed only by the sentence-initial strip, which uses it to decide
        that a capitalized opening word is grammar. No detector stops scanning
        anything because a value appears here, so this set needs no admission
        gate. Degrades to empty like the rest: no places, no strip.
        """

        return self._resolve().all_values

    def invalidate(self) -> None:
        """Drop the cached dimension so the next call re-reads gold."""

        self._cache.invalidate(_PLACE_DIMENSION_CACHE_KEY)
        self._warned = False


_RESOLVER: GovernedPlaceDimensionResolver | None = None
_RESOLVER_LOCK = Lock()


def get_governed_place_dimension() -> GovernedPlaceDimensionResolver:
    global _RESOLVER
    if _RESOLVER is not None:
        return _RESOLVER
    with _RESOLVER_LOCK:
        if _RESOLVER is None:
            _RESOLVER = GovernedPlaceDimensionResolver()
        return _RESOLVER


def warm_governed_place_dimension() -> None:
    """Resolve the dimension at startup so no Genie turn pays for it inline.

    One DISTINCT over a low-cardinality gold column plus the detector probes,
    ~4s against the 428 live values. Without this it lands on whichever Ask
    Genie turn happens to follow a TTL expiry -- the surface least able to
    absorb it. Called from ``backend.main``'s lifespan alongside the warehouse
    and Lakebase warms.

    Log-and-continue, like every other warm hook: the resolver already degrades
    fail-closed, so a failed warm costs governed city names in one answer
    rather than a boot refusal.
    """

    from time import monotonic

    start = monotonic()
    try:
        resolver = get_governed_place_dimension()
        emit(
            log,
            "place_dimension_warm_succeeded",
            dependency="warehouse",
            outcome="success",
            duration_ms=int((monotonic() - start) * 1000),
            conflicting_values=len(resolver.conflicting_values()),
            name_shape_values=len(resolver.name_shape_safe_values()),
            protected_class_values=len(resolver.protected_class_safe_values()),
        )
    except Exception as exc:  # noqa: BLE001 -- log-and-continue is the contract
        emit(
            log,
            "place_dimension_warm_failed",
            level=logging.WARNING,
            dependency="warehouse",
            outcome="error",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )


def _reset_governed_place_dimension_for_tests(
    resolver: GovernedPlaceDimensionResolver | None = None,
) -> None:
    """Test helper: swap (or clear) the process-wide resolver."""

    global _RESOLVER
    with _RESOLVER_LOCK:
        _RESOLVER = resolver
