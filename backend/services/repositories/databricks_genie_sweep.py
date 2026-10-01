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

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Any, Literal

from backend.services.genie_answers import (
    GenieAnswerSection,
    GenieMessageResponse,
    GenieProof,
    GenieReasoningStep,
    default_follow_up_questions,
)
from backend.services.genie_completion_stages import (
    GenieJobStage,
    report_sections,
    report_stage,
)
from backend.services.observability import emit
from backend.services.repositories.databricks_genie_sweep_plan import (  # noqa: F401 - re-exported by name
    _DEPTH_EXPLICIT_RE,
    _DEPTH_PART_RES,
    _MAX_PLANNED,
    _MAX_PLANNED_DEEP,
    _MAX_TITLE_WORDS,
    _MIN_PLANNED,
    _MIN_PLANNED_DEEP,
    _PLAN_LINE_MAX_CHARS,
    _PLAN_LINE_RE,
    _PLAN_TITLE_RE,
    _parse_planned_items,
    _parse_planned_questions,
    _planned_question_guard_hit,
    _planning_prompt,
    _split_planned_line,
    is_deep_analysis_request,
    plan_sub_analyses,
    plan_sub_questions,
)
from backend.services.repositories.databricks_genie_trust import _genie_question_hash

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.services.repositories.databricks_genie import (
        DatabricksGenieRepository,
    )

# Sweep observability. Both shipping gates used to be silent: an aborted
# sweep fell through to the single-turn answer with nothing in the log to
# say a plan was attempted, how many lines survived the screen, or why a
# section was omitted (live persona probe 2026-08-10; live demo probe
# 2026-09-08). Every event here carries labels and hashes only — never a
# planned question's text, a narrative, or a row.
log = logging.getLogger("mip-genie-sweep")

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
    """A section ships when it carries prose or rows a reader can use.

    A withheld draft no longer renders as a status line: the adapter writes
    a plain-language digest of the verified rows (and, live-first, asks
    Genie for a verified rewrite first), so a section with rows is content
    even when its first draft failed verification. Only the legacy plumbing
    markers — kept for older responses — still disqualify prose.
    """

    answer = (response.answer or "").strip()
    if answer:
        lowered = answer.lower()
        if not any(marker in lowered for marker in _PLUMBING_ANSWER_MARKERS):
            return True
    return bool(response.table_rows)


def _section_is_renderable(title: str | None, response: GenieMessageResponse) -> bool:
    """Run the router's visible-text scan on ONE section before it is composed.

    Every section now renders its own rows and chart labels, so the router
    scans all of them. Scanning only at the router meant one unsafe cell in
    one section (live 2026-09-08: a place value colliding with a protected
    term) blocked the whole seven-part answer as ``policy_blocked``. The same
    fail-closed scan applied per section drops just that section, disclosed
    as a gap; the router scan stays as the final backstop.
    """

    from backend.services.genie_message_policy import (
        _without_allowed_literals,
        genie_response_has_unsafe_visible_text,
        genie_visible_text_unsafe,
        governed_row_literals,
    )

    literals = governed_row_literals(response.table_rows)
    if title and genie_visible_text_unsafe(_without_allowed_literals(title, literals)):
        return False
    if genie_visible_text_unsafe(_without_allowed_literals(response.question, literals)):
        return False
    return not genie_response_has_unsafe_visible_text(response)


_SectionVerdict = Literal["ship", "unsafe", "no_content"]


def _section_verdict(title: str | None, response: GenieMessageResponse | None) -> _SectionVerdict:
    """The shipping verdict the post-loop used to compute, now per result.

    ``ship``: governed content that passed the section's own visible-text
    scan; ``unsafe``: governed content the scan withheld; ``no_content``:
    no result, a non-data-bearing source or nothing a reader can use.
    """

    if (
        response is not None
        and response.source in _DATA_BEARING_SOURCES
        and _has_rendered_prose(response)
    ):
        return "ship" if _section_is_renderable(title, response) else "unsafe"
    return "no_content"


def _narrative_was_withheld(response: GenieMessageResponse) -> bool:
    proof = response.proof
    if proof is None:
        return False
    return any(
        "withheld" in gap.lower() and "narrative" in gap.lower()
        for gap in proof.known_data_gaps
    )

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
            "numbers); state the recommended offer and the signal behind it; "
            "and end with the one cross-cutting insight a lender could not read off "
            "any single screen. If the results above do not support one of "
            "these, say so rather than inventing it. Copy every figure exactly "
            "as it appears above — never add, subtract, average, convert or "
            "turn figures into percentages; compare in words instead. Write "
            "for a lending executive: no table, column or query names, and no "
            "description of how the analysis was run. Describe priorities and "
            "recommended offers; never use the words call, target, contact or "
            "reach out, and never describe outreach."
        )
    else:
        ask = (
            "Write the executive synthesis in 4 to 8 sentences: the biggest "
            "cross-cutting insights and what a lender should act on first. Use "
            "ONLY numbers that appear in the results above, copied exactly — "
            "never add, subtract, average, convert or turn them into "
            "percentages; compare in words instead — and no headings. Write for "
            "a lending executive: no table, column or query names, and no "
            "description of how the analysis was run. Describe priorities and "
            "recommended offers; never use the words call, target, contact or "
            "reach out, and never describe outreach."
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

    from backend.services.genie_message_policy import (
        _without_allowed_literals,
        genie_visible_text_unsafe,
        governed_row_literals,
    )
    from backend.services.repositories.databricks_genie_numeric import (
        _unsupported_answer_numeric_claims,
    )

    combined_rows = [row for _, resp in sections for row in (resp.table_rows or [])]
    literals = governed_row_literals(combined_rows)
    try:
        draft = repo.ask_raw(_synthesis_prompt(question, sections, deep=deep))
    except Exception:  # noqa: BLE001 - synthesis is additive, never blocking
        return None, None
    draft = (draft or "").strip()
    if not draft:
        return None, None
    if _unsupported_answer_numeric_claims(draft, combined_rows, question):
        # Live-first repair: hand Genie the verified figures and ask once for
        # a rewrite, then verify the rewrite exactly like the first draft.
        try:
            retry = repo.ask_raw(_synthesis_repair_prompt(question, combined_rows))
        except Exception:  # noqa: BLE001 - the retry is additive too
            retry = None
        draft = (retry or "").strip()
        if not draft or _unsupported_answer_numeric_claims(draft, combined_rows, question):
            return None, (
                "A cross-section synthesis draft was omitted: it carried numbers "
                "the verified section results could not support."
            )
    if genie_visible_text_unsafe(_without_allowed_literals(draft, literals)):
        # Live 2026-09-08: the guard refused an ordinary synthesis on "the
        # cleanest product call because…". One guard-aware rewrite, verified
        # like the first draft; a second hit omits the synthesis for real.
        try:
            reworded = repo.ask_raw(
                _synthesis_repair_prompt(question, combined_rows, reason="wording")
            )
        except Exception:  # noqa: BLE001 - the retry is additive too
            reworded = None
        draft = (reworded or "").strip()
        if (
            not draft
            or _unsupported_answer_numeric_claims(draft, combined_rows, question)
            or genie_visible_text_unsafe(_without_allowed_literals(draft, literals))
        ):
            return None, (
                "A cross-section synthesis draft was withheld by the output "
                "safety guard."
            )
    return draft, None


# The rewrite digest must cover EVERY row the synthesis is verified against:
# a figure quoted from a row outside the digest is unsupported by
# construction (live 2026-09-08: seven sections, ~100 rows, a 40-row digest).
_SYNTHESIS_REPAIR_MAX_ROWS = 160
_SYNTHESIS_REPAIR_MAX_COLS = 8


def _synthesis_repair_prompt(
    question: str, rows: list[dict[str, object]], *, reason: str = "figure"
) -> str:
    lines: list[str] = []
    for row in rows[:_SYNTHESIS_REPAIR_MAX_ROWS]:
        cells = [
            f"{column.replace('_', ' ')}: {value}"
            for column, value in list(row.items())[:_SYNTHESIS_REPAIR_MAX_COLS]
            if value is not None
        ]
        if cells:
            lines.append("- " + "; ".join(cells))
    digest = "\n".join(lines)
    cause = (
        "used a figure that is not in the verified results"
        if reason == "figure"
        else "used wording the compliance filter rejects"
    )
    return (
        "Do not generate SQL for this message. Your synthesis for the question "
        f'"{question}" {cause}. '
        "Rewrite it in 6 to 12 sentences for a lending executive. Quote at "
        "most eight figures, each copied exactly from the list below — never "
        "add, subtract, average, round, convert or turn figures into "
        "percentages; compare in words instead. No table, column or query "
        "names, no headings, no mention of this instruction, never the words "
        "call, target, contact or reach out, never a person's name, and no "
        "asterisks around words.\n\n"
        f"Verified figures:\n{digest}"
    )


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


def _answer_section(
    title: str | None, sub_question: str, response: GenieMessageResponse
) -> GenieAnswerSection:
    """One shipped sub-analysis as it renders; the reveal and the final
    composition both build it here, so a revealed section IS the final one."""

    return GenieAnswerSection(
        title=title or sub_question,
        question=sub_question,
        answer=(response.answer or "").strip(),
        trusted_assets=list(response.trusted_assets),
        sql_query=response.sql_query,
        row_count=response.row_count,
        table_rows=response.table_rows,
        visualization=response.visualization,
        narrative_withheld=_narrative_was_withheld(response),
    )


def _report_verified_sections(
    planned: list[str],
    titles: dict[str, str | None],
    results: list[GenieMessageResponse | None],
    verdicts: list[_SectionVerdict],
) -> None:
    """Report every section judged ``ship`` so far, in PLAN order (genie-01
    phase 1b). Never a section before its verdict; never the synthesis."""

    snapshot: list[dict[str, Any]] = []
    for index, (sub_question, response) in enumerate(zip(planned, results, strict=True)):
        if verdicts[index] == "ship" and response is not None:
            section = _answer_section(titles.get(sub_question), sub_question, response)
            snapshot.append({"index": index, **section.model_dump(mode="json")})
    report_sections(snapshot)


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
    report_stage(GenieJobStage.PLANNING)
    # A second sweep in the same turn (the outcome-triggered planner after a
    # policy_blocked turn) starts its reveal from nothing.
    report_sections([])
    planned_items, dropped = plan_sub_analyses(repo, question, deep=deep)
    planned = [question_text for _, question_text in planned_items]
    titles = {question_text: title for title, question_text in planned_items}
    plan_floor = _MIN_PLANNED_DEEP if deep else _MIN_PLANNED
    emit(
        log,
        "genie_sweep_plan",
        dependency="genie",
        outcome="planned" if len(planned) >= plan_floor else "aborted_plan_floor",
        duration_ms=round((time.monotonic() - started) * 1000, 1),
        question_hash=_genie_question_hash(question),
        deep=deep,
        planned=len(planned),
        dropped=len(dropped),
        floor=plan_floor,
    )
    if len(planned) < plan_floor:
        return None
    report_stage(GenieJobStage.RESEARCHING, 0, len(planned))

    def _one(sub_question: str) -> GenieMessageResponse | None:
        turn_started = time.monotonic()
        try:
            response = repo.respond(
                sub_question,
                allow_sweep=False,
                poll_timeout_s=_SWEEP_POLL_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - a failed theme becomes a disclosed gap
            emit(
                log,
                "genie_sweep_section",
                dependency="genie",
                outcome="error",
                duration_ms=round((time.monotonic() - turn_started) * 1000, 1),
                section_hash=_genie_question_hash(sub_question),
                error_type=type(exc).__name__,
            )
            return None
        emit(
            log,
            "genie_sweep_section",
            dependency="genie",
            outcome=response.source,
            duration_ms=round((time.monotonic() - turn_started) * 1000, 1),
            section_hash=_genie_question_hash(sub_question),
            data_bearing=response.source in _DATA_BEARING_SOURCES,
            rendered_prose=_has_rendered_prose(response),
            row_count=response.row_count or 0,
        )
        return response

    results: list[GenieMessageResponse | None] = [None] * len(planned)
    # Judged as each result is collected, so a section is revealed once it
    # has passed its own checks; the post-loop reads these verdicts.
    verdicts: list[_SectionVerdict] = ["no_content"] * len(planned)
    with ThreadPoolExecutor(max_workers=_SWEEP_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_one, sub_question): index
            for index, sub_question in enumerate(planned)
        }
        pending = set(futures)
        budget_end = started + _SWEEP_WALL_BUDGET_S
        try:
            while pending:
                remaining = budget_end - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                collected = [futures[future] for future in done]
                for future in done:
                    try:
                        results[futures[future]] = future.result()
                    except Exception:  # noqa: BLE001 - becomes a disclosed gap
                        results[futures[future]] = None
                # The stage report (a cancel point) still comes first, so a
                # stopped sweep is never delayed by judging what it collected.
                report_stage(GenieJobStage.RESEARCHING, len(planned) - len(pending), len(planned))
                for index in collected:
                    verdicts[index] = _section_verdict(titles.get(planned[index]), results[index])
                if any(verdicts[index] == "ship" for index in collected):
                    _report_verified_sections(planned, titles, results, verdicts)
        finally:
            # Also when a stage report raises the owner's cancel: sub-turns
            # that have not started never start.
            for future in pending:
                future.cancel()

    sections: list[tuple[str, GenieMessageResponse]] = []
    gaps: list[str] = list(dropped)
    unfinished = 0
    withheld_by_guard = 0
    for index, (sub_question, response) in enumerate(zip(planned, results, strict=True)):
        if response is not None and verdicts[index] != "no_content":
            if verdicts[index] == "unsafe":
                withheld_by_guard += 1
                emit(
                    log,
                    "genie_sweep_section",
                    dependency="genie",
                    outcome="unsafe_visible_text",
                    section_hash=_genie_question_hash(sub_question),
                    row_count=response.row_count or 0,
                )
                gaps.append(
                    "One planned section was withheld by the output safety guard "
                    "and was omitted; the other sections are unaffected."
                )
                continue
            sections.append((sub_question, response))
        else:
            if response is None:
                unfinished += 1
            gaps.append(
                f"The planned analysis '{sub_question}' returned no governed "
                "result on this run and was omitted."
            )
    emit(
        log,
        "genie_sweep_result",
        dependency="genie",
        outcome="shipped" if len(sections) >= _MIN_PLANNED else "aborted_section_floor",
        duration_ms=round((time.monotonic() - started) * 1000, 1),
        question_hash=_genie_question_hash(question),
        deep=deep,
        planned=len(planned),
        sections=len(sections),
        omitted=len(planned) - len(sections),
        unfinished=unfinished,
        withheld_by_guard=withheld_by_guard,
        floor=_MIN_PLANNED,
    )
    if len(sections) < _MIN_PLANNED:
        return None

    assets: list[str] = []
    for _, response in sections:
        for asset in response.trusted_assets:
            if asset not in assets:
                assets.append(asset)

    # Business-facing composition: the verified summary leads, then one
    # titled section per sub-analysis, each carrying its own rows and chart.
    # How the answer was produced (the plan, the per-section governed SQL) is
    # disclosed in the process trace and the proof drawer, not in the body
    # (user feedback 2026-09-08: the method preamble read as internal
    # pipeline thoughts to a business reader).
    report_stage(GenieJobStage.SYNTHESIZING)
    synthesis, synthesis_gap = _synthesize_closing(repo, question, sections, deep=deep)
    if synthesis_gap:
        gaps.append(synthesis_gap)
    answer_sections = [
        _answer_section(titles.get(sub_question), sub_question, response)
        for sub_question, response in sections
    ]
    body_parts: list[str] = []
    if synthesis:
        body_parts.append(f"**Summary**\n\n{synthesis}")
    for section in answer_sections:
        body_parts.append(f"**{section.title}**\n\n{section.answer}")
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
        summary=synthesis,
        sections=answer_sections,
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
