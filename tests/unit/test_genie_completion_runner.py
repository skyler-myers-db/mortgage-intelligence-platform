"""The completion runner (audit 2026-09-21 ``genie-01``).

* Slot accounting through the REAL backpressure middleware (TestClient with
  X-Enable-Backpressure-Test): the request's Genie slot is adopted at
  enqueue, held while the job runs and released when it ends, on success,
  failure and an unexpected exception; a joined complete adopts nothing.
* Offline equivalence: the async job path, the legacy job path and the
  pre-job inline path (no table) serve the same governed JSON (modulo the
  signed tokens' nonce/exp and minted request ids) and write the same audit
  rows, across six repository outcomes.
* The runner's audit rows carry the enqueuing request's correlation id; the
  stage sink writes the real repository's stages from the runner thread
  only; a failing stage write never fails the answer.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import _backpressure_controller, app
from backend.services import genie_completion_jobs as jobs
from backend.services import genie_completion_runner as runner
from backend.services.genie_answers import GenieAnswerSection, GenieMessageResponse
from backend.services.genie_client import GenieResponse
from backend.services.repositories.databricks_repo import DatabricksGenieRepository
from backend.services.resilience import CircuitBreaker
from tests.fixtures.genie_job_lakebase import FakeJobLakebase
from tests.fixtures.genie_job_turns import (
    ACTOR,
    FakeAudit,
    FakeRepo,
    answer,
    install,
    post_complete,
    post_status,
    wait_for_job,
)


@pytest.fixture(autouse=True)
def _drain_runner() -> Any:
    yield
    runner._reset_executor_for_tests()


def test_the_runner_owns_the_audit_write_and_the_governed_finalize() -> None:
    source = inspect.getsource(runner.complete_governed_turn)

    assert "_required_audit_write(" in source
    assert "_finalize_genie_response(" in source
    assert 'action="genie.run_query"' in source
    assert 'event_type="RUN_GENIE"' in source


# ------------------------------------------------------- slot accounting


def _genie_slots() -> int:
    return int(_backpressure_controller._semaphores["genie"]._value)  # type: ignore[attr-defined]


def _await_slots(expected: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while _genie_slots() != expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _genie_slots() == expected


def _bp_headers() -> dict[str, str]:
    # The progress token is minted for ACTOR; the fixture clears the buckets.
    return {"X-Forwarded-Email": ACTOR, "X-Enable-Backpressure-Test": "1"}


@pytest.fixture
def slots() -> Any:
    _backpressure_controller.clear()
    baseline = _genie_slots()
    yield baseline
    _await_slots(baseline)
    _backpressure_controller.clear()


def test_the_job_holds_the_request_slot_until_it_succeeds(monkeypatch: Any, slots: int) -> None:
    gate = threading.Event()
    repo, audit, lakebase = FakeRepo(gate=gate), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)

    res = post_complete(client, headers=_bp_headers())

    assert res.status_code == 202
    assert repo.started.wait(10)
    assert _genie_slots() == slots - 1, "the running job keeps the request's Genie slot"
    gate.set()
    assert wait_for_job(lakebase)["status"] == "succeeded"
    _await_slots(slots)


@pytest.mark.parametrize("failure", ["completion_error", "runner_exception"])
def test_the_job_releases_its_slot_when_it_fails(monkeypatch: Any, slots: int, failure: str) -> None:
    repo = FakeRepo(error=RuntimeError("boom")) if failure == "completion_error" else FakeRepo()
    audit, lakebase = FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    if failure == "runner_exception":

        def claim_explodes(*_: Any, **__: Any) -> bool:
            raise RuntimeError("claim exploded")

        monkeypatch.setattr(jobs, "claim", claim_explodes)

    res = post_complete(TestClient(app), headers=_bp_headers())

    assert res.status_code == 202
    assert wait_for_job(lakebase, statuses=("failed",))["failure_kind"] == "internal"
    _await_slots(slots)


def test_a_joined_complete_adopts_nothing_and_the_runner_still_releases_once(monkeypatch: Any, slots: int) -> None:
    gate = threading.Event()
    repo, audit, lakebase = FakeRepo(gate=gate), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)
    headers = _bp_headers()

    first = post_complete(client, headers=headers)
    assert repo.started.wait(10)
    joined = post_complete(client, headers=headers)

    assert (first.status_code, joined.status_code) == (202, 202)
    assert joined.json()["job_id"] == first.json()["job_id"]
    assert _genie_slots() == slots - 1, "only the runner holds a slot; the joiner's was released"
    gate.set()
    wait_for_job(lakebase)
    _await_slots(slots)
    assert len(repo.calls) == 1


def test_the_legacy_inline_job_holds_the_request_slot_only_for_the_request(monkeypatch: Any, slots: int) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)

    res = post_complete(TestClient(app), respond_async=False, headers=_bp_headers())

    assert res.status_code == 200
    assert _genie_slots() == slots


def test_status_polls_take_no_genie_slot_and_forty_a_minute_never_429(monkeypatch: Any, slots: int) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)
    headers = _bp_headers()
    job_id = post_complete(client, headers=headers).json()["job_id"]
    wait_for_job(lakebase)
    _await_slots(slots)

    codes = [post_status(client, job_id, headers=headers).status_code for _ in range(40)]

    assert codes == [200] * 40
    assert _genie_slots() == slots


# ------------------------------------------------- offline equivalence


def _blocked_output(question: str) -> GenieMessageResponse:
    return answer(question).model_copy(
        update={"answer": "Call John Smith at 312-555-0142 about his 124,946 dollar balance."}
    )


def _deep(question: str) -> GenieMessageResponse:
    base = answer(question)
    return base.model_copy(
        update={
            "summary": "Illinois leads the footprint.",
            "sections": [
                GenieAnswerSection(title="By state", question="How are they spread by state?", answer="Illinois leads."),
            ],
        }
    )


def _policy_blocked(question: str) -> GenieMessageResponse:
    return GenieMessageResponse(
        conversation_id="conv-job-1",
        question=question,
        question_hash="0" * 16,
        answer="The generated response did not pass the governed output policy.",
        source="policy_blocked",
        trusted_assets=[],
        refusal_reason="output_policy",
    )


def _degraded(question: str) -> GenieMessageResponse:
    return GenieMessageResponse(
        conversation_id="conv-job-1",
        question=question,
        answer="Genie is temporarily unavailable. No data was generated for this question.",
        source="degraded",
        trusted_assets=[],
    )


_SCENARIOS = {
    "answered": (answer, False),
    "output_blocked": (_blocked_output, False),
    "repository_policy_blocked": (_policy_blocked, False),
    "degraded": (_degraded, False),
    "deep_sections": (_deep, False),
    "replayed": (answer, True),
}

#: What each scenario must really exercise, so the equivalence is not vacuous.
_EXPECTED_AUDIT_ACTIONS = {
    "answered": ["genie.run_query"],
    "output_blocked": ["genie.response_blocked"],
    "repository_policy_blocked": ["genie.run_query"],
    "degraded": ["genie.run_query"],
    "deep_sections": ["genie.run_query"],
    "replayed": [],
}


def _comparable(body: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in body.items() if key != "elapsed_ms"}
    body["actions"] = [
        {key: value for key, value in action.items() if key not in {"confirmation_token", "request_id"}}
        for action in body.get("actions") or []
    ]
    return body


def _audit_kwargs(audit: FakeAudit) -> list[dict[str, Any]]:
    return [{key: value for key, value in write.items() if key != "_correlation_id"} for write in audit.writes]


def _served(mode: str, scenario: str, monkeypatch: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    response, replayed = _SCENARIOS[scenario]
    repo, audit = FakeRepo(response=response), FakeAudit()
    lakebase = FakeJobLakebase(jobs_table=mode != "inline_no_table")
    lakebase.message_recorded = replayed
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)
    if mode == "async":
        job_id = post_complete(client).json()["job_id"]
        wait_for_job(lakebase)
        body = post_status(client, job_id).json()["response"]
    else:
        # Without the table only an older tab's complete runs inline (an
        # async one is refused): the pre-job path is the legacy body.
        res = post_complete(client, respond_async=False)
        assert res.status_code == 200
        body = res.json()
    return body, _audit_kwargs(audit)


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_async_legacy_and_pre_job_inline_serve_the_same_governed_turn(monkeypatch: Any, scenario: str) -> None:
    inline_body, inline_audit = _served("inline_no_table", scenario, monkeypatch)
    legacy_body, legacy_audit = _served("legacy_job", scenario, monkeypatch)
    async_body, async_audit = _served("async", scenario, monkeypatch)

    assert _comparable(legacy_body) == _comparable(inline_body)
    assert _comparable(async_body) == _comparable(inline_body)
    assert [write["action"] for write in inline_audit] == _EXPECTED_AUDIT_ACTIONS[scenario]
    assert legacy_audit == inline_audit
    assert async_audit == inline_audit
    for body in (inline_body, legacy_body, async_body):
        for action in body.get("actions") or []:
            assert action["confirmation_token"] and action["request_id"]


# ------------------------------------------- correlation id and stages


def test_runner_audit_rows_carry_the_enqueuing_request_correlation_id(monkeypatch: Any) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)

    res = post_complete(
        TestClient(app),
        headers={"X-Forwarded-Email": "lo@example.com", "X-Correlation-ID": "job-corr-0001"},
    )
    wait_for_job(lakebase)

    assert res.status_code == 202
    assert [write["_correlation_id"] for write in audit.run_query_rows()] == ["job-corr-0001"]


class _StubGenie:
    def __init__(self) -> None:
        self.resilient = SimpleNamespace(breaker=CircuitBreaker("genie", failure_threshold=1, cooldown_s=60.0))

    def resume_message(self, conversation_id: str, message_id: str) -> GenieResponse:
        return GenieResponse(
            answer_text="Illinois leads with 48,396 in-the-money borrowers, ahead of Texas at 10,914.",
            sql_query=(
                "SELECT state, COUNT(*) AS in_the_money_borrowers FROM mip.gold.borrower_360 "
                "WHERE in_the_money = TRUE GROUP BY state ORDER BY 2 DESC"
            ),
            sql_result_rows=[
                {"state": "IL", "in_the_money_borrowers": 48396},
                {"state": "TX", "in_the_money_borrowers": 10914},
            ],
            conversation_id=conversation_id,
            message_id=message_id,
            trusted_assets=["mip.gold.borrower_360"],
        )


def _real_repo_job(monkeypatch: Any, lakebase: FakeJobLakebase, *, respond_async: bool = True) -> Any:
    repo = DatabricksGenieRepository(_StubGenie())  # type: ignore[arg-type]
    install(monkeypatch, repo=repo, audit=FakeAudit(), lakebase=lakebase)
    return post_complete(TestClient(app), respond_async=respond_async)


def test_the_job_publishes_the_real_pipeline_stages_then_finalizing(monkeypatch: Any) -> None:
    lakebase = FakeJobLakebase()

    res = _real_repo_job(monkeypatch, lakebase)
    job = wait_for_job(lakebase)

    assert res.status_code == 202
    assert job["status"] == "succeeded"
    assert [stage for stage, _, _ in lakebase.stage_writes] == [
        "collecting",
        "verifying",
        "cross_checking",
        "finalizing",
    ]


def test_the_legacy_inline_path_publishes_no_stages(monkeypatch: Any) -> None:
    lakebase = FakeJobLakebase()

    res = _real_repo_job(monkeypatch, lakebase, respond_async=False)

    assert res.status_code == 200
    assert lakebase.stage_writes == []
    assert "stage" not in lakebase.job_statements


def test_failing_stage_writes_never_fail_the_answer_and_warn_once(monkeypatch: Any, caplog: Any) -> None:
    lakebase = FakeJobLakebase()
    lakebase.fail_stage_writes = True
    monkeypatch.setattr(jobs, "_WARNED_AT", {})

    with caplog.at_level(logging.WARNING, logger="mip-genie-jobs"):
        _real_repo_job(monkeypatch, lakebase)
        job = wait_for_job(lakebase)

    assert job["status"] == "succeeded"
    assert lakebase.job_statements.count("stage") == 4
    warnings = [record for record in caplog.records if record.getMessage() == "genie_job_stage_write_failed"]
    assert len(warnings) == 1
