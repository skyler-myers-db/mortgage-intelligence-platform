"""A Stop reaches every sub-turn of the deep sweep (W5c genie-03, item 2c).

``run_planned_sweep`` runs each planned sub-analysis on a worker pool. The
owner's cancel predicate now travels to each sub-turn (``cancel_scope``), so
its Genie polls and stage boundaries are cancel points; the owner waits in
slices, so it sees the stop within one slice even while every sub-turn is
blocked; and a stopped sweep shuts its pool down without waiting. Pinned with
a fake repository whose sub-turn "polls" block: a running sub-turn stops
within one poll interval, a sub-turn that has not started never starts, the
owner wakes within its slice when every sub-turn blocks without a cancel
point, the job ends promptly, the RESEARCHING report still precedes
report_sections, and a sweep nobody stops is unchanged.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.genie_completion_stages import (
    GenieJobStage,
    GenieTurnCancelled,
    cooperative_cancel_point,
    stage_sink,
)
from backend.services.repositories import databricks_genie_sweep as sweep

_QUESTION = (
    "Do a full analysis on all of the data -- what are the deepest and most "
    "useful insights from everything as a lender?"
)
_TITLES = [
    "Market size by state — How many borrowers are in the money in each state?",
    "Rate spread mix — What is the average rate spread by segment?",
    "Equity depth — How is available equity distributed across the book?",
    "Listing pipeline — How many listed-for-sale properties carry a mortgage?",
    "Lien activity — Which competitor liens appeared in the last 30 days?",
    "Payoff timing — How many loans pay off within twelve months by state?",
    "Purchase intent — How many borrowers listed a property this quarter by state?",
    "Term mix — How do fifteen and thirty year terms compare by segment?",
]
_POLL_S = 0.02


def _plan(count: int) -> str:
    return "\n".join(f"{index + 1}. {line}" for index, line in enumerate(_TITLES[:count]))


def _response(question: str) -> GenieMessageResponse:
    rows: list[dict[str, Any]] = [{"state": "IL", "borrowers": 48396}, {"state": "TX", "borrowers": 10914}]
    return GenieMessageResponse(
        conversation_id="conv-sweep", message_id=f"msg-{abs(hash(question)) % 997}",
        question=question, question_hash="h", answer=f"Illinois leads for: {question[:30]}", source="genie",
        trusted_assets=["mip.gold.borrower_360"], sql_query="SELECT state FROM mip.gold.borrower_360",
        row_count=len(rows), table_rows=rows,
        proof=GenieProof(source_assets=["mip.gold.borrower_360"], row_count=len(rows), trusted=True),
    )


class _BlockingRepo:
    """Each sub-turn 'polls' Genie until ``release`` is set.

    ``cooperative`` polls call the real cancel point between polls (as
    ``GenieClient._poll_message`` does); otherwise the sub-turn blocks like
    one long HTTP call with no cancel point at all.
    """

    def __init__(self, *, planned: int = 5, cooperative: bool = True, release: bool = False, max_hold_s: float = 4.0) -> None:
        self.planned = planned
        self.cooperative = cooperative
        #: A sub-turn ends by itself after this long, so a broken stop goes
        #: red on its timing instead of hanging the suite.
        self.max_hold_s = max_hold_s
        self.release = threading.Event()
        if release:
            self.release.set()
        self.started: list[str] = []
        self.finished: list[str] = []
        self.stopped_at: list[float] = []
        self.lock = threading.Lock()

    def ask_raw(self, prompt: str) -> str | None:
        if "executive synthesis" in prompt:
            return "Refinance economics lead the book; act on the verified segments first."
        return _plan(self.planned)

    def respond(self, question: str, conversation_id: str | None = None, *,
                allow_sweep: bool = True, poll_timeout_s: int | None = None) -> GenieMessageResponse:
        with self.lock:
            self.started.append(question)
        held_until = time.monotonic() + self.max_hold_s
        try:
            while not self.release.wait(_POLL_S) and time.monotonic() < held_until:
                if self.cooperative:
                    cooperative_cancel_point()
        except GenieTurnCancelled:
            with self.lock:
                self.stopped_at.append(time.monotonic())
            raise
        with self.lock:
            self.finished.append(question)
        return _response(question)


class _Owner:
    """The runner side: a sink whose predicate is the Stop, recording events."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.events: list[tuple[str, Any]] = []

    def stage(self, stage: GenieJobStage, done: int | None, planned: int | None) -> None:
        self.events.append(("stage", (stage.value, done, planned)))

    def sections(self, snapshot: list[dict[str, Any]]) -> None:
        self.events.append(("sections", [item["index"] for item in snapshot]))


def _stop_when_started(repo: _BlockingRepo, owner: _Owner, count: int) -> threading.Thread:
    def watch() -> None:
        deadline = time.monotonic() + 10
        while len(repo.started) < count and time.monotonic() < deadline:
            time.sleep(0.005)
        owner.stop.set()

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    return watcher


def _run(repo: _BlockingRepo, owner: _Owner) -> GenieMessageResponse | None:
    with stage_sink(owner.stage, cancelled=owner.stop.is_set, sections=owner.sections):
        return sweep.run_planned_sweep(repo, _QUESTION, deep=False)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _short_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep, "_SWEEP_CANCEL_SLICE_S", 0.1)


def test_a_running_sub_turn_stops_within_one_poll_interval() -> None:
    repo, owner = _BlockingRepo(planned=5), _Owner()
    _stop_when_started(repo, owner, 5)

    with pytest.raises(GenieTurnCancelled):
        _run(repo, owner)
    stopped = time.monotonic()
    deadline = time.monotonic() + 5
    while len(repo.stopped_at) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(repo.stopped_at) == 5, "every running sub-turn stopped at its next poll"
    assert repo.finished == []
    assert max(repo.stopped_at) - stopped < 1.0
    repo.release.set()


def test_a_sub_turn_that_has_not_started_never_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep, "_SWEEP_MAX_WORKERS", 3)
    repo, owner = _BlockingRepo(planned=7), _Owner()
    _stop_when_started(repo, owner, 3)

    with pytest.raises(GenieTurnCancelled):
        _run(repo, owner)
    time.sleep(0.3)

    assert len(repo.started) == 3, "the four queued sub-analyses never ran"
    repo.release.set()


def test_the_owner_wakes_within_its_slice_and_ends_promptly_when_every_sub_turn_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No cancel point inside the sub-turns (one long HTTP call each): only
    # the owner's slice notices, and it does not wait for them to finish.
    # (A 3 s budget: one unsliced wait would only notice at its expiry.)
    monkeypatch.setattr(sweep, "_SWEEP_WALL_BUDGET_S", 3.0)
    repo, owner = _BlockingRepo(planned=5, cooperative=False), _Owner()
    _stop_when_started(repo, owner, 5)
    started = time.monotonic()

    with pytest.raises(GenieTurnCancelled):
        _run(repo, owner)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"the stopped sweep waited {elapsed:.2f}s for blocked sub-turns"
    assert repo.finished == [], "the blocked sub-turns were still running when the owner ended"
    repo.release.set()


def test_the_researching_report_still_precedes_report_sections() -> None:
    repo, owner = _BlockingRepo(planned=5, release=True), _Owner()

    result = _run(repo, owner)

    assert result is not None and len(result.sections) == 5
    kinds = [kind for kind, _ in owner.events]
    for index, (kind, payload) in enumerate(owner.events):
        if kind == "sections" and payload:
            # Every section ships here, so the reveal right after a
            # collection holds exactly what that collection's report counted.
            assert owner.events[index - 1] == ("stage", ("researching", len(payload), 5)), owner.events
    assert kinds.count("sections") >= 2


def test_a_sweep_nobody_stops_ships_every_section() -> None:
    repo, owner = _BlockingRepo(planned=5, release=True), _Owner()

    result = _run(repo, owner)

    assert result is not None
    assert len(repo.finished) == 5 and repo.stopped_at == []
    assert [section.question for section in result.sections] == [line.split(" — ", 1)[1] for line in _TITLES[:5]]
