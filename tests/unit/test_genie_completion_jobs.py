"""Genie completion as a server-side job (audit 2026-09-21 ``genie-01``).

Through the real router and middleware stack (TestClient) with an in-memory
job table (tests/fixtures/genie_job_lakebase.py): one job per (actor,
conversation, message) so a reloaded or retried complete never runs the
governed tail twice, the opt-in 202 versus the legacy 200, the status poll's
authorization, lease-based expiry on read and in the sweep every new job
runs, a job-less async complete refused (a legacy one inline), canned
failure hints only, and a stored
result that holds neither the question nor any live authorization.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.api.genie as genie_api
from backend.main import app
from backend.services import genie_completion_jobs as jobs
from backend.services import genie_completion_runner as runner
from backend.services.genie_client import GenieClientError
from backend.services.genie_completion_stages import (
    GENIE_JOB_EXPIRED_HINT,
    GENIE_JOB_FAILURE_HINTS,
    GenieJobFailureKind,
)
from backend.services.genie_deterministic import _policy_blocked_genie_output_response
from backend.services.genie_message_policy import GenieMessageRequest
from backend.services.lakebase import LakebaseError
from backend.services.resilience import DependencyDownError
from tests.fixtures.genie_job_lakebase import FakeJobLakebase
from tests.fixtures.genie_job_turns import (
    ACTOR,
    CONV,
    HEADERS,
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
)

STATUS_ROUTE = "/api/genie/message/status"


@pytest.fixture(autouse=True)
def _drain_runner() -> Any:
    yield
    runner._reset_executor_for_tests()


def _setup(monkeypatch: Any, **repo_kwargs: Any) -> tuple[TestClient, FakeRepo, FakeAudit, FakeJobLakebase]:
    repo, audit, lakebase = FakeRepo(**repo_kwargs), FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    return TestClient(app), repo, audit, lakebase


# ------------------------------------------------------------ submit flag


class _SubmitClient:
    def submit_message(self, question: str, conversation_id: str | None = None) -> tuple[str, str]:
        return CONV, MSG


@pytest.mark.parametrize("jobs_table", [True, False])
def test_submit_advertises_completion_jobs_only_when_the_table_exists(monkeypatch: Any, jobs_table: bool) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase(jobs_table=jobs_table)
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    monkeypatch.setattr(genie_api, "get_genie_client", lambda: _SubmitClient())

    res = TestClient(app).post("/api/genie/message/submit", json={"question": QUESTION}, headers=HEADERS)

    assert res.status_code == 200
    assert res.json()["completed"] is False
    assert res.json()["completion_jobs"] is jobs_table


# --------------------------------------------------- 202 job versus 200


def test_async_complete_answers_202_and_the_status_poll_delivers_the_answer(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _setup(monkeypatch)

    res = post_complete(client)

    assert res.status_code == 202
    body = res.json()
    assert body["kind"] == "genie_completion_job"
    assert body["status"] in {"queued", "running", "succeeded"}
    assert body["terminal"] is (body["status"] == "succeeded")
    job = wait_for_job(lakebase)
    assert job["status"] == "succeeded"

    polled = client.post(
        STATUS_ROUTE,
        json={
            "conversation_id": CONV,
            "message_id": MSG,
            "progress_token": token(),
            "question": QUESTION,
            "job_id": body["job_id"],
        },
        headers=HEADERS,
    )

    assert polled.status_code == 200
    status = polled.json()
    assert (status["status"], status["stage"], status["terminal"], status["failed"]) == ("succeeded", "done", True, False)
    assert status["stage_label"] == "Governed answer recorded"
    delivered = status["response"]
    assert delivered["question"] == QUESTION
    assert delivered["answer"].startswith("There are 124,946")
    assert delivered["actions"][0]["confirmation_token"]
    assert len(repo.calls) == 1
    assert len(audit.run_query_rows()) == 1
    # The job ran the governed tail: session + message rows recorded once.
    assert len(lakebase.executed) == 2


def test_legacy_complete_without_respond_async_answers_200_and_records_the_job(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _setup(monkeypatch)

    res = post_complete(client, respond_async=False)

    assert res.status_code == 200
    assert res.json()["answer"].startswith("There are 124,946")
    assert res.json()["actions"][0]["confirmation_token"]
    assert lakebase.only_job()["status"] == "succeeded"
    assert len(repo.calls) == 1
    assert len(audit.run_query_rows()) == 1


# ---------------------------------------------------- one job per turn


def test_a_repeated_async_complete_joins_the_job_and_never_completes_twice(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _setup(monkeypatch)

    first = post_complete(client)
    wait_for_job(lakebase)
    second = post_complete(client)

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["status"] == "succeeded"
    assert second.json()["response"]["answer"].startswith("There are 124,946")
    assert len(repo.calls) == 1
    assert len(audit.run_query_rows()) == 1


def test_a_legacy_retry_joins_the_finished_job_without_a_second_audit(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _setup(monkeypatch)

    first = post_complete(client, respond_async=False)
    retried = post_complete(client, respond_async=False)

    assert (first.status_code, retried.status_code) == (200, 200)
    assert retried.json()["answer"] == first.json()["answer"]
    assert retried.json()["question"] == QUESTION
    assert retried.json()["actions"][0]["request_id"] == first.json()["actions"][0]["request_id"]
    assert len(repo.calls) == 1
    assert len(audit.run_query_rows()) == 1


@pytest.mark.parametrize("respond_async", [True, False])
def test_two_concurrent_completes_run_one_completion_and_one_audit(monkeypatch: Any, respond_async: bool) -> None:
    gate = threading.Event()
    client, repo, audit, lakebase = _setup(monkeypatch, gate=gate)
    monkeypatch.setattr(runner, "JOINED_POLL_S", 0.01)
    results: list[Any] = []

    def call() -> None:
        results.append(post_complete(client, respond_async=respond_async))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert repo.started.wait(10)
    if respond_async:
        for thread in threads:
            thread.join(10)
        assert sorted(result.status_code for result in results) == [202, 202]
    gate.set()
    for thread in threads:
        thread.join(15)
    wait_for_job(lakebase)

    assert len(repo.calls) == 1
    assert len(audit.run_query_rows()) == 1
    if not respond_async:
        assert [result.status_code for result in results] == [200, 200]
        assert results[0].json()["answer"] == results[1].json()["answer"]


# ---------------------------------------------------- status authorization


def test_status_rejects_a_foreign_actor_a_wrong_question_and_an_unknown_job(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    job_id = post_complete(client).json()["job_id"]
    wait_for_job(lakebase)

    foreign = post_status(client, job_id, headers={"X-Forwarded-Email": "someone-else@example.com"})
    wrong_question = post_status(
        client, job_id, question="Completely different question about equity?", progress_token=token()
    )
    tampered = post_status(client, job_id, progress_token=token()[:-4] + "AAAA")
    unknown = post_status(client, "00000000-0000-4000-8000-000000000000")
    malformed = post_status(client, "not-a-job-id")

    assert foreign.status_code == 403
    assert wrong_question.status_code == 400
    assert tampered.status_code == 400
    assert unknown.status_code == 404
    assert malformed.status_code == 422
    for res in (foreign, wrong_question, tampered, unknown):
        assert "124,946" not in res.text


def test_status_for_another_conversation_of_the_same_actor_is_a_404(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    job_id = post_complete(client).json()["job_id"]
    wait_for_job(lakebase)

    other = client.post(
        STATUS_ROUTE,
        json={
            "conversation_id": "conv-other",
            "message_id": MSG,
            "progress_token": token(conversation_id="conv-other"),
            "question": QUESTION,
            "job_id": job_id,
        },
        headers=HEADERS,
    )

    assert other.status_code == 404


# ---------------------------------------------------------------- expiry


def _seed_own_job(lakebase: FakeJobLakebase, **overrides: Any) -> dict[str, Any]:
    from backend.services.genie_progress import genie_question_binding_hash

    return lakebase.insert_row(
        actor_email=ACTOR,
        conversation_id=CONV,
        message_id=MSG,
        question_hash=genie_question_binding_hash(QUESTION),
        **overrides,
    )


def test_a_stale_lease_expires_on_read_and_a_live_lease_is_untouched(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    row = _seed_own_job(lakebase, status="running", stage="researching", lease_owner="other-worker")

    lakebase.advance(jobs.LEASE_S - 5)
    live = post_status(client, row["job_id"]).json()
    lakebase.advance(10)
    stale = post_status(client, row["job_id"]).json()

    # A runner on another worker that still renews its lease is never expired.
    assert (live["status"], live["stage"], live["terminal"]) == ("running", "researching", False)
    assert (stale["status"], stale["stage"], stale["failed"]) == ("expired", "expired", True)
    assert stale["error_hint"] == GENIE_JOB_EXPIRED_HINT
    assert stale["response"] is None


def test_a_restarted_process_neither_renews_nor_finishes_the_dead_process_job(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    row = _seed_own_job(lakebase, lease_owner=jobs.PROCESS_ID)
    jobs.HEARTBEAT.track(row["job_id"], lakebase)  # the old process was renewing it
    try:
        monkeypatch.setattr(jobs, "PROCESS_ID", "restarted-process")
        lakebase.advance(jobs.LEASE_S + 1)
        jobs.HEARTBEAT.beat()  # a beat from the NEW identity renews nothing

        assert jobs.claim(lakebase, row["job_id"]) is False
        assert jobs.succeed(lakebase, row["job_id"], {"v": 1}) is False
        expired = post_status(client, row["job_id"]).json()
    finally:
        jobs.HEARTBEAT.untrack(row["job_id"])

    assert expired["status"] == "expired"


def test_the_heartbeat_keeps_a_long_job_alive(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    row = _seed_own_job(lakebase, status="running", lease_owner=jobs.PROCESS_ID)
    jobs.HEARTBEAT.track(row["job_id"], lakebase)
    try:
        for _ in range(12):  # four lease lengths of a 200 s deep sweep
            lakebase.advance(jobs.HEARTBEAT_S)
            jobs.HEARTBEAT.beat()
        polled = post_status(client, row["job_id"]).json()
    finally:
        jobs.HEARTBEAT.untrack(row["job_id"])

    assert polled["status"] == "running"


def test_a_served_answer_past_its_window_expires_and_its_result_is_nulled(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    job_id = post_complete(client).json()["job_id"]
    wait_for_job(lakebase)
    assert post_status(client, job_id).json()["response"] is not None

    lakebase.advance(16 * 60)
    expired = post_status(client, job_id).json()

    assert (expired["status"], expired["response"]) == ("expired", None)
    assert lakebase.only_job()["result_json"] is None


def test_every_new_job_sweeps_other_actors_stale_rows_and_nulls_their_answers(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    served = lakebase.insert_row(status="succeeded", stage="done", result_json={"v": 1, "response": {"answer": "x"}})
    dead = lakebase.insert_row(status="running", stage="verifying")
    fresh = lakebase.insert_row(status="succeeded", stage="done", result_json={"v": 1})
    lakebase.advance(jobs.LEASE_S + 1)
    lakebase.rows[fresh["job_id"]]["expires_at"] = lakebase.now.replace(year=2027)
    lakebase.rows[served["job_id"]]["expires_at"] = lakebase.now.replace(year=2025)

    assert post_complete(client).status_code == 202

    rows = lakebase.rows
    assert (rows[served["job_id"]]["status"], rows[served["job_id"]]["result_json"]) == ("expired", None)
    assert rows[dead["job_id"]]["status"] == "expired"
    assert rows[fresh["job_id"]]["status"] == "succeeded"
    assert rows[fresh["job_id"]]["result_json"] == {"v": 1}
    assert lakebase.job_statements[:2] == ["sweep", "insert"]


def test_the_sweep_is_bounded(monkeypatch: Any) -> None:
    lakebase = FakeJobLakebase()
    for _ in range(jobs.SWEEP_LIMIT + 7):
        lakebase.insert_row(status="running")
    lakebase.advance(jobs.LEASE_S + 1)

    assert jobs.sweep_expired(lakebase) == jobs.SWEEP_LIMIT
    assert sum(row["status"] == "expired" for row in lakebase.rows.values()) == jobs.SWEEP_LIMIT


# -------------------------------------------------- table not provisioned


class _ProbeBlipLakebase(FakeJobLakebase):
    """The job table exists, but the capability probe's query fails once."""

    def __init__(self) -> None:
        super().__init__()
        self.probe_failures = 1

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if sql is jobs._PROBE_SQL and self.probe_failures:
            self.probe_failures -= 1
            raise LakebaseError("connection reset (fake)")
        return super().fetchone(sql, params)


_INELIGIBLE_CONV = "conv.job.1"


def _unjobbed_complete(client: TestClient, case: str, *, respond_async: bool) -> Any:
    """A complete that cannot get a job: no table, or ids outside the job grammar."""

    if case == "no_table":
        return post_complete(client, respond_async=respond_async)
    body: dict[str, Any] = {
        "conversation_id": _INELIGIBLE_CONV,
        "message_id": MSG,
        "progress_token": token(conversation_id=_INELIGIBLE_CONV),
        "question": QUESTION,
    }
    if respond_async:
        body["respond_async"] = True
    return client.post("/api/genie/message/complete", json=body, headers=HEADERS)


@pytest.mark.parametrize("case", ["no_table", "ineligible_ids"])
def test_an_async_complete_that_cannot_get_a_job_is_refused_before_any_genie_work(monkeypatch: Any, case: str) -> None:
    # The browser re-sends an async complete (a timeout, a reload); a job-less
    # inline run could not be joined, so the re-send would run the governed
    # tail and write RUN_GENIE a second time. Refused, and never re-sent.
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase(jobs_table=case != "no_table")
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)

    res = _unjobbed_complete(TestClient(app), case, respond_async=True)

    assert res.status_code == 503
    assert res.json()["detail"] == "lakebase is temporarily unavailable"
    assert res.json().get("retryable") is not True, "the transport must not re-send it"
    assert repo.calls == []
    assert audit.writes == []
    assert lakebase.rows == {}
    assert lakebase.executed == []


@pytest.mark.parametrize("case", ["no_table", "ineligible_ids"])
def test_a_legacy_complete_that_cannot_get_a_job_runs_the_inline_governed_path(monkeypatch: Any, case: str) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), FakeJobLakebase(jobs_table=case != "no_table")
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)

    res = _unjobbed_complete(TestClient(app), case, respond_async=False)

    assert res.status_code == 200
    assert res.json()["answer"].startswith("There are 124,946")
    assert lakebase.rows == {}
    assert lakebase.job_statements == []
    assert len(repo.calls) == 1
    assert len(audit.run_query_rows()) == 1


def test_a_probe_blip_refuses_the_async_complete_and_its_resend_runs_the_turn_once(monkeypatch: Any) -> None:
    repo, audit, lakebase = FakeRepo(), FakeAudit(), _ProbeBlipLakebase()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    client = TestClient(app)

    refused = post_complete(client)

    assert refused.status_code == 503
    assert repo.calls == []
    assert audit.writes == []

    accepted = post_complete(client)
    job = wait_for_job(lakebase)

    assert accepted.status_code == 202
    assert job["status"] == "succeeded"
    assert len(repo.calls) == 1
    assert len(audit.run_query_rows()) == 1


# ------------------------------------------------------ canned failures


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (GenieClientError("warehouse said: state=42 col=borrower_ssn"), GenieJobFailureKind.UPSTREAM_ERROR),
        (
            DependencyDownError("genie", reason="breaker open col=borrower_ssn", kind=DependencyDownError.KIND_BREAKER_OPEN),
            GenieJobFailureKind.DEPENDENCY_DOWN,
        ),
        (HTTPException(status_code=503, detail="lakebase down col=borrower_ssn"), GenieJobFailureKind.DEPENDENCY_DOWN),
        (RuntimeError("unexpected col=borrower_ssn"), GenieJobFailureKind.INTERNAL),
    ],
)
def test_a_failed_job_carries_only_its_canned_hint(monkeypatch: Any, error: BaseException, kind: GenieJobFailureKind) -> None:
    client, _repo, audit, lakebase = _setup(monkeypatch, error=error)

    job_id = post_complete(client).json()["job_id"]
    job = wait_for_job(lakebase)
    status = post_status(client, job_id)

    assert job["failure_kind"] == kind.value
    body = status.json()
    assert (body["status"], body["stage"], body["failed"], body["terminal"]) == ("failed", "failed", True, True)
    assert body["error_hint"] == GENIE_JOB_FAILURE_HINTS[kind]
    assert "borrower_ssn" not in status.text
    assert audit.run_query_rows() == []


# -------------------------------------- stored result: no question, no auth


def _policy_blocked(question: str) -> Any:
    return _policy_blocked_genie_output_response(
        GenieMessageRequest(question=question, conversation_id=CONV), answer(question)
    )


@pytest.mark.parametrize("turn", ["answered", "policy_blocked"])
def test_the_stored_result_holds_neither_the_question_nor_a_confirmation_token(monkeypatch: Any, turn: str) -> None:
    response = answer if turn == "answered" else _policy_blocked
    client, _repo, _audit, lakebase = _setup(monkeypatch, response=response)

    job_id = post_complete(client).json()["job_id"]
    wait_for_job(lakebase)
    stored = json.dumps(lakebase.only_job()["result_json"])
    delivered = post_status(client, job_id).json()["response"]

    assert QUESTION not in stored
    assert "confirmation_token" not in stored
    legacy = response(QUESTION)
    assert delivered["question"] == legacy.question
    if turn == "answered":
        action = delivered["actions"][0]
        assert action["confirmation_token"]
        assert action["request_id"] in stored  # same request id: a retried action still dedupes
    else:
        assert delivered["source"] == "policy_blocked"
        assert delivered["actions"] == []


def test_each_poll_re_signs_the_same_actions_for_the_polling_actor(monkeypatch: Any) -> None:
    client, _repo, _audit, lakebase = _setup(monkeypatch)
    job_id = post_complete(client).json()["job_id"]
    wait_for_job(lakebase)

    first = post_status(client, job_id).json()["response"]["actions"][0]
    second = post_status(client, job_id).json()["response"]["actions"][0]

    assert first["request_id"] == second["request_id"]
    assert first["confirmation_token"] and second["confirmation_token"]
    assert first["confirmation_token"] != second["confirmation_token"]


# ------------------------------------------------ the served-answer window


def test_the_browser_resume_window_stays_inside_the_token_that_authorizes_it() -> None:
    import re
    from pathlib import Path

    from backend.services.genie_progress import GENIE_PROGRESS_TOKEN_TTL_S

    source = (Path(__file__).resolve().parents[2] / "frontend/src/lib/genieAsk.ts").read_text(encoding="utf-8")
    match = re.search(r"export const JOB_RESUME_WINDOW_MS = (\d+) \* 60_000;", source)
    assert match is not None
    resume_window_s = int(match.group(1)) * 60
    assert GENIE_PROGRESS_TOKEN_TTL_S == 15 * 60
    assert 0 < resume_window_s < GENIE_PROGRESS_TOKEN_TTL_S
