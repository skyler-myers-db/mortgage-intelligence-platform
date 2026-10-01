"""Verified sections revealed as the deep sweep judges them (genie-01 phase 1b).

``run_planned_sweep`` judges each sub-analysis as its result is collected
and reports, through ``report_sections``, the plan-ordered snapshot of every
section judged ``ship`` so far. Pinned here: only shipped sections are ever
reported (never one the per-section output scan withheld, never before its
verdict, never the synthesis), in plan order, only from the owner thread; a
sweep starts its reveal with an empty snapshot; every reported item IS the
final section; and the final response is identical with and without a sink.
"""

from __future__ import annotations

import threading
from typing import Any

from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.genie_completion_sections import REVEAL_SECTION_FLOOR
from backend.services.genie_completion_stages import GenieJobStage, stage_sink
from backend.services.repositories import databricks_genie_sweep as sweep

_QUESTION = (
    "Do a full analysis on all of the data -- what are the deepest and most "
    "useful insights from everything as a lender?"
)
_PLAN = """1. Market size by state — How many borrowers are in the money in each state?
2. Rate spread mix — What is the average rate spread by segment?
3. Equity depth — How is available equity distributed across the book?
4. Listing pipeline — How many listed-for-sale properties carry a mortgage?
5. Lien activity — Which competitor liens appeared in the last 30 days?
"""


class _Repo:
    """Planner turn, synthesis turn and one scripted sub-turn per question."""

    def __init__(self, *, unsafe: str | None = None, empty: str | None = None) -> None:
        self.unsafe = unsafe
        self.empty = empty
        self.lock = threading.Lock()

    def ask_raw(self, prompt: str) -> str | None:
        if "executive synthesis" in prompt:
            return "Refinance economics lead the book; act on the verified segments first."
        return _PLAN

    def respond(self, question: str, conversation_id: str | None = None, *,
                allow_sweep: bool = True, poll_timeout_s: int | None = None) -> GenieMessageResponse:
        rows: list[dict[str, Any]] = [{"state": "IL", "borrowers": 48396}, {"state": "TX", "borrowers": 10914}]
        if self.unsafe and self.unsafe in question:
            rows = [{"state": "IL", "contact": "jane.doe@example.com"}]
        answer = "" if self.empty and self.empty in question else f"Illinois leads for: {question[:30]}"
        return GenieMessageResponse(
            conversation_id="conv-sweep", message_id=f"msg-{abs(hash(question)) % 997}",
            question=question, question_hash="h", answer=answer, source="genie",
            trusted_assets=["mip.gold.borrower_360"], sql_query="SELECT state FROM mip.gold.borrower_360",
            row_count=len(rows), table_rows=rows if answer else None,
            proof=GenieProof(source_assets=["mip.gold.borrower_360"], row_count=len(rows), trusted=True),
        )


def _run(repo: _Repo) -> tuple[GenieMessageResponse | None, list[list[dict[str, Any]]], list[int]]:
    snapshots: list[list[dict[str, Any]]] = []
    threads: list[int] = []

    def sections(snapshot: list[dict[str, Any]]) -> None:
        threads.append(threading.get_ident())
        snapshots.append(snapshot)

    def stage(_stage: GenieJobStage, _done: int | None, _planned: int | None) -> None:
        return None

    with stage_sink(stage, sections=sections):
        result = sweep.run_planned_sweep(repo, _QUESTION, deep=False)  # type: ignore[arg-type]
    return result, snapshots, threads


def test_the_reveal_floor_is_the_sweep_shipping_floor() -> None:
    assert REVEAL_SECTION_FLOOR == sweep._MIN_PLANNED


def test_only_shipped_sections_are_reported_in_plan_order_from_the_owner_thread() -> None:
    result, snapshots, threads = _run(_Repo(empty="equity"))

    assert result is not None
    assert snapshots[0] == [], "a sweep starts its reveal from nothing"
    assert set(threads) == {threading.get_ident()}
    final = snapshots[-1]
    indices = [item["index"] for item in final]
    assert indices == sorted(indices) == [0, 1, 3, 4], "the no-content section is never reported"
    for earlier in snapshots[1:]:
        assert [item["index"] for item in earlier] == sorted(item["index"] for item in earlier)
        assert {item["index"] for item in earlier} <= set(indices)
    # Every reported item IS the final section (one shared builder).
    assert [{k: v for k, v in item.items() if k != "index"} for item in final] == [
        section.model_dump(mode="json") for section in result.sections
    ]
    assert all("Summary" not in item["title"] for item in final), "never the synthesis"


def test_a_section_the_output_scan_withholds_is_never_reported() -> None:
    result, snapshots, _ = _run(_Repo(unsafe="rate spread"))

    assert result is not None
    reported = {item["index"] for snapshot in snapshots for item in snapshot}
    assert 1 not in reported
    assert reported == {0, 2, 3, 4}
    assert not any("jane.doe" in str(snapshot) for snapshot in snapshots)


def test_no_section_is_reported_off_the_owner_thread() -> None:
    seen: list[list[dict[str, Any]]] = []

    def stage(_stage: GenieJobStage, _done: int | None, _planned: int | None) -> None:
        return None

    with stage_sink(stage, sections=seen.append):
        worker = threading.Thread(target=lambda: sweep.report_sections([{"index": 0}]))
        worker.start()
        worker.join(5)
    assert seen == []


def test_the_final_response_is_identical_with_and_without_a_sink() -> None:
    with_sink, _, _ = _run(_Repo(unsafe="lien", empty="equity"))
    without = sweep.run_planned_sweep(_Repo(unsafe="lien", empty="equity"), _QUESTION)  # type: ignore[arg-type]

    assert with_sink is not None and without is not None
    drop = {"elapsed_ms"}
    left = with_sink.model_dump(mode="json", exclude=drop)
    right = without.model_dump(mode="json", exclude=drop)
    left["proof"].pop("elapsed_ms"), right["proof"].pop("elapsed_ms")
    assert left == right


def test_the_sweep_proof_counts_the_summary_and_each_section_figures() -> None:
    from backend.services.genie_answers import GenieClaimsSummary, GenieVerifiedClaim

    class _ClaimRepo(_Repo):
        def ask_raw(self, prompt: str) -> str | None:
            if "executive synthesis" in prompt:
                return "Illinois leads with 48,396 borrowers; act there first."
            return super().ask_raw(prompt)

        def respond(self, question: str, conversation_id: str | None = None, *,
                    allow_sweep: bool = True, poll_timeout_s: int | None = None) -> GenieMessageResponse:
            response = super().respond(question, allow_sweep=allow_sweep, poll_timeout_s=poll_timeout_s)
            own = GenieClaimsSummary(verified=1, total=1, items=[
                GenieVerifiedClaim(token="10,914", kind="number", derivation="returned_value")])
            assert response.proof is not None
            response.proof = response.proof.model_copy(update={"claims": own})
            return response

    result = sweep.run_planned_sweep(_ClaimRepo(), _QUESTION)  # type: ignore[arg-type]

    assert result is not None and result.proof is not None and result.proof.claims is not None
    claims = result.proof.claims
    assert (claims.verified, claims.total) == (6, 6)
    assert claims.items[0].section is None and claims.items[0].token == "48,396"
    assert [item.section for item in claims.items[1:]] == [section.title for section in result.sections]
