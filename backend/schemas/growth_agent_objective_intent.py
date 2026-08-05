"""Shared pre-planner segment-intent boundary for Growth request schemas."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NoReturn

from fastapi import HTTPException

from backend.schemas.growth_agent_segment_intent import (
    reject_unsupported_segment_relationships,
    require_affirmative_unsegmented_objective,
    segment_mode_from_prompt,
    segments_from_prompt,
)
from backend.schemas.growth_agent_single_segment_grammar import (
    without_supported_coverage_scope,
    without_supported_state_scope,
)

GrowthSegmentMode = Literal["any", "all"]


class GrowthNamedWorkflowFamily(StrEnum):
    REFI_BRANCH = "refi_branch"
    DOSSIER = "dossier"
    LISTING = "listing"
    COMPETITOR_RECAPTURE = "competitor_recapture"
    HIGH_EQUITY_HELOC = "high_equity_heloc"
    BRANCH_CAPACITY = "branch_capacity"
    SOURCE_FRESHNESS = "source_freshness"


@dataclass(frozen=True)
class GrowthObjectiveIntent:
    named_family: GrowthNamedWorkflowFamily | None
    segment_codes: tuple[str, ...]
    segment_mode: GrowthSegmentMode

_ROUTING_NOUN_RE = re.compile(
    r"\b(?:candidates?|cohorts?|leads?|opportunities|opportunity|segments?|signals?)\b"
)
_MULTI_SEGMENT_ROUTING_SUBJECT_RE = re.compile(r"\bborrowers?\b")
_ROUTING_COMMAND_RE = re.compile(
    r"^(?:assemble|build|create|find|identify|open|prepare|run|select|show|top|track)\b"
)
_NUMERIC_BPS_RE = re.compile(r"\b(?:above|below|over|under)\s+\d+\s+(?:bp|bps)\b")
_POLICY_CRITERION_RE = re.compile(
    r"\b(?:criterion|documented|eligibility|mandatory|required|depends|determines)\b"
)
_ZERO_MENTION_SIGNAL_SCOPE_RE = re.compile(r"\b(?:competitor|qualifying)\s+signals?\b")
_SEGMENT_OPERATION_RE = re.compile(
    r"\b(?:all\s+selected|at\s+least\s+one|both|either|exclusive|intersect(?:ion)?|"
    r"one\s+or\s+more|union|xor)\b|\b(?:do\s+not|not)\b"
)
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;\n\r]+")
_SOURCE_WORKFLOW_TERM_RE = re.compile(
    r"\b(?:sources?|fresh(?:ness)?|readiness|stale\s+data|data\s+ops|"
    r"refresh(?:ed|es|ing)?)\b"
)
_DOSSIER_WORKFLOW_TERM_RE = re.compile(
    r"\b(?:dossiers?|borrower\s+story|customer\s+360|borrower\s+360|explain\s+top)\b"
)
_BRANCH_CAPACITY_WORKFLOW_TERM_RE = re.compile(
    r"\b(?:capacity|aging|stale\s+approved|loan\s+officer)\b"
)
_REFI_BRANCH_RE = re.compile(
    r"\b(?:branch\s+manager\s+(?:review|monitor)|branch\s+review|branch\s+follow-up)\b"
)
_COMPETITOR_NAMED_RE = re.compile(
    r"^(?:run\s+(?:the\s+)?competitor\s+recapture\s+monitor|"
    r"monitor\s+competitor\s+recapture\s+opportunities)[.!?]?$"
)
_COMPETITOR_NAMED_CLAUSE_RE = re.compile(
    r"\b(?:run\s+(?:the\s+)?competitor\s+recapture\s+monitor|"
    r"monitor\s+competitor\s+recapture\s+opportunities)\b"
)
_HIGH_EQUITY_HELOC_NAMED_RE = re.compile(
    r"^(?:run\s+(?:the\s+)?high-equity\s*/\s*heloc\s+watch|"
    r"find\s+high\s+equity\s+or\s+heloc\s+opportunities)[.!?]?$"
)
_HIGH_EQUITY_HELOC_NAMED_CLAUSE_RE = re.compile(
    r"\b(?:run\s+(?:the\s+)?high-equity\s*/\s*heloc\s+watch|"
    r"find\s+high\s+equity\s+or\s+heloc\s+opportunities)\b"
)
_LISTING_NAMED_CLAUSE_RE = re.compile(
    r"\btrack\s+listed(?:-|\s+)for(?:-|\s+)sale\s+purchase\s+opportunities\b"
)
_SOURCE_REVIEWED_OBJECT_RE = re.compile(
    r"(?:"
    r"(?:sources?|feeds?|data[-\s]+sources?)\s+(?:freshness|readiness|status)|"
    r"(?:freshness|readiness)\s+(?:for|of)\s+"
    r"(?:(?:all|current|our|several|the|trusted)\s+)?(?:sources?|feeds?)|"
    r"refreshed\s+(?:sources?|feeds?)|"
    r"data[-\s]+ops\s+(?:readiness|status)"
    r")"
)
_SOURCE_REVIEWED_COMMAND_RE = re.compile(
    rf"^(?:"
    rf"(?:check|monitor|review)\s+(?:(?:all|our|the|trusted)\s+)?"
    rf"{_SOURCE_REVIEWED_OBJECT_RE.pattern}|"
    rf"run\s+(?:the\s+)?(?:source|feed)[-\s]+(?:freshness|readiness)\s+"
    rf"(?:check|monitor|review|sentinel)"
    rf")"
    rf"(?:\s+before\s+(?:i\s+demo\s+this|the\s+(?:demo|walkthrough)))?$"
)
_DOSSIER_COMMANDS = (("explain",), ("open",), ("prepare",), ("review",), ("run",))
_DOSSIER_WORDS = frozenset(
    "best borrower customer dossier dossiers explain for opportunities open prepare review run story the top".split()
)
_BRANCH_COMMANDS = (("find",), ("focus", "on"), ("monitor",), ("review",), ("run",))
_BRANCH_WORDS = frozenset(
    "aging approved branch capacity find focus for lead leads loan manager monitor officer on review run stale the".split()
)
_NAMED_NONROUTING_CONTEXTS: dict[
    GrowthNamedWorkflowFamily,
    frozenset[tuple[str, ...]],
] = {
    GrowthNamedWorkflowFamily.SOURCE_FRESHNESS: frozenset(
        {
            ("before", "i", "demo", "this"),
            ("before", "the", "demo"),
            ("before", "the", "walkthrough"),
        }
    ),
    GrowthNamedWorkflowFamily.DOSSIER: frozenset(
        {
            ("for", "the", "best", "opportunities"),
            ("for", "the", "top", "opportunities"),
        }
    ),
    GrowthNamedWorkflowFamily.BRANCH_CAPACITY: frozenset(
        {
            ("for", "branch", "manager", "review"),
            ("for", "the", "branch", "manager", "review"),
        }
    ),
}
_EXPECTED_NAMED_SEGMENTS: dict[GrowthNamedWorkflowFamily, frozenset[str]] = {
    GrowthNamedWorkflowFamily.REFI_BRANCH: frozenset({"itm"}),
    GrowthNamedWorkflowFamily.DOSSIER: frozenset(),
    GrowthNamedWorkflowFamily.LISTING: frozenset({"listed"}),
    GrowthNamedWorkflowFamily.COMPETITOR_RECAPTURE: frozenset({"retention"}),
    GrowthNamedWorkflowFamily.HIGH_EQUITY_HELOC: frozenset({"permit", "equity"}),
    GrowthNamedWorkflowFamily.BRANCH_CAPACITY: frozenset(),
    GrowthNamedWorkflowFamily.SOURCE_FRESHNESS: frozenset(),
}


def assert_reviewed_growth_segment_objective(
    prompt: str,
    *,
    explicit_segment_codes: Sequence[str] = (),
    explicit_segment_mode: str | None = None,
) -> None:
    """Raise 422 unless schema-visible routing intent is affirmative and exact."""

    classify_growth_objective_intent(
        prompt,
        explicit_segment_codes=explicit_segment_codes,
        explicit_segment_mode=explicit_segment_mode,
    )


def classify_growth_objective_intent(
    prompt: str,
    *,
    explicit_segment_codes: Sequence[str] = (),
    explicit_segment_mode: str | None = None,
) -> GrowthObjectiveIntent:
    """Return one validated named workflow or one exact custom-segment intent."""

    normalized = prompt.lower()
    prompt_codes = tuple(segments_from_prompt(normalized))
    explicit_codes = tuple(dict.fromkeys(str(code) for code in explicit_segment_codes))
    families = _named_workflow_families(normalized, prompt_codes)
    if len(families) > 1:
        _raise_named_workflow_conflict()
    family = next(iter(families), None)
    if family is not None:
        expected_codes = _EXPECTED_NAMED_SEGMENTS[family]
        if set(prompt_codes) != expected_codes or (
            explicit_codes and set(explicit_codes) != expected_codes
        ) or (
            explicit_codes and explicit_segment_mode not in {None, "any"}
        ):
            _raise_named_workflow_conflict()
        _validate_named_workflow_family(normalized, family)
        return GrowthObjectiveIntent(
            named_family=family,
            segment_codes=prompt_codes,
            segment_mode="any",
        )
    if explicit_codes:
        reject_unsupported_segment_relationships(
            normalized,
            explicit_segment_codes=explicit_codes,
            explicit_segment_mode=explicit_segment_mode,
        )
        return GrowthObjectiveIntent(
            named_family=None,
            segment_codes=explicit_codes,
            segment_mode="all" if explicit_segment_mode == "all" else "any",
        )
    routing_clauses = _routing_clauses(prompt)
    routed_codes = {
        code for clause in routing_clauses for code in segments_from_prompt(clause)
    }
    if len(routing_clauses) > 1 and len(routed_codes) > 1:
        reject_unsupported_segment_relationships(prompt)
    for clause in routing_clauses:
        if segments_from_prompt(clause):
            reject_unsupported_segment_relationships(clause)
        else:
            require_affirmative_unsegmented_objective(clause)
    parsed_mode = (
        segment_mode_from_prompt(normalized)
        if len(prompt_codes) >= 2
        and (routing_clauses or _SEGMENT_OPERATION_RE.search(normalized))
        else "any"
    )
    mode: GrowthSegmentMode = "all" if parsed_mode == "all" else "any"
    return GrowthObjectiveIntent(
        named_family=None,
        segment_codes=prompt_codes,
        segment_mode=mode,
    )


def _named_workflow_families(
    prompt: str,
    prompt_codes: tuple[str, ...],
) -> frozenset[GrowthNamedWorkflowFamily]:
    families: set[GrowthNamedWorkflowFamily] = set()
    if _SOURCE_WORKFLOW_TERM_RE.search(prompt):
        families.add(GrowthNamedWorkflowFamily.SOURCE_FRESHNESS)
    if _DOSSIER_WORKFLOW_TERM_RE.search(prompt):
        families.add(GrowthNamedWorkflowFamily.DOSSIER)
    if _BRANCH_CAPACITY_WORKFLOW_TERM_RE.search(prompt):
        families.add(GrowthNamedWorkflowFamily.BRANCH_CAPACITY)
    if _REFI_BRANCH_RE.search(prompt) and set(prompt_codes) == {"itm"}:
        families.add(GrowthNamedWorkflowFamily.REFI_BRANCH)
    # Discover exact named clauses before validating that the entire request
    # belongs to one family. Otherwise two catalog commands joined in one
    # sentence disappear into the custom-segment fallback.
    if _LISTING_NAMED_CLAUSE_RE.search(prompt):
        families.add(GrowthNamedWorkflowFamily.LISTING)
    if _COMPETITOR_NAMED_CLAUSE_RE.search(prompt):
        families.add(GrowthNamedWorkflowFamily.COMPETITOR_RECAPTURE)
    if _HIGH_EQUITY_HELOC_NAMED_CLAUSE_RE.search(prompt):
        families.add(GrowthNamedWorkflowFamily.HIGH_EQUITY_HELOC)
    return frozenset(families)


def _is_named_listing_prompt(prompt: str) -> bool:
    words = tuple(re.findall(r"[a-z]+", prompt))
    words = without_supported_state_scope(words)
    words = without_supported_coverage_scope(words)
    return words == ("track", "listed", "for", "sale", "purchase", "opportunities")


def _validate_named_workflow_family(
    prompt: str,
    family: GrowthNamedWorkflowFamily,
) -> None:
    if family is GrowthNamedWorkflowFamily.REFI_BRANCH:
        reject_unsupported_segment_relationships(prompt)
        return
    if family is GrowthNamedWorkflowFamily.LISTING:
        if not _is_named_listing_prompt(prompt):
            _raise_named_workflow_conflict()
        return
    if family is GrowthNamedWorkflowFamily.COMPETITOR_RECAPTURE:
        if _COMPETITOR_NAMED_RE.fullmatch(prompt) is None:
            _raise_named_workflow_conflict()
        return
    if family is GrowthNamedWorkflowFamily.HIGH_EQUITY_HELOC:
        if _HIGH_EQUITY_HELOC_NAMED_RE.fullmatch(prompt) is None:
            _raise_named_workflow_conflict()
        return
    if family is GrowthNamedWorkflowFamily.SOURCE_FRESHNESS:
        for clause in _objective_clauses(prompt):
            if _SOURCE_REVIEWED_COMMAND_RE.fullmatch(clause) is not None:
                continue
            if _is_closed_named_nonrouting_context(clause, family):
                continue
            _raise_named_workflow_conflict()
        return
    pattern, words, commands = {
        GrowthNamedWorkflowFamily.DOSSIER: (
            _DOSSIER_WORKFLOW_TERM_RE,
            _DOSSIER_WORDS,
            _DOSSIER_COMMANDS,
        ),
        GrowthNamedWorkflowFamily.BRANCH_CAPACITY: (
            _BRANCH_CAPACITY_WORKFLOW_TERM_RE,
            _BRANCH_WORDS,
            _BRANCH_COMMANDS,
        ),
    }[family]
    for clause in _objective_clauses(prompt):
        if pattern.search(clause) and _contains_only_family_language(
            clause,
            words,
            commands,
        ):
            continue
        if _is_closed_named_nonrouting_context(clause, family):
            continue
        # Once a named workflow is present, never pass an unowned clause to
        # the generic custom-workflow grammar. That grammar intentionally
        # accepts commands such as ``build a reviewed custom workflow``; using
        # it here would silently discard the extra command while still routing
        # the request to the named workflow.
        _raise_named_workflow_conflict()


def _contains_only_family_language(
    value: str,
    words: frozenset[str],
    commands: tuple[tuple[str, ...], ...],
) -> bool:
    tokens = tuple(re.findall(r"[a-z]+", value))
    has_command = any(
        tokens[index : index + len(command)] == command
        for command in commands
        for index in range(len(tokens))
    )
    punctuation = re.sub(r"[a-z]+|\d+", "", value)
    return (
        has_command
        and all(token in words for token in tokens)
        and re.fullmatch(r"[\s,;:.!?-]*", punctuation) is not None
    )


def _is_closed_named_nonrouting_context(
    value: str,
    family: GrowthNamedWorkflowFamily,
) -> bool:
    """Accept only inert, family-specific context within a named request."""

    tokens = tuple(re.findall(r"[a-z]+", value))
    punctuation = re.sub(r"[a-z]+|\d+", "", value)
    return (
        tokens in _NAMED_NONROUTING_CONTEXTS.get(family, frozenset())
        and re.fullmatch(r"[\s,;:.!?-]*", punctuation) is not None
    )


def _raise_named_workflow_conflict() -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail=(
            "A request must select one reviewed named workflow without mixing unrelated "
            "named-workflow or segment criteria."
        ),
    )


def _routing_clauses(prompt: str) -> tuple[str, ...]:
    """Select cohort-routing clauses without capturing CTA or policy prose."""

    clauses: list[str] = []
    for raw_clause in _CLAUSE_BOUNDARY_RE.split(prompt.lower()):
        clause = raw_clause.strip()
        if not clause:
            continue
        mentions = segments_from_prompt(clause)
        word_count = len(re.findall(r"[a-z]+|\d+", clause))
        routing_noun = _ROUTING_NOUN_RE.search(clause)
        if routing_noun is None and len(set(mentions)) >= 2:
            routing_noun = _MULTI_SEGMENT_ROUTING_SUBJECT_RE.search(clause)
        routing_command = _ROUTING_COMMAND_RE.search(clause)
        words_before_routing_noun = (
            len(re.findall(r"[a-z]+|\d+", clause[: routing_noun.start()]))
            if routing_noun
            else 0
        )
        is_direct_routing_clause = routing_noun is not None and (
            (
                bool(mentions)
                and routing_command is not None
                and word_count <= 20
            )
            or (
                not mentions
                and (
                    (
                        routing_command is not None
                        and words_before_routing_noun <= 5
                    )
                    or _ZERO_MENTION_SIGNAL_SCOPE_RE.search(clause) is not None
                )
            )
        )
        if is_direct_routing_clause or _NUMERIC_BPS_RE.search(clause) or (
            mentions and word_count <= 4 and _POLICY_CRITERION_RE.search(clause) is None
        ):
            clauses.append(clause)
    return tuple(clauses)


def _objective_clauses(prompt: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in _CLAUSE_BOUNDARY_RE.split(prompt.lower())
        if clause.strip()
    )
