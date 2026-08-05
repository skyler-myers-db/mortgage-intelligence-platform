"""Connector-aware reviewed segment intent parsing for the Growth Agent."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

from fastapi import HTTPException

from backend.schemas.growth_agent_single_segment_grammar import (
    SUPPORTED_REVIEW_SUFFIXES,
    contains_only_affirmative_prefix,
    contains_only_affirmative_suffix,
    contains_only_affirmative_unsegmented_objective,
    without_supported_coverage_scope,
    without_supported_state_scope,
)


@dataclass(frozen=True)
class _SegmentMention:
    code: str
    start: int
    end: int


@dataclass(frozen=True)
class _RelationGap:
    relationships: frozenset[str]
    serial_separator: bool = False


_SEGMENT_PROMPT_TERMS: dict[str, str] = {
    "itm": "itm|in the money|refi|refinance|rate spread|economic incentive|prime refi",
    "listed": "listed and for sale|listed-for-sale|listed for sale|listed|listing|for sale|purchase",
    "permit": "permit|heloc intent|heloc|home equity line",
    "investor": "investor|multi property|multi-property|owner link|portfolio owner",
    "equity": "equity|cash out|cash-out|high equity",
    "retention": "retention|recapture|current customer",
}
_SEGMENT_TERM_RE: dict[str, re.Pattern[str]] = {
    code: re.compile(
        r"\b(?:"
        + "|".join(re.escape(term) for term in sorted(terms.split("|"), key=len, reverse=True))
        + r")\b"
    )
    for code, terms in _SEGMENT_PROMPT_TERMS.items()
}
_CLOSED_UNSUPPORTED_RELATIONSHIP_RE = re.compile(
    r"\bwith\s+no\s+(?:double|dual|overlapping|shared)\s+"
    r"(?:borrowers?|matches|members?)\s+across\b"
)
_INTERSECTION_CONNECTOR_RE = re.compile(
    r"\b(?:as\s+well\s+as|together\s+with|and|plus|with)\b|[&+]"
)
_UNION_CONNECTOR_RE = re.compile(r"\bor\b")
_MODE_MODIFIER_RE = re.compile(
    r"\b(?:all\s+selected|at\s+least\s+one(?:\s+of)?|one\s+or\s+more(?:\s+of)?|"
    r"intersection|union|both|either|any(?:\s+of)?)\b"
)
_DISCONNECTED_RELATION_RE = re.compile(r"[.!?;:\n\r\u2013\u2014/|\\]")
_INTERSECTION_MODIFIERS = frozenset({"all selected", "intersection", "both"})
_UNION_MODIFIERS = frozenset(
    {
        "any",
        "any of",
        "at least one",
        "at least one of",
        "either",
        "one or more",
        "one or more of",
        "union",
    }
)
_UNION_ENUMERATION_MODIFIERS = _UNION_MODIFIERS - {"either"}
_RELATION_FILLER_WORDS = frozenset(
    {
        "also",
        "are",
        "borrower",
        "borrowers",
        "candidate",
        "candidates",
        "cohort",
        "cohorts",
        "customer",
        "customers",
        "for",
        "having",
        "home",
        "homes",
        "household",
        "households",
        "in",
        "lead",
        "leads",
        "matching",
        "member",
        "members",
        "opportunities",
        "opportunity",
        "of",
        "properties",
        "property",
        "reviewed",
        "segment",
        "segments",
        "selected",
        "signal",
        "signals",
        "the",
        "these",
        "those",
        "who",
        "with",
    }
)
_PREFIX_WORDS = frozenset(
    (
        "a across agent all an any as assemble at are borrower borrowers both branch build "
        "candidate candidates cohort cohorts create custom either find for from human identify "
        "in intersection lead leads least member members more of one open opportunities "
        "opportunity or prepare prime review reviewed run segment segments select selected show signal "
        "signals the these those union workflow with who"
    ).split()
)
_SUFFIX_LABEL_WORDS = frozenset(
    (
        "borrower borrowers candidate candidates cohort cohorts home homes lead leads member "
        "members opportunities opportunity properties property reviewed segment segments "
        "selected signal signals the"
    ).split()
)
def segments_from_prompt(prompt: str) -> list[str]:
    """Return recognized reviewed segment codes in stable catalog order."""

    prompt = prompt.lower()
    mentioned_codes = {mention.code for mention in _segment_mentions(prompt)}
    return [code for code in _SEGMENT_PROMPT_TERMS if code in mentioned_codes]


def reject_unsupported_segment_relationships(
    prompt: str,
    *,
    explicit_segment_codes: Sequence[str] = (),
    explicit_segment_mode: str | None = None,
) -> None:
    """Prove that a multi-segment request uses only reviewed Any/All grammar.

    Explicit ``segment_codes`` and ``segment_mode`` can resolve otherwise
    ambiguous comma-separated prose, but they cannot turn an unsupported or
    conflicting set operation into the broader Any/All operations supported by
    Module 0. The proof is closed: every prefix word, inter-segment gap, and
    suffix phrase must belong to the reviewed grammar.
    """

    prompt = prompt.lower()
    mentions = _segment_mentions(prompt)
    prompt_codes = {mention.code for mention in mentions}
    explicit_codes = {
        str(code).strip().lower() for code in explicit_segment_codes if str(code)
    }
    if explicit_codes and prompt_codes and prompt_codes != explicit_codes:
        _raise_segment_code_mismatch()
    if len(prompt_codes) == 1:
        _validate_single_segment_affirmative(prompt, mentions=mentions)
        return
    if not prompt_codes:
        if explicit_codes:
            require_affirmative_unsegmented_objective(prompt)
        return
    prompt_mode = _validated_segment_relationship(
        prompt,
        mentions=mentions,
        allow_comma_ambiguity=bool(explicit_codes),
    )
    if (
        explicit_codes
        and prompt_mode is not None
        and explicit_segment_mode in {"any", "all"}
        and prompt_mode != explicit_segment_mode
    ):
        _raise_ambiguous_relationship()


def require_affirmative_unsegmented_objective(prompt: str) -> None:
    """Reject a zero-mention segment command unless its full grammar is affirmative."""

    if not contains_only_affirmative_unsegmented_objective(prompt):
        _raise_unreviewed_single_segment_grammar()


def segment_mode_from_prompt(prompt: str) -> str:
    """Resolve Any/All only from grammar connecting recognized segment mentions."""

    prompt = prompt.lower()
    mentions = _segment_mentions(prompt)
    prompt_codes = {mention.code for mention in mentions}
    if len(prompt_codes) == 1:
        _validate_single_segment_affirmative(prompt, mentions=mentions)
        return "any"
    if not prompt_codes:
        return "any"
    mode = _validated_segment_relationship(
        prompt,
        mentions=mentions,
        allow_comma_ambiguity=False,
    )
    if mode is not None:
        return mode
    _raise_ambiguous_relationship()


def is_closed_reviewed_segment_signal_criterion(value: str) -> bool:
    """Recognize a closed list of reviewed signals without resolving its set mode.

    This helper exists for the shared protected-health scanner. It proves only
    that every criterion token is a known Growth segment term, relationship
    connector, label, or review suffix. The authoritative parser below still
    decides whether those relationships form one supported Any/All operation.
    """

    normalized = value.lower().strip()
    mentions = _segment_mentions(normalized)
    if (
        len({mention.code for mention in mentions}) < 2
        or normalized[: mentions[0].start].strip()
        or not _contains_only_suffix_language(normalized[mentions[-1].end :])
    ):
        return False
    return all(
        _relation_gap(normalized[left.end : right.start]) is not None
        for left, right in zip(mentions, mentions[1:], strict=False)
    )


def is_closed_unsupported_segment_relationship(prompt: str) -> bool:
    """Return whether a bounded unsupported set operator is otherwise reviewed.

    The protected-health criterion detector intentionally fails closed on an
    audience followed by an unknown ``with ...`` criterion. A negative
    multi-segment set operator can have that same shape. This proof removes
    only the bounded unsupported operator, then requires the remaining request
    to parse as ordinary reviewed Any/All grammar. Extra health, PII, or free
    text therefore cannot use this precedence exception.
    """

    normalized = prompt.lower()
    if len(set(segments_from_prompt(normalized))) < 2:
        return False
    candidate, substitutions = _CLOSED_UNSUPPORTED_RELATIONSHIP_RE.subn(
        "with",
        normalized,
    )
    if substitutions != 1:
        return False
    try:
        segment_mode_from_prompt(candidate)
    except HTTPException:
        return False
    return True


def _validated_segment_relationship(
    prompt: str,
    *,
    mentions: Sequence[_SegmentMention],
    allow_comma_ambiguity: bool,
) -> str | None:
    """Return a proved Any/All relationship, or ``None`` for explicit comma mode."""

    if not mentions:
        if not _contains_only_prefix_language(prompt):
            _raise_ambiguous_relationship()
        modifiers = _all_modifiers(prompt)
        return _single_supported_modifier_mode(modifiers)

    prefix = prompt[: mentions[0].start]
    suffix = prompt[mentions[-1].end :]
    if not _contains_only_prefix_language(prefix) or not _contains_only_suffix_language(suffix):
        _raise_ambiguous_relationship()

    relationships: set[str] = set()
    prefix_start = max(
        prompt.rfind(".", 0, mentions[0].start),
        prompt.rfind(";", 0, mentions[0].start),
        prompt.rfind("?", 0, mentions[0].start),
        prompt.rfind("!", 0, mentions[0].start),
    )
    scoped_prefix = prompt[prefix_start + 1 : mentions[0].start]
    prefix_modifiers = _scoped_modifiers(scoped_prefix)
    prompt_modifiers = _all_modifiers(prompt)
    # Mode words only govern segment relationships when they appear in the
    # reviewed prefix grammar. A trailing or otherwise unscoped modifier must
    # not silently override the connectors that were actually parsed.
    if prompt_modifiers - prefix_modifiers:
        _raise_ambiguous_relationship()
    if "union" in prompt_modifiers and prompt_modifiers & _INTERSECTION_MODIFIERS:
        _raise_ambiguous_relationship()
    prefix_relationships = _modifier_relationships(scoped_prefix)
    has_scoped_union_enumeration = bool(
        prefix_modifiers & _UNION_ENUMERATION_MODIFIERS
    )
    if len(mentions) > 2 and re.search(r"\bboth\b", scoped_prefix):
        _raise_ambiguous_relationship()
    relationships.update(prefix_relationships)
    gaps: list[_RelationGap] = []
    for left, right in zip(mentions, mentions[1:], strict=False):
        gap = _relation_gap(prompt[left.end : right.start])
        if gap is None:
            _raise_ambiguous_relationship()
        gaps.append(gap)
        # In conventional set grammar, the `and` in "the union of A and B" or
        # "at least one of A and B" enumerates union members; it does not
        # change the operation to an intersection. This exception is limited
        # to enumeration modifiers, so `either A and B` still fails closed.
        if has_scoped_union_enumeration and gap.relationships == {"all"}:
            relationships.add("any")
        else:
            relationships.update(gap.relationships)

    serial_separator_indexes = [index for index, gap in enumerate(gaps) if gap.serial_separator]
    if serial_separator_indexes:
        explicit_gap_indexes = [index for index, gap in enumerate(gaps) if gap.relationships]
        has_unambiguous_prefix = len(prefix_relationships) == 1
        # A comma list needs either a later connector (A, B, or C) or an
        # unambiguous prefix that scopes every comma-only mention. A comma after
        # an explicit connector is always an unbound tail, even when a prefix
        # modifier appeared earlier ("both A and B, C").
        if (
            explicit_gap_indexes
            and max(serial_separator_indexes) > min(explicit_gap_indexes)
        ) or (
            not explicit_gap_indexes
            and not has_unambiguous_prefix
            and not allow_comma_ambiguity
        ):
            _raise_ambiguous_relationship()

    if relationships == {"all"}:
        return "all"
    if relationships == {"any"}:
        return "any"
    if not relationships and allow_comma_ambiguity and serial_separator_indexes:
        return None
    if (
        not relationships
        and allow_comma_ambiguity
        and len({mention.code for mention in mentions}) <= 1
    ):
        return None
    _raise_ambiguous_relationship()


def _raise_ambiguous_relationship() -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail=(
            "Multiple reviewed segments require one explicit relationship: use intersection "
            "language such as 'both' or union language such as 'either', or submit reviewed "
            "segment_codes with segment_mode."
        ),
    )


def _raise_unreviewed_single_segment_grammar() -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail=(
            "A reviewed single-segment workflow requires affirmative segment criteria using "
            "supported mortgage, geography, and review language."
        ),
    )


def _raise_segment_code_mismatch() -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail=(
            "Prompt segment mentions must exactly match the reviewed segment_codes selection."
        ),
    )


def _segment_mentions(prompt: str) -> list[_SegmentMention]:
    candidates = [
        _SegmentMention(code=code, start=match.start(), end=match.end())
        for code, pattern in _SEGMENT_TERM_RE.items()
        for match in pattern.finditer(prompt)
    ]
    candidates.sort(key=lambda item: (item.start, -(item.end - item.start)))
    resolved: list[_SegmentMention] = []
    for candidate in candidates:
        if any(
            candidate.start < current.end and current.start < candidate.end for current in resolved
        ):
            continue
        if (
            resolved
            and resolved[-1].code == candidate.code
            and _contains_only_relation_filler(
                prompt[resolved[-1].end : candidate.start]
            )
        ):
            previous = resolved[-1]
            resolved[-1] = _SegmentMention(
                code=previous.code,
                start=previous.start,
                end=candidate.end,
            )
            continue
        resolved.append(candidate)
    return resolved


def _single_supported_modifier_mode(modifiers: set[str]) -> str | None:
    relationships: set[str] = set()
    if modifiers & _INTERSECTION_MODIFIERS:
        relationships.add("all")
    if modifiers & _UNION_MODIFIERS:
        relationships.add("any")
    if relationships == {"all"}:
        return "all"
    if relationships == {"any"}:
        return "any"
    if relationships:
        _raise_ambiguous_relationship()
    return None


def _validate_single_segment_affirmative(
    prompt: str,
    *,
    mentions: Sequence[_SegmentMention],
) -> None:
    if not mentions or _all_modifiers(prompt):
        _raise_unreviewed_single_segment_grammar()
    prefix = prompt[: mentions[0].start]
    suffix = prompt[mentions[-1].end :]
    if not contains_only_affirmative_prefix(prefix) or not contains_only_affirmative_suffix(
        suffix
    ):
        _raise_unreviewed_single_segment_grammar()
    for left, right in zip(mentions, mentions[1:], strict=False):
        gap = _relation_gap(prompt[left.end : right.start])
        if gap is None or len(gap.relationships) != 1:
            _raise_unreviewed_single_segment_grammar()


def _contains_only_prefix_language(value: str) -> bool:
    words = re.findall(r"[a-z]+", value)
    if not all(word in _PREFIX_WORDS for word in words):
        return False
    punctuation = re.sub(r"[a-z]+", "", value)
    return re.fullmatch(r"[\s,;:.!?]*", punctuation) is not None


def _contains_only_suffix_language(value: str) -> bool:
    normalized = re.sub(r"[,.!?]", " ", value)
    words = tuple(re.findall(r"[a-z]+", normalized))
    for review_suffix in SUPPORTED_REVIEW_SUFFIXES:
        if words[-len(review_suffix) :] == review_suffix:
            words = words[: -len(review_suffix)]
            break
    words = without_supported_state_scope(words)
    words = without_supported_coverage_scope(words)
    if not all(word in _SUFFIX_LABEL_WORDS for word in words):
        return False
    punctuation = re.sub(r"[a-z]+", "", value)
    return re.fullmatch(r"[\s,.!?]*", punctuation) is not None


def _modifier_relationships(prefix: str) -> set[str]:
    modifiers = _scoped_modifiers(prefix)
    relationships: set[str] = set()
    if modifiers & _INTERSECTION_MODIFIERS:
        relationships.add("all")
    if modifiers & _UNION_MODIFIERS:
        relationships.add("any")
    return relationships


def _scoped_modifiers(prefix: str) -> set[str]:
    matches = list(_MODE_MODIFIER_RE.finditer(prefix))
    modifiers: set[str] = set()
    for first_index, first_match in enumerate(matches):
        suffix = list(prefix[first_match.start() :])
        suffix_start = first_match.start()
        relevant = matches[first_index:]
        for match in relevant:
            start = match.start() - suffix_start
            end = match.end() - suffix_start
            suffix[start:end] = " " * (end - start)
        if not _contains_only_relation_filler("".join(suffix), allowed_punctuation=",:"):
            continue
        for match in relevant:
            modifiers.add(" ".join(match.group(0).split()))
    return modifiers


def _all_modifiers(value: str) -> set[str]:
    return {" ".join(match.group(0).split()) for match in _MODE_MODIFIER_RE.finditer(value)}


def _relation_gap(gap: str) -> _RelationGap | None:
    # A relationship cannot jump a sentence/clause terminator. Without this
    # guard, an earlier `and` could silently absorb a later disconnected
    # segment mention into the same intersection.
    if _DISCONNECTED_RELATION_RE.search(gap):
        return None
    relationships: set[str] = set()
    connector_spans: list[tuple[int, int]] = []
    for match in _INTERSECTION_CONNECTOR_RE.finditer(gap):
        relationships.add("all")
        connector_spans.append(match.span())
    for match in _UNION_CONNECTOR_RE.finditer(gap):
        relationships.add("any")
        connector_spans.append(match.span())
    if not connector_spans:
        if "," not in gap or not _contains_only_relation_filler(gap, allowed_punctuation=","):
            return None
        return _RelationGap(frozenset(), serial_separator=True)
    if len(connector_spans) != 1:
        return None
    residual = list(gap)
    for start, end in connector_spans:
        residual[start:end] = " " * (end - start)
    if not _contains_only_relation_filler("".join(residual), allowed_punctuation=","):
        return None
    return _RelationGap(frozenset(relationships))


def _contains_only_relation_filler(value: str, *, allowed_punctuation: str = "") -> bool:
    words = re.findall(r"[a-z]+", value)
    if not all(word in _RELATION_FILLER_WORDS for word in words):
        return False
    punctuation = re.sub(r"[a-z]+", "", value)
    return re.fullmatch(rf"[\s{re.escape(allowed_punctuation)}]*", punctuation) is not None
