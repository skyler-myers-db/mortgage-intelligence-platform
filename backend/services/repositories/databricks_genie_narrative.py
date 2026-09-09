"""Narrative shaping for governed Genie answers.

Single responsibility: turn verified rows and a live Genie narrative into the
prose the product shows -- cell and column formatting, the factual row
summary, the narrative-repair prompt, source citation, the verification note,
and the contradiction check that decides whether Genie's own voice can be
restored. Nothing here executes SQL or decides policy; it only renders and
checks text against numbers that were already verified.
"""
from __future__ import annotations

import re
from typing import Any

from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_client import GenieResponse
from backend.services.repositories.databricks_genie_numeric import (
    _unsupported_answer_numeric_claims,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    genie_follow_up_questions,
    genie_native_visualization,
    genie_reasoning_trace_from_thoughts,
)
from backend.services.repositories.databricks_genie_trace import (
    WITHHELD_CONTRADICTED,
    WITHHELD_NO_NARRATIVE_DETERMINISTIC,
    WITHHELD_UNSAFE_TEXT,
    WITHHELD_UNVERIFIED_NUMBERS,
    GenieProcessTrace,
)
from backend.services.repositories.databricks_genie_visualization import _is_genie_identifier_column

_SOURCE_LINE_RE = re.compile(
    r"(?im)^\s*source\s*:\s*`?[A-Za-z_][\w-]*\.[A-Za-z_]\w*\.[A-Za-z_]\w*`?\.?\s*$"
)

# A citation Genie wrote INLINE at the end of a paragraph, or one naming
# several assets ("Source: mip.gold.borrower_360, mip.gold.evidence_events"),
# never matched the own-line pattern above — so the app appended a second,
# narrower line. That printed a duplicate "Source:" on 7 of 24 audited answers
# and silently dropped the evidence table from a multi-asset citation
# (live persona audit 2026-08-07).
_INLINE_SOURCE_RE = re.compile(
    r"(?i)source\s*:\s*`?[A-Za-z_][\w-]*\.[A-Za-z_]\w*\.[A-Za-z_]\w*"
)


def _format_cell(value: object, column: str = "") -> str:
    """Render one governed cell for the factual fallback summary.

    Identifier-shaped columns (zip, year, ids) are rendered verbatim: a ZIP
    thousands-separated as "75,040" is wrong on screen and reads as a measure.
    """

    if column and (
        _is_genie_identifier_column(column)
        or column.strip().lower() in _VERBATIM_CELL_COLUMNS
    ):
        return str(value).strip()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    try:  # numeric strings arrive from the SQL layer as text
        numeric = float(text)
    except (TypeError, ValueError):
        return text
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.2f}"


# Columns rendered verbatim in the factual fallback: codes and years are
# identifiers, not quantities.
_VERBATIM_CELL_COLUMNS = frozenset(
    {
        "zip",
        "zipcode",
        "zip_code",
        "postal_code",
        "county_fips_5",
        "situs_cbsa_code",
        "cbsa_code",
        "msa_cbsa_code",
        "year",
        "origination_year",
        "snapshot_date",
        "refreshed_at",
        "state",
    }
)


def _humanize_column(column: str) -> str:
    return column.replace("_", " ").strip()


_UNIT_SUFFIXES = (
    ("_bps", " bps"),
    ("_pct", "%"),
    ("_percent", "%"),
    ("_percentage", "%"),
)


def _plain_label(column: str) -> tuple[str, str]:
    """(business label, unit suffix) for a result column name."""

    lower = column.lower()
    suffix = ""
    for marker, unit in _UNIT_SUFFIXES:
        if lower.endswith(marker):
            lower = lower[: -len(marker)]
            suffix = unit
            break
    words = lower.replace("_", " ").split()
    replacements = {"avg": "average", "pct": "percent", "cnt": "count", "num": "number"}
    words = [replacements.get(word, word) for word in words]
    return (" ".join(words).strip() or column, suffix)


def _plain_pairs(row: dict[str, Any], *, limit: int) -> list[str]:
    pairs: list[str] = []
    for column, value in row.items():
        if value is None:
            continue
        label, unit = _plain_label(column)
        text = _format_cell(value, column)
        if unit == "%" and not text.endswith("%"):
            text = f"{text}%"
        elif unit and not text.endswith(unit):
            text = f"{text}{unit}"
        pairs.append(f"{label} {text}")
        if len(pairs) >= limit:
            break
    return pairs


def _sentence_join(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _factual_row_summary(
    rows: list[dict[str, Any]] | None,
    trusted_assets: list[str],
    *,
    withheld_reason: str,
) -> str:
    """Plain-language lead over the verified rows when the model's prose is withheld.

    Written for the business reader: no asset names, no query talk, and no
    notice about the withheld draft in the body. The reason the draft was
    withheld is disclosed where operators look for it — the proof drawer's
    known data gaps and the process trace — never as body prose (user
    feedback 2026-09-08: the previous wording read as internal pipeline
    thoughts, not a report).
    """

    del trusted_assets, withheld_reason  # disclosed in the proof, not the prose
    if not rows:
        return "This analysis returned no matching rows."
    row_count = len(rows)
    if row_count == 1:
        pairs = _plain_pairs(rows[0], limit=8)
        if not pairs:
            return "One row was returned; see the table below."
        lead = _sentence_join(pairs)
        return f"{lead[0].upper()}{lead[1:]}."
    lead = _sentence_join(_plain_pairs(rows[0], limit=5))
    opener = f"{row_count:,} results, shown in the chart and table below."
    if not lead:
        return opener
    return f"{opener} The leading row: {lead}."


_NARRATIVE_REPAIR_MAX_ROWS = 12
_NARRATIVE_REPAIR_MAX_COLS = 8
_UNVERIFIED_CLAIMS_GAP_MARKER = "could not be verified against the returned rows"
_NARRATIVE_REWRITTEN_GAP = (
    "Genie's first draft carried a figure the returned rows could not support; "
    "it rewrote the narrative from the verified figures and the rewrite passed "
    "verification."
)


def _narrative_repair_prompt(question: str, rows: list[dict[str, Any]]) -> str:
    """Ask the space to rewrite its summary from the figures it actually returned.

    Live-first: the deterministic layer never authors the narrative. When
    Genie's draft cites a number the rows do not contain, the honest next step
    is to hand Genie its own verified rows and ask for a rewrite, then verify
    that rewrite exactly as the first draft was verified.
    """

    digest_lines: list[str] = []
    for row in rows[:_NARRATIVE_REPAIR_MAX_ROWS]:
        cells: list[str] = []
        for column, value in list(row.items())[:_NARRATIVE_REPAIR_MAX_COLS]:
            if value is None:
                continue
            cells.append(f"{_humanize_column(column)}: {_format_cell(value, column)}")
        if cells:
            digest_lines.append("- " + "; ".join(cells))
    digest = "\n".join(digest_lines)
    return (
        "Do not generate SQL for this message. Your previous summary for the "
        f'question "{question}" used a figure that is not in the rows your query '
        "returned. Rewrite the summary for a business reader in 2 to 5 "
        "sentences using ONLY the figures below, exactly as written: no "
        "rounding, no derived percentages or totals you did not return, no "
        "table or column names, no SQL, no mention of this instruction, and "
        "never the words call, target, contact or reach out.\n\n"
        f"Rows:\n{digest}"
    )


def _ensure_answer_cites_source(answer: str | None, trusted_assets: list[str]) -> str:
    """Append a source line only when the text cites no asset at all."""

    text = (answer or "").strip()
    if not trusted_assets or _SOURCE_LINE_RE.search(text) or _INLINE_SOURCE_RE.search(text):
        return text
    source = trusted_assets[0]
    if not text:
        return f"Source: {source}"
    return f"{text}\n\nSource: {source}"


def _default_verification_note(
    trusted_assets: list[str],
    metric_value: str | None,
    row_count: int,
) -> str:
    """A short, honest note that the recognized-shape count was re-verified.

    This is appended to Genie's own narrative -- it sharpens/corroborates the
    live text with the re-executed gold-grain figure rather than replacing it.
    """
    asset = trusted_assets[0] if trusted_assets else "the trusted gold tables"
    if metric_value is not None:
        return f"Verified against {asset}: {metric_value} at the trusted gold grain."
    if row_count:
        return (
            f"Verified against {asset}: {row_count:,} rows recomputed at the "
            "trusted gold grain."
        )
    return f"Verified against {asset} at the trusted gold grain."


def _parse_metric_value(metric: object) -> float | None:
    """Normalize a canonical metric (often a pre-formatted string) to float."""
    if metric is None:
        return None
    try:
        return float(str(metric).replace(",", "").strip())
    except ValueError:
        return None


def _narrative_contradicts_metric(
    narrative: str, metric: object
) -> tuple[bool, str | None]:
    """Detect a numeric claim in model prose that the verified metric disproves.

    Conservative by design: only same-scale numbers count as claims (a "35%"
    threshold in prose must not contradict a 122,598 count), zero counts as a
    claim against large metrics (the classic wrong-zero turn), and a claim
    within 1% of the metric is treated as agreement (rounding/formatting).
    Returns (contradicted, first_conflicting_claim_formatted).
    """
    metric_parsed = _parse_metric_value(metric)
    if metric_parsed is None or not narrative:
        return False, None
    metric_f = metric_parsed
    # Identifier guard (external audit 2026-07-08): digits embedded in asset
    # or column names (mip.gold.borrower_360) are not numeric claims — a
    # number only counts when not attached to word characters or dots.
    raw = [
        t.replace(",", "")
        for t in re.findall(r"(?<![\w.])\d[\d,]*\.?\d*(?![\w])", narrative)
    ]
    try:
        nums = [float(t) for t in raw if t]
    except ValueError:  # pragma: no cover - regex only yields numeric tokens
        return False, None
    if metric_f >= 1000:
        claims = [n for n in nums if n == 0 or n >= 1000]
    else:
        # An asserted zero contradicts ANY positive verified metric (external
        # audit 2026-07-08: "0 matching borrowers" vs verified 304 slipped
        # through the magnitude band).
        claims = [n for n in nums if n == 0 or metric_f / 10 <= n <= metric_f * 10]
    if not claims:
        return False, None
    tolerance = max(1.0, 0.01 * abs(metric_f))
    if any(abs(n - metric_f) <= tolerance for n in claims):
        return False, None
    worst = claims[0]
    formatted = f"{worst:,.0f}" if worst == int(worst) else f"{worst:,}"
    return True, formatted


def _restore_live_voice(
    canonical: GenieMessageResponse,
    result: GenieResponse,
    *,
    narrative_withheld: bool = False,
    trace: GenieProcessTrace | None = None,
) -> GenieMessageResponse:
    """Keep a recognized-shape trusted answer's governance but restore voice.

    ``_canonical_genie_answer`` re-executes the count, builds proof, plans the
    visualization, and suggests actions -- all governance we keep. What it used
    to also do was overwrite Genie's narrative with hand-authored template
    phrasing and drop the live-intelligence fields. Here we:

    * Lead with Genie's own (already PII-gated) narrative and APPEND a short
      verification note instead of replacing it. When the live turn has no
      usable narrative even after the repair loop, we fall back to the precise
      deterministic template already on ``canonical.answer`` and disclose that
      honestly in ``proof.known_data_gaps``.
    * Carry through ``genie_status``, ``reasoning_trace``,
      ``native_visualization`` and ``follow_up_questions`` from the live result
      exactly like the generic adaptation path does.

    ``trace`` is the in-progress deterministic process trace from
    ``_adapt_genie_response``; this function appends the canonical /
    execution / verification / composition steps it alone can observe. A
    missing trace (direct callers, older tests) starts a fresh one so the
    canonical half of the story still ships.
    """
    trace = trace if trace is not None else GenieProcessTrace()
    proof = canonical.proof
    canonical_rows = canonical.table_rows or []
    if canonical.metric_value:
        canonical_shape = "metric"
    elif len(canonical_rows) > 1:
        canonical_shape = "ranking"
    else:
        canonical_shape = ""
    trace.canonical(shape=canonical_shape)
    trace.execute(row_count=len(canonical_rows), assets=canonical.trusted_assets)
    # A guard-flagged narrative is withheld wholesale: the deterministic
    # canonical answer ships instead, and the withholding is disclosed below.
    narrative = "" if narrative_withheld else (result.answer_text or "").strip()
    if narrative_withheld and proof is not None:
        gap = (
            "Genie's draft narrative was withheld by the output safety guard; "
            "presenting the verified deterministic summary instead."
        )
        if gap not in proof.known_data_gaps:
            proof = proof.model_copy(
                update={"known_data_gaps": [*proof.known_data_gaps, gap]}
            )
    updates: dict[str, Any] = {}
    contradicted, _ = _narrative_contradicts_metric(
        narrative,
        canonical.metric_value,
    )
    unsupported_claims = _unsupported_answer_numeric_claims(
        narrative,
        canonical.table_rows,
        canonical.question,
    )
    if narrative_withheld:
        trace.narrative_withheld(reason=WITHHELD_UNSAFE_TEXT)
    elif narrative and contradicted:
        trace.narrative_withheld(reason=WITHHELD_CONTRADICTED)
    elif narrative and unsupported_claims:
        trace.narrative_withheld(reason=WITHHELD_UNVERIFIED_NUMBERS)
    elif narrative:
        trace.verified()
    else:
        trace.narrative_withheld(reason=WITHHELD_NO_NARRATIVE_DETERMINISTIC)
    if "**#1" in (canonical.answer or ""):
        trace.composed_brief()
    if narrative and contradicted:
        updates["answer"] = canonical.answer
        if proof is not None:
            gap = (
                "Genie's draft narrative was superseded because it contradicted "
                "the governed recomputation; the unsupported prose was removed."
            )
            if gap not in proof.known_data_gaps:
                proof = proof.model_copy(
                    update={"known_data_gaps": [*proof.known_data_gaps, gap]}
                )
    elif narrative and unsupported_claims:
        # Recognized shapes must obey the same all-claims rule as generic
        # Genie turns. One matching count cannot launder another unsupported
        # rate, balance, percentage, or count in the same narrative.
        updates["answer"] = canonical.answer
        if proof is not None:
            gap = (
                "Genie's draft narrative included numeric or financial claims "
                "that were not supported by the governed recomputation; the "
                "unsupported prose was removed."
            )
            if gap not in proof.known_data_gaps:
                proof = proof.model_copy(
                    update={"known_data_gaps": [*proof.known_data_gaps, gap]}
                )
    elif narrative:
        if "**#1" in (canonical.answer or ""):
            # Any teaching-analyst ranking brief (marked by its per-candidate
            # headers) is the product's deep analysis; a surviving live
            # narrative leads and the verified brief follows, instead of
            # flattening to a one-line note.
            answer = f"{narrative}\n\n{canonical.answer}"
        else:
            note = _default_verification_note(
                canonical.trusted_assets,
                canonical.metric_value,
                len(canonical.table_rows or []),
            )
            answer = narrative
            if note and note not in answer:
                answer = f"{answer}\n\n{note}"
        updates["answer"] = _ensure_answer_cites_source(answer, canonical.trusted_assets)
    elif proof is not None and not narrative_withheld:
        gap = "Genie returned no narrative; presenting the verified deterministic summary."
        if gap not in proof.known_data_gaps:
            proof = proof.model_copy(
                update={"known_data_gaps": [*proof.known_data_gaps, gap]}
            )
    reasoning_trace = trace.steps(genie_reasoning_trace_from_thoughts(result.thoughts))
    if proof is not None:
        updates["proof"] = proof.model_copy(
            update={
                "reasoning_trace": reasoning_trace,
                "conversation_id": result.conversation_id,
                "message_id": result.message_id,
            }
        )
    # Canonical answers intentionally carry deterministic proof identifiers.
    # Feedback, however, belongs to the live Conversation API turn, so retain
    # its identity on the response without changing the recomputed metric.
    updates["conversation_id"] = result.conversation_id
    updates["message_id"] = result.message_id
    updates["elapsed_ms"] = result.elapsed_ms
    updates["reasoning_trace"] = reasoning_trace
    updates["follow_up_questions"] = genie_follow_up_questions(result.suggested_questions)
    updates["native_visualization"] = genie_native_visualization(result.native_visualization)
    updates["genie_status"] = result.genie_status
    return canonical.model_copy(update=updates)
