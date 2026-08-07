"""Deterministic Genie process-trace builder.

The proof panel used to render four identical "Analyzed the request within the
governed Genie workflow." lines. These tests pin the replacement: distinct,
server-authored steps describing what the governed pipeline actually did, all
of which must clear the same visible-text guard every other rendered Genie
string is held to.
"""

from __future__ import annotations

from typing import Any

from backend.services.genie_answers import GenieReasoningStep
from backend.services.genie_client import GenieResponse
from backend.services.genie_message_policy import genie_visible_text_unsafe
from backend.services.repositories.databricks_genie import _adapt_genie_response
from backend.services.repositories.databricks_genie_trace import (
    GENIE_MAX_TRACE_STEPS,
    NARRATIVE_WITHHELD_CONTENT,
    SUPERSEDED_TRANSLATED_CONTENTS,
    WITHHELD_CONTRADICTED,
    WITHHELD_NO_NARRATIVE,
    WITHHELD_NO_NARRATIVE_DETERMINISTIC,
    WITHHELD_UNSAFE_TEXT,
    WITHHELD_UNVERIFIED_NUMBERS,
    GenieProcessTrace,
)

_TRACE_KINDS = {
    "guardrails",
    "live",
    "trust",
    "repair",
    "canonical",
    "execute",
    "verify",
    "compose",
}


class _StubSqlClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:  # noqa: ARG002
        return self.rows

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:  # noqa: ARG002
        return self.rows[0] if self.rows else None


def _fully_populated_traces() -> list[GenieProcessTrace]:
    """One trace per branch combination that can ship, covering every step."""
    traces: list[GenieProcessTrace] = []
    for withheld_reason in (
        None,
        WITHHELD_UNSAFE_TEXT,
        WITHHELD_UNVERIFIED_NUMBERS,
        WITHHELD_CONTRADICTED,
        WITHHELD_NO_NARRATIVE,
        WITHHELD_NO_NARRATIVE_DETERMINISTIC,
        "unrecognized_reason_falls_back",
    ):
        for shape in ("metric", "ranking", "", "unknown-shape"):
            for assets in (
                ["mip.gold.borrower_360"],
                ["mip.gold.lead_population", "mip.gold.borrower_360"],
                [],
                None,
            ):
                for sql_query in ("SELECT 1 FROM mip.gold.borrower_360", None):
                    trace = GenieProcessTrace()
                    trace.guardrails()
                    trace.repair()
                    trace.live_turn(sql_query=sql_query, assets=assets)
                    trace.trust()
                    trace.canonical(shape=shape)
                    trace.execute(row_count=0, assets=assets)
                    trace.execute(row_count=1, assets=assets)
                    trace.execute(row_count=1_234, assets=assets)
                    if withheld_reason is None:
                        trace.verified()
                    else:
                        trace.narrative_withheld(reason=withheld_reason)
                    trace.composed_brief()
                    traces.append(trace)
    return traces


def test_every_populated_trace_string_passes_the_visible_text_guard() -> None:
    for trace in _fully_populated_traces():
        steps = trace.steps()
        assert steps, "a fully populated trace must emit steps"
        for step in steps:
            assert step.kind in _TRACE_KINDS, step.kind
            assert step.kind == step.kind.lower()
            assert not genie_visible_text_unsafe(step.kind), step.kind
            assert not genie_visible_text_unsafe(step.content), step.content


def test_every_withheld_reason_string_passes_the_visible_text_guard() -> None:
    for content in NARRATIVE_WITHHELD_CONTENT.values():
        assert not genie_visible_text_unsafe(content), content


def test_trace_dedupes_identical_contents_and_caps_length() -> None:
    trace = GenieProcessTrace()
    for _ in range(5):
        trace.guardrails()
        trace.trust()
    contents = [step.content for step in trace.steps()]
    assert len(contents) == 2
    assert len(set(contents)) == 2

    long_trace = GenieProcessTrace()
    for index in range(GENIE_MAX_TRACE_STEPS * 2):
        long_trace.execute(row_count=index, assets=["mip.gold.borrower_360"])
    assert len(long_trace.steps()) == GENIE_MAX_TRACE_STEPS


def test_translated_thoughts_only_append_when_they_add_something() -> None:
    trace = GenieProcessTrace()
    trace.guardrails()
    generic = [
        GenieReasoningStep(kind=kind, content=content)
        for kind, content in zip(
            ("analysis", "query"), sorted(SUPERSEDED_TRANSLATED_CONTENTS), strict=False
        )
    ]
    assert [step.content for step in trace.steps(generic)] == [
        step.content for step in trace.steps()
    ]

    novel = [GenieReasoningStep(kind="answer", content="Summarized the verified result for review.")]
    appended = trace.steps(novel)
    assert appended[-1].kind == "answer"
    assert appended[-1].content == "Summarized the verified result for review."


def test_unsafe_translated_thought_never_reaches_the_trace() -> None:
    trace = GenieProcessTrace()
    trace.guardrails()
    unsafe = [GenieReasoningStep(kind="analysis", content="Contact john smith about the result.")]
    assert [step.content for step in trace.steps(unsafe)] == [
        step.content for step in trace.steps()
    ]


def test_repaired_turn_reports_the_repair_step() -> None:
    live = GenieResponse(
        answer_text="Two borrowers match the governed screen.",
        sql_query="SELECT borrower_id FROM mip.gold.borrower_360 LIMIT 2",
        sql_result_rows=[{"borrower_id": "B-0AAAAAAAAAAAA"}, {"borrower_id": "B-0BBBBBBBBBBBB"}],
        conversation_id="conv-repair",
        message_id="msg-repair",
    )
    result = _adapt_genie_response(
        "How many borrowers are eligible?",
        live,
        sql_client=None,
        repaired=True,
    )
    kinds = [step.kind for step in result.reasoning_trace]
    assert kinds[:4] == ["guardrails", "repair", "live", "trust"]
    assert "regenerate the answer as governed SQL" in result.reasoning_trace[1].content


def test_unrepaired_turn_omits_the_repair_step() -> None:
    live = GenieResponse(
        answer_text="Two borrowers match the governed screen.",
        sql_query="SELECT borrower_id FROM mip.gold.borrower_360 LIMIT 2",
        sql_result_rows=[{"borrower_id": "B-0AAAAAAAAAAAA"}, {"borrower_id": "B-0BBBBBBBBBBBB"}],
        conversation_id="conv-plain",
        message_id="msg-plain",
    )
    result = _adapt_genie_response("How many borrowers are eligible?", live, sql_client=None)
    assert "repair" not in [step.kind for step in result.reasoning_trace]


def test_withheld_narrative_reports_the_actual_reason() -> None:
    live = GenieResponse(
        answer_text="Email borrower@example.com about the two matches.",
        sql_query="SELECT borrower_id FROM mip.gold.borrower_360 LIMIT 2",
        sql_result_rows=[{"borrower_id": "B-0AAAAAAAAAAAA"}, {"borrower_id": "B-0BBBBBBBBBBBB"}],
        conversation_id="conv-withheld",
        message_id="msg-withheld",
    )
    result = _adapt_genie_response("Which borrowers are eligible?", live, sql_client=None)
    verify = [step for step in result.reasoning_trace if step.kind == "verify"]
    assert verify == [
        GenieReasoningStep(
            kind="verify", content=NARRATIVE_WITHHELD_CONTENT[WITHHELD_UNSAFE_TEXT]
        )
    ]
    assert "borrower@example.com" not in " ".join(
        step.content for step in result.reasoning_trace
    )


def test_ranking_turn_reports_canonical_execution_and_composition() -> None:
    live = GenieResponse(
        answer_text="",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="conv-rank",
        message_id="msg-rank",
    )
    rows = [
        {
            "borrower_id": f"B-0{index}AAAAAAAAAA"[:15],
            "opportunity_score": 90 - index,
            "state": "IL",
            "segment_codes": ["in_the_money"],
        }
        for index in range(3)
    ]
    result = _adapt_genie_response(
        "Who are the top borrowers by opportunity score across the current coverage?",
        live,
        sql_client=_StubSqlClient(rows),  # type: ignore[arg-type]
    )
    kinds = [step.kind for step in result.reasoning_trace]
    assert "canonical" in kinds
    assert "execute" in kinds
    contents = {step.kind: step.content for step in result.reasoning_trace}
    assert "unique-borrower grain" in contents["canonical"]
    assert "PII columns stripped at the boundary" in contents["execute"]
    assert result.proof is not None
    assert [step.model_dump() for step in result.proof.reasoning_trace] == [
        step.model_dump() for step in result.reasoning_trace
    ]
