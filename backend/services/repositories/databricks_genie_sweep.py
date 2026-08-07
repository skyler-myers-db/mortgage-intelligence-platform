"""Full-analysis sweep: agentic decomposition for "analyze everything" asks.

"Do a full analysis on all of the data" can never be one SQL statement, so the
single-turn pipeline used to dead-end in a governed refusal. Under the
live-first doctrine the answer must still be GENIE'S OWN WORK — so this module
decomposes the ask into a fixed set of themed sub-questions, conducts a real
live Genie turn for each (every sub-turn runs the complete policy pipeline:
prompt guards upstream, SQL trust policy, claims verification, PII redaction,
disclosed rescue), and assembles the verified results into one executive
answer. Deterministic code here ORCHESTRATES and FORMATS; it never authors an
analytic figure. Sections quote the sub-answers verbatim and the proof carries
every generated SQL statement, labeled per section.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from backend.services.genie_answers import (
    GenieMessageResponse,
    GenieProof,
    GenieReasoningStep,
    default_follow_up_questions,
)
from backend.services.repositories.databricks_genie_canonical import (
    _canonical_itm_state_scope,
    _normalized_question,
    _specific_top_borrower_intent,
)
from backend.services.repositories.databricks_genie_trust import _genie_question_hash

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.services.repositories.databricks_genie import (
        DatabricksGenieRepository,
    )

# Sources that mean a sub-turn produced governed analytic content.
_DATA_BEARING_SOURCES = frozenset({"genie", "trusted_sql"})

# Sweep fan-out cap. The Genie Conversation API tolerates concurrent
# conversations; four keeps the burst polite while holding wall time near the
# slowest single turn.
_SWEEP_MAX_WORKERS = 4

_FULL_ANALYSIS_PHRASES = (
    "full analysis",
    "complete analysis",
    "comprehensive analysis",
    "deep analysis",
    "deep-dive analysis",
    "executive summary",
    "executive briefing",
    "state of the book",
    "state of the portfolio",
    "full picture",
    "complete picture",
)

_BROAD_SCOPE_TERMS = (
    "all of the data",
    "all the data",
    "all our data",
    "everything",
    "entire book",
    "whole book",
    "entire portfolio",
    "whole portfolio",
    "across the board",
)

_INSIGHT_TERMS = (
    "analysis",
    "analyze",
    "analyse",
    "insight",
    "insights",
    "summary",
    "overview",
    "briefing",
    "deep dive",
    "picture",
)

# Themed sub-questions. Every entry is a phrasing the live space handles well
# (several are pinned by the release eval pack, including the breadth
# category). Order is the presentation order.
FULL_ANALYSIS_SWEEP: tuple[tuple[str, str], ...] = (
    (
        "Refinance economics",
        "How many borrowers are currently in-the-money, and what is the average rate spread?",
    ),
    (
        "Home-equity capacity",
        "How many borrowers have at least 35% modeled equity across the current Cotality data coverage?",
    ),
    (
        "Where the opportunity concentrates",
        "Break down in-the-money borrowers by current coverage state; which state leads?",
    ),
    (
        "30-day funnel trend",
        "How did the lead population and approvals change over the last 30 days?",
    ),
    (
        "The rate lock-in cohort",
        "How big is the rate lock-in cohort and what is its median origination rate?",
    ),
    (
        "What triggered recently",
        "What trigger evidence fired in the last 7 days, grouped by signal type?",
    ),
    (
        "Who to act on first",
        "What are the top borrower candidates across all segments overall, what makes "
        "them such good candidates exactly (for each one), and what is the exact offer "
        "we should make to each and why?",
    ),
)


def is_full_analysis_question(question: str) -> bool:
    """True for open-ended analyze-everything asks; narrow scopes stay single-turn."""

    q = _normalized_question(question)
    if _canonical_itm_state_scope(question) is not None:
        return False
    if _specific_top_borrower_intent(q) is not None:
        return False
    if any(phrase in q for phrase in _FULL_ANALYSIS_PHRASES):
        return True
    return any(term in q for term in _BROAD_SCOPE_TERMS) and any(
        term in q for term in _INSIGHT_TERMS
    )


def _labeled_sql(sections: list[tuple[str, GenieMessageResponse]]) -> str | None:
    parts: list[str] = []
    for title, response in sections:
        if response.sql_query:
            parts.append(f"-- [{title}]\n{response.sql_query.strip()}")
    return "\n\n".join(parts) if parts else None


def run_full_analysis_sweep(
    repo: DatabricksGenieRepository,
    question: str,
) -> GenieMessageResponse | None:
    """Conduct the themed live sweep and assemble the executive answer.

    Returns ``None`` when fewer than three sub-analyses produce governed
    content — the caller then falls through to the normal single-turn
    pipeline, which fails honestly.
    """

    started = time.monotonic()

    def _one(sub_question: str) -> GenieMessageResponse | None:
        try:
            return repo.respond(sub_question, allow_sweep=False)
        except Exception:  # noqa: BLE001 - a failed theme becomes a disclosed gap
            return None

    with ThreadPoolExecutor(max_workers=_SWEEP_MAX_WORKERS) as pool:
        results = list(pool.map(_one, [q for _, q in FULL_ANALYSIS_SWEEP]))

    sections: list[tuple[str, GenieMessageResponse]] = []
    gaps: list[str] = []
    for (title, _), response in zip(FULL_ANALYSIS_SWEEP, results, strict=True):
        if (
            response is not None
            and response.source in _DATA_BEARING_SOURCES
            and (response.answer or "").strip()
        ):
            sections.append((title, response))
        else:
            gaps.append(
                f"The '{title}' analysis returned no governed result on this run "
                "and was omitted."
            )
    if len(sections) < 3:
        return None

    assets: list[str] = []
    for _, response in sections:
        for asset in response.trusted_assets:
            if asset not in assets:
                assets.append(asset)

    intro = (
        f"I ran a {len(sections)}-part live analysis across the governed assets "
        f"({', '.join(assets)}). Each section below is Genie's own governed answer — "
        "its generated SQL for every section is in the proof drawer — assembled "
        "here into one lender briefing."
    )
    body_parts = [intro]
    for title, response in sections:
        body_parts.append(f"**{title}**\n\n{(response.answer or '').strip()}")
    answer = "\n\n".join(body_parts)

    # The action table comes from the ranking section when it survived,
    # otherwise the largest data-bearing section.
    anchor = next(
        (resp for title, resp in sections if title == "Who to act on first"),
        max((resp for _, resp in sections), key=lambda r: r.row_count or 0),
    )

    trace = [
        GenieReasoningStep(
            kind="orchestrate",
            content=(
                f"Decomposed the full-analysis ask into {len(FULL_ANALYSIS_SWEEP)} "
                "themed live Genie analyses; every figure comes from Genie's own "
                "governed SQL."
            ),
        )
    ]
    for title, response in sections:
        cited = ", ".join(response.trusted_assets) or "the trusted assets"
        trace.append(
            GenieReasoningStep(
                kind="live",
                content=f"'{title}' answered live over {cited}.",
            )
        )
    for _, response in sections:
        if response.proof is not None:
            for gap in response.proof.known_data_gaps:
                if gap not in gaps:
                    gaps.append(gap)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    proof = GenieProof(
        source_assets=assets,
        row_count=anchor.row_count or 0,
        trusted=all(
            resp.proof is not None and resp.proof.trusted for _, resp in sections
        ),
        filters=[],
        known_data_gaps=gaps[:12],
        conversation_id=anchor.conversation_id or None,
        message_id=anchor.message_id,
        elapsed_ms=elapsed_ms,
        generated_at=anchor.proof.generated_at if anchor.proof is not None else None,
        sql_query=_labeled_sql(sections),
        reasoning_trace=trace,
    )
    return GenieMessageResponse(
        conversation_id=anchor.conversation_id or "",
        message_id=anchor.message_id,
        elapsed_ms=elapsed_ms,
        question=question,
        question_hash=_genie_question_hash(question),
        answer=answer,
        source="genie",
        trusted_assets=assets,
        sql_query=_labeled_sql(sections),
        row_count=anchor.row_count or 0,
        proof=proof,
        visualization=anchor.visualization,
        actions=anchor.actions,
        table_rows=anchor.table_rows,
        follow_up_questions=default_follow_up_questions(),
        reasoning_trace=trace,
    )

