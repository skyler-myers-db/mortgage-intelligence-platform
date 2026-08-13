"""Closed reviewed workflow and server-rendered copy grammars.

Clauses the audience-criterion machine recognizes as reviewed Module 0
product workflows or server-owned campaign copy, each checked by fullmatch
BEFORE its fail-closed nets. Every grammar here is self-contained -- no
population or attribute fragment reaches in -- which is what makes this a
clean seam: split out of ``marketing_selection_criteria`` 2026-08-13 when
the #225 destination-tail fix pushed that module over the 900-line
file-size gate (899 at base). Pure move, verdict-identical under the
1,482-probe battery; the consent-reroute grammar stayed behind because it
embeds the coreference population fragment.
"""

from __future__ import annotations

import re

_REVIEWED_NON_POPULATION_STRATEGY_RE = re.compile(
    # This grammar allocates aggregate outreach capacity; it does not select
    # people or define an audience predicate. Keep both the allocatable object
    # and every grouping dimension closed so a health criterion cannot hide in
    # a canonical strategy-board sentence.
    r"^(?:(?:prioritize|allocate|sequence|rank|order)\s+(?:the\s+)?(?:next\s+)?"
    r"(?:[0-9]{1,9}\s+)?(?:outreach\s+)?(?:touches|capacity|budget)\s+"
    r"(?:by|across)\s+"
    r"(?:states?|count(?:y|ies)|markets?|metros?|segments?|offer\s+lanes?)"
    r"(?:\s*,\s*(?:states?|count(?:y|ies)|markets?|metros?|segments?|"
    r"offer\s+lanes?))*"
    r"(?:\s*,?\s+and\s+(?:states?|count(?:y|ies)|markets?|metros?|segments?|"
    r"offer\s+lanes?))?|"
    # Internal queue ownership is an operating instruction, not an audience
    # criterion. Keep the reviewed object and terminal role closed.
    r"(?:review|inspect|confirm)\s+(?:the\s+)?"
    r"(?:priority|queue|workload|capacity|ownership)\s+distribution\s+and\s+"
    r"(?:assign|confirm)\s+(?:the\s+)?next\s+"
    r"(?:owner|operator|reviewer))$",
    re.IGNORECASE,
)
_REVIEWED_SCREEN_GATE_WORKFLOW_RE = re.compile(
    r"^(?:compose\s+(?:a\s+)?(?:reviewed\s+)?(?:growth\s+)?plan\s+to\s+)?"
    r"screen\s+(?:prime\s+)?(?:refi|refinance|mortgage)\s+economics"
    r"(?:\s*,\s*|\s+)(?:then\s+)?gate\s+to\s+eligible(?:\s+leads?)?"
    r"(?:\s*,?\s*(?:and|then)\s+(?:"
    r"prepare\s+(?:a\s+)?lead\s+queue\s+handoff\s+for\s+review|"
    r"hand\s+off\s+for\s+review))?$",
    re.IGNORECASE,
)
_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION = (
    r"(?:borrowers\s+whose\s+current\s+property\s+and\s+listing\s+signals\s+support\s+"
    r"a\s+next[ -]?home\s+conversation|"
    r"borrowers\s+with\s+refinance\s+economics\s+and\s+usable\s+home\s+equity|"
    r"borrowers\s+whose\s+equity\s+and\s+heloc\s+propensity\s+support\s+an\s+"
    r"equity[ -]?access\s+review|"
    r"borrowers\s+whose\s+current\s+lien\s+economics\s+support\s+a\s+refinance\s+review|"
    r"borrowers\s+with\s+substantial\s+modeled\s+equity\s+for\s+a\s+cash[ -]?out\s+review|"
    r"multi[ -]?property\s+borrowers\s+whose\s+financing\s+needs\s+may\s+span\s+more\s+"
    r"than\s+one\s+property|"
    r"existing\s+or\s+former\s+customers\s+with\s+a\s+timely\s+retention\s+review\s+signal|"
    r"borrowers\s+who\s+should\s+receive\s+education\s+rather\s+than\s+a\s+"
    r"product[ -]?specific\s+claim)"
)
_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION_RE = re.compile(
    rf"^{_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION}$",
    re.IGNORECASE,
)
_REVIEWED_CAMPAIGN_AUDIENCE_SUMMARY_RE = re.compile(
    rf"^the\s+selected\s+audience\s+is\s+led\s+by\s+"
    rf"{_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION}\s+and\s+is\s+ready\s+for\s+a\s+"
    r"controlled\s+message\s+test\.?$",
    re.IGNORECASE,
)
_REVIEWED_NAMED_LENDER_RATE_QUERY_RE = re.compile(
    r"^(?:[Ll]ist|[Ss]how|[Cc]ount)\s+(?:[A-Z][A-Za-z0-9&.'-]*\s+){1,3}"
    r"(?:borrowers?|customers?|homeowners?|applicants?)\s+whose\s+"
    r"(?:mortgage\s+)?(?:rate|payment|ltv|loan[- ]to[- ]value|equity)\s+"
    r"(?:is\s+)?(?:above|below|over|under|at\s+least|no\s+more\s+than)\s+"
    r"[0-9olieast]{1,3}(?:\.[0-9olieast]{1,3})?"
    r"(?:\s*(?:%|percent|bps?|basis\s+points?))?$",
)


def is_reviewed_campaign_audience_summary_text(value: str) -> bool:
    """Return true only for the closed server-rendered offer-audience summary."""

    return _REVIEWED_CAMPAIGN_AUDIENCE_SUMMARY_RE.fullmatch(str(value).strip()) is not None


def is_reviewed_campaign_audience_description_text(value: str) -> bool:
    """Return true only for one closed server-owned offer-audience description."""

    return _REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION_RE.fullmatch(str(value).strip()) is not None
