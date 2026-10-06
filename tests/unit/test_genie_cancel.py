"""``POST /api/genie/message/cancel``: the owner's audited Stop (audit genie-03).

Through the real router and middleware stack (TestClient) with the in-memory
job table (tests/fixtures/genie_job_lakebase.py), whose ``transaction()``
rolls back on error: token authorization, the 16-hex label check, no
question on the wire, exactly one ``GENIE_TURN_CANCELLED`` row per accepted
cancel written in the flag's own transaction (rolled back with it), the
no-op outcomes (repeat, recorded, recording, ended) with no audit row, a
queued job ended at once whose later claim fails, and the audit payload under
the REAL metadata policy. A Stop past the commit point is answered by the
History settle (``recorded`` / ``recording``), which reads and never writes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import genie_completion_cancel as cancel
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
    post_complete,
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


# The History settle (2026-09-30 Stop copy ruling): the locked row decides
# whether the Stop is accepted; a Stop past the commit point is answered by
# whether the turn's History row exists, never by the job status alone.
_RECORDED = "set"
_SETTLE_TABLE = [
    # (status, recorded_at, history_row, cancel_requested) -> (outcome, status)
    pytest.param("running", _RECORDED, True, False, ("recorded", "running"), id="running_recorded_in_history"),
    pytest.param("running", _RECORDED, False, False, ("recording", "running"), id="running_recorded_still_writing"),
    pytest.param("succeeded", _RECORDED, True, False, ("recorded", "succeeded"), id="succeeded_in_history"),
    pytest.param("succeeded", _RECORDED, False, False, ("recording", "succeeded"), id="policy_blocked_success_not_kept"),
    pytest.param("expired", _RECORDED, True, False, ("recorded", "expired"), id="lease_lost_after_the_record"),
    pytest.param("expired", _RECORDED, False, False, ("recording", "expired"), id="expired_before_history"),
    pytest.param("failed", _RECORDED, False, False, ("recording", "failed"), id="failed_after_the_commit_point"),
    pytest.param("failed", _RECORDED, True, False, ("recorded", "failed"), id="failed_after_history"),
    pytest.param("failed", None, False, False, ("ended", "failed"), id="failed_never_recorded"),
    pytest.param("expired", None, False, False, ("ended", "expired"), id="expired_never_recorded"),
    pytest.param("running", None, False, True, ("cancelled", "running"), id="running_cancel_already_requested"),
    pytest.param("cancelled", None, False, True, ("cancelled", "cancelled"), id="already_cancelled"),
]


def _seed_state(lakebase: FakeJobLakebase, status: str, recorded_at: str | None, cancel_requested: bool) -> dict[str, Any]:
    stage = {"running": "finalizing", "succeeded": "done", "cancelled": "cancelled"}.get(status, status)
    return _seed(
        lakebase,
        status=status,
        stage=stage,
        recorded_at=lakebase.now if recorded_at == _RECORDED else None,
        cancel_requested_at=lakebase.now if cancel_requested else None,
        result_json={"v": 1} if status == "succeeded" else None,
    )


def _history(lakebase: FakeJobLakebase, **overrides: Any) -> None:
    lakebase.add_history_row(
        **{"actor_email": ACTOR, "conversation_id": CONV, "message_id": MSG, "question_hash": LABEL, **overrides}
    )


def _no_write_and_no_audit(lakebase: FakeJobLakebase, before: dict[str, Any]) -> None:
    assert set(lakebase.job_statements) <= {"cancel_lock", "settle"}, lakebase.job_statements
    assert lakebase.rows[before["job_id"]] == before, "no settle path writes"
    assert lakebase.audit_rows == []
    assert lakebase.executed == []


@pytest.fixture(autouse=True)
def _instant_settle(monkeypatch: Any) -> None:
    monkeypatch.setattr(cancel, "_SETTLE_INTERVAL_S", 0.0)


@pytest.mark.parametrize(("status", "recorded_at", "history_row", "cancel_requested", "expected"), _SETTLE_TABLE)
def test_a_stop_that_changes_nothing_answers_from_the_locked_row_and_history(
    monkeypatch: Any,
    status: str,
    recorded_at: str | None,
    history_row: bool,
    cancel_requested: bool,
    expected: tuple[str, str],
) -> None:
    client, audit, lakebase = _setup(monkeypatch)
    row = _seed_state(lakebase, status, recorded_at, cancel_requested)
    if history_row:
        _history(lakebase)
    before = dict(lakebase.rows[row["job_id"]])
    jobs.HEARTBEAT.track(row["job_id"], lakebase)  # type: ignore[arg-type]  # its runner may still run
    try:
        res = _cancel(client, row["job_id"])
    finally:
        jobs.HEARTBEAT.untrack(row["job_id"])

    assert res.status_code == 200, res.text
    assert (res.json()["outcome"], res.json()["status"]) == expected
    _no_write_and_no_audit(lakebase, before)
    assert audit.writes == []
    assert not jobs.CANCELS.is_marked(row["job_id"])
    # Only a Stop past the commit point reads History: once, or up to five
    # times while the job still runs with no row yet.
    reads = {"recorded": 1, "recording": 5 if expected[1] == "running" else 1}.get(expected[0], 0)
    assert lakebase.settle_reads == reads


@pytest.mark.parametrize(
    ("second_read", "expected"),
    [
        pytest.param({"history": True}, ("recorded", "running"), id="row_appears"),
        pytest.param({"history": True, "status": "succeeded"}, ("recorded", "succeeded"), id="row_and_success"),
        pytest.param({"status": "succeeded"}, ("recording", "succeeded"), id="succeeded_without_a_row"),
        pytest.param({"status": "failed"}, ("recording", "failed"), id="failed_before_the_row"),
    ],
)
def test_the_settle_rereads_a_running_job_until_it_moves(
    monkeypatch: Any, second_read: dict[str, Any], expected: tuple[str, str]
) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed_state(lakebase, "running", _RECORDED, False)

    def move(read: int) -> None:
        if read != 2:
            return
        if second_read.get("history"):
            _history(lakebase)
        if "status" in second_read:
            lakebase.rows[row["job_id"]]["status"] = second_read["status"]

    lakebase.on_settle_read = move
    res = _cancel(client, row["job_id"])

    assert (res.json()["outcome"], res.json()["status"]) == expected
    assert lakebase.settle_reads == 2
    assert lakebase.audit_rows == []


def test_a_history_row_under_a_non_genie_session_source_still_counts(monkeypatch: Any) -> None:
    # GENIE_MESSAGE_OWNERSHIP_SQL keeps only source='genie' sessions; History
    # shows every recorded row, so the settle must count it too.
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed_state(lakebase, "succeeded", _RECORDED, False)
    _history(lakebase, source="trusted_sql")

    res = _cancel(client, row["job_id"])

    assert (res.json()["outcome"], res.json()["status"]) == ("recorded", "succeeded")


@pytest.mark.parametrize(("offset_s", "expected"), [(5.0, "recorded"), (-5.0, "recording")], ids=["newer", "older"])
def test_a_fallback_id_row_counts_only_when_written_after_the_job(
    monkeypatch: Any, offset_s: float, expected: str
) -> None:
    # A response with no message id is stored as ``{source}-{hash}``: the
    # label arm finds it, but an older identical question never stands in.
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed_state(lakebase, "succeeded", _RECORDED, False)
    created = lakebase.rows[row["job_id"]]["created_at"]
    _history(lakebase, message_id=f"trusted_sql-{LABEL}", created_at=created + timedelta(seconds=offset_s))

    res = _cancel(client, row["job_id"])

    assert (res.json()["outcome"], res.json()["status"]) == (expected, "succeeded")


def test_another_actors_or_conversations_history_row_never_counts(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed_state(lakebase, "succeeded", _RECORDED, False)
    _history(lakebase, actor_email=OTHER_ACTOR)
    _history(lakebase, conversation_id="conv-other")

    res = _cancel(client, row["job_id"])

    assert res.json()["outcome"] == "recording"


def test_an_unreadable_first_settle_read_is_a_503_and_writes_nothing(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    row = _seed_state(lakebase, "succeeded", _RECORDED, False)
    before = dict(lakebase.rows[row["job_id"]])
    lakebase.fail_settle_reads = 1

    down = _cancel(client, row["job_id"])

    assert down.status_code == 503
    assert down.json()["detail"] == "lakebase is temporarily unavailable"
    _no_write_and_no_audit(lakebase, before)
    retried = _cancel(client, row["job_id"])
    assert (retried.status_code, retried.json()["outcome"]) == (200, "recording")


def test_a_later_settle_read_error_keeps_the_last_values(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    _seed_state(lakebase, "running", _RECORDED, False)

    def fail_the_second(read: int) -> None:
        lakebase.fail_settle_reads = 1 if read == 2 else 0

    lakebase.on_settle_read = fail_the_second
    res = _cancel(client, lakebase.only_job()["job_id"])

    assert (res.status_code, res.json()["outcome"], res.json()["status"]) == (200, "recording", "running")
    assert lakebase.settle_reads == 2


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


# ------------------------------------- the pre-cancel: a Stop before the 202


def _precancel(client: TestClient, *, omit: bool = False, **overrides: Any) -> Any:
    body: dict[str, Any] = {
        "conversation_id": CONV,
        "message_id": MSG,
        "progress_token": token(),
        "question_hash": LABEL,
        **({} if omit else {"job_id": None}),
        **overrides,
    }
    return client.post(CANCEL_ROUTE, json=body, headers=HEADERS)


@pytest.mark.parametrize("omit", [False, True], ids=["job_id_null", "job_id_absent"])
def test_a_stop_before_the_job_exists_inserts_it_cancelled_with_one_audit_row(monkeypatch: Any, omit: bool) -> None:
    client, audit, lakebase = _setup(monkeypatch)

    res = _precancel(client, omit=omit)

    assert res.status_code == 200, res.text
    row = lakebase.only_job()
    assert res.json() == {
        "kind": "genie_completion_cancel",
        "job_id": row["job_id"],
        "outcome": "cancelled",
        "status": "cancelled",
    }
    assert (row["status"], row["stage"], row["lease_owner"]) == ("cancelled", "cancelled", jobs.PROCESS_ID)
    assert row["question_hash"] == genie_question_binding_hash(QUESTION)
    assert row["cancel_requested_at"] is not None and row["precancelled_at"] is not None
    assert row["finished_at"] is not None
    assert (row["recorded_at"], row["result_json"], row["sections_json"], row["deep"]) == (None, None, None, None)
    assert lakebase.job_statements == ["cancel_lock", "cancel_precancel"]
    [written] = _cancelled_rows(lakebase)
    assert json.loads(written["metadata"]) == {
        "action": "genie.turn_cancelled",
        "conversation_id": CONV,
        "message_id": MSG,
        "question_hash": LABEL,
        "genie_job_id": row["job_id"],
        "status": "pre_job",
    }
    assert QUESTION not in json.dumps(written, default=str)
    assert audit.writes == []
    assert not jobs.CANCELS.is_marked(row["job_id"])


def test_a_repeat_stop_on_a_precancelled_turn_is_cancelled_with_no_write(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    first = _precancel(client)
    before = lakebase.only_job()

    by_turn = _precancel(client)
    by_job = _cancel(client, first.json()["job_id"])

    assert (by_turn.json()["outcome"], by_turn.json()["status"]) == ("cancelled", "cancelled")
    assert (by_job.json()["outcome"], by_job.json()["job_id"]) == ("cancelled", first.json()["job_id"])
    assert len(_cancelled_rows(lakebase)) == 1
    assert lakebase.only_job() == before
    assert lakebase.job_statements.count("cancel_precancel") == 1


@pytest.mark.parametrize(
    "case",
    ["named_job_without_a_row", "named_job_of_another_row", "ineligible_turn_ids"],
)
def test_a_stop_that_cannot_name_this_turns_job_is_a_404_and_writes_nothing(monkeypatch: Any, case: str) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    if case == "named_job_without_a_row":
        res = _cancel(client, DIGIT_HEAVY_JOB_ID)
    elif case == "named_job_of_another_row":
        _seed(lakebase)
        res = _cancel(client, DIGIT_HEAVY_JOB_ID)
    else:
        bad = "msg/with.dots"
        res = _precancel(client, message_id=bad, progress_token=token(message_id=bad))

    assert res.status_code == 404
    assert res.json()["detail"] == "Genie completion job not found"
    assert lakebase.audit_rows == []
    assert all(row["cancel_requested_at"] is None for row in lakebase.rows.values())
    assert "cancel_precancel" not in lakebase.job_statements


def test_an_audit_failure_rolls_the_precancel_insert_back_and_answers_503(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    lakebase.fail_audit_inserts = 1

    refused = _precancel(client)

    assert refused.status_code == 503
    assert refused.json()["detail"] == "lakebase is temporarily unavailable"
    assert lakebase.rows == {}, "the pre-cancelled row rolled back with its audit row"
    assert lakebase.audit_rows == []
    retried = _precancel(client)
    assert (retried.status_code, retried.json()["outcome"]) == (200, "cancelled")
    assert len(_cancelled_rows(lakebase)) == 1


def test_a_later_async_complete_joins_the_precancelled_job_and_runs_nothing(monkeypatch: Any) -> None:
    client, audit, lakebase = _setup(monkeypatch)
    repo = FakeRepo()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    stopped = _precancel(client)

    joined = post_complete(client)

    assert joined.status_code == 202, joined.text
    assert (joined.json()["job_id"], joined.json()["status"], joined.json()["terminal"]) == (
        stopped.json()["job_id"],
        "cancelled",
        True,
    )
    runner._reset_executor_for_tests()  # waits for anything enqueued (nothing)
    assert repo.calls == [], "no runner ran: the joined row is terminal and never adopted"
    assert audit.run_query_rows() == [], "no RUN_GENIE row"
    assert lakebase.only_job()["status"] == "cancelled"
    assert len(_cancelled_rows(lakebase)) == 1


def test_a_later_legacy_complete_joining_a_precancelled_job_is_a_503_and_runs_nothing(monkeypatch: Any) -> None:
    client, audit, lakebase = _setup(monkeypatch)
    repo = FakeRepo()
    install(monkeypatch, repo=repo, audit=audit, lakebase=lakebase)
    _precancel(client)

    joined = post_complete(client, respond_async=False)

    assert joined.status_code == 503
    assert repo.calls == []
    assert audit.run_query_rows() == []


def test_adoptable_never_takes_a_precancelled_row(monkeypatch: Any) -> None:
    client, _audit, lakebase = _setup(monkeypatch)
    _precancel(client)
    enrollment = jobs.create_or_join(
        lakebase,  # type: ignore[arg-type]
        actor=ACTOR,
        conversation_id=CONV,
        message_id=MSG,
        question_hash=genie_question_binding_hash(QUESTION),
        expires_at_epoch=int(lakebase.now.timestamp()) + 600,
        deep=False,
    )

    assert enrollment.created is False
    assert enrollment.job.status.value == "cancelled"
    assert jobs.adoptable(enrollment.job) is False
    assert jobs.claim(lakebase, enrollment.job.job_id) is False  # type: ignore[arg-type]


# ------------------------------------------- the Genie-side cancel tripwire


def test_the_pinned_sdk_genie_api_has_no_message_cancel() -> None:
    """Audit genie-03 residual (W5c): a Stop cancels only this app's record.

    The pinned databricks-sdk GenieAPI has no cancel operation for a
    chat-mode conversation message (the REST reference's only cancel is the
    agent-mode response cancel, an API this app does not use), and
    ``delete_conversation_message`` is a deletion, not a cancel. So the copy
    keeps 'Genie may keep the question as context'. An SDK bump that adds a
    cancel method goes red here and forces the decision
    (docs/observability.md section 9).
    """

    from databricks.sdk.service.dashboards import GenieAPI

    methods = [name for name in dir(GenieAPI) if not name.startswith("_")]
    assert "create_message" in methods and "delete_conversation_message" in methods, "the inspected surface"
    assert [name for name in methods if "cancel" in name.lower()] == []
