"""Agentic planned sweep: fresh per-question decomposition, no keyword routing.

The trigger is behavioral (live turn + repair both lack SQL proof) and the
decomposition is planned by the live space itself. These tests pin the
contract: the plan is parsed and guard-screened, every planned sub-question
executes as its own live turn with recursion off, failures are disclosed, and
an unusable plan falls through to the honest single-turn pipeline.
"""

from __future__ import annotations

import logging

import pytest

from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.repositories.databricks_genie_sweep import (
    _SWEEP_POLL_TIMEOUT_S,
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


# Live replay 2026-09-08 (paychex space, the user's own "full analysis"
# question): the deep planner wrote nine lines of 160-336 characters, and the
# parser's old 240-character cap dropped exactly the three longest — the
# ranked shortlist with its signal columns, that cohort's comparison with the
# population, and its offer mix with the signals behind each offer. The plan
# fell to the deep floor and the sweep aborted to a single-screen answer.
_LONG_PLAN_LINE = (
    "3. What are the top 25 marketable borrowers overall by opportunity score, "
    "with borrower_id, state, city, segment membership, opportunity score, "
    "confidence, rate spread, equity percentage, listing status, investor flag, "
    "competitor-lien flag, propensity triggers, recommended offer, and why-now "
    "fields, and how do they compare with the full marketable population on the "
    "same measures?"
)


def test_plan_parsing_keeps_long_deep_lines() -> None:
    assert len(_LONG_PLAN_LINE) > 240
    planned = _parse_planned_questions(
        "1. How many borrowers are currently in-the-money?\n" + _LONG_PLAN_LINE + "\n",
        deep=True,
    )
    assert len(planned) == 2
    assert planned[1].startswith("What are the top 25 marketable borrowers")
    assert planned[1].endswith("same measures?")


def test_plan_parsing_still_rejects_runaway_lines() -> None:
    runaway = "1. " + ("borrowers " * 200).strip() + "?"
    assert len(runaway) > 1_500
    assert _parse_planned_questions(runaway, deep=True) == []


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
        self.poll_timeouts: list[int | None] = []
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
        poll_timeout_s: int | None = None,
    ) -> GenieMessageResponse:
        self.calls.append((question, allow_sweep))
        self.poll_timeouts.append(poll_timeout_s)
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
    # Business-facing composition: the verified summary leads; the method is
    # disclosed in the trace and proof, never as a body preamble.
    assert result.answer.startswith("**Summary**")
    assert "act on the 3 verified segments first" in result.answer
    assert result.summary is not None and "verified segments" in result.summary
    assert "I asked the governed space" not in result.answer
    assert "governed SQL" not in result.answer
    assert len(result.sections) == 4
    assert all(section.title == section.question for section in result.sections)
    assert all(section.table_rows for section in result.sections)
    assert all(section.narrative_withheld is False for section in result.sections)
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


def _sweep_events(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str | None, dict]]:
    return [
        (
            str(getattr(rec, "mip_event", "")),
            getattr(rec, "mip_outcome", None),
            dict(getattr(rec, "mip_extras", None) or {}),
        )
        for rec in caplog.records
        if rec.name == "mip-genie-sweep"
    ]


def test_sweep_logs_plan_sections_and_result_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both shipping gates were silent; the operator must be able to read
    from the log how many lines the plan kept, what each section returned,
    and which gate decided — with hashes and labels only, never text."""

    caplog.set_level(logging.INFO, logger="mip-genie-sweep")
    repo = _StubRepo(failures=frozenset({"states"}))
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]
    assert result is not None

    events = _sweep_events(caplog)
    names = [name for name, _, _ in events]
    assert names[0] == "genie_sweep_plan"
    assert names[-1] == "genie_sweep_result"
    assert names.count("genie_sweep_section") == 4

    _, plan_outcome, plan = events[0]
    assert plan_outcome == "planned"
    assert plan["planned"] == 4 and plan["dropped"] == 0 and plan["deep"] is False

    section_outcomes = sorted(outcome for name, outcome, _ in events if name == "genie_sweep_section")
    assert section_outcomes == ["genie", "genie", "genie", "policy_blocked"]
    blocked = next(
        extras for name, outcome, extras in events
        if name == "genie_sweep_section" and outcome == "policy_blocked"
    )
    assert blocked["data_bearing"] is False

    _, result_outcome, summary = events[-1]
    assert result_outcome == "shipped"
    assert summary["sections"] == 3 and summary["omitted"] == 1 and summary["unfinished"] == 0

    # Labels and hashes only: no planned question text reaches the log.
    for _, _, extras in events:
        assert "states" not in " ".join(str(v) for v in extras.values())
        assert all(len(str(extras.get(key, ""))) == 16 for key in ("question_hash", "section_hash") if key in extras)


def test_sweep_logs_the_plan_floor_abort(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="mip-genie-sweep")
    repo = _StubRepo(plan_text="1. Only one planned question here?\n")
    assert run_planned_sweep(repo, _USER_QUESTION) is None  # type: ignore[arg-type]
    events = _sweep_events(caplog)
    assert [name for name, _, _ in events] == ["genie_sweep_plan"]
    assert events[0][1] == "aborted_plan_floor"
    assert events[0][2]["planned"] == 1 and events[0][2]["floor"] == 3


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
    # Deep plans deliberately over-ask (7-10 against a floor of 5): the
    # planner rewords every run, so plan-time guard drops vary and a plan
    # sized to the floor aborts the sweep the moment one is dropped.
    assert "between 7 and 10" in repo.raw_prompts[0]
    # The deep synthesis contract replaces the generic one.
    assert "deep executive synthesis" in repo.raw_prompts[1]
    assert len(repo.calls) == 5
    assert all(allow_sweep is False for _, allow_sweep in repo.calls)


def test_deep_sweep_requires_the_deeper_plan() -> None:
    # A three-line plan is enough for a rescue sweep but not for a deep ask.
    repo = _StubRepo()
    assert run_planned_sweep(repo, _DEEP_QUESTION, deep=True) is None  # type: ignore[arg-type]


def test_sub_analyses_carry_the_sweep_poll_deadline() -> None:
    """Deep sections legitimately outrun the interactive 45s deadline.

    Measured live 2026-08-10: cross-population scans took 82-120s, so the
    interactive deadline timed out exactly the deepest sections and starved
    the sweep below its section floor.
    """

    repo = _StubRepo(plan_text=_DEEP_PLAN_TEXT)
    result = run_planned_sweep(repo, "Do a deep analysis of the portfolio.", deep=True)

    assert result is not None
    assert repo.poll_timeouts
    assert all(value == _SWEEP_POLL_TIMEOUT_S for value in repo.poll_timeouts)


# ---------------------------------------------------------------------------
# Titled plans, per-section data and the synthesis rewrite (2026-09-08: the
# deep answer read as pipeline chatter — question-length headings, a method
# preamble, one chart for seven findings, and a withheld synthesis with no
# second chance).
# ---------------------------------------------------------------------------

from backend.services.repositories.databricks_genie_sweep import (  # noqa: E402
    _narrative_was_withheld,
    _parse_planned_items,
    _split_planned_line,
    plan_sub_analyses,
)


def test_planned_lines_carry_a_short_title_when_the_planner_supplies_one() -> None:
    text = """1. Market size by state — How many marketable borrowers are in each state?
2. Offer mix: What is the recommended-offer mix across the population?
3. How many borrowers are in-the-money and what is the average rate-and-term spread?
4. This heading is far too long to be a section title for anyone — Which segments lead?"""
    items = _parse_planned_items(text, deep=True)
    assert items == [
        ("Market size by state", "How many marketable borrowers are in each state?"),
        ("Offer mix", "What is the recommended-offer mix across the population?"),
        (None, "How many borrowers are in-the-money and what is the average rate-and-term spread?"),
        (None, "This heading is far too long to be a section title for anyone — Which segments lead?"),
    ]
    # A hyphen is never a separator: in-the-money stays inside the question.
    assert _split_planned_line("Refi economics - how many are in-the-money?") == (
        None,
        "Refi economics - how many are in-the-money?",
    )


def test_titles_are_screened_like_the_questions_they_head() -> None:
    repo = _StubRepo(
        plan_text=(
            "1. Hispanic borrowers — How many borrowers are in-the-money in Illinois?\n"
            "2. Market size — How many borrowers are in-the-money?\n"
        )
    )
    planned, dropped = plan_sub_analyses(repo, _USER_QUESTION)  # type: ignore[arg-type]
    assert planned == [("Market size", "How many borrowers are in-the-money?")]
    assert len(dropped) == 1


def test_sections_use_planner_titles_and_carry_their_own_rows() -> None:
    repo = _StubRepo(
        plan_text=(
            "1. Refi economics — How many borrowers are currently in-the-money, and what is the average rate spread?\n"
            "2. Where the opportunity sits — Which states concentrate the most refinance opportunity right now?\n"
            "3. Funnel movement — How did the lead population and approvals change over the last 30 days?\n"
        )
    )
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]
    assert result is not None
    assert [section.title for section in result.sections] == [
        "Refi economics",
        "Where the opportunity sits",
        "Funnel movement",
    ]
    assert result.answer.startswith("**Summary**")
    assert "**Refi economics**" in result.answer
    assert "**How many borrowers are currently in-the-money" not in result.answer
    for section in result.sections:
        assert section.question.endswith("?")
        assert section.table_rows and section.row_count == 3
        assert section.trusted_assets == ["mip.gold.borrower_360"]
        assert section.sql_query


def test_narrative_withheld_flag_reads_the_section_proof() -> None:
    withheld = GenieMessageResponse(
        conversation_id="c",
        question="q",
        answer="2 results, shown in the chart and table below.",
        source="genie",
        trusted_assets=["mip.gold.borrower_360"],
        proof=GenieProof(
            known_data_gaps=[
                "Genie's draft narrative included numeric or financial claims that "
                "could not be verified against the returned rows; the prose was "
                "withheld and the verified rows are shown."
            ]
        ),
    )
    assert _narrative_was_withheld(withheld) is True
    clean = withheld.model_copy(update={"proof": GenieProof()})
    assert _narrative_was_withheld(clean) is False
    assert _narrative_was_withheld(clean.model_copy(update={"proof": None})) is False


class _RetryingSynthesisRepo(_StubRepo):
    """First synthesis cites a figure the rows lack; the rewrite uses the rows."""

    def ask_raw(self, prompt: str) -> str | None:
        self.raw_prompts.append(prompt)
        if "Verified figures:" in prompt:
            return (
                "Across the verified results, refinance economics is the strongest "
                "opportunity and the lender should act on those segments first."
            )
        if "executive synthesis" in prompt:
            return "The book holds 4,242,424 borrowers, so act on the 3 verified segments first."
        return self.plan_text


def test_synthesis_gets_one_verified_rewrite_before_it_is_omitted() -> None:
    repo = _RetryingSynthesisRepo()
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]
    assert result is not None
    assert len(repo.raw_prompts) == 3
    assert "Verified figures:" in repo.raw_prompts[2]
    assert result.summary is not None
    assert "4,242,424" not in result.summary
    assert "refinance economics is the strongest" in result.summary
    assert result.proof is not None
    assert not any("synthesis draft was omitted" in gap for gap in result.proof.known_data_gaps)


class _UnsafeCellRepo(_StubRepo):
    """One planned section returns a row value the output guard refuses."""

    def respond(self, question: str, conversation_id: str | None = None, *, allow_sweep: bool = True, poll_timeout_s: int | None = None) -> GenieMessageResponse:
        response = super().respond(question, conversation_id, allow_sweep=allow_sweep, poll_timeout_s=poll_timeout_s)
        if "states concentrate" in question:
            return response.model_copy(
                update={"table_rows": [{"state": "IL", "note": "Call John Smith at 312-555-0142"}]}
            )
        return response


def test_an_unsafe_section_is_withheld_alone_and_the_rest_ships(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Live 2026-09-08: with every section's rows scanned at the router, one
    unsafe cell blocked a seven-part answer as policy_blocked. The same scan
    per section drops only that section and discloses it."""

    caplog.set_level(logging.INFO, logger="mip-genie-sweep")
    repo = _UnsafeCellRepo()
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]
    assert result is not None
    assert result.source == "genie"
    assert len(result.sections) == 3
    assert all("states concentrate" not in section.question for section in result.sections)
    assert "312-555-0142" not in result.answer
    assert result.proof is not None
    assert any("withheld by the output safety guard" in gap for gap in result.proof.known_data_gaps)
    events = _sweep_events(caplog)
    assert any(outcome == "unsafe_visible_text" for name, outcome, _ in events if name == "genie_sweep_section")
    assert events[-1][2]["withheld_by_guard"] == 1 and events[-1][2]["sections"] == 3


class _WordingRetryRepo(_StubRepo):
    """First synthesis uses a word the output guard refuses; the rewrite does not."""

    def ask_raw(self, prompt: str) -> str | None:
        self.raw_prompts.append(prompt)
        if "Verified figures:" in prompt and "compliance filter" in prompt:
            return "Refinance economics is the strongest opportunity; act on those segments first."
        if "executive synthesis" in prompt:
            return "This gives the cleanest product call because there is no offer ambiguity."
        return self.plan_text


def test_synthesis_gets_one_guard_aware_rewrite_before_it_is_withheld() -> None:
    repo = _WordingRetryRepo()
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]
    assert result is not None
    assert len(repo.raw_prompts) == 3
    assert "compliance filter" in repo.raw_prompts[2]
    assert result.summary is not None and "cleanest product call" not in result.summary
    assert result.proof is not None
    assert not any("withheld by the output safety guard" in gap for gap in result.proof.known_data_gaps)


class _LabelledRowsRepo(_StubRepo):
    """Section rows carry a Title Case gold label the prose quotes."""

    def respond(self, question: str, conversation_id: str | None = None, *, allow_sweep: bool = True, poll_timeout_s: int | None = None) -> GenieMessageResponse:
        response = super().respond(question, conversation_id, allow_sweep=allow_sweep, poll_timeout_s=poll_timeout_s)
        return response.model_copy(
            update={
                "answer": "The lowest segment is Permit Activity at 0 borrowers.",
                "table_rows": [{"segment_code": "permit", "name": "Permit Activity", "borrowers": "0"}],
            }
        )


def test_a_section_quoting_its_own_gold_label_still_ships() -> None:
    repo = _LabelledRowsRepo()
    result = run_planned_sweep(repo, _USER_QUESTION)  # type: ignore[arg-type]
    assert result is not None
    assert len(result.sections) == 4
    assert all("Permit Activity" in section.answer for section in result.sections)
    assert result.proof is not None
    assert not any("withheld by the output safety guard" in gap for gap in result.proof.known_data_gaps)
