"""Human-name-shape detection for governed free-text boundaries.

Borrower names never ship; display identities are synthetic masked IDs. These
detectors keep name-shaped text out of model-authored and operator-authored
prose without treating governed product, geography, or segment phrases as
identities.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

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
# Closed English function-word classes, capitalized only because they open a
# sentence. "Which Washington cities have the most in-the-money borrowers?"
# reads as the person "Which Washington" to the title-case pair heuristic, and
# it is not a narrow case: EVERY one of 39 sentence-initial words probed on
# 2026-08-12 paired with a following proper noun, refusing 289 of the 428
# governed ``mip.gold.borrower_360`` city values under "Which {City}
# borrowers ...".
#
# This bank alone is NOT sufficient authority to strip. An adversarial review
# on 2026-08-12 broke the earlier unconditional version: ``Do`` and ``An`` are
# ordinary family and given names, so "Do Nguyen qualifies for a HELOC?" had
# its first token eaten and the lone surname could not pair -- a PII regression
# against a portfolio whose live gold carries WESTMINSTER, GARDEN GROVE and
# SANTA ANA. The docstring's claim that "the following pair still scans" holds
# only for names of three tokens or more, and every word in a bank this size is
# some two-token name's first half.
#
# So the strip is gated on BOTH sides: a function word from this bank AND a
# following token run that is a KNOWN PLACE (see
# :func:`_sentence_initial_place_strip_pattern`).
#
# The place half is necessary but ALSO not sufficient, which a second review
# pass on 2026-08-12 proved: 229 of the live gold city values are single tokens
# that are ordinary family names (MEDINA, PARKER, KENT, CARSON, PRESTON,
# MILTON, ELIZABETH), so "Do Medina qualifies for a HELOC?" lost its pair --
# 464 of 468 place terms flipped that way. The place vocabulary cannot fix
# that: it is legitimately a place AND legitimately a surname.
#
# What fixes it is this bank excluding every word attested in FIRST name
# position. ``Do``, ``An`` and ``No`` are surname-first Vietnamese and Korean
# family names, which is exactly the position the strip consumes; ``Will`` and
# ``May`` are given names. Prepositions like ``In``, ``To`` and ``By`` stay:
# they are attested only in last position ("Medina In"), which the strip never
# touches. ``test_genie_prompt_guard_geography`` pins the exclusion.
_SENTENCE_INITIAL_FUNCTION_WORDS: tuple[str, ...] = (
    # fmt: off
    # interrogatives
    "Which", "What", "Who", "Whom", "Whose", "Where", "When", "Why", "How",
    # auxiliaries and modals ("Do" is excluded -- see the name-collision note)
    "Does", "Did", "Is", "Are", "Was", "Were", "Am", "Be", "Been",
    "Being", "Has", "Have", "Had", "Can", "Could", "Should", "Would", "Shall",
    "Must", "Might",
    # determiners and quantifiers ("An" and "No" excluded, same reason)
    "The", "Any", "All", "Each", "Every", "Some", "Most", "Both", "Many",
    "Few", "Top", "Only", "Other", "Another", "Same", "Several", "Such",
    "This", "That", "These", "Those", "Our", "Their",
    # prepositions, conjunctions, connectives
    "In", "On", "At", "By", "For", "From", "Of", "To", "With", "Within",
    "Without", "Among", "Amongst", "Across", "Between", "Before", "After",
    "During", "Over", "Under", "Near", "Per", "Since", "Than", "Then", "Into",
    "About", "Against", "Through", "Toward", "Towards", "And", "But", "Or",
    "Nor", "If", "Also", "However", "Because", "While", "Versus",
    # sentence-initial adverbs
    "Just", "Now", "Please", "Overall", "Currently", "Today", "Still", "Even",
    "Once",
    # fmt: on
)
# The federal list, not a coverage footprint: these are the state names a
# governed analytics question routes by, and unlike the gold ``city`` dimension
# they never change when Cotality coverage refreshes. Two-word states already
# live in ``_REVIEWED_NON_PERSON_PHRASES``; the pair heuristic only ever
# misreads the ONE-word ones, which is what this covers.
US_STATE_NAMES: tuple[str, ...] = (
    # fmt: off
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "Wisconsin", "Wyoming",
    # fmt: on
)


@lru_cache(maxsize=8)
def _sentence_initial_place_strip_pattern(place_terms: tuple[str, ...]) -> re.Pattern[str] | None:
    """Strip a sentence-opening function word ONLY before a known place.

    Both halves are load-bearing. Sentence-initial position is what keeps a
    surname that follows one of these words mid-sentence intact ("Contacted Do
    Nguyen"); the place lookahead is what keeps a surname that follows one at
    the START intact ("Do Nguyen qualifies"). Neither alone is enough, and the
    2026-08-12 review broke the version that had only the first.

    The lookahead is not consumed, so the place itself still reaches every
    scanner. Function words match case-sensitively (only a Title-case leader
    can join a title-case pair); the place matches case-insensitively because
    gold stores ``BELLEVUE`` and users type ``Bellevue``.
    """

    cleaned = sorted({term.strip() for term in place_terms if term.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(
        r"(?:(?<=^)|(?<=[.!?;:\n]))(\s*)(?:"
        + "|".join(_SENTENCE_INITIAL_FUNCTION_WORDS)
        + r")\s+(?=(?i:"
        + "|".join(re.escape(term) for term in cleaned)
        + r")(?![A-Za-z0-9]))"
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
        # Metro / region formants. "Puget Sound", "Inland Empire", "Bay Area"
        # and the like are geography drill-down vocabulary (a hero surface per
        # CLAUDE.md) that the pair scan read as people -- 12 refusals measured
        # 2026-08-12. This bank is GLOBAL (campaign, outreach and operator-note
        # validators read it too, exactly as they already do for "park", "bay"
        # and "forest"), so ``delta`` was dropped after review: it is a real
        # family name and "Spoke with Marcus Delta" stopped being caught. The
        # rest are geographic nouns with no attested surname use.
        "area", "basin", "coast", "empire", "peninsula", "sound",
        "corridor", "district", "tract", "zone",
        # Second pass, same class: "Front Range", "High Desert", "Wasatch
        # Front" refused (18 prompts, 22 answer-surface blocks), and the
        # governed display labels "Investor Product" and "Competitor
        # Recapture" read as people in narrative prose.
        "range", "desert", "front", "product", "recapture",
        "equity", "heloc", "loan", "mortgage", "offer", "queue", "refi",
        "refinance", "review",
        "candidate", "candidates", "intent", "risk", "sale", "segment", "segments",
        # Scoring vocabulary. "Opportunity Score" is the product's own ranking
        # label and Genie writes it into every top-N borrower list; it withheld
        # a live refinance-plus-HELOC top-10 narrative on paychex 2026-08-12.
        # Neither word is a surname, and only the pair's LAST word is matched
        # here, so this exempts "<Word> Score", never a name.
        "score", "scores", "confidence",
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

_PERSON_LEXICON = _COMMON_FIRST_NAMES | _COMMON_LAST_NAMES
_LEXICON_TOKEN_RE = re.compile(r"[^A-Za-z]+")


def shares_token_with_person_lexicon(value: str) -> bool:
    """True when any whole token of ``value`` is a reviewed person name.

    The admission gate for every relaxation that stops scanning a span for
    identities. ``ELIZABETH`` is a live governed city AND a reviewed first
    name; ``YORBA LINDA`` carries ``LINDA``. Exempting either would blind the
    detector to ``Elizabeth Smith``, so a colliding span keeps scanning.

    Shared rather than copied: ``genie_place_dimension`` gates its governed
    exemption set on this, and the Genie output policy gates its two positional
    strips on it. Two private copies would drift apart the first time the
    lexicons grow.
    """

    return any(
        token.casefold() in _PERSON_LEXICON
        for token in _LEXICON_TOKEN_RE.split(value)
        if token
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
    # The repo-authored redaction label for a non-tenant lien holder
    # (gold_borrower_360.sql: COALESCE(lr.display_name, 'Competitor Other')),
    # not a Cotality value. It is the live `current_lender_ref` for most rows,
    # so it lands in every "which borrowers are with a competitor" answer and
    # withheld one on paychex 2026-08-12.
    "Competitor Other",
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
    # A CLAUDE.md domain term (the Cotality mastered owner/entity relationship
    # identifier) that the pair scan read as a person: even "What is an Owner
    # Link?" refused. Sits alongside the other governed product nouns above.
    "Owner Link",
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
    # Governed work-artefact nouns. "Give me a ranked call list for today"
    # matched on "call list for" (4 refusals, 2026-08-12); a call list is a
    # queue of masked IDs, never a person being addressed.
    r"lists?|queues?|sheets?|campaigns?|batches?|waves?|rosters?|"
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


def _pair_is_boundary_artifact(text: str, match: re.Match[str]) -> bool:
    """True when a flagged pair is the greedy head of a governed phrase.

    "Strong Home Equity fit" — the non-overlapping pair scan consumes
    "Strong Home" before it can see the exempt "Home Equity" pair (live
    approve-rationale refusal, 2026-08-08). If the flagged pair's last word
    begins a governed non-person pair with the next capitalized word, the
    flag is a tokenization artifact — unless the pair leads with a known
    first name, which is never exempt this way ("John Smith Equity" stays
    caught by the lexicon scan regardless).
    """

    tokens = [token for token in re.split(r"\s+|\|", match.group(0).strip()) if token]
    if not tokens or tokens[0].casefold() in _COMMON_FIRST_NAMES:
        return False
    following = re.match(r"\s+([A-Z][a-z]{1,30})\b", text[match.end() :])
    if following is None:
        return False
    return titlecase_pair_is_non_person(f"{tokens[-1]} {following.group(1)}")


def contains_human_name_shape(
    value: str,
    *,
    allowed_phrases: Sequence[str] = (),
    include_titlecase: bool = True,
    sentence_initial_place_terms: Sequence[str] = (),
) -> bool:
    """Detect title-case names and reviewed common lowercase first/last pairs.

    General two-word lowercase prose is not treated as an identity. The common
    pair vocabulary closes the audited ``john smith`` class without turning
    ordinary mortgage phrases into false positives.

    ``sentence_initial_place_terms`` supplies the KNOWN PLACES (US states plus
    the live governed city dimension) before which a sentence-opening function
    word is orthography rather than an identity — "Which Washington cities ..."
    is not a person named "Which Washington". Passing nothing disables the
    strip entirely, so campaign, outreach, and operator-note surfaces are
    bit-for-bit unchanged; only the Ask Genie prompt and answer surfaces opt
    in. See :func:`_sentence_initial_place_strip_pattern` for why the place
    half is required.
    """

    text = _remove_reviewed_non_person_phrases(str(value), allowed_phrases=allowed_phrases)
    strip_pattern = _sentence_initial_place_strip_pattern(tuple(sentence_initial_place_terms))
    if strip_pattern is not None:
        text = strip_pattern.sub(r"\1", text)
    text = _LEADING_ANALYTICS_COMMAND_RE.sub(" ", text)
    if include_titlecase and any(
        not titlecase_pair_is_non_person(match.group(0))
        and not _pair_is_boundary_artifact(text, match)
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
