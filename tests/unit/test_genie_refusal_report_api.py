"""Contract tests for the hash-only Genie refusal false-positive report.

``POST /api/genie/refusal-report`` (audit 2026-09-21 genie-05). The Lakebase
store is faked the way ``test_genie_feedback_api.py`` fakes it: a
transaction handle whose ``execute`` answers the report INSERT / audit
INSERT / audit-link UPDATE in order, so the tests prove the row shape, the
one-transaction audit write, replay idempotency, and that no prompt text can
enter the ledger.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from backend.main import _backpressure_controller, app
from backend.schemas.audit import AuditEvent
from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.genie_deterministic import (
    _block_unsafe_genie_output,
    _deterministic_genie_response,
)
from backend.services.genie_message_policy import GenieMessageRequest
from backend.services.genie_refusal_reason import refusal_report_hash
from backend.services.lakebase import LakebaseError, get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)
ACTOR = "lo@example.com"
ACTOR_HEADERS = {"X-Forwarded-Email": ACTOR}
QUESTION = "Which zyrplax borrowers are eligible for a HELOC?"
QUESTION_HASH = refusal_report_hash(QUESTION)
REPORT_PATH = "/api/genie/refusal-report"


class _ExecuteResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _FakeConn:
    def __init__(self, lakebase: _FakeLakebase) -> None:
        self.lakebase = lakebase

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> _ExecuteResult:
        return _ExecuteResult(self.lakebase.handle_execute(sql, params or {}))


class _FakeLakebase:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.reports: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.audit_rows: list[dict[str, Any]] = []
        self.order: list[str] = []

    @contextmanager
    def transaction(self) -> Any:
        if self.fail:
            raise LakebaseError("lakebase down")
        yield _FakeConn(self)

    def handle_execute(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "INSERT INTO mip_app.genie_refusal_reports" in sql:
            key = (str(params["actor_email"]), str(params["question_hash"]), str(params["refusal_reason"]))
            if key in self.reports:
                self.order.append("duplicate")
                return None
            row = {"report_id": uuid4(), **params, "audit_event_id": None}
            self.reports[key] = row
            self.order.append("report")
            return {"report_id": row["report_id"]}
        if "INSERT INTO mip_app.action_audit" in sql:
            row = {
                "audit_id": uuid4(),
                "audit_sequence": len(self.audit_rows) + 1,
                "event_at": None,
                **params,
            }
            self.audit_rows.append(row)
            self.order.append("audit")
            return row
        if "UPDATE mip_app.genie_refusal_reports" in sql:
            for row in self.reports.values():
                if str(row["report_id"]) == str(params["report_id"]):
                    row["audit_event_id"] = params["audit_event_id"]
                    self.order.append("link")
                    return {"report_id": row["report_id"]}
            return None
        raise AssertionError(f"unexpected execute SQL: {sql}")


def _install(lakebase: _FakeLakebase) -> None:
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase


def teardown_function(_func: object) -> None:
    app.dependency_overrides.pop(get_lakebase_client, None)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "question_hash": QUESTION_HASH,
        "refusal_reason": "unreviewed_criterion",
        "conversation_id": "01f13d4968af1b249dc388fd5b18b195",
        "message_id": None,
    }
    body.update(overrides)
    return body


def test_report_writes_one_row_and_one_audit_event_in_order() -> None:
    lakebase = _FakeLakebase()
    _install(lakebase)

    response = client.post(REPORT_PATH, json=_body(), headers=ACTOR_HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["duplicate"] is False
    assert body["report_id"]
    assert body["audit_event_id"]
    assert lakebase.order == ["report", "audit", "link"]
    report = next(iter(lakebase.reports.values()))
    assert report["actor_email"] == ACTOR
    assert report["question_hash"] == QUESTION_HASH
    assert report["refusal_reason"] == "unreviewed_criterion"
    assert report["conversation_id"] == "01f13d4968af1b249dc388fd5b18b195"
    assert report["message_id"] is None
    assert str(report["audit_event_id"]) == body["audit_event_id"]
    audit = lakebase.audit_rows[0]
    assert audit["actor_email"] == ACTOR
    assert audit["event_type"] == "GENIE_REFUSAL_REPORT"
    assert audit["entity_type"] == "genie_message"
    assert '"action_type": "refusal_report"' in audit["metadata"]
    assert '"refusal_reason": "unreviewed_criterion"' in audit["metadata"]
    # The ledger keeps its established 16-hex label; the full digest lives
    # only on the report row.
    assert QUESTION_HASH[:16] in audit["metadata"]
    assert QUESTION_HASH not in audit["metadata"]


def _report_from_refusal(lakebase: _FakeLakebase, refused: GenieMessageResponse) -> dict[str, Any]:
    """File the report exactly as the card does, from the refused turn's own fields."""

    _install(lakebase)
    response = client.post(
        REPORT_PATH,
        json={
            "question_hash": refused.refusal_report_hash,
            "refusal_reason": refused.refusal_reason,
            "conversation_id": refused.conversation_id or None,
            "message_id": refused.message_id,
        },
        headers=ACTOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert len(lakebase.audit_rows) == 1
    return lakebase.audit_rows[0]


def _single_ledger_row(audit: InMemoryAuditStore, action: str) -> AuditEvent:
    rows = [event for event in audit.list(limit=50) if event.action == action]
    assert len(rows) == 1, [event.action for event in audit.list(limit=50)]
    return rows[0]


@pytest.mark.parametrize(
    ("prompt", "conversation_id"),
    [
        # Mixed case and runs of whitespace: any case/space folding on one
        # side only would split the join.
        ("Target  HISPANIC   neighborhoods with this Offer.", None),
        ("Which  ZYRPLAX borrowers are   eligible for a HELOC?", "01f13d4968af1b249dc388fd5b18b195"),
    ],
)
def test_report_audit_joins_the_refused_prompt_ledger_row(
    prompt: str, conversation_id: str | None
) -> None:
    ledger = InMemoryAuditStore()
    refused = _deterministic_genie_response(
        GenieMessageRequest(question=prompt, conversation_id=conversation_id),
        actor=ACTOR,
        audit=ledger,
        background=BackgroundTasks(),
        lakebase=MagicMock(),
        borrower_repo=MagicMock(),
    )
    assert refused is not None and refused.source == "refused"
    refusal_row = _single_ledger_row(ledger, "genie.refused_prompt")

    report_row = _report_from_refusal(_FakeLakebase(), refused)

    metadata = json.loads(report_row["metadata"])
    assert metadata["question_hash"] == refusal_row.payload_json["question_hash"]
    assert report_row["entity_id"] == refusal_row.entity_id
    assert metadata["refusal_reason"] == refusal_row.payload_json["refusal_reason"]


def test_report_audit_joins_the_response_blocked_ledger_row() -> None:
    ledger = InMemoryAuditStore()
    payload = GenieMessageRequest(question="How many  In-The-Money borrowers are in Ohio?")
    live = GenieMessageResponse(
        conversation_id="01f13d4968af1b249dc388fd5b18b195",
        message_id="01f13d4a0b7c1e5f8a2b3c4d5e6f7a8b",
        question=payload.question,
        answer="Unsafe generated text.",
        source="genie",
        trusted_assets=[],
        proof=GenieProof(),
    )
    blocked = _block_unsafe_genie_output(ledger, actor=ACTOR, payload=payload, response=live)
    assert blocked.source == "policy_blocked"
    blocked_row = _single_ledger_row(ledger, "genie.response_blocked")

    report_row = _report_from_refusal(_FakeLakebase(), blocked)

    metadata = json.loads(report_row["metadata"])
    assert metadata["question_hash"] == blocked_row.payload_json["question_hash"]
    assert report_row["entity_id"] == blocked_row.entity_id


def test_replay_for_the_same_actor_hash_and_family_is_a_duplicate_without_a_second_audit() -> None:
    lakebase = _FakeLakebase()
    _install(lakebase)

    first = client.post(REPORT_PATH, json=_body(), headers=ACTOR_HEADERS)
    second = client.post(REPORT_PATH, json=_body(), headers=ACTOR_HEADERS)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == {
        "accepted": True,
        "duplicate": True,
        "report_id": None,
        "audit_event_id": None,
    }
    assert len(lakebase.reports) == 1
    assert len(lakebase.audit_rows) == 1


def test_families_without_a_governed_audit_code_still_record_the_report() -> None:
    lakebase = _FakeLakebase()
    _install(lakebase)

    response = client.post(
        REPORT_PATH,
        json=_body(refusal_reason="outreach_instruction", conversation_id=""),
        headers=ACTOR_HEADERS,
    )

    assert response.status_code == 200, response.text
    report = next(iter(lakebase.reports.values()))
    assert report["refusal_reason"] == "outreach_instruction"
    assert report["conversation_id"] is None
    metadata = lakebase.audit_rows[0]["metadata"]
    assert '"action_type": "refusal_report"' in metadata
    assert "refusal_reason" not in metadata


def test_report_accepts_only_the_full_hash_never_text() -> None:
    lakebase = _FakeLakebase()
    _install(lakebase)

    for bad in (QUESTION, QUESTION_HASH[:16], QUESTION_HASH.upper(), "x" * 64):
        response = client.post(
            REPORT_PATH, json=_body(question_hash=bad), headers=ACTOR_HEADERS
        )
        assert response.status_code == 422, bad
        assert bad not in response.text
        assert "zyrplax" not in response.text
    assert lakebase.reports == {}
    assert lakebase.audit_rows == []


def test_report_has_no_field_that_could_carry_the_question() -> None:
    lakebase = _FakeLakebase()
    _install(lakebase)

    response = client.post(
        REPORT_PATH, json=_body(question=QUESTION, comment=QUESTION), headers=ACTOR_HEADERS
    )

    # Unknown keys are ignored by the schema; nothing reaches the row.
    assert response.status_code == 200, response.text
    report = next(iter(lakebase.reports.values()))
    assert "question" not in report and "comment" not in report
    assert "zyrplax" not in lakebase.audit_rows[0]["metadata"]


def test_report_rejects_an_unknown_family_and_malformed_ids() -> None:
    lakebase = _FakeLakebase()
    _install(lakebase)

    unknown_family = client.post(
        REPORT_PATH, json=_body(refusal_reason="protected_class_proxy"), headers=ACTOR_HEADERS
    )
    assert unknown_family.status_code == 422
    bad_id = client.post(
        REPORT_PATH, json=_body(conversation_id="john smith <x@y.z>"), headers=ACTOR_HEADERS
    )
    assert bad_id.status_code == 422
    assert "john" not in bad_id.text
    assert lakebase.reports == {}


def test_report_requires_json_and_surfaces_lakebase_outage_safely() -> None:
    _install(_FakeLakebase())
    wrong_type = client.post(
        REPORT_PATH,
        content="question_hash=abc",
        headers={**ACTOR_HEADERS, "Content-Type": "text/plain"},
    )
    assert wrong_type.status_code == 415

    _install(_FakeLakebase(fail=True))
    down = client.post(REPORT_PATH, json=_body(), headers=ACTOR_HEADERS)
    assert down.status_code == 503
    assert "lakebase down" not in down.text


def test_report_shares_the_feedback_endpoint_rate_budget() -> None:
    report_budget = _backpressure_controller.classify("POST", "/api/v1/genie/refusal-report")
    feedback_budget = _backpressure_controller.classify("POST", "/api/v1/genie/feedback")
    assert report_budget is not None
    assert report_budget == feedback_budget
    assert report_budget.scope == "genie"
