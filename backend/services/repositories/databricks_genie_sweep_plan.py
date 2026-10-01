"""The deep-research planner: Genie decomposes a broad question into sub-questions.

Moved verbatim out of ``databricks_genie_sweep`` (audit 2026-09-21 W5b,
critique correction 18) so the sweep module stays under the 900-line gate
before ``run_planned_sweep`` is restructured. Every name here is re-exported
from ``databricks_genie_sweep`` so existing importers keep working. Planned
questions and titles are model-authored text: each one is screened by the
router's guard battery (``_planned_question_guard_hit``) and dropped, with a
disclosure, on any hit. No regex here changed in the move.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from backend.api import genie_guardrails as prompt_guardrails
from backend.services.genie_message_policy import (
    identity_prompt_match,
    protected_prompt_match,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.services.repositories.databricks_genie import (
        DatabricksGenieRepository,
    )

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

# A planned line is one numbered or bulleted sentence. The upper bound only
# rejects runaway text; it must not select which sub-questions survive. Live
# replay 2026-09-08: with the cap at 240 characters the deep planner's three
# LONGEST lines — the ranked shortlist with its signal columns, that cohort's
# comparison with the population, and its offer mix with the signals behind
# each offer — were dropped by the parser before the guard ever saw them
# (they ran 250-430 characters), leaving the plan on or under the deep floor
# and aborting the sweep to a single-screen answer. Every surviving line still
# re-enters the full guard battery, so a longer line is more screened text,
# not less. The router accepts prompts to 4,000 characters; 1,500 leaves the
# runaway bound well inside that.
_PLAN_LINE_MAX_CHARS = 1_500
_PLAN_LINE_RE = re.compile(
    r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(.{10," + str(_PLAN_LINE_MAX_CHARS) + r"})\s*$"
)

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
    # Per-item rationale. "What makes each one a strong candidate" and "why
    # does each rank where it does" are the same ask as "why each" — the
    # demo-question screen 2026-09-08 ran the VP's top-candidates question as
    # a single turn because only the bare "why each" form was recognised.
    re.compile(
        r"\b(?:why\s+each|why\s+every|rationale|justif|reasoning|explain\s+why|"
        r"evaluate\s+why|what\s+makes\s+(?:each|every|them|these|those)|"
        r"why\s+(?:does|do|is|are|did)\s+(?:each|every|they|these|those))\b",
        re.IGNORECASE,
    ),
    # Offer recommendation: an adjective-qualified offer, or the direct
    # "which offer should we make/recommend" call.
    re.compile(
        r"\b(?:(?:best|ideal|right|optimal|recommended?|curated)\s+(?:\w+\s+)?offers?|"
        r"(?:which|what)\s+offers?\s+(?:should|would|could|do|we)\b|"
        r"offers?\s+(?:for|to)\s+each)\b",
        re.IGNORECASE,
    ),
    # Comparative / portfolio context, including the participle and
    # "relative to / rest of the book" forms of the same comparison.
    re.compile(
        r"\b(?:compar(?:e|ed|es|ison)|versus|vs\.?|stand\s+out|against\s+the|"
        r"percentile|relative\s+to|"
        r"(?:rest|remainder)\s+of\s+the\s+(?:portfolio|book|population|coverage)|"
        r"(?:across|over)\s+the\s+(?:entire|whole)?\s*(?:portfolio|book|population))\b",
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
        "line per question and no preamble or closing text. Format every line "
        "as `Title — question`: the Title is a section heading of at most six "
        "plain words a lending executive would use (for example `Market size "
        "by state — How many marketable borrowers are in each state?`)."
    )


# `Title — question`. The planner is asked for an em dash; a colon is accepted
# because models drift to it. A hyphen is NOT a separator (in-the-money,
# rate-and-term) — a line without a recognised separator is all question.
_PLAN_TITLE_RE = re.compile(r"^(?P<title>[^:—]{3,80}?)\s*[—:]\s+(?P<question>.{10,})$")
_MAX_TITLE_WORDS = 8


def _split_planned_line(candidate: str) -> tuple[str | None, str]:
    match = _PLAN_TITLE_RE.match(candidate)
    if not match:
        return None, candidate
    title = match.group("title").strip().strip("\"'*_`")
    question = match.group("question").strip()
    if not title or len(title.split()) > _MAX_TITLE_WORDS or not question:
        return None, candidate
    return title, question


def _parse_planned_items(
    text: str | None, *, deep: bool = False
) -> list[tuple[str | None, str]]:
    """(title, question) per planned line; title is None when the line had none."""

    if not text:
        return []
    # The planner's own "this is not an analytics request" verdict. Nothing
    # here inspects the USER's wording — the live space decides, and the
    # normal single-turn path then answers "help"-style prompts directly
    # instead of burning a seven-turn sweep on them.
    if "NO_PLAN" in text.upper():
        return []
    planned: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = _PLAN_LINE_RE.match(line)
        if not match:
            continue
        candidate = match.group(1).strip().strip("\"'")
        if not candidate:
            continue
        title, question = _split_planned_line(candidate)
        if not question.endswith("?"):
            question = f"{question}?"
        if question in seen:
            continue
        seen.add(question)
        planned.append((title, question))
    return planned[: (_MAX_PLANNED_DEEP if deep else _MAX_PLANNED)]


def _parse_planned_questions(text: str | None, *, deep: bool = False) -> list[str]:
    return [question for _, question in _parse_planned_items(text, deep=deep)]


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

    planned_items, dropped = plan_sub_analyses(repo, question, deep=deep)
    return [question_text for _, question_text in planned_items], dropped


def plan_sub_analyses(
    repo: DatabricksGenieRepository,
    question: str,
    *,
    deep: bool = False,
) -> tuple[list[tuple[str | None, str]], list[str]]:
    """Titled decomposition: (title, question) per surviving planned line.

    The title is planner-authored model text and is screened with the same
    battery as the question it heads; a title that trips the screen drops the
    whole line.
    """

    try:
        planning_turn = repo.ask_raw(_planning_prompt(question, deep=deep))
    except Exception:  # noqa: BLE001 - planner failure falls through honestly
        return [], []
    planned: list[tuple[str | None, str]] = []
    dropped: list[str] = []
    for title, candidate in _parse_planned_items(planning_turn, deep=deep):
        hit = _planned_question_guard_hit(candidate) or (
            _planned_question_guard_hit(title) if title else None
        )
        if hit is not None:
            dropped.append(
                "One planned sub-analysis used selection vocabulary outside the "
                f"reviewed set ({hit}) and was not executed."
            )
            continue
        planned.append((title, candidate))
    return planned, dropped
