"""The verified-sections writer and the status poll that serves it.

Audit 2026-09-21 ``genie-01`` phase 1b with integrator ruling R1: no
verified section reaches the client before its ``GENIE_SECTION_REVEALED``
audit row is written, in the same transaction as the ``sections_json``
UPDATE; a failed audit write withholds the section until the final answer;
an UPDATE the job no longer allows rolls the rows back. Below the floor only
the count is stored. Through the real status route (TestClient): sections
only while running with no cancel requested, only from the floor, only when
the poller's revision differs, and never on a terminal job.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import genie_completion_jobs as jobs
from backend.services import genie_completion_sections as sections
from backend.services.genie_progress import genie_question_binding_hash, genie_question_hash
from tests.fixtures.genie_job_lakebase import FakeJobLakebase
from tests.fixtures.genie_job_turns import (
    ACTOR,
    CONV,
    MSG,
    QUESTION,
    FakeAudit,
    FakeRepo,
    install,
    post_status,
)

_ROWS = [{"state": "IL", "borrowers": 48396}, {"state": "TX", "borrowers": 10914}]


def _item(index: int, *, rows: int = 2, withheld: bool = False) -> dict[str, Any]:
    table = [dict(_ROWS[n % 2], rank=n) for n in range(rows)]
    return {
        "index": index,
        "title": f"Section {index}",
        "question": f"How many borrowers are in the money in part {index}?",
        "answer": f"Illinois leads part {index} with 48,396 borrowers.",
        "trusted_assets": ["mip.gold.borrower_360"],
        "sql_query": f"SELECT state FROM mip.gold.borrower_360 -- {index}",
        "row_count": rows,
        "table_rows": table,
        "visualization": None,
        "narrative_withheld": withheld,
    }


def _running_job(lakebase: FakeJobLakebase, **overrides: Any) -> str:
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
    return str(lakebase.insert_row(**fields)["job_id"])


def _context() -> sections.RevealContext:
    return sections.RevealContext(
        actor=ACTOR,
        conversation_id=CONV,
        message_id=MSG,
        question_label=genie_question_hash(QUESTION),
        correlation_id="corr-reveal",
    )


def _revealed_rows(lakebase: FakeJobLakebase) -> list[dict[str, Any]]:
    return [row for row in lakebase.audit_rows if row["event_type"] == "GENIE_SECTION_REVEALED"]


def _write(lakebase: FakeJobLakebase, job_id: str, snapshot: list[dict[str, Any]], state: Any) -> None:
    sections.write_sections(lakebase, job_id, _context(), snapshot, state)


# ------------------------------------------------------------- the writer


def test_below_the_floor_only_the_count_is_stored_and_nothing_is_audited() -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    state = sections._JobReveal()

    _write(lakebase, job_id, [_item(0), _item(2)], state)

    assert lakebase.rows[job_id]["sections_json"] == {"v": 1, "rev": 1, "count": 2, "sections": []}
    assert _revealed_rows(lakebase) == []


def test_each_revealed_section_is_audited_once_in_the_same_transaction_and_never_carries_text() -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    state = sections._JobReveal()

    _write(lakebase, job_id, [_item(0), _item(1), _item(3, withheld=True)], state)
    _write(lakebase, job_id, [_item(0), _item(1), _item(3, withheld=True), _item(4)], state)

    stored = lakebase.rows[job_id]["sections_json"]
    assert (stored["rev"], stored["count"]) == (2, 4)
    assert [item["index"] for item in stored["sections"]] == [0, 1, 3, 4]
    rows = _revealed_rows(lakebase)
    assert len(rows) == 4, "one row per section, never a second one for a re-sent section"
    metadata = [json.loads(row["metadata"]) for row in rows]
    assert [m["section_index"] for m in metadata] == [0, 1, 3, 4]
    assert [m["verification_verdict"] for m in metadata] == ["verified", "verified", "verified_rows_digest", "verified"]
    assert {m["genie_job_id"] for m in metadata} == {job_id}
    assert all(m["question_hash"] == genie_question_hash(QUESTION) and len(m["sql_hash"]) == 16 for m in metadata)
    flat = json.dumps(rows, default=str)
    assert "in the money in part" not in flat and "48396" not in flat and "Illinois" not in flat


def test_an_audit_failure_withholds_the_section_until_the_final_answer(caplog: pytest.LogCaptureFixture) -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    state = sections._JobReveal()
    _write(lakebase, job_id, [_item(0), _item(1), _item(2)], state)
    lakebase.fail_audit_inserts = 1

    with caplog.at_level(logging.WARNING, logger="mip-genie-jobs"):
        _write(lakebase, job_id, [_item(0), _item(1), _item(2), _item(3)], state)
    _write(lakebase, job_id, [_item(0), _item(1), _item(2), _item(3), _item(4)], state)

    stored = lakebase.rows[job_id]["sections_json"]
    assert [item["index"] for item in stored["sections"]] == [0, 1, 2, 4], "section 3 stays withheld"
    assert stored["count"] == 5
    assert [json.loads(row["metadata"])["section_index"] for row in _revealed_rows(lakebase)] == [0, 1, 2, 4]


@pytest.mark.parametrize("state_change", ["cancel_requested_at", "recorded_at", "ended"])
def test_a_job_that_no_longer_allows_a_reveal_rolls_its_audit_rows_back(state_change: str) -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    if state_change == "ended":
        lakebase.rows[job_id]["status"] = "failed"
    else:
        lakebase.rows[job_id][state_change] = lakebase.now

    _write(lakebase, job_id, [_item(0), _item(1), _item(2)], sections._JobReveal())

    assert lakebase.rows[job_id]["sections_json"] is None
    assert _revealed_rows(lakebase) == []


def test_an_empty_snapshot_starts_a_new_sweep_and_rows_are_capped() -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    state = sections._JobReveal()
    _write(lakebase, job_id, [_item(0), _item(1), _item(2)], state)

    _write(lakebase, job_id, [], state)
    assert lakebase.rows[job_id]["sections_json"]["sections"] == []
    _write(lakebase, job_id, [_item(0, rows=80), _item(1), _item(2)], state)

    stored = lakebase.rows[job_id]["sections_json"]
    assert len(stored["sections"][0]["table_rows"]) == sections.REVEAL_MAX_ROWS
    assert stored["sections"][0]["row_count"] == 80
    assert len(_revealed_rows(lakebase)) == 6, "the second sweep's sections are new sections"


def test_an_oversized_snapshot_is_skipped_once_and_reveals_nothing(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(sections, "SECTIONS_JSON_MAX_BYTES", 200)
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    state = sections._JobReveal()

    with caplog.at_level(logging.WARNING, logger="mip-genie-jobs"):
        _write(lakebase, job_id, [_item(0), _item(1), _item(2)], state)
        _write(lakebase, job_id, [_item(0), _item(1), _item(2), _item(3)], state)

    assert lakebase.rows[job_id]["sections_json"] is None
    assert _revealed_rows(lakebase) == []
    assert [r.getMessage() for r in caplog.records].count("genie_job_sections_skipped") == 1


def test_the_process_writer_audits_off_the_caller_thread_under_the_turn_correlation_id() -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)

    sections.SECTION_WRITER.submit(lakebase, job_id, _context(), [_item(0), _item(1), _item(2)])
    deadline = time.monotonic() + 10
    while not sections.SECTION_WRITER.idle() and time.monotonic() < deadline:
        time.sleep(0.01)
    sections.SECTION_WRITER.forget(job_id)

    rows = _revealed_rows(lakebase)
    assert len(rows) == 3
    assert {row["correlation_id"] for row in rows} == {"corr-reveal"}


# --------------------------------------------------------- the status poll


def _poll(lakebase: FakeJobLakebase, job_id: str, monkeypatch: pytest.MonkeyPatch, **body: Any) -> dict[str, Any]:
    install(monkeypatch, repo=FakeRepo(), audit=FakeAudit(), lakebase=lakebase)
    res = post_status(TestClient(app), job_id, **body)
    assert res.status_code == 200, res.text
    return dict(res.json())


def test_the_poll_reveals_from_the_floor_and_only_a_new_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    state = sections._JobReveal()
    _write(lakebase, job_id, [_item(0), _item(1)], state)

    below = _poll(lakebase, job_id, monkeypatch)
    assert (below["verified_sections"], below["sections_rev"], below["revealed_sections"]) == (2, 1, None)

    _write(lakebase, job_id, [_item(0), _item(1), _item(2)], state)
    first = _poll(lakebase, job_id, monkeypatch, sections_rev=1)
    assert first["verified_sections"] == 3 and first["sections_rev"] == 2
    assert [s["title"] for s in first["revealed_sections"]] == ["Section 0", "Section 1", "Section 2"]
    assert all("index" not in s and "actions" not in s for s in first["revealed_sections"])
    again = _poll(lakebase, job_id, monkeypatch, sections_rev=2)
    assert (again["sections_rev"], again["revealed_sections"]) == (2, None)
    # The poll itself never writes an audit row.
    assert len(_revealed_rows(lakebase)) == 3


@pytest.mark.parametrize("ending", ["cancel_requested", "succeeded", "failed", "expired", "cancelled"])
def test_a_terminal_or_cancel_requested_job_never_carries_sections(monkeypatch: pytest.MonkeyPatch, ending: str) -> None:
    lakebase = FakeJobLakebase()
    job_id = _running_job(lakebase)
    _write(lakebase, job_id, [_item(0), _item(1), _item(2)], sections._JobReveal())
    row = lakebase.rows[job_id]
    if ending == "cancel_requested":
        row["cancel_requested_at"] = lakebase.now
    elif ending == "cancelled":
        row.update(status="cancelled", stage="cancelled", cancel_requested_at=lakebase.now)
    else:
        row.update(status=ending, stage={"succeeded": "done"}.get(ending, ending))
        if ending == "succeeded":
            row["result_json"] = None

    status = _poll(lakebase, job_id, monkeypatch)

    assert (status["verified_sections"], status["sections_rev"], status["revealed_sections"]) == (None, None, None)
