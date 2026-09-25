"""Server-owned completion stages (audit 2026-09-21 ``genie-01`` / ``delivery-04``).

The repository pipeline reports where a governed completion is through
``report_stage``. These tests drive the REAL ``DatabricksGenieRepository``
through ``respond_existing`` with a stub Genie client and pin three things:

* the stage order each path reports (single, repair, rewrite, deep sweep,
  policy-blocked rescue, degraded),
* the sink is silent everywhere except the thread that installed it, so the
  deep sweep's worker-pool sub-turns never report,
* reporting never changes the answer: the response JSON is identical with and
  without a sink across every scenario, and a sink that raises is swallowed.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from backend.services.genie_client import GenieResponse
from backend.services.genie_completion_stages import (
    GENIE_JOB_STAGE_LABELS,
    GenieJobStage,
    report_stage,
    stage_sink,
)
from backend.services.repositories.databricks_repo import DatabricksGenieRepository
from backend.services.resilience import CircuitBreaker, DependencyDownError

QUESTION = "Which states have the most in-the-money borrowers?"
DEEP_QUESTION = (
    "Analyze the full dataset of eligible borrowers, list the absolute top "
    "potential borrowers, evaluate why each is a good candidate, and what the "
    "best curated offer for each would be"
)
TRUSTED_SQL = (
    "SELECT state, COUNT(*) AS in_the_money_borrowers FROM mip.gold.borrower_360 "
    "WHERE in_the_money = TRUE GROUP BY state ORDER BY 2 DESC"
)
ROWS = [
    {"state": "IL", "in_the_money_borrowers": 48396},
    {"state": "TX", "in_the_money_borrowers": 10914},
]
_DEEP_PLAN = """1. Top candidates — Which borrowers have the highest opportunity scores and what are their rate spreads?
2. Population comparison — How do the top borrowers compare with the whole eligible population on rate spread?
3. Offer mix — What is the recommended-offer mix for the top borrowers?
4. Concentration — Which states concentrate the top borrowers?
5. Liens and listings — How many top borrowers carry competitor liens or active listings?"""


def _trusted(answer: str, *, message_id: str = "msg-1", conversation_id: str = "conv-1") -> GenieResponse:
    return GenieResponse(
        answer_text=answer,
        sql_query=TRUSTED_SQL,
        sql_result_rows=[dict(row) for row in ROWS],
        conversation_id=conversation_id,
        message_id=message_id,
        trusted_assets=["mip.gold.borrower_360"],
    )


def _prose(answer: str) -> GenieResponse:
    return GenieResponse(
        answer_text=answer,
        sql_query=None,
        sql_result_rows=[],
        conversation_id="conv-raw",
        message_id="msg-raw",
    )


def _clean_answer() -> str:
    return "Illinois leads with 48,396 in-the-money borrowers, ahead of Texas at 10,914."


@dataclass
class _StubGenie:
    """The slice of ``ResilientGenieClient`` the repository calls."""

    resume: GenieResponse | Exception
    on_ask: Callable[[str], GenieResponse] = lambda prompt: _trusted(_clean_answer())
    breaker_state: str = "closed"
    asks: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def resilient(self) -> SimpleNamespace:
        breaker = CircuitBreaker("genie", failure_threshold=1, cooldown_s=60.0)
        if self.breaker_state == "open":
            breaker.record_failure()
        return SimpleNamespace(breaker=breaker)

    def resume_message(self, conversation_id: str, message_id: str) -> GenieResponse:
        _ = conversation_id, message_id
        if isinstance(self.resume, Exception):
            raise self.resume
        return self.resume

    def ask(self, question: str, conversation_id: str | None = None, **_: Any) -> GenieResponse:
        _ = conversation_id
        with self._lock:
            self.asks.append(question)
        return self.on_ask(question)


def _deep_on_ask(prompt: str) -> GenieResponse:
    if prompt.startswith("Plan, do not query"):
        return _prose(_DEEP_PLAN)
    if "executive synthesis" in prompt:
        return _prose("Illinois leads with 48,396 in-the-money borrowers; act there first.")
    return _trusted(_clean_answer(), message_id=f"sub-{abs(hash(prompt)) % 10_000}")


def _scenarios() -> dict[str, tuple[str, Callable[[], _StubGenie]]]:
    return {
        "single": (QUESTION, lambda: _StubGenie(resume=_trusted(_clean_answer()))),
        "repair": (
            "Which zips have the most in-the-money refi candidates?",
            lambda: _StubGenie(
                resume=GenieResponse(
                    answer_text="The top ZIP is 60617.",
                    sql_query=None,
                    sql_result_rows=[],
                    conversation_id="conv-1",
                    message_id="msg-1",
                ),
                on_ask=lambda prompt: _trusted(_clean_answer(), message_id="msg-repair"),
            ),
        ),
        "rewrite": (
            QUESTION,
            lambda: _StubGenie(
                resume=_trusted("Illinois leads with 99,999 in-the-money borrowers."),
                on_ask=lambda prompt: _prose(_clean_answer()),
            ),
        ),
        "deep": (DEEP_QUESTION, lambda: _StubGenie(resume=_trusted(_clean_answer()), on_ask=_deep_on_ask)),
        "policy_blocked": (
            QUESTION,
            lambda: _StubGenie(
                resume=GenieResponse(
                    answer_text="Here are the rows.",
                    sql_query="SELECT ssn FROM other_catalog.secret.customers",
                    sql_result_rows=[{"ssn": "x"}],
                    conversation_id="conv-1",
                    message_id="msg-1",
                ),
                on_ask=lambda prompt: _prose("NO_PLAN"),
            ),
        ),
        "breaker_open": (QUESTION, lambda: _StubGenie(resume=_trusted(_clean_answer()), breaker_state="open")),
        "dependency_down": (
            QUESTION,
            lambda: _StubGenie(
                resume=DependencyDownError(
                    "genie", reason="resume failed", kind=DependencyDownError.KIND_RETRIES_EXHAUSTED
                )
            ),
        ),
    }


def _complete(
    name: str,
    *,
    sink: Callable[[GenieJobStage, int | None, int | None], None] | None,
) -> tuple[dict[str, Any], _StubGenie]:
    question, make = _scenarios()[name]
    genie = make()
    repo = DatabricksGenieRepository(genie)  # type: ignore[arg-type]
    if sink is None:
        response = repo.respond_existing(question, conversation_id="conv-1", message_id="msg-1")
    else:
        with stage_sink(sink):
            response = repo.respond_existing(question, conversation_id="conv-1", message_id="msg-1")
    return _normalized(response.model_dump(mode="json")), genie


_VOLATILE_KEYS = frozenset({"elapsed_ms", "generated_at"})


def _normalized(value: Any) -> Any:
    """Drop wall-clock fields; everything else must match byte for byte."""

    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items() if key not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, int | None, int | None]] = []
        self.threads: set[int] = set()
        self._lock = threading.Lock()

    def __call__(self, stage: GenieJobStage, done: int | None, planned: int | None) -> None:
        with self._lock:
            self.events.append((stage.value, done, planned))
            self.threads.add(threading.get_ident())

    @property
    def stages(self) -> list[str]:
        return [stage for stage, _, _ in self.events]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("single", ["collecting", "verifying", "cross_checking"]),
        ("repair", ["collecting", "repairing", "verifying", "cross_checking"]),
        ("rewrite", ["collecting", "verifying", "cross_checking", "rewriting"]),
        ("policy_blocked", ["collecting", "verifying", "planning"]),
        ("breaker_open", []),
        ("dependency_down", ["collecting"]),
    ],
)
def test_each_path_reports_its_stages_in_order(name: str, expected: list[str]) -> None:
    recorder = _Recorder()

    _complete(name, sink=recorder)

    assert recorder.stages == expected


def test_the_deep_sweep_reports_plan_parts_and_synthesis_from_the_owner_thread_only() -> None:
    recorder = _Recorder()

    response, genie = _complete("deep", sink=recorder)

    assert response["sections"], "the stub sweep must ship its sections"
    # Five sub-turns each ran the full pipeline (cross-check included) on the
    # sweep's worker pool; none of them reported.
    assert recorder.threads == {threading.get_ident()}
    assert recorder.stages[0] == "planning"
    assert recorder.stages[-1] == "synthesizing"
    assert "cross_checking" not in recorder.stages
    parts = [(done, planned) for stage, done, planned in recorder.events if stage == "researching"]
    assert parts[0] == (0, 5)
    assert parts[-1] == (5, 5)
    assert [done for done, _ in parts] == sorted(done for done, _ in parts)
    assert all(planned == 5 for _, planned in parts)
    assert sum(1 for prompt in genie.asks if prompt.startswith("Plan, do not query")) == 1


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_reporting_never_changes_the_governed_answer(name: str) -> None:
    without_sink, _ = _complete(name, sink=None)
    with_sink, _ = _complete(name, sink=_Recorder())

    assert with_sink == without_sink


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_a_failing_sink_is_swallowed_and_the_answer_is_unchanged(name: str) -> None:
    def explode(stage: GenieJobStage, done: int | None, planned: int | None) -> None:
        raise RuntimeError(f"sink down at {stage}")

    without_sink, _ = _complete(name, sink=None)
    with_failing_sink, _ = _complete(name, sink=explode)

    assert with_failing_sink == without_sink


def test_report_stage_is_a_no_op_without_a_sink_and_off_the_owner_thread() -> None:
    recorder = _Recorder()
    report_stage(GenieJobStage.VERIFYING)  # no sink installed: nothing to call

    with stage_sink(recorder):
        report_stage(GenieJobStage.VERIFYING)
        # A worker that INHERITED the sink (an executor that propagates the
        # context) still never reports: only the installing thread does.
        context = contextvars.copy_context()
        worker = threading.Thread(target=context.run, args=(report_stage, GenieJobStage.RESEARCHING, 1, 3))
        worker.start()
        worker.join()

    report_stage(GenieJobStage.FINALIZING)  # uninstalled again on exit
    assert recorder.stages == ["verifying"]


def test_no_stage_label_claims_the_answer_is_ready() -> None:
    assert set(GENIE_JOB_STAGE_LABELS) == set(GenieJobStage)
    for label in GENIE_JOB_STAGE_LABELS.values():
        assert "ready" not in label.lower(), label
