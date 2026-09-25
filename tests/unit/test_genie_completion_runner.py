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
  stage sink reports the real repository's stages from the runner thread
  only and the stage writer puts them on the job row off that thread, so a
  hung or failing stage write never holds or fails the answer.
* Cancel (audit genie-03): a cancel before the commit point records nothing
  (0 RUN_GENIE, 0 tokens, 0 session rows) and ends the job ``cancelled``;
  one after it changes nothing; the legacy inline job honours the same gate.
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
from backend.services.genie_completion_stages import GENIE_JOB_CANCELLED_HINT
from backend.services.genie_progress import genie_question_hash
from backend.services.repositories.databricks_repo import DatabricksGenieRepository
from backend.services.resilience import CircuitBreaker
from tests.fixtures.genie_job_lakebase import FakeJobLakebase
from tests.fixtures.genie_job_turns import (
    ACTOR,
    CONV,
    MSG,
    QUESTION,
    FakeAudit,
    FakeRepo,
    answer,
    install,
    post_complete,
    post_status,
    token,
    wait_for_job,
    wait_for_stage_writer,
)


@pytest.fixture(autouse=True)
def _drain_runner() -> Any:
    yield
    runner._reset_executor_for_tests()
    wait_for_stage_writer()


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


def test_a_heartbeat_that_cannot_start_releases_the_adopted_slot(monkeypatch: Any, slots: int) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)

    def track_explodes(*_: Any, **__: Any) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(jobs.HEARTBEAT, "track", track_explodes)

    res = post_complete(TestClient(app, raise_server_exceptions=False), headers=_bp_headers())

    assert res.status_code == 500
    assert repo.calls == []
    # The request adopted its Genie slot before the heartbeat failed to start;
    # the runner, not the middleware, owes the release.
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
    """Genie's side of a single live turn; ``gate`` holds ``resume_message``."""

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.resilient = SimpleNamespace(breaker=CircuitBreaker("genie", failure_threshold=1, cooldown_s=60.0))
        self.gate = gate
        self.started = threading.Event()

    def resume_message(self, conversation_id: str, message_id: str) -> GenieResponse:
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(10), "the test never released Genie"
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


def _real_repo_job(
    monkeypatch: Any,
    lakebase: FakeJobLakebase,
    *,
    respond_async: bool = True,
    genie: _StubGenie | None = None,
) -> Any:
    repo = DatabricksGenieRepository(genie or _StubGenie())  # type: ignore[arg-type]
    install(monkeypatch, repo=repo, audit=FakeAudit(), lakebase=lakebase)
    return post_complete(TestClient(app), respond_async=respond_async)


_PIPELINE = ["collecting", "verifying", "cross_checking", "finalizing"]


def _record_reported_stages(monkeypatch: Any) -> list[str]:
    """What the runner hands the stage writer, in report order."""

    reported: list[str] = []
    submit = jobs.STAGE_WRITER.submit

    def recording(
        lakebase: Any,
        job_id: str,
        stage: jobs.GenieJobStage,
        parts_done: int | None = None,
        parts_planned: int | None = None,
    ) -> None:
        reported.append(str(stage))
        submit(lakebase, job_id, stage, parts_done, parts_planned)

    monkeypatch.setattr(jobs.STAGE_WRITER, "submit", recording)
    return reported


def _in_report_order(written: list[str], reported: list[str]) -> bool:
    remaining = iter(reported)
    return all(stage in remaining for stage in written)


def test_the_job_reports_the_real_pipeline_stages_then_finalizing(monkeypatch: Any) -> None:
    lakebase = FakeJobLakebase()
    reported = _record_reported_stages(monkeypatch)

    res = _real_repo_job(monkeypatch, lakebase)
    job = wait_for_job(lakebase)
    wait_for_stage_writer()

    assert res.status_code == 202
    assert job["status"] == "succeeded"
    assert reported == _PIPELINE
    # Latest wins: a stage superseded before its write is skipped, and what
    # is written keeps the report order.
    assert _in_report_order([stage for stage, _, _ in lakebase.stage_writes], reported)


def test_the_stage_writer_puts_the_current_stage_on_the_running_job(monkeypatch: Any) -> None:
    lakebase, genie = FakeJobLakebase(), _StubGenie(gate=threading.Event())
    assert genie.gate is not None

    try:
        _real_repo_job(monkeypatch, lakebase, genie=genie)
        assert genie.started.wait(10)
        deadline = time.monotonic() + 10
        while lakebase.only_job()["stage"] != "collecting" and time.monotonic() < deadline:
            time.sleep(0.01)
        row = lakebase.only_job()
        assert (row["status"], row["stage"]) == ("running", "collecting")
    finally:
        genie.gate.set()
    assert wait_for_job(lakebase)["status"] == "succeeded"


class _HungStageLakebase(FakeJobLakebase):
    """Every stage write hangs until ``release``; nothing else does."""

    def __init__(self) -> None:
        super().__init__()
        self.stage_write_entered = threading.Event()
        self.release = threading.Event()

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        if sql is jobs._STAGE_SQL:
            self.stage_write_entered.set()
            self.release.wait(10)
        super().execute(sql, params)


def test_a_hung_stage_write_never_holds_the_governed_answer(monkeypatch: Any) -> None:
    # A slow Lakebase must not slow the governed thread: in the deep sweep's
    # wait loop that delay would leave finished sub-analyses uncollected. Genie
    # answers only once the first stage write is hung, so the rest of the turn
    # runs while it is.
    lakebase = _HungStageLakebase()
    genie = _StubGenie(gate=lakebase.stage_write_entered)

    try:
        res = _real_repo_job(monkeypatch, lakebase, genie=genie)
        job = wait_for_job(lakebase, timeout=5.0)

        assert res.status_code == 202
        assert job["status"] == "succeeded"
        assert job["result_json"] is not None
        assert not lakebase.release.is_set(), "the stage write was still hung when the answer was stored"
    finally:
        lakebase.release.set()
    wait_for_stage_writer()


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
        wait_for_stage_writer()

    assert job["status"] == "succeeded"
    assert 1 <= lakebase.job_statements.count("stage") <= len(_PIPELINE)
    warnings = [record for record in caplog.records if record.getMessage() == "genie_job_stage_write_failed"]
    assert len(warnings) == 1


# ------------------------------------------ cancel (audit 2026-09-21 genie-03)


def _cancel(client: TestClient, job_id: str) -> Any:
    return client.post(
        "/api/genie/message/cancel",
        json={
            "conversation_id": CONV,
            "message_id": MSG,
            "progress_token": token(),
            "job_id": job_id,
            "question_hash": genie_question_hash(QUESTION),
        },
        headers={"X-Forwarded-Email": ACTOR},
    )


def _cancelled_audit_rows(lakebase: FakeJobLakebase) -> list[dict[str, Any]]:
    return [row for row in lakebase.audit_rows if row["event_type"] == "GENIE_TURN_CANCELLED"]


_ENDED = ("cancelled", "succeeded", "failed")


def test_a_cancel_before_the_commit_records_nothing_and_releases_the_slot(monkeypatch: Any, slots: int) -> None:
    gate = threading.Event()
    repo, audit, lakebase = FakeRepo(gate=gate), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)

    job_id = post_complete(client, headers=_bp_headers()).json()["job_id"]
    assert repo.started.wait(10)
    cancelled = _cancel(client, job_id)
    gate.set()
    job = wait_for_job(lakebase, statuses=_ENDED)

    assert cancelled.json()["outcome"] == "cancelled"
    assert (job["status"], job["stage"], job["result_json"], job["recorded_at"]) == ("cancelled", "cancelled", None, None)
    assert audit.run_query_rows() == [], "0 RUN_GENIE"
    assert audit.writes == []
    assert lakebase.executed == [], "0 session or message rows"
    assert "commit" not in lakebase.job_statements, "the FINALIZING stage report ended it"
    assert len(_cancelled_audit_rows(lakebase)) == 1
    _await_slots(slots)
    assert not jobs.CANCELS.is_marked(job_id)
    assert job_id not in jobs.HEARTBEAT.tracked()
    status = post_status(client, job_id).json()
    assert (status["status"], status["terminal"], status["failed"], status["response"]) == ("cancelled", True, False, None)
    assert status["error_hint"] == GENIE_JOB_CANCELLED_HINT


def test_the_commit_point_refuses_a_cancel_this_process_never_saw(monkeypatch: Any) -> None:
    # Another process accepted the cancel and no heartbeat has marked it yet:
    # no stage report stops the turn, the DB commit point does.
    gate = threading.Event()
    repo, audit, lakebase = FakeRepo(gate=gate), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)

    job_id = post_complete(client).json()["job_id"]
    assert repo.started.wait(10)
    with lakebase._lock:
        lakebase.rows[job_id]["cancel_requested_at"] = lakebase.now
    gate.set()
    job = wait_for_job(lakebase, statuses=_ENDED)

    assert job["status"] == "cancelled"
    statements = [name for name in lakebase.job_statements if name != "stage"]
    assert statements[-3:] == ["commit", "cancel_state", "end_cancelled"]
    assert audit.writes == []
    assert lakebase.executed == []


class _GatedAudit(FakeAudit):
    """Holds the RUN_GENIE write: the record is committed, the row not yet written."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, **kwargs: Any) -> None:
        if kwargs.get("action") == "genie.run_query":
            self.entered.set()
            assert self.release.wait(10)
        super().write(**kwargs)


def test_a_cancel_after_the_commit_is_recorded_with_exactly_one_run_genie(monkeypatch: Any) -> None:
    repo, audit, lakebase = FakeRepo(), _GatedAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)

    job_id = post_complete(client).json()["job_id"]
    assert audit.entered.wait(10)
    late = _cancel(client, job_id)
    audit.release.set()
    job = wait_for_job(lakebase)

    assert late.json()["outcome"] == "recorded"
    assert job["status"] == "succeeded"
    assert job["cancel_requested_at"] is None and job["recorded_at"] is not None
    assert len(audit.run_query_rows()) == 1
    assert _cancelled_audit_rows(lakebase) == []
    assert not jobs.CANCELS.is_marked(job_id)


def test_a_stage_boundary_cancel_ends_the_job_at_the_next_report(monkeypatch: Any) -> None:
    lakebase, genie = FakeJobLakebase(), _StubGenie(gate=threading.Event())
    reported = _record_reported_stages(monkeypatch)
    assert genie.gate is not None

    job_id = _real_repo_job(monkeypatch, lakebase, genie=genie).json()["job_id"]
    assert genie.started.wait(10)
    assert _cancel(TestClient(app), job_id).json()["outcome"] == "cancelled"
    genie.gate.set()
    job = wait_for_job(lakebase, statuses=_ENDED)
    wait_for_stage_writer()

    assert job["status"] == "cancelled"
    # Genie answered after the cancel: the next report (VERIFYING) ended it,
    # before the cross-check, the rewrite and the commit.
    assert reported == ["collecting", "verifying"]
    assert "commit" not in lakebase.job_statements


def test_a_lost_commit_fails_the_job_with_dependency_down_and_no_run_genie(monkeypatch: Any) -> None:
    gate = threading.Event()
    repo, audit, lakebase = FakeRepo(gate=gate), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)

    job_id = post_complete(TestClient(app)).json()["job_id"]
    assert repo.started.wait(10)
    with lakebase._lock:
        lakebase.rows[job_id]["recorded_at"] = lakebase.now  # recorded elsewhere: the commit matches no row
    gate.set()
    job = wait_for_job(lakebase, statuses=("failed", "succeeded"))

    assert (job["status"], job["failure_kind"]) == ("failed", "dependency_down")
    assert audit.run_query_rows() == []
    assert lakebase.executed == []


def test_the_legacy_inline_job_honours_the_cancel_gate(monkeypatch: Any) -> None:
    gate = threading.Event()
    repo, audit, lakebase = FakeRepo(gate=gate), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)
    results: list[Any] = []
    caller = threading.Thread(target=lambda: results.append(post_complete(client, respond_async=False)))

    caller.start()
    assert repo.started.wait(10)
    job_id = lakebase.only_job()["job_id"]
    assert _cancel(client, job_id).json()["outcome"] == "cancelled"
    gate.set()
    caller.join(15)

    [res] = results
    assert res.status_code == 503
    assert res.json().get("retryable") is not True
    assert lakebase.only_job()["status"] == "cancelled"
    assert audit.writes == []
    assert lakebase.executed == []
    assert not jobs.CANCELS.is_marked(job_id)


def test_the_inline_job_commits_before_its_record(monkeypatch: Any) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)

    res = post_complete(TestClient(app), respond_async=False)

    assert res.status_code == 200
    assert lakebase.only_job()["recorded_at"] is not None
    assert "commit" in lakebase.job_statements
    assert len(audit.run_query_rows()) == 1
