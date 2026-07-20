"""Shared borrower-facing claim policy for campaign, agent, and audit text."""

from __future__ import annotations

import re

_REQUIREMENT = (
    r"(?:(?:(?:all|any|applicable|our|the|every|each|lending|loan|mortgage|"
    r"eligibility|qualification|underwriting)\s+){0,6}"
    r"(?:requirements?|criteria|standards?|conditions?|bar|screen|review))"
)
_QUALIFIED_OUTCOME = r"(?:eligible|approved|pre[- ]?approved|qualified)"

_UNSUPPORTED_QUALIFICATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\b(?:you|you['’]re|you['’]ve\s+been)\s+"
        rf"(?:(?:may|can|will|would)\s+)?(?:qualif(?:y|ied)|"
        rf"(?:are|were|have\s+been)\s+{_QUALIFIED_OUTCOME})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\byou\s+(?:(?:have|had)\s+)?(?:meet|met|satisf(?:y|ied)|fulfill(?:ed)?|"
        rf"clear(?:ed)?|pass(?:ed)?|fit)\s+{_REQUIREMENT}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\byou\s+(?:(?:seem|appear|remain)\s+|(?:are|were)\s+(?:deemed|found)\s+)"
        rf"{_QUALIFIED_OUTCOME}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\byour\s+(?:profile|application|record|records|information|file)\s+"
        rf"(?:(?:has|have|had)\s+)?"
        rf"(?:meet|meets|met|satisf(?:y|ies|ied)|fulfill(?:s|ed)?|clear(?:s|ed)?|"
        rf"pass(?:es|ed)?|fit(?:s|ted)?)\s+{_REQUIREMENT}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:our\s+(?:review|records|analysis|assessment)|the\s+(?:review|records))\s+"
        rf"(?:show|shows|showed|confirm|confirms|confirmed|verif(?:y|ies|ied)|"
        rf"validat(?:e|es|ed)|indicat(?:e|es|ed)|demonstrat(?:e|es|ed)|"
        rf"establish|establishes|established|prov(?:e|es|ed|ing))\s+"
        rf"(?:(?:that\s+)?you\s+(?:are\s+)?{_QUALIFIED_OUTCOME}|your\s+(?:eligibility|qualification))\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bwe(?:(?:['’]ve)|(?:\s+(?:have|had)))?\s+"
        rf"(?:approved|pre[- ]?approved|qualified|cleared)\s+(?:"
        rf"you(?:\s+(?:as\s+)?{_QUALIFIED_OUTCOME})?|"
        r"your\s+(?:application|profile|loan|mortgage|request))\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bwe\s+(?:determined|found|deemed|confirmed)\s+(?:that\s+)?(?:"
        rf"you\s+(?:are\s+)?{_QUALIFIED_OUTCOME}|"
        r"this\s+(?:mortgage|loan|offer|product)\s+is\s+suitable\s+for\s+you)\b",
        re.IGNORECASE,
    ),
)


def contains_unsupported_borrower_qualification_claim(value: str) -> bool:
    """Return whether public copy definitely claims the addressee qualifies."""

    text = str(value)
    return any(pattern.search(text) is not None for pattern in _UNSUPPORTED_QUALIFICATION_PATTERNS)
