"""Source-gap response copy for pre-Genie guardrails."""

from __future__ import annotations

import re

_CREDIT_SOURCE_GAP_RE = re.compile(
    r"\b(?:fico|credit\s+scores?|credit[-\s]+bureau|tri[-\s]+merge|vantage\s*score)\b",
    re.IGNORECASE,
)


def source_gap_answer(question: str, source_gap_match: str, *, source: str) -> str:
    if _CREDIT_SOURCE_GAP_RE.search(question) or _CREDIT_SOURCE_GAP_RE.search(source_gap_match):
        return (
            "Credit-bureau and FICO score feeds are not live in this workspace yet. "
            "I will not infer credit-score eligibility or count the missing feed as "
            f"zero demand. Source: {source}."
        )
    return (
        "Cotality Building Permits records are pending/roadmap and not live in "
        "this workspace yet. "
        "Cotality MLS/listing rows are live, but I will not infer filed permit "
        f"activity from listing or propensity signals. Source: {source}."
    )
