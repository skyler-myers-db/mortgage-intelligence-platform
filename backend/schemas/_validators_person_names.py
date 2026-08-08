"""Human-name-shape detection for governed free-text boundaries.

Borrower names never ship; display identities are synthetic masked IDs. These
detectors keep name-shaped text out of model-authored and operator-authored
prose without treating governed product, geography, or segment phrases as
identities.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from backend.schemas._validators_tenant import configured_public_lender_name

_TITLECASE_HUMAN_NAME_RE = re.compile(
    r"\b[A-Z][a-z]{1,30}(?:\s+|\s*\|\s*)(?:[A-Z](?:\s+|\s*\|\s*))?" r"[A-Z][a-z]{1,30}\b"
)
_LEADING_ANALYTICS_COMMAND_RE = re.compile(
    # Analytics commands plus operator-note verbs ("Discussed Home Equity
    # options", "Approved El Paso wave"): a capitalized verb pairing with the
    # next capitalized word is sentence structure, not a name. Stripping the
    # verb never hides a real name — the name pair itself still scans.
    r"\b(?:Compare|Explain|Find|List|Open|Prioritize|Rank|Review|Show|Target|"
    r"Approved|Called|Confirmed|Contacted|Discussed|Emailed|Noted|Paused|"
    r"Rejected|Reviewed|Scheduled|Sent|Shared|Spoke|Updated)\s+(?=[A-Z])"
)
# Title-case pairs ending in these words are never person names here:
# admin/geographic place-name suffixes (Lake Forest, Grand Prairie, Coral
# Springs — city strings are sanctioned analytics output and borrower rows
# carry the same values), governed mortgage-product phrases ("Purchase
# Mortgage", "Cash-Out Refinance"), and governed segment display labels
# ("Prime Refi Candidates", "Home Equity Candidate", "Retention Risk").
# Borrower names never ship; display identities are synthetic masked IDs.
_NON_PERSON_TITLECASE_SUFFIXES = frozenset(
    {
        # fmt: off
        "borough", "city", "county", "metro", "msa", "parish", "region", "township",
        "arbor", "bay", "beach", "bluffs", "canyon", "creek", "falls", "forest",
        "gardens", "grove", "harbor", "heights", "hills", "island", "junction",
        "lake", "lakes", "meadows", "mesa", "oaks", "park", "pines", "plains",
        "point", "prairie", "rapids", "ridge", "shores", "springs", "station",
        "valley", "village", "vista", "woods",
        "equity", "heloc", "loan", "mortgage", "offer", "queue", "refi",
        "refinance", "review",
        "candidate", "candidates", "intent", "risk", "sale", "segment", "segments",
        # fmt: on
    }
)
# Mirror rule for the FIRST word of a title-case pair: toponym formants — the
# Spanish articles and geographic feature nouns US place names are built from
# (El Paso, Fort Worth, San Antonio, Corpus Christi, Baton Rouge, Round Rock).
# This is a closed grammatical class with zero overlap with human first names,
# so exempting it cannot admit a real person (2026-08-07 cross-surface audit:
# these cities were refused on campaign copy, notes, and rationale fields).
_NON_PERSON_TITLECASE_PREFIXES = frozenset(
    {
        # fmt: off
        "baton", "boca", "cape", "castle", "cedar", "coral", "corpus", "council",
        "del", "des", "eagle", "east", "el", "fort", "grand", "lake", "las",
        "little", "long", "los", "mount", "new", "north", "port", "round",
        "saint", "san", "santa", "sioux", "south", "st", "terre", "west",
        # fmt: on
    }
)


def titlecase_pair_is_non_person(pair_text: str) -> bool:
    """True when a title-case pair is governed geography/product, not a name.

    A pair is non-person when its last word is an admin/geographic/product
    suffix (Lake Forest, Home Equity) or its first word is a toponym formant
    (El Paso, Fort Worth). Shared so every surface classifies identically.
    """

    tokens = [token for token in re.split(r"\s+|\|", pair_text.strip()) if token]
    if not tokens:
        return False
    return (
        tokens[-1].casefold() in _NON_PERSON_TITLECASE_SUFFIXES
        or tokens[0].casefold() in _NON_PERSON_TITLECASE_PREFIXES
    )
_COMMON_FIRST_NAMES = frozenset(
    {
        "alice",
        "barbara",
        "david",
        "elizabeth",
        "james",
        "jane",
        "jennifer",
        "john",
        "joseph",
        "linda",
        "maria",
        "mary",
        "michael",
        "patricia",
        "richard",
        "robert",
        "sarah",
        "thomas",
        "william",
    }
)
_COMMON_LAST_NAMES = frozenset(
    {
        "anderson",
        "brown",
        "davis",
        "doe",
        "garcia",
        "gonzalez",
        "hernandez",
        "johnson",
        "jones",
        "lee",
        "lopez",
        "martinez",
        "miller",
        "moore",
        "rodriguez",
        "smith",
        "taylor",
        "thomas",
        "williams",
        "wilson",
    }
)

_REVIEWED_NON_PERSON_PHRASES: tuple[str, ...] = (
    "Mortgage Intelligence Platform",
    "Mortgage Growth Agent",
    "Daily Refi Opportunity Brief",
    "Listed-for-Sale Purchase Watch",
    "Competitor Recapture Monitor",
    "High-Equity HELOC Watch",
    "Borrower Dossier Review",
    "Call consent",
    "Branch Manager Capacity Review",
    "Custom Segment Workflow",
    "Source Freshness Sentinel",
    "Building Permits",
    "Equal Housing Lender",
    "Equal Housing",
    "Offer Orchestrator",
    "Portfolio Builder",
    "Genie Conversation",
    "Databricks Genie",
    "Databricks Agent Responses",
    "Unity Catalog",
    "Supervisor Agent",
    "Growth Agent",
    "Lead Queue",
    "Borrower Dossier",
    "Mosaic AI",
    "Agent Bricks",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "West Virginia",
    "United States",
)

_CONTEXTUAL_HUMAN_NAME_RE = re.compile(
    r"\b(?:call|contact|email|message|text|ask|target|prioritize|dear|hello|hi)\s+"
    # Prepositions after the verb ("target for reverse mortgages", "call with
    # an offer") are sentence structure, never a person's first name.
    r"(?!(?:for|with|on|in|by|from|into|over|under|near|toward|towards|across|during|after|against|without|"
    r"to|the|a|an|and|or|at|about|before|if|when|this|that|these|those|your|our|us|me|you|"
    r"them|him|her|it|then|provider|carrier|system|platform|service|gateway|authorization|consent|permission|outreach|contact|records?|"
    # Domain population/ranking vocabulary: "prioritize overall", "contact
    # borrowers", "target top segments" are core product phrasings, not
    # person-name lookups. Real names never take these words.
    r"all|any|each|every|only|both|overall|first|next|now|today|top|"
    r"borrowers?|leads?|candidates?|prospects?|customers?|clients?|"
    r"segments?|cohorts?|homeowners?|investors?|people|everyone|anyone|someone|"
    r"is|are|was|were|will|would|can|could|may|might|has|have|had)\b)"
    r"[A-Za-z]{2,30}\s+[A-Za-z]{2,30}\b|"
    # "X Y qualifies" catches case-normalized names ("john smith qualifies").
    # Quantifier/population pairs ("each one qualifies", "which borrower
    # qualifies") are grammar, not identities.
    r"\b(?!(?:each|every|any|no|which|that|this|the|one)\b)[A-Za-z]{2,30}\s+"
    r"(?!(?:one|ones|borrower|borrowers|candidate|candidates|customer|customers|lead|leads)\b)"
    r"[A-Za-z]{2,30}\s+(?:qualifies?|is the top borrower)\b",
    re.IGNORECASE,
)


def contains_human_name_shape(
    value: str,
    *,
    allowed_phrases: Sequence[str] = (),
    include_titlecase: bool = True,
) -> bool:
    """Detect title-case names and reviewed common lowercase first/last pairs.

    General two-word lowercase prose is not treated as an identity. The common
    pair vocabulary closes the audited ``john smith`` class without turning
    ordinary mortgage phrases into false positives.
    """

    text = _remove_reviewed_non_person_phrases(str(value), allowed_phrases=allowed_phrases)
    text = _LEADING_ANALYTICS_COMMAND_RE.sub(" ", text)
    if include_titlecase and any(
        not titlecase_pair_is_non_person(match.group(0))
        for match in _TITLECASE_HUMAN_NAME_RE.finditer(text)
    ):
        return True
    if any(
        not _contextual_match_targets_non_person(match)
        for match in _CONTEXTUAL_HUMAN_NAME_RE.finditer(text)
    ):
        return True
    words = re.findall(r"[A-Za-z]{2,30}", text.casefold())
    return any(
        first in _COMMON_FIRST_NAMES and last in _COMMON_LAST_NAMES
        for first, last in zip(words, words[1:], strict=False)
    )


def _remove_reviewed_non_person_phrases(
    value: str,
    *,
    allowed_phrases: Sequence[str],
) -> str:
    phrases = {
        *_REVIEWED_NON_PERSON_PHRASES,
        configured_public_lender_name(),
        *allowed_phrases,
    }
    cleaned = value
    for phrase in sorted((item.strip() for item in phrases if item.strip()), key=len, reverse=True):
        cleaned = re.sub(re.escape(phrase), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


_CONTEXTUAL_TRIGGER_VERBS = frozenset(
    {
        "call",
        "contact",
        "email",
        "message",
        "text",
        "ask",
        "target",
        "prioritize",
        "dear",
        "hello",
        "hi",
    }
)


def _contextual_match_targets_non_person(match: re.Match[str]) -> bool:
    """True when a contextual-verb hit targets governed geography/product.

    "Prioritize Purchase Mortgage leads" and "contact Fort Worth homeowners"
    are product phrasing, not a person being addressed; "call john smith"
    stays a hit because the pair is not a governed non-person pair.
    """

    words = [word for word in re.split(r"\s+", match.group(0).strip()) if word]
    if not words:
        return False
    pair = words[1:3] if words[0].casefold() in _CONTEXTUAL_TRIGGER_VERBS else words[:2]
    return titlecase_pair_is_non_person(" ".join(pair))


def contains_contextual_human_name(value: str) -> bool:
    """Detect name-shaped text in contexts where a person is being addressed.

    General lowercase two-word prose is intentionally not classified as a
    name. The governed free-text boundaries use this alongside mechanical PII
    checks and title-case detection to catch case-normalized names such as
    ``call john smith`` without treating ordinary sentences as identities.
    """

    return any(
        not _contextual_match_targets_non_person(match)
        for match in _CONTEXTUAL_HUMAN_NAME_RE.finditer(str(value))
    )
