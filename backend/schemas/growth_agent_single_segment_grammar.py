"""Closed affirmative clause grammar for one reviewed Growth segment."""

from __future__ import annotations

import re

from backend.schemas.usps import US_STATE_NAME_BY_CODE, USPS_STATE_CODES

SUPPORTED_REVIEW_SUFFIXES = (
    ("and", "prepare", "it", "for", "review"),
    ("and", "prepare", "it", "for", "human", "review"),
    ("and", "prepare", "them", "for", "review"),
    ("and", "prepare", "them", "for", "human", "review"),
    ("and", "prepare", "the", "cohort", "for", "review"),
    ("and", "prepare", "the", "cohort", "for", "human", "review"),
    ("for", "review"),
    ("for", "human", "review"),
    ("before", "the", "branch", "review"),
    ("before", "branch", "review"),
    ("for", "the", "branch", "review"),
    ("for", "branch", "review"),
    ("for", "a", "branch", "manager", "review"),
    ("for", "a", "branch", "manager", "monitor"),
    ("for", "branch", "follow", "up"),
)
_PREFIX_WORDS = frozenset(
    (
        "a an applicants assemble balances borrower borrowers build candidate candidates cohort "
        "cohorts create current custom dealing find for high home homeowners identify in "
        "individuals lead leads loan locate market mortgage open opportunities opportunity "
        "prepare prime rate rates residents review reviewed run show signal signals spread spreads "
        "strong the top track underwriting with"
    ).split()
)
_SUFFIX_WORDS = frozenset(
    (
        "among and borrower borrowers candidate candidates cohort cohorts conditions dealing "
        "economics high home homeowners lead leads market markets mortgage opportunities "
        "opportunity options properties property rate rates residents review reviewed signal "
        "signals spread spreads strong the underwriting with"
    ).split()
)
_REVIEW_SUFFIXES = SUPPORTED_REVIEW_SUFFIXES + (
    ("for", "weekly", "monitoring"),
    ("for", "daily", "monitoring"),
)
_SAFE_SUFFIX_CLAUSES = frozenset(
    {
        (
            "cohort",
            "health",
            "information",
            "is",
            "excluded",
            "from",
            "campaign",
            "eligibility",
        ),
        ("health", "information", "is", "excluded", "from", "campaign", "eligibility"),
    }
)
_STATE_WORD_SEQUENCES = frozenset(
    {(code.lower(),) for code in USPS_STATE_CODES}
    | {tuple(name.lower().split()) for name in US_STATE_NAME_BY_CODE.values()}
)
_UNSEGMENTED_OBJECTIVE_WORDS = frozenset(
    (
        "a agent approved around assemble build candidate candidates cohort cohorts competitor "
        "compose create custom find for freshness governed growth human lead leads open plan plans "
        "prepare qualifying queue review reviewed run segment segments select show signal signals "
        "source the track workflow workflows"
    ).split()
)


def contains_only_affirmative_prefix(value: str) -> bool:
    words = re.findall(r"[a-z]+", value)
    if not all(word in _PREFIX_WORDS for word in words):
        return False
    return _contains_only_punctuation(value)


def contains_only_affirmative_suffix(value: str) -> bool:
    words = tuple(re.findall(r"[a-z]+|\d+", value))
    if words in _SAFE_SUFFIX_CLAUSES:
        return _contains_only_punctuation(value)
    for review_suffix in _REVIEW_SUFFIXES:
        if words[-len(review_suffix) :] == review_suffix:
            words = words[: -len(review_suffix)]
            break
    words = without_supported_state_scope(words)
    return all(word in _SUFFIX_WORDS for word in words) and _contains_only_punctuation(value)


def contains_only_affirmative_unsegmented_objective(value: str) -> bool:
    """Prove a reviewed non-segment/custom objective from closed vocabulary."""

    words = re.findall(r"[a-z]+", value)
    return bool(words) and all(word in _UNSEGMENTED_OBJECTIVE_WORDS for word in words) and (
        _contains_only_punctuation(value)
    )


def without_supported_coverage_scope(words: tuple[str, ...]) -> tuple[str, ...]:
    """Strip the exact visible current-coverage suffix, and no broader prose."""

    suffix = ("across", "current", "coverage")
    if words[-len(suffix) :] == suffix:
        return words[: -len(suffix)]
    return words


def without_supported_state_scope(words: tuple[str, ...]) -> tuple[str, ...]:
    for index, word in enumerate(words):
        if word not in {"across", "in", "within"}:
            continue
        if _is_supported_state_sequence(words[index + 1 :]):
            return words[:index]
    return words


def _contains_only_punctuation(value: str) -> bool:
    punctuation = re.sub(r"[a-z]+|\d+", "", value)
    return re.fullmatch(r"[\s,;:.!?-]*", punctuation) is not None


def _is_supported_state_sequence(words: tuple[str, ...]) -> bool:
    remaining = words
    state_names = sorted(_STATE_WORD_SEQUENCES, key=len, reverse=True)
    while remaining:
        match = next(
            (state for state in state_names if remaining[: len(state)] == state),
            None,
        )
        if match is None:
            return False
        remaining = remaining[len(match) :]
        if not remaining:
            return True
        if remaining[0] != "and":
            return False
        remaining = remaining[1:]
    return False
