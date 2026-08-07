"""Agentic decomposition: Genie plans and executes its own analysis sweep.

Some questions ("do a full analysis on all of the data") can never be one SQL
statement. Nothing here decides that with keywords: the trigger is behavioral
— the live turn (and its one-shot repair) came back without SQL proof — and
the decomposition itself is planned BY GENIE, fresh for each question. The
planner turn asks the governed space to break the user's request into
self-contained analytics questions over its own trusted assets; every planned
sub-question is screened by the same prompt guard battery the router applies
(planned questions are model-authored text), then executed as its own live
turn through the complete policy pipeline (SQL trust, claims verification, PII
redaction, disclosed rescue). Deterministic code here orchestrates, screens,
and formats; it never authors a question, a figure, or an analytic choice.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from backend.api import genie_guardrails as prompt_guardrails
from backend.services.genie_answers import (
    GenieMessageResponse,
    GenieProof,
    GenieReasoningStep,
    default_follow_up_questions,
)
from backend.services.genie_message_policy import (
    identity_prompt_match,
    protected_prompt_match,
)
from backend.services.repositories.databricks_genie_trust import _genie_question_hash

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.services.repositories.databricks_genie import (
        DatabricksGenieRepository,
    )

# Sources that mean a sub-turn produced governed analytic content.
_DATA_BEARING_SOURCES = frozenset({"genie", "trusted_sql"})

# A turn can be governed and still carry no prose: when the narrative is
# withheld the answer is a status line about the pipeline. Printing that under
# a section heading is worse than omitting the section (live persona audit
# 2026-08-07: 3 of 5 sections read "the draft narrative was withheld").
_PLUMBING_ANSWER_MARKERS = (
    "the draft narrative was withheld",
    "did not pass the governed output policy",
    "did not return trusted sql",
)


def _has_rendered_prose(response: GenieMessageResponse) -> bool:
    answer = (response.answer or "").strip()
    if not answer:
        return False
    lowered = answer.lower()
    return not any(marker in lowered for marker in _PLUMBING_ANSWER_MARKERS)

# Fan-out cap: polite to the Conversation API while keeping wall time near the
# slowest single turn.
_SWEEP_MAX_WORKERS = 4

_MIN_PLANNED = 3
_MAX_PLANNED = 7

_PLAN_LINE_RE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(.{10,240})\s*$")


def _planning_prompt(question: str) -> str:
    return (
        "Plan, do not query: for this message only, do not generate SQL and do "
        "not execute anything. The user asked a broad question that cannot be "
        "answered by a single SQL statement:\n\n"
        f'"{question}"\n\n'
        "If this is not an analytics request at all — a greeting, a request "
        "for help using the product, or unintelligible input — reply with "
        "exactly NO_PLAN and nothing else.\n\n"
        "Otherwise break it into the specific analytics questions YOU judge "
        "most useful, "
        "phrased as neutral read-only analytics (prefer 'top borrowers by "
        "opportunity score' over audience-selection wording like 'eligible "
        "for' or 'characteristics of'), "
        f"between {_MIN_PLANNED} and {_MAX_PLANNED} of them, each self-contained "
        "and answerable with one SQL query over your trusted assets. Choose the "
        "angles yourself based on what the question is really asking and which "
        "of your assets can answer it. Reply ONLY with a numbered list, one "
        "question per line, no preamble and no closing text."
    )


def _parse_planned_questions(text: str | None) -> list[str]:
    if not text:
        return []
    # The planner's own "this is not an analytics request" verdict. Nothing
    # here inspects the USER's wording — the live space decides, and the
    # normal single-turn path then answers "help"-style prompts directly
    # instead of burning a seven-turn sweep on them.
    if "NO_PLAN" in text.upper():
        return []
    planned: list[str] = []
    for line in text.splitlines():
        match = _PLAN_LINE_RE.match(line)
        if not match:
            continue
        candidate = match.group(1).strip().strip("\"'")
        if not candidate:
            continue
        if not candidate.endswith("?"):
            candidate = f"{candidate}?"
        if candidate not in planned:
            planned.append(candidate)
    return planned[:_MAX_PLANNED]


def _planned_question_guard_hit(question: str) -> str | None:
    """Screen a model-authored planned question with the router's guard battery.

    Planned questions are model text used as prompts, so they get the same
    treatment user prompts get at the router. Any hit drops the question from
    the plan (disclosed), never executes it.
    """

    if protected_prompt_match(question):
        return "protected-class screen"
    if identity_prompt_match(question):
        return "PII / identity screen"
    if prompt_guardrails.pii_prompt_match(question):
        return "PII screen"
    if prompt_guardrails.instruction_override_prompt_match(question):
        return "instruction-override screen"
    if prompt_guardrails.scope_bypass_prompt_match(question):
        return "scope screen"
    if prompt_guardrails.cross_lender_prompt_match(question):
        return "cross-lender screen"
    return None


def plan_sub_questions(
    repo: DatabricksGenieRepository,
    question: str,
) -> tuple[list[str], list[str]]:
    """Ask the live space to decompose the question; screen what comes back.

    Returns (planned, dropped_disclosures). Planning failures return ([], []).
    """

    try:
        planning_turn = repo.ask_raw(_planning_prompt(question))
    except Exception:  # noqa: BLE001 - planner failure falls through honestly
        return [], []
    planned_raw = _parse_planned_questions(planning_turn)
    planned: list[str] = []
    dropped: list[str] = []
    for candidate in planned_raw:
        hit = _planned_question_guard_hit(candidate)
        if hit is not None:
            dropped.append(
                "One planned sub-analysis used selection vocabulary outside the "
                f"reviewed set ({hit}) and was not executed."
            )
            continue
        planned.append(candidate)
    return planned, dropped


def _synthesis_prompt(question: str, sections: list[tuple[str, GenieMessageResponse]]) -> str:
    digest_lines = []
    for sub_question, response in sections:
        snippet = " ".join((response.answer or "").split())[:400]
        digest_lines.append(f"- {sub_question} -> {snippet}")
    digest = "\n".join(digest_lines)
    return (
        "Do not generate SQL for this message. Below are the verified results "
        "of the analyses you just ran for the question "
        f'"{question}":\n\n{digest}\n\n'
        "Write the executive synthesis in 4 to 8 sentences: the biggest "
        "cross-cutting insights and what a lender should act on first. Use "
        "ONLY numbers that appear in the results above, and no headings."
    )


def _synthesize_closing(
    repo: DatabricksGenieRepository,
    question: str,
    sections: list[tuple[str, GenieMessageResponse]],
) -> tuple[str | None, str | None]:
    """One live Genie turn writes the cross-section synthesis; verify or omit.

    Returns (synthesis, omission_disclosure). The synthesis ships only when it
    passes the same claims verification every narrative gets (numbers checked
    against the union of the sections' returned rows) and the output-text
    guard; otherwise it is omitted with a disclosed gap. Never authored
    server-side.
    """

    from backend.services.genie_message_policy import genie_visible_text_unsafe
    from backend.services.repositories.databricks_genie_numeric import (
        _unsupported_answer_numeric_claims,
    )

    try:
        draft = repo.ask_raw(_synthesis_prompt(question, sections))
    except Exception:  # noqa: BLE001 - synthesis is additive, never blocking
        return None, None
    draft = (draft or "").strip()
    if not draft:
        return None, None
    combined_rows = [row for _, resp in sections for row in (resp.table_rows or [])]
    if _unsupported_answer_numeric_claims(draft, combined_rows, question):
        return None, (
            "A cross-section synthesis draft was omitted: it carried numbers "
            "the verified section results could not support."
        )
    if genie_visible_text_unsafe(draft):
        return None, (
            "A cross-section synthesis draft was withheld by the output "
            "safety guard."
        )
    return draft, None


def _live_follow_ups(sections: list[tuple[str, GenieMessageResponse]]) -> list[str]:
    """Genie's own suggested questions from the live sub-turns (already
    guard-screened at each turn's adaptation); curated defaults only when the
    live turns offered none."""

    merged: list[str] = []
    for _, response in sections:
        for suggestion in response.follow_up_questions:
            if suggestion and suggestion not in merged:
                merged.append(suggestion)
    return merged[:4] if merged else default_follow_up_questions()


def _labeled_sql(sections: list[tuple[str, GenieMessageResponse]]) -> str | None:
    parts: list[str] = []
    for title, response in sections:
        if response.sql_query:
            parts.append(f"-- [{title}]\n{response.sql_query.strip()}")
    return "\n\n".join(parts) if parts else None


def run_planned_sweep(
    repo: DatabricksGenieRepository,
    question: str,
) -> GenieMessageResponse | None:
    """Plan the decomposition live, execute each sub-question live, assemble.

    Returns ``None`` when the plan cannot be formed or fewer than three
    sub-analyses produce governed content — the caller then continues the
    normal single-turn pipeline, which fails honestly.
    """

    started = time.monotonic()
    planned, dropped = plan_sub_questions(repo, question)
    if len(planned) < _MIN_PLANNED:
        return None

    def _one(sub_question: str) -> GenieMessageResponse | None:
        try:
            return repo.respond(sub_question, allow_sweep=False)
        except Exception:  # noqa: BLE001 - a failed theme becomes a disclosed gap
            return None

    with ThreadPoolExecutor(max_workers=_SWEEP_MAX_WORKERS) as pool:
        results = list(pool.map(_one, planned))

    sections: list[tuple[str, GenieMessageResponse]] = []
    gaps: list[str] = list(dropped)
    for sub_question, response in zip(planned, results, strict=True):
        if (
            response is not None
            and response.source in _DATA_BEARING_SOURCES
            and _has_rendered_prose(response)
        ):
            sections.append((sub_question, response))
        else:
            gaps.append(
                f"The planned analysis '{sub_question}' returned no governed "
                "result on this run and was omitted."
            )
    if len(sections) < _MIN_PLANNED:
        return None

    assets: list[str] = []
    for _, response in sections:
        for asset in response.trusted_assets:
            if asset not in assets:
                assets.append(asset)

    intro = (
        f"I asked the governed space to plan this request itself; it decomposed "
        f"the question into {len(planned)} sub-analyses and answered each with "
        f"its own governed SQL over {', '.join(assets)}. Every section below is "
        "Genie's own answer — the generated SQL for each is in the proof drawer."
    )
    body_parts = [intro]
    for sub_question, response in sections:
        body_parts.append(f"**{sub_question}**\n\n{(response.answer or '').strip()}")
    synthesis, synthesis_gap = _synthesize_closing(repo, question, sections)
    if synthesis:
        body_parts.append(f"**What this adds up to**\n\n{synthesis}")
    elif synthesis_gap:
        gaps.append(synthesis_gap)
    answer = "\n\n".join(body_parts)

    anchor = max((resp for _, resp in sections), key=lambda r: r.row_count or 0)

    trace = [
        GenieReasoningStep(
            kind="orchestrate",
            content=(
                "The broad question produced no single governed query, so the live "
                f"space planned its own decomposition: {len(planned)} "
                "sub-analyses, each executed as its own governed turn."
            ),
        )
    ]
    for _, response in sections:
        cited = ", ".join(response.trusted_assets) or "the trusted assets"
        trace.append(
            GenieReasoningStep(
                kind="live",
                content=f"Planned analysis answered live over {cited}.",
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
        follow_up_questions=_live_follow_ups(sections),
        reasoning_trace=trace,
    )
