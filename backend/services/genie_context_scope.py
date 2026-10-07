"""Render a closed Genie context into a reviewed, battery-tested scoped question.

Audit 2026-09-21 genie-04, phase 2. The browser sends a :class:`GenieContext`
(codes only) and gets back either a sentence for the Genie composer or the
closed reason none is offered. The sentence is a PREFILL: the user reads it,
may edit it, and presses Ask, so the scope rides inside the question text and
therefore inside the prompt of record, its ``question_hash`` and the
``RUN_GENIE`` audit row. Nothing here changes the submit path.

Pure by construction: no Lakebase, no warehouse query, no Genie call and no
audit write. The guards it runs read the governed place dimension the startup
warm already resolved (the same read every submit makes), and the city slot
reads only the set that warm PUBLISHED: when it is empty the slot is withheld
as ``place_unavailable`` rather than reaching for live coverage.

Fail-closed twice over. Every vocabulary is closed (segment names from the
registry, federal state names, shipped county names screened by the governed
place gate, the governed city set, ``ZIP code NNNNN``, ``Competitor X``), and
every rendering then runs the exact deterministic matcher set the phase-1
template battery runs (``tests/unit/test_genie_context_templates.py``), the
Sales Ops router and the request model before it is returned. A hit withholds
the prompt as ``refused`` and emits one event naming the matcher and the slot
NAMES, never the prompt and never a slot value. The same battery runs again
at submit, unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Final, Literal

from pydantic import ValidationError

from backend.schemas.genie_context import (
    STATE_FIPS_TO_USPS,
    GenieContext,
    GenieContextScopeResponse,
    GenieContextWithheld,
)
from backend.schemas.marketing_selection_reviewed_places import (
    COUNTY_NAME_EXCLUSIONS,
    US_STATE_CODES,
    US_STATE_NAMES_FEDERAL,
    governed_analytics_cities,
    is_governed_analytics_location,
)
from backend.services.county_names import county_name_for_fips
from backend.services.genie_deterministic import _is_outreach_writer_request
from backend.services.genie_message_policy import (
    GenieMessageRequest,
    identity_prompt_match,
    protected_prompt_match,
)
from backend.services.genie_prompt_guardrails import (
    cross_lender_prompt_match,
    instruction_override_prompt_match,
    off_topic_prompt_match,
    pii_prompt_match,
    scope_bypass_prompt_match,
    source_gap_prompt_match,
)
from backend.services.genie_sales_ops import _sales_ops_question_kind
from backend.services.observability import emit

log = logging.getLogger(__name__)

#: Segment code -> the lower-cased registry name a template interpolates (the
#: phase-1 ``genieContext.segmentPhrase`` rule: title case reads as a person
#: name to the prose heuristic). Parity-tested against ``segmentMetadata.ts``.
SEGMENT_PHRASES: Final[Mapping[str, str]] = {
    "itm": "prime refi candidates",
    "listed": "listed for sale",
    "permit": "heloc intent",
    "investor": "investor / multi-property",
    "equity": "home equity candidate",
    "retention": "retention risk",
    "second_lien_itm": "second-lien consolidation",
    "heloc_draw_to_payback": "heloc draw ending",
    "home_equity_history": "home equity history",
    "refi_propensity": "refi propensity",
    "itm_on_related_property": "itm on related property",
    "payoff_loss_leads": "payoff loss",
}

#: Parity with ``genieContextTemplates.json`` ``segmentsWithoutTemplates``: the
#: source-gap guard refuses every question about the permit-activity overlay.
SEGMENTS_WITHOUT_TEMPLATES: Final[frozenset[str]] = frozenset({"permit_activity"})

#: Competitor refs the battery refuses, with the reason each is withheld. Empty
#: today: all 27 public refs pass in every rendering the battery covers.
LENDER_REF_EXCLUSIONS: Final[Mapping[str, str]] = {}

#: County FIPS codes whose rendering the battery refuses, with the reason each
#: is withheld ('excluded_value'). Found by tests/unit/test_genie_context_model.py
#: over every shipped county; the same test fails once a guard accepts one.
#: These sit on top of the place gate's own ``COUNTY_NAME_EXCLUSIONS``.
COUNTY_FIPS_EXCLUSIONS: Final[Mapping[str, str]] = {
    "20017": "Chase County, KS: the cross-lender guard reads Chase as a lender name.",
    "24033": "Prince George's County, MD: the identity guard reads the possessive as a person.",
    "24035": "Queen Anne's County, MD: the identity guard reads the possessive as a person.",
    "31029": "Chase County, NE: the cross-lender guard reads Chase as a lender name.",
    "35013": "Dona Ana County, NM: the n-tilde is outside the governed place shape.",
}

STATE_NAME_BY_CODE: Final[Mapping[str, str]] = dict(
    zip(US_STATE_CODES, US_STATE_NAMES_FEDERAL, strict=True)
)

#: The closed template set. ``{scope}`` is ``in <place>`` or the coverage
#: phrase the shipped starters use; ``{segments}`` is one or two
#: ``the <name> segment`` phrases joined by ``and`` (all) or ``or`` (any).
COVERAGE_SCOPE: Final = "across the current Cotality data coverage"
SEGMENT_TEMPLATE: Final = "How many borrowers {scope} are in {segments}, and what is the average rate spread?"
PLACE_TEMPLATE: Final = "How many borrowers are in {place}, and what is the average rate spread?"
LENDER_SEGMENT_TEMPLATE: Final = (
    "How many borrowers {scope} with {lender} as the current lender are in {segments}, "
    "and what is the average rate spread?"
)
LENDER_TEMPLATE: Final = (
    "How many borrowers {scope} have {lender} as the current lender, "
    "and what is the average rate spread?"
)

#: Exactly ``test_genie_context_templates._PROMPT_MATCHERS`` (parity-tested).
GENIE_CONTEXT_PROMPT_MATCHERS: Final[tuple[tuple[str, Callable[[str], object]], ...]] = (
    ("protected", protected_prompt_match),
    ("identity", identity_prompt_match),
    ("pii", pii_prompt_match),
    ("off_topic", off_topic_prompt_match),
    ("scope_bypass", scope_bypass_prompt_match),
    ("instruction_override", instruction_override_prompt_match),
    ("cross_lender", cross_lender_prompt_match),
    ("source_gap", source_gap_prompt_match),
    ("outreach_writer", _is_outreach_writer_request),
)


class _Withheld(Exception):
    def __init__(self, reason: GenieContextWithheld) -> None:
        super().__init__(reason)
        self.reason: GenieContextWithheld = reason


def _county_phrase(county_fips: str) -> str:
    name = county_name_for_fips(county_fips)
    state = STATE_FIPS_TO_USPS.get(county_fips[:2])
    if name is None or state is None:
        raise _Withheld("place_unavailable")
    if county_fips in COUNTY_FIPS_EXCLUSIONS or " ".join(name.lower().split()) in COUNTY_NAME_EXCLUSIONS:
        raise _Withheld("excluded_value")
    # Periods stripped: the reviewed-analytics text splits clauses on '.', so
    # "St. Louis County" would arrive as two clauses (see admitted_county_names).
    bare = " ".join(name.replace(".", " ").split())
    county = bare if bare.lower().endswith(" county") else f"{bare} County"
    return f"{county}, {state}"


def _city_phrase(city_key: str) -> str:
    if not governed_analytics_cities():
        raise _Withheld("place_unavailable")
    city, state = city_key.rsplit("~", 1)
    phrase = f"{' '.join(city.split()).title()}, {state}"
    if not is_governed_analytics_location(phrase):
        raise _Withheld("place_unavailable")
    return phrase


def scope_place_phrase(ctx: GenieContext) -> str | None:
    """The narrowest place the context names, as reviewed vocabulary, or None."""

    if ctx.county_fips is not None:
        phrase = _county_phrase(ctx.county_fips)
    elif ctx.city is not None:
        phrase = _city_phrase(ctx.city)
    elif ctx.zip is not None:
        return f"ZIP code {ctx.zip}"
    elif ctx.state is not None:
        phrase = STATE_NAME_BY_CODE[ctx.state]
    else:
        return None
    if not is_governed_analytics_location(phrase):
        raise _Withheld("excluded_value")
    return phrase


def _segments_phrase(ctx: GenieContext) -> str | None:
    if not ctx.segment_codes:
        return None
    if any(code in SEGMENTS_WITHOUT_TEMPLATES for code in ctx.segment_codes):
        raise _Withheld("excluded_value")
    phrases = [f"the {SEGMENT_PHRASES[code]} segment" for code in ctx.segment_codes]
    joiner = " and " if ctx.segment_mode == "all" else " or "
    return joiner.join(phrases)


def compose_genie_context_prompt(ctx: GenieContext) -> str:
    """Fill the closed template for ``ctx`` (raises ``_Withheld``; no battery)."""

    place = scope_place_phrase(ctx)
    segments = _segments_phrase(ctx)
    lender = ctx.lender_ref
    if lender is not None and lender in LENDER_REF_EXCLUSIONS:
        raise _Withheld("excluded_value")
    scope = f"in {place}" if place is not None else COVERAGE_SCOPE
    if lender is not None:
        if segments is not None:
            return LENDER_SEGMENT_TEMPLATE.format(scope=scope, lender=lender, segments=segments)
        return LENDER_TEMPLATE.format(scope=scope, lender=lender)
    if segments is not None:
        return SEGMENT_TEMPLATE.format(scope=scope, segments=segments)
    if place is not None:
        return PLACE_TEMPLATE.format(place=place)
    raise _Withheld("unsupported_combination")


def genie_context_battery_hit(prompt: str) -> str | None:
    """The first guard that intercepts ``prompt``, by name, or None."""

    for name, matcher in GENIE_CONTEXT_PROMPT_MATCHERS:
        if matcher(prompt):
            return name
    if _sales_ops_question_kind(prompt) is not None:
        return "sales_ops"
    try:
        GenieMessageRequest(question=prompt)
    except ValidationError:
        return "request_model"
    return None


def _filled_slots(ctx: GenieContext) -> list[str]:
    slots = {
        "segment_codes": bool(ctx.segment_codes),
        "state": ctx.state is not None,
        "county_fips": ctx.county_fips is not None,
        "zip": ctx.zip is not None,
        "city": ctx.city is not None,
        "lender_ref": ctx.lender_ref is not None,
        "assignee": ctx.assignee is not None,
    }
    return [name for name, filled in slots.items() if filled]


def _withheld(
    ctx: GenieContext,
    reason: GenieContextWithheld,
    omitted: list[Literal["assignee"]],
    *,
    matcher: str | None = None,
) -> GenieContextScopeResponse:
    # Slot NAMES and the matcher name only: never the prompt, never a value.
    if matcher is None:
        emit(
            log,
            "genie_context_scope_withheld",
            outcome="withheld",
            reason=reason,
            slots=_filled_slots(ctx),
        )
    else:
        emit(
            log,
            "genie_context_scope_withheld",
            level=logging.WARNING,
            outcome="withheld",
            reason=reason,
            slots=_filled_slots(ctx),
            matcher=matcher,
        )
    return GenieContextScopeResponse(prompt=None, withheld=reason, omitted=omitted)


def render_genie_context_scope(ctx: GenieContext) -> GenieContextScopeResponse:
    """Render ``ctx`` into a guard-clean question, or say why none is offered."""

    # Genie's governed space holds no assignments, and a sales-ops-shaped
    # prefill is role-gated: an assignee filter is never rendered, only named.
    omitted: list[Literal["assignee"]] = ["assignee"] if ctx.assignee is not None else []
    try:
        prompt = compose_genie_context_prompt(ctx)
    except _Withheld as exc:
        return _withheld(ctx, exc.reason, omitted)
    hit = genie_context_battery_hit(prompt)
    if hit is not None:
        return _withheld(ctx, "refused", omitted, matcher=hit)
    return GenieContextScopeResponse(prompt=prompt, withheld=None, omitted=omitted)
