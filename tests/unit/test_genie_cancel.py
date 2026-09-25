"""``POST /api/genie/message/cancel``: the owner's audited Stop (audit genie-03).

Through the real router and middleware stack (TestClient) with the in-memory
job table (tests/fixtures/genie_job_lakebase.py), whose ``transaction()``
rolls back on error: token authorization, the 16-hex label check, no
question on the wire, exactly one ``GENIE_TURN_CANCELLED`` row per accepted
cancel written in the flag's own transaction (rolled back with it), the
no-op outcomes (repeat, recorded, ended) with no audit row, a queued job
ended at once whose later claim fails, and the audit payload under the REAL
metadata policy.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import genie_completion_jobs as jobs
from backend.services import genie_completion_runner as runner
from backend.services import observability
from backend.services.audit_metadata_policy import AuditMetadataValueViolation
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.genie_progress import genie_question_binding_hash, genie_question_hash
from backend.services.lakebase import LakebaseError
from tests.fixtures.genie_job_lakebase import FakeJobLakebase
from tests.fixtures.genie_job_turns import (
    ACTOR,
    CONV,
    HEADERS,
    MSG,
    QUESTION,
    FakeAudit,
    FakeRepo,
    install,
    token,
)

CANCEL_ROUTE = "/api/genie/message/cancel"
LABEL = genie_question_hash(QUESTION)
OTHER_ACTOR = "someone-else@example.com"
DIGIT_HEAVY_JOB_ID = "12345678-1234-4123-8123-123456789012"


@pytest.fixture(autouse=True)
def _clean_marks() -> Any:
    yield
    runner._reset_executor_for_tests()
    jobs.CANCELS._marked.clear()


def _setup(monkeypatch: Any, **lakebase_kwargs: Any) -> tuple[TestClient, FakeAudit, FakeJobLakebase]:
    audit, lakebase = FakeAudit(), FakeJobLakebase(**lakebase_kwargs)
    install(monkeypatch, repo=FakeRepo(), audit=audit, lakebase=lakebase)
    return TestClient(app), audit, lakebase


def _seed(lakebase: FakeJobLakebase, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "actor_email": ACTOR,
        "conversation_id": CONV,
        "message_id": MSG,
        "question_hash": genie_question_binding_hash(QUESTION),
        "status": "running",
        "stage": "researching",
        "lease_owner": jobs.PROCESS_ID,
        "deep": True,
    }
    fields.update(overrides)
    return lakebase.insert_row(**fields)


def _cancel(client: TestClient, job_id: str, *, headers: dict[str, str] | None = None, **overrides: Any) -> Any:
    body = {
        "conversation_id": CONV,
        "message_id": MSG,
        "progress_token": token(),
        "job_id": job_id,
        "question_hash": LABEL,
        **overrides,
    }
    return client.post(CANCEL_ROUTE, json=body, headers=headers or HEADERS)


def _cancelled_rows(lakebase: FakeJobLakebase) -> list[dict[str, Any]]:
    return [row for row in lakebase.audit_rows if row["event_type"] == "GENIE_TURN_CANCELLED"]


# ------------------------------------------------------------ authorization


def test_a_bad_token_is_a_400_and_changes_nothing(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase)

    res = _cancel(client, row["job_id"], progress_token=token()[:-4] + "AAAA")

    assert res.status_code == 400
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None
    assert lakebase.audit_rows == []


def test_another_actors_turn_is_a_404(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase)

    res = _cancel(
        client,
        row["job_id"],
        headers={"X-Forwarded-Email": OTHER_ACTOR},
        progress_token=token(actor=OTHER_ACTOR),
    )

    assert res.status_code == 404
    assert res.json()["detail"] == "Genie completion job not found"
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None
    assert lakebase.audit_rows == []


def test_a_label_that_is_not_the_tokens_turn_is_a_400(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase)
    other = "Which counties hold the most HELOC candidates?"

    wrong_label = _cancel(client, row["job_id"], question_hash="0" * 16)
    # A token and label that agree, but for another question than the job's.
    other_turn = _cancel(
        client, row["job_id"], progress_token=token(question=other), question_hash=genie_question_hash(other)
    )

    assert (wrong_label.status_code, other_turn.status_code) == (400, 400)
    assert other_turn.json()["detail"] == "question does not match the submitted Genie turn"
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None
    assert lakebase.audit_rows == []


def test_a_body_carrying_the_question_is_a_422(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase)

    res = _cancel(client, row["job_id"], question=QUESTION)

    assert res.status_code == 422
    assert QUESTION not in res.text
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None


def test_without_the_job_table_there_is_no_job_to_cancel(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch, cancel_columns=False)

    res = _cancel(client, DIGIT_HEAVY_JOB_ID)

    assert res.status_code == 404
    assert lakebase.job_statements == []


class _ProbeDownLakebase(FakeJobLakebase):
    """Lakebase unreachable for the table probe (never cached) until healed."""

    probe_down = True

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if sql is jobs._PROBE_SQL and self.probe_down:
            raise LakebaseError("connection refused (fake)")
        return super().fetchone(sql, params)


def test_a_lakebase_outage_at_the_table_probe_is_a_503_not_a_404(monkeypatch: Any) -> None:
    audit, lakebase = FakeAudit(), _ProbeDownLakebase()
    install(monkeypatch, repo=FakeRepo(), audit=audit, lakebase=lakebase)
    client = TestClient(app)
    row = _seed(lakebase)

    down = _cancel(client, row["job_id"])

    assert down.status_code == 503
    assert down.json()["detail"] == "lakebase is temporarily unavailable"
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None
    assert lakebase.audit_rows == []

    lakebase.probe_down = False
    healed = _cancel(client, row["job_id"])

    assert (healed.status_code, healed.json()["outcome"]) == (200, "cancelled")
    assert len(_cancelled_rows(lakebase)) == 1


# ----------------------------------------------------------------- accepted


def test_an_accepted_cancel_writes_one_audit_row_in_its_transaction_and_marks_the_runner(monkeypatch: Any) -> None:
    client, audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase)
    jobs.HEARTBEAT.track(row["job_id"], lakebase)  # type: ignore[arg-type]  # its runner runs here
    try:
        res = _cancel(client, row["job_id"])
    finally:
        jobs.HEARTBEAT.untrack(row["job_id"])

    assert res.status_code == 200
    assert res.json() == {
        "kind": "genie_completion_cancel",
        "job_id": row["job_id"],
        "outcome": "cancelled",
        "status": "running",
    }
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is not None
    assert lakebase.rows[row["job_id"]]["status"] == "running", "the runner ends a running job itself"
    assert lakebase.job_statements[-2:] == ["cancel_lock", "cancel_accept"]
    [written] = _cancelled_rows(lakebase)
    assert written["actor_email"] == ACTOR
    assert written["entity_type"] == "genie_message"
    metadata = json.loads(written["metadata"])
    assert metadata == {
        "action": "genie.turn_cancelled",
        "conversation_id": CONV,
        "message_id": MSG,
        "question_hash": LABEL,
        "genie_job_id": row["job_id"],
        "status": "running",
    }
    assert QUESTION not in json.dumps(written, default=str)
    assert audit.writes == [], "the cancel's row is written in the job transaction, not the store"
    assert jobs.CANCELS.is_marked(row["job_id"])


def test_a_repeat_is_idempotent_and_adds_no_audit_row(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase)

    first = _cancel(client, row["job_id"])
    second = _cancel(client, row["job_id"])

    assert (first.json()["outcome"], second.json()["outcome"]) == ("cancelled", "cancelled")
    assert len(_cancelled_rows(lakebase)) == 1
    assert lakebase.job_statements.count("cancel_accept") == 1


def test_another_process_running_job_is_accepted_but_not_marked_here(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase, lease_owner="other-process")

    res = _cancel(client, row["job_id"])

    assert res.json()["outcome"] == "cancelled"
    assert len(_cancelled_rows(lakebase)) == 1
    # That process's heartbeat RETURNING sees it (test_genie_completion_jobs).
    assert not jobs.CANCELS.is_marked(row["job_id"])


class _RunnerEndsBeforeTheMark(FakeJobLakebase):
    """The runner's commit UPDATE waited on the cancel's row lock, saw the
    cancel, and ended (untrack, then discard its mark) right after the
    cancel's transaction committed, before the route's post-commit mark."""

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with super().transaction() as conn:
            yield conn
        for job_id in list(self.rows):
            jobs.HEARTBEAT.untrack(job_id)
            jobs.CANCELS.discard(job_id)


def test_a_mark_after_the_runner_ended_is_never_left_behind(monkeypatch: Any) -> None:
    audit, lakebase = FakeAudit(), _RunnerEndsBeforeTheMark()
    install(monkeypatch, repo=FakeRepo(), audit=audit, lakebase=lakebase)
    row = _seed(lakebase)
    jobs.HEARTBEAT.track(row["job_id"], lakebase)  # type: ignore[arg-type]

    res = _cancel(TestClient(app), row["job_id"])

    assert res.json()["outcome"] == "cancelled"
    assert len(_cancelled_rows(lakebase)) == 1
    assert row["job_id"] not in jobs.HEARTBEAT.tracked()
    # Nobody would ever discard it: the process set would keep it forever.
    assert not jobs.CANCELS.is_marked(row["job_id"])


class _Slot:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


def test_a_queued_job_is_cancelled_at_once_and_its_later_claim_fails_and_releases_the_slot(monkeypatch: Any) -> None:
    client, audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase, status="queued", stage="queued")
    jobs.HEARTBEAT.track(row["job_id"], lakebase)  # type: ignore[arg-type]  # enqueued, waiting for a worker

    res = _cancel(client, row["job_id"])

    assert res.json() == {**res.json(), "outcome": "cancelled", "status": "cancelled"}
    cancelled = lakebase.rows[row["job_id"]]
    assert (cancelled["status"], cancelled["stage"]) == ("cancelled", "cancelled")
    assert cancelled["finished_at"] is not None
    assert len(_cancelled_rows(lakebase)) == 1
    assert not jobs.CANCELS.is_marked(row["job_id"]), "a terminal job needs no in-memory mark"

    repo = FakeRepo()
    turn = runner.GovernedTurn(
        actor=ACTOR,
        question=QUESTION,
        conversation_id=CONV,
        message_id=MSG,
        live_campaign_run_marker=None,
        repo=repo,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        lakebase=lakebase,  # type: ignore[arg-type]
    )
    slot = _Slot()
    runner._run_job(turn, row["job_id"], slot, "corr-cancel-queued")  # type: ignore[arg-type]

    # The job's correlation id never outlives the job on the calling thread
    # (a pooled worker keeps its context between jobs).
    assert observability.correlation_id_var.get() is None
    assert slot.released == 1
    assert repo.calls == []
    assert audit.writes == []
    assert row["job_id"] not in jobs.HEARTBEAT.tracked()
    assert lakebase.rows[row["job_id"]]["status"] == "cancelled"


# ------------------------------------------------------------------ no-ops


@pytest.mark.parametrize(
    "state",
    [
        {"status": "succeeded", "stage": "done", "result_json": {"v": 1}},
        {"status": "running", "stage": "finalizing", "recorded_at": "set"},
    ],
    ids=["succeeded", "running_recorded"],
)
def test_a_recorded_answer_is_not_cancelled_and_nothing_is_audited(monkeypatch: Any, state: dict[str, Any]) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    overrides = dict(state)
    if overrides.get("recorded_at") == "set":
        overrides["recorded_at"] = lakebase.now
    row = _seed(lakebase, **overrides)
    jobs.HEARTBEAT.track(row["job_id"], lakebase)  # type: ignore[arg-type]  # its runner may still run
    try:
        res = _cancel(client, row["job_id"])
    finally:
        jobs.HEARTBEAT.untrack(row["job_id"])

    assert res.status_code == 200
    assert res.json()["outcome"] == "recorded"
    assert res.json()["status"] == state["status"]
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None
    assert lakebase.audit_rows == []
    assert not jobs.CANCELS.is_marked(row["job_id"])


@pytest.mark.parametrize("status", ["failed", "expired"])
def test_a_failed_or_expired_job_has_ended(monkeypatch: Any, status: str) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase, status=status, stage=status)

    res = _cancel(client, row["job_id"])

    assert (res.status_code, res.json()["outcome"], res.json()["status"]) == (200, "ended", status)
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None
    assert lakebase.audit_rows == []


# --------------------------------------------------------------- fail closed


def test_an_audit_insert_failure_rolls_the_flag_back_and_a_retry_audits_once(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase)
    lakebase.fail_audit_inserts = 1
    jobs.HEARTBEAT.track(row["job_id"], lakebase)  # type: ignore[arg-type]  # its runner runs here
    try:
        refused = _cancel(client, row["job_id"])
        marked_after_refusal = jobs.CANCELS.is_marked(row["job_id"])
    finally:
        jobs.HEARTBEAT.untrack(row["job_id"])

    assert refused.status_code == 503
    assert refused.json()["detail"] == "lakebase is temporarily unavailable"
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is None, "the flag rolled back with the audit row"
    assert lakebase.audit_rows == []
    assert not marked_after_refusal

    retried = _cancel(client, row["job_id"])

    assert (retried.status_code, retried.json()["outcome"]) == (200, "cancelled")
    assert len(_cancelled_rows(lakebase)) == 1
    assert lakebase.rows[row["job_id"]]["cancel_requested_at"] is not None


# ------------------------------------------------------ the metadata policy


def test_the_audit_payload_passes_the_real_metadata_policy_with_a_digit_heavy_uuid(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed(lakebase, job_id=DIGIT_HEAVY_JOB_ID)

    res = _cancel(client, DIGIT_HEAVY_JOB_ID)

    assert res.status_code == 200, res.text
    [written] = _cancelled_rows(lakebase)
    assert json.loads(written["metadata"])["genie_job_id"] == DIGIT_HEAVY_JOB_ID
    assert row["job_id"] == DIGIT_HEAVY_JOB_ID


@pytest.mark.parametrize("value", ["not-a-uuid", "312-555-0142", "job 42"])
def test_genie_job_id_is_uuid_validated_by_the_metadata_policy(value: str) -> None:
    with pytest.raises(AuditMetadataValueViolation):
        build_safe_audit_metadata({"genie_job_id": value}, action="genie.turn_cancelled")

    accepted = build_safe_audit_metadata({"genie_job_id": DIGIT_HEAVY_JOB_ID}, action="genie.turn_cancelled")
    assert accepted["genie_job_id"] == DIGIT_HEAVY_JOB_ID
