"""Agentic planned sweep: fresh per-question decomposition, no keyword routing.

The trigger is behavioral (live turn + repair both lack SQL proof) and the
decomposition is planned by the live space itself. These tests pin the
contract: the plan is parsed and guard-screened, every planned sub-question
executes as its own live turn with recursion off, failures are disclosed, and
an unusable plan falls through to the honest single-turn pipeline.
"""

from __future__ import annotations

from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.repositories.databricks_genie_sweep import (
    _parse_planned_questions,
    _planned_question_guard_hit,
    is_deep_analysis_request,
    run_planned_sweep,
)

_USER_QUESTION = (
    "Do a full analysis on all of the data -- what are the deepest and most "
    "useful insights from everything as a lender?"
)

_PLAN_TEXT = """Here is the plan:
1. How many borrowers are currently in-the-money, and what is the average rate spread?
2. Which states concentrate the most refinance opportunity right now?
3) How did the lead population and approvals change over the last 30 days?
- What trigger evidence fired in the last 7 days, grouped by signal type?
"""


def test_plan_parsing_accepts_numbered_and_bulleted_lines() -> None:
    planned = _parse_planned_questions(_PLAN_TEXT)
    assert len(planned) == 4
    assert planned[0].endswith("?")
    assert "states concentrate" in planned[1]
    assert planned[3].startswith("What trigger evidence")


def test_plan_parsing_handles_empty_and_prose_only_text() -> None:
    assert _parse_planned_questions(None) == []
    assert _parse_planned_questions("I cannot break this down.") == []


def test_planned_questions_are_guard_screened() -> None:
    assert _planned_question_guard_hit(
        "How many borrowers are currently in-the-money?"
    ) is None
    # Model-authored plans get the same fair-lending screen user prompts get.
    assert _planned_question_guard_hit(
        "Break down average lead score by borrower race?"
    ) is not None
    assert _planned_question_guard_hit(
        "Give me the names of every borrower in ZIP 60601?"
    ) is not None


class _StubRepo:
    """Stands in for DatabricksGenieRepository: planner turn + sub-turns."""

    def __init__(
        self,
        plan_text: str | None = _PLAN_TEXT,
        failures: frozenset[str] = frozenset(),
    ) -> None:
        self.plan_text = plan_text
        self.calls: list[tuple[str, bool]] = []
        self.raw_prompts: list[str] = []
        self._failures = failures

    def ask_raw(self, prompt: str) -> str | None:
        self.raw_prompts.append(prompt)
        if "executive synthesis" in prompt:
            return "The strongest opportunity is refinance economics; act on the 3 verified segments first."
        return self.plan_text

    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
        *,
        allow_sweep: bool = True,
    ) -> GenieMessageResponse:
        self.calls.append((question, allow_sweep))
        if any(marker in question for marker in self._failures):
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
            answer=f"Governed answer for: {question[:44]}",
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


def test_sweep_plans_fresh_and_executes_each_sub_question_live() -> None:
    repo = _StubRepo()
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]

    assert result is not None
    assert result.source == "genie"
    # Two live raw turns: the plan (carrying the user's question verbatim,
    # no templates) and the Genie-authored closing synthesis.
    assert len(repo.raw_prompts) == 2
    assert _USER_QUESTION in repo.raw_prompts[0]
    assert "executive synthesis" in repo.raw_prompts[1]
    assert "**What this adds up to**" in result.answer
    assert "act on the 3 verified segments first" in result.answer
    # Every planned sub-question ran as its own live turn with recursion off.
    assert len(repo.calls) == 4
    assert all(allow_sweep is False for _, allow_sweep in repo.calls)
    # Sections are headed by the PLANNED questions themselves.
    assert "**How many borrowers are currently in-the-money" in result.answer
    assert result.sql_query is not None and result.sql_query.count("-- [") == 4
    kinds = [step.kind for step in result.reasoning_trace]
    assert kinds[0] == "orchestrate"
    assert kinds.count("live") == 4
    assert result.proof is not None and result.proof.trusted is True


def test_sweep_discloses_failed_sections_and_keeps_going() -> None:
    repo = _StubRepo(failures=frozenset({"trigger evidence"}))
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]

    assert result is not None
    assert "trigger evidence fired" not in result.answer.split("**", 1)[1]
    assert result.proof is not None
    assert any("returned no governed" in gap for gap in result.proof.known_data_gaps)


def test_sweep_returns_none_when_plan_unusable() -> None:
    assert run_planned_sweep(_StubRepo(plan_text=None), _USER_QUESTION) is None  # type: ignore[arg-type]
    assert (
        run_planned_sweep(_StubRepo(plan_text="No list here."), _USER_QUESTION)  # type: ignore[arg-type]
        is None
    )


def test_sweep_returns_none_when_too_few_sections_survive() -> None:
    repo = _StubRepo(failures=frozenset({"in-the-money", "states", "lead population"}))
    assert run_planned_sweep(repo, _USER_QUESTION) is None  # type: ignore[arg-type]


_DEEP_QUESTION = (
    "Analyze the full dataset of eligible borrowers and find determine a list of "
    "absolute top potential borrowers. Evaluate why each borrower is an especially "
    "good candidate, and what the absolute best curated offer for each would be and why."
)

_DEEP_PLAN_TEXT = """1. Which borrowers have the highest opportunity scores and what are their rate spreads and equity percentages?
2. How do the top borrowers compare with the whole eligible population on rate spread and equity?
3. What is the recommended-offer mix for the top borrowers?
4. Which states and segments concentrate the top borrowers?
5. How many top borrowers carry competitor liens or active listings?"""


def test_is_deep_analysis_request_classifies_the_family() -> None:
    assert is_deep_analysis_request(_DEEP_QUESTION)
    assert is_deep_analysis_request(
        "Do a deep analysis of our eligible borrowers and rank the top candidates."
    )
    # Two analytic parts without explicit depth wording.
    assert is_deep_analysis_request(
        "Rank the top borrowers and recommend the best offer for each."
    )
    # Single-part asks stay on the single-turn path.
    assert not is_deep_analysis_request("How many in-the-money borrowers are in TX?")
    assert not is_deep_analysis_request("Show borrowers by state.")
    assert not is_deep_analysis_request("What is the average equity percentage?")


def test_deep_sweep_uses_deep_plan_floor_and_synthesis() -> None:
    repo = _StubRepo(plan_text=_DEEP_PLAN_TEXT)
    result = run_planned_sweep(repo, _DEEP_QUESTION, deep=True)  # type: ignore[arg-type]

    assert result is not None
    assert result.source == "genie"
    # The deep planning prompt demands the comparison/offer/concentration
    # coverage and the wider plan floor.
    assert "deep-analysis request" in repo.raw_prompts[0]
    assert "between 5 and 7" in repo.raw_prompts[0]
    # The deep synthesis contract replaces the generic one.
    assert "deep executive synthesis" in repo.raw_prompts[1]
    assert len(repo.calls) == 5
    assert all(allow_sweep is False for _, allow_sweep in repo.calls)


def test_deep_sweep_requires_the_deeper_plan() -> None:
    # A three-line plan is enough for a rescue sweep but not for a deep ask.
    repo = _StubRepo()
    assert run_planned_sweep(repo, _DEEP_QUESTION, deep=True) is None  # type: ignore[arg-type]
