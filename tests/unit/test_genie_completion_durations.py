"""The typical-duration hint of a Genie completion job (audit genie-01).

``typical_seconds`` is the recent median (created -> recorded) of one class
of job, deep sweep or single turn, from at least ``MIN_SAMPLES`` recorded
jobs, clamped and cached. It rides the 202 and every status poll of a job
that still runs, never a terminal one, never one without a class, and never
writes an audit row. A failing query is no hint, not a failed poll.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import genie_completion_durations as durations
from backend.services import genie_completion_jobs as jobs
from backend.services import genie_completion_runner as runner
from backend.services.genie_progress import genie_question_binding_hash
from backend.services.lakebase import LakebaseError
from tests.fixtures.genie_job_lakebase import FakeJobLakebase
from tests.fixtures.genie_job_turns import (
    ACTOR,
    CONV,
    MSG,
    QUESTION,
    FakeAudit,
    FakeRepo,
    install,
    post_complete,
    post_status,
)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    durations._reset_for_tests()
    yield
    runner._reset_executor_for_tests()
    durations._reset_for_tests()


def _recorded(lakebase: FakeJobLakebase, seconds: float, *, deep: bool = False, count: int = 1) -> None:
    for _ in range(count):
        created = lakebase.now - timedelta(minutes=5)
        lakebase.insert_row(
            status="succeeded",
            stage="done",
            deep=deep,
            created_at=created,
            recorded_at=created + timedelta(seconds=seconds),
        )


def test_no_hint_below_the_sample_floor() -> None:
    # Literal counts: the floor is 20 samples (a count derived from the
    # constant would follow a lowered floor down).
    lakebase = FakeJobLakebase()
    _recorded(lakebase, 40, count=19)

    assert durations.typical_completion_seconds(lakebase, deep=False) is None

    durations._reset_for_tests()
    _recorded(lakebase, 40)
    assert durations.typical_completion_seconds(lakebase, deep=False) == 40


def test_the_classes_are_separate_and_older_rows_do_not_count() -> None:
    lakebase = FakeJobLakebase()
    _recorded(lakebase, 20, count=durations.MIN_SAMPLES)
    _recorded(lakebase, 170, deep=True, count=durations.MIN_SAMPLES)
    stale = lakebase.now - timedelta(days=15)
    for _ in range(50):
        lakebase.insert_row(status="succeeded", deep=True, created_at=stale, recorded_at=stale + timedelta(seconds=900))

    assert durations.typical_completion_seconds(lakebase, deep=False) == 20
    assert durations.typical_completion_seconds(lakebase, deep=True) == 170


def test_a_swept_delivered_answer_is_a_sample_and_a_failed_job_never_is() -> None:
    # A job that failed AFTER its commit point has recorded_at set but never
    # delivered: 30 of them do not fill the floor of 19 delivered answers.
    lakebase = FakeJobLakebase()
    _recorded(lakebase, 40, count=19)
    failed_at = lakebase.now - timedelta(minutes=5)
    for _ in range(30):
        lakebase.insert_row(
            status="failed",
            stage="failed",
            failure_kind="internal",
            deep=False,
            created_at=failed_at,
            recorded_at=failed_at + timedelta(seconds=900),
        )
    assert durations.typical_completion_seconds(lakebase, deep=False) is None

    # Every delivered answer outlives its window (the progress token's exp,
    # submit + 15 min), and the sweep every new job runs turns it 'expired':
    # its recorded_at - created_at is still a completion time.
    durations._reset_for_tests()
    _recorded(lakebase, 40)
    lakebase.advance(16 * 60)
    assert jobs.sweep_expired(lakebase) == 20
    assert sorted({row["status"] for row in lakebase.rows.values()}) == ["expired", "failed"]
    assert durations.typical_completion_seconds(lakebase, deep=False) == 40


@pytest.mark.parametrize(("seconds", "expected"), [(12.4, 12), (12.6, 13), (0.2, 1), (5000.0, 3600)])
def test_the_median_is_rounded_and_clamped(seconds: float, expected: int) -> None:
    lakebase = FakeJobLakebase()
    _recorded(lakebase, seconds, count=durations.MIN_SAMPLES)

    assert durations.typical_completion_seconds(lakebase, deep=False) == expected


def test_a_hint_is_cached_for_ten_minutes_and_a_miss_for_one(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(durations.time, "monotonic", lambda: clock["now"])
    lakebase, empty = FakeJobLakebase(), FakeJobLakebase()
    _recorded(lakebase, 30, count=durations.MIN_SAMPLES)

    for client in (lakebase, empty):
        durations.typical_completion_seconds(client, deep=False)
    clock["now"] += durations.MISS_TTL_S + 1
    for client in (lakebase, empty):
        durations.typical_completion_seconds(client, deep=False)
    assert (lakebase.job_statements.count("durations"), empty.job_statements.count("durations")) == (1, 2)

    clock["now"] += durations.CACHE_TTL_S
    assert durations.typical_completion_seconds(lakebase, deep=False) == 30
    assert lakebase.job_statements.count("durations") == 2


class _FailingLakebase(FakeJobLakebase):
    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if sql is durations._DURATIONS_SQL:
            raise LakebaseError("durations query refused (fake)")
        return super().fetchone(sql, params)


def test_a_failing_query_is_no_hint_and_one_throttled_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="mip-genie-jobs"):
        first = durations.typical_completion_seconds(_FailingLakebase(), deep=True)
        second = durations.typical_completion_seconds(_FailingLakebase(), deep=True)

    assert (first, second) == (None, None)
    warnings = [record for record in caplog.records if record.getMessage() == "genie_job_durations_failed"]
    assert len(warnings) == 1


# ---------------------------------------------------------- on the wire


def _own_job(lakebase: FakeJobLakebase, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "actor_email": ACTOR,
        "conversation_id": CONV,
        "message_id": MSG,
        "question_hash": genie_question_binding_hash(QUESTION),
        "status": "running",
        "stage": "verifying",
        "lease_owner": jobs.PROCESS_ID,
        "deep": False,
    }
    fields.update(overrides)
    return lakebase.insert_row(**fields)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, 45),
        ({"status": "queued", "stage": "queued"}, 45),
        ({"deep": None}, None),
        ({"status": "failed", "stage": "failed"}, None),
        ({"status": "cancelled", "stage": "cancelled", "cancel_requested_at": "now"}, None),
    ],
    ids=["running", "queued", "unclassified", "failed", "cancelled"],
)
def test_the_status_poll_carries_the_hint_only_while_a_classified_job_runs(
    monkeypatch: Any, overrides: dict[str, Any], expected: int | None
) -> None:
    audit, lakebase = FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=FakeRepo(), audit=audit, lakebase=lakebase)
    _recorded(lakebase, 45, count=durations.MIN_SAMPLES)
    if overrides.get("cancel_requested_at") == "now":
        overrides = {**overrides, "cancel_requested_at": lakebase.now}
    row = _own_job(lakebase, **overrides)

    status = post_status(TestClient(app), row["job_id"]).json()

    assert status["typical_seconds"] == expected
    assert audit.writes == []
    assert lakebase.audit_rows == []


def test_the_202_carries_the_hint_for_a_new_job(monkeypatch: Any) -> None:
    audit, lakebase = FakeAudit(), FakeJobLakebase()
    install(monkeypatch, repo=FakeRepo(), audit=audit, lakebase=lakebase)
    _recorded(lakebase, 38, count=durations.MIN_SAMPLES)

    res = post_complete(TestClient(app))

    # The 202 reports the job as enrolled: queued, so still running.
    assert res.status_code == 202
    assert (res.json()["status"], res.json()["typical_seconds"]) == ("queued", 38)
    assert audit.writes == [] or {write["action"] for write in audit.writes} == {"genie.run_query"}
