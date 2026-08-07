"""Full-analysis sweep: matcher scope and live-first orchestration contract."""

from __future__ import annotations

import pytest

from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.repositories.databricks_genie_sweep import (
    FULL_ANALYSIS_SWEEP,
    is_full_analysis_question,
    run_full_analysis_sweep,
)

_USER_QUESTION = (
    "Do a full analysis on all of the data -- what are the deepest and most "
    "useful insights from everything as a lender?"
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (_USER_QUESTION, True),
        ("Give me an executive summary of the book.", True),
        ("Analyze everything and tell me the biggest insights.", True),
        ("What is the state of the portfolio?", True),
        # Narrow scopes stay single-turn.
        ("Do a full analysis of Texas refinance candidates.", False),
        ("Deep analysis of the HELOC segment, please.", False),
        ("How many borrowers are currently in-the-money?", False),
        ("Show me the top 10 borrowers by lead score in Illinois.", False),
    ],
)
def test_full_analysis_matcher(question: str, expected: bool) -> None:
    assert is_full_analysis_question(question) is expected


def test_sweep_sub_questions_do_not_recurse() -> None:
    for _, sub_question in FULL_ANALYSIS_SWEEP:
        assert is_full_analysis_question(sub_question) is False, sub_question


class _StubRepo:
    """Stands in for DatabricksGenieRepository; records sub-question fan-out."""

    def __init__(self, failures: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, bool]] = []
        self._failures = failures

    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
        *,
        allow_sweep: bool = True,
    ) -> GenieMessageResponse:
        self.calls.append((question, allow_sweep))
        if question in self._failures:
            return GenieMessageResponse(
                conversation_id="",
                question=question,
                question_hash="h",
                answer="blocked",
                source="policy_blocked",
                trusted_assets=[],
            )
        return GenieMessageResponse(
            conversation_id=f"conv-{len(self.calls)}",
            message_id=f"msg-{len(self.calls)}",
            question=question,
            question_hash="h",
            answer=f"Governed answer for: {question[:40]}",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            sql_query="SELECT 1 FROM mip.gold.borrower_360",
            row_count=3,
            proof=GenieProof(
                source_assets=["mip.gold.borrower_360"],
                row_count=3,
                trusted=True,
                generated_at="2026-08-07T00:00:00Z",
            ),
            table_rows=[{"borrower_id": "B-1ABCDEFGHIJKL"}],
        )


def test_sweep_composes_all_sections_with_live_sub_turns() -> None:
    repo = _StubRepo()
    result = run_full_analysis_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]

    assert result is not None
    assert result.source == "genie"
    # Every themed sub-question ran as its own live turn with recursion off.
    assert len(repo.calls) == len(FULL_ANALYSIS_SWEEP)
    assert all(allow_sweep is False for _, allow_sweep in repo.calls)
    for title, _ in FULL_ANALYSIS_SWEEP:
        assert f"**{title}**" in result.answer
    assert result.sql_query is not None
    assert result.sql_query.count("-- [") == len(FULL_ANALYSIS_SWEEP)
    assert result.proof is not None
    assert result.proof.trusted is True
    kinds = [step.kind for step in result.reasoning_trace]
    assert kinds[0] == "orchestrate"
    assert kinds.count("live") == len(FULL_ANALYSIS_SWEEP)


def test_sweep_discloses_failed_sections_and_keeps_going() -> None:
    failing = FULL_ANALYSIS_SWEEP[1][1]
    repo = _StubRepo(failures=frozenset({failing}))
    result = run_full_analysis_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]

    assert result is not None
    assert f"**{FULL_ANALYSIS_SWEEP[1][0]}**" not in result.answer
    assert result.proof is not None
    assert any("returned no governed result" in gap for gap in result.proof.known_data_gaps)


def test_sweep_returns_none_when_too_few_sections_survive() -> None:
    failures = frozenset(q for _, q in FULL_ANALYSIS_SWEEP[:5])
    repo = _StubRepo(failures=failures)
    assert run_full_analysis_sweep(repo, _USER_QUESTION) is None  # type: ignore[arg-type]
