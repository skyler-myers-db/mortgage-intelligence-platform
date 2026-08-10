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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
_SWEEP_MAX_WORKERS = 8
# Sub-analyses are deliberately deeper than one screen: measured live
# 2026-08-10, the cross-population scans took 82s, 94s and 120s while the
# shallow ones took ~40s. The interactive 45s poll deadline therefore
# dropped precisely the deepest sections, leaving too few to ship and
# aborting the sweep — the user saw a single-screen answer instead.
_SWEEP_POLL_TIMEOUT_S = 180
# Databricks Apps returns 504 at ~300s, so the sweep must finish INSIDE
# that or the user gets a gateway error instead of an answer (live persona
# probe 2026-08-10 timed out at 300.7s once deep routing widened). Ship
# whatever sections completed within the budget and disclose the rest as
# gaps — the floor still decides whether the sweep is worth shipping.
_SWEEP_WALL_BUDGET_S = 200.0

_MIN_PLANNED = 3
_MAX_PLANNED = 7
# Deep plans ask for MORE candidates than the floor needs. The planner
# rewords every run, so a phrasing that clears the plan-time guard on one
# run can trip it on the next; live runs 2026-08-10 lost 2-3 of 7 and
# landed exactly ON the floor of 5, where one more drop aborts the sweep.
# Over-planning buys margin without touching the guard — which cannot be
# relaxed here, since the sweep calls respond() directly and so bypasses
# the router's guard battery.
_MAX_PLANNED_DEEP = 10
# Deep-analysis asks get the larger plan floor: a shortlist + per-item why +
# offer call cannot be told in three queries.
_MIN_PLANNED_DEEP = 5

_PLAN_LINE_RE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(.{10,240})\s*$")

# Closed signals for "this question demands a multi-part deep analysis".
# Live capture 2026-08-08: a top-borrowers/why-each/best-offer ask ran as ONE
# governed SQL turn — the same single query any app screen runs — because the
# planner only engaged as a policy-blocked rescue. Two or more distinct
# analytic parts (or an explicit depth request) route to the planner first.
_DEPTH_EXPLICIT_RE = re.compile(
    r"\b(?:deep|comprehensive|complete|thorough|full|in[- ]depth|end[- ]to[- ]end)\b"
    r".{0,40}\b(?:analysis|analyz|analys|review|dive|assessment|picture|study|"
    r"read|breakdown|rundown|overview|look|investigation|investigat|"
    r"examination|examin|audit|exploration|explor|teardown|interrogat)",
    re.IGNORECASE,
)
_DEPTH_PART_RES: tuple[re.Pattern[str], ...] = (
    # Ranked shortlist.
    re.compile(
        r"\b(?:top|best|strongest|highest[- ]potential|most\s+promising|rank|curated?\s+list)\b",
        re.IGNORECASE,
    ),
    # Per-item rationale.
    re.compile(
        r"\b(?:why\s+each|why\s+every|rationale|justif|reasoning|explain\s+why|"
        r"evaluate\s+why)\b",
        re.IGNORECASE,
    ),
    # Offer recommendation.
    re.compile(
        r"\b(?:best|ideal|right|optimal|recommended?|curated)\s+(?:\w+\s+)?offers?\b",
        re.IGNORECASE,
    ),
    # Comparative / portfolio context.
    re.compile(
        r"\b(?:compare|versus|vs\.?|stand\s+out|against\s+the|percentile|"
        r"across\s+the\s+(?:entire\s+)?(?:portfolio|book|population))\b",
        re.IGNORECASE,
    ),
)


def is_deep_analysis_request(question: str) -> bool:
    """True when the ask is inherently multi-part (shortlist + why + offer)."""

    if _DEPTH_EXPLICIT_RE.search(question):
        return True
    parts = sum(1 for pattern in _DEPTH_PART_RES if pattern.search(question))
    return parts >= 2


def _planning_prompt(question: str, *, deep: bool = False) -> str:
    if deep:
        # The angles named here are decomposition COVERAGE hints — the live
        # space still authors the plan, phrases each sub-question, and can
        # substitute angles its assets answer better. Nothing here injects
        # criteria; every sub-question re-enters the full guard battery.
        angle_guidance = (
            "This is a deep-analysis request, so the plan must go materially "
            "beyond a single ranked list. Cover, in the sub-questions YOU "
            "write: (a) the ranked cohort itself with its governed score and "
            "the underlying signal columns (rate spread, equity, triggers); "
            "(b) how those top borrowers compare with the whole eligible "
            "population on the same measures (averages or percentiles, so "
            "'why these' is provable); (c) the recommended-offer mix for the "
            "cohort and the signals behind each offer; (d) at least one "
            "concentration or co-occurrence angle (geography, segments, "
            "competitor liens, listing status) that a single screen would "
            "not show. "
            f"Plan between {_MIN_PLANNED_DEEP + 2} and {_MAX_PLANNED_DEEP} "
            "questions.\n\n"
        )
        count_line = ""
    else:
        angle_guidance = ""
        count_line = f"between {_MIN_PLANNED} and {_MAX_PLANNED} of them, "
    return (
        "Plan, do not query: for this message only, do not generate SQL and do "
        "not execute anything. The user asked a broad question that cannot be "
        "answered by a single SQL statement:\n\n"
        f'"{question}"\n\n'
        "If this is not an analytics request at all — a greeting, a request "
        "for help using the product, or unintelligible input — reply with "
        "exactly NO_PLAN and nothing else.\n\n"
        f"{angle_guidance}"
        "Otherwise break it into the specific analytics questions YOU judge "
        "most useful, "
        "phrased as neutral read-only analytics (prefer 'top borrowers by "
        "opportunity score' over audience-selection wording like 'eligible "
        "for' or 'characteristics of'), "
        f"{count_line}each self-contained "
        "and answerable with one SQL query over your trusted assets. Choose the "
        "angles yourself based on what the question is really asking and which "
        "of your assets can answer it. Reply ONLY with a numbered list, one "
        "question per line, no preamble and no closing text."
    )


def _parse_planned_questions(text: str | None, *, deep: bool = False) -> list[str]:
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
    return planned[: (_MAX_PLANNED_DEEP if deep else _MAX_PLANNED)]


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
    *,
    deep: bool = False,
) -> tuple[list[str], list[str]]:
    """Ask the live space to decompose the question; screen what comes back.

    Returns (planned, dropped_disclosures). Planning failures return ([], []).
    """

    try:
        planning_turn = repo.ask_raw(_planning_prompt(question, deep=deep))
    except Exception:  # noqa: BLE001 - planner failure falls through honestly
        return [], []
    planned_raw = _parse_planned_questions(planning_turn, deep=deep)
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


def _synthesis_prompt(
    question: str,
    sections: list[tuple[str, GenieMessageResponse]],
    *,
    deep: bool = False,
) -> str:
    # Deep syntheses weave per-borrower detail across sections, so each
    # section keeps a larger verified digest to draw from.
    budget = 700 if deep else 400
    digest_lines = []
    for sub_question, response in sections:
        snippet = " ".join((response.answer or "").split())[:budget]
        digest_lines.append(f"- {sub_question} -> {snippet}")
    digest = "\n".join(digest_lines)
    if deep:
        ask = (
            "Write the deep executive synthesis in 8 to 14 sentences, no "
            "headings. It must do four things, each grounded ONLY in numbers "
            "that appear in the results above: name the standout borrowers or "
            "cohort and the figures that put them on top; say why they stand "
            "out RELATIVE to the wider population (use the comparison "
            "numbers); state the offer call and the signal behind it; and end "
            "with the one cross-cutting insight a lender could not read off "
            "any single screen. If the results above do not support one of "
            "these, say so rather than inventing it."
        )
    else:
        ask = (
            "Write the executive synthesis in 4 to 8 sentences: the biggest "
            "cross-cutting insights and what a lender should act on first. Use "
            "ONLY numbers that appear in the results above, and no headings."
        )
    return (
        "Do not generate SQL for this message. Below are the verified results "
        "of the analyses you just ran for the question "
        f'"{question}":\n\n{digest}\n\n'
        f"{ask}"
    )


def _synthesize_closing(
    repo: DatabricksGenieRepository,
    question: str,
    sections: list[tuple[str, GenieMessageResponse]],
    *,
    deep: bool = False,
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
        draft = repo.ask_raw(_synthesis_prompt(question, sections, deep=deep))
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
    *,
    deep: bool = False,
) -> GenieMessageResponse | None:
    """Plan the decomposition live, execute each sub-question live, assemble.

    Returns ``None`` when the plan cannot be formed or fewer than three
    sub-analyses produce governed content — the caller then continues the
    normal single-turn pipeline, which fails honestly. ``deep`` widens the
    plan floor and the synthesis contract for deep-analysis asks; the
    section floor for shipping stays ``_MIN_PLANNED`` so a partly-failed
    deep run still ships its surviving sections with disclosed gaps.
    """

    started = time.monotonic()
    planned, dropped = plan_sub_questions(repo, question, deep=deep)
    if len(planned) < (_MIN_PLANNED_DEEP if deep else _MIN_PLANNED):
        return None

    def _one(sub_question: str) -> GenieMessageResponse | None:
        try:
            return repo.respond(
                sub_question,
                allow_sweep=False,
                poll_timeout_s=_SWEEP_POLL_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 - a failed theme becomes a disclosed gap
            return None

    results: list[GenieMessageResponse | None] = [None] * len(planned)
    with ThreadPoolExecutor(max_workers=_SWEEP_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_one, sub_question): index
            for index, sub_question in enumerate(planned)
        }
        pending = set(futures)
        budget_end = started + _SWEEP_WALL_BUDGET_S
        while pending:
            remaining = budget_end - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    results[futures[future]] = future.result()
                except Exception:  # noqa: BLE001 - becomes a disclosed gap
                    results[futures[future]] = None
        for future in pending:
            future.cancel()

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
    synthesis, synthesis_gap = _synthesize_closing(repo, question, sections, deep=deep)
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
                (
                    "This is a deep-analysis request, so the live space planned "
                    f"its own decomposition first: {len(planned)} sub-analyses, "
                    "each executed as its own governed turn."
                )
                if deep
                else (
                    "The broad question produced no single governed query, so the "
                    f"live space planned its own decomposition: {len(planned)} "
                    "sub-analyses, each executed as its own governed turn."
                )
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
