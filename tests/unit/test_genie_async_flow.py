"""Async Genie lifecycle: submit → progress → complete.

Pins the 2026-07-31 live-progress contract:

* submit runs the identical deterministic guard battery and resolves those
  turns inline; live turns return ids plus a signed progress token,
* progress is a pure, token-authorized poll that exposes only server-owned
  vocabulary (stage map + translated process steps + generated SQL) and
  never raw model thoughts or upstream error text,
* complete replays the synchronous live tail (output policy, audit,
  finalize) on the already-submitted message, with the question
  hash-pinned to the guarded submit.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend.api.genie as genie_api
from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.genie_actions import sign_genie_claims
from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.genie_client import (
    _GENIE_SUCCESS_STATES,
    _GENIE_TERMINAL_ERROR_STATES,
)
from backend.services.genie_progress import (
    build_genie_progress,
    genie_question_binding_hash,
    genie_question_hash,
    mint_genie_progress_token,
)
from backend.services.lakebase import get_lakebase_client
from backend.services.repositories.factory import get_genie_answer_repository
from backend.services.resilience import DependencyDownError

ACTOR = "lo@example.com"
HEADERS = {"X-Forwarded-Email": ACTOR}
QUESTION = "How many borrowers are currently in the money?"
RAW_THOUGHT = "PRIVATE-MODEL-THOUGHT examining borrower_360 rate spread columns"


class _FakeGenieClient:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, str | None]] = []
        self.peeked: list[tuple[str, str]] = []
        self.message: dict[str, Any] = {"status": "SUBMITTED", "attachments": []}

    def submit_message(
        self, question: str, conversation_id: str | None = None
    ) -> tuple[str, str]:
        self.submitted.append((question, conversation_id))
        return "conv-async-1", "msg-async-1"

    def peek_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        self.peeked.append((conversation_id, message_id))
        return self.message


class _FakeRepo:
    def __init__(self) -> None:
        self.existing_calls: list[dict[str, str]] = []

    def respond(self, question: str, conversation_id: str | None = None) -> GenieMessageResponse:
        raise AssertionError("sync respond must not run on the async happy path")

    def respond_existing(
        self, question: str, *, conversation_id: str, message_id: str
    ) -> GenieMessageResponse:
        self.existing_calls.append(
            {
                "question": question,
                "conversation_id": conversation_id,
                "message_id": message_id,
            }
        )
        return GenieMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            question=question,
            question_hash=genie_question_hash(question),
            answer="There are 124,946 borrowers in the money.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=1,
            genie_status="COMPLETED",
            proof=GenieProof(
                source_assets=["mip.gold.borrower_360"],
                row_count=1,
                trusted=True,
                conversation_id=conversation_id,
                message_id=message_id,
            ),
            table_rows=[{"in_the_money_borrowers": "124946"}],
        )


class _FakeAudit:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def write(self, **kwargs: Any) -> None:
        self.writes.append(kwargs)


class _FakeLakebase:
    def __init__(self) -> None:
        self.executed: list[str] = []
        #: When True, the message-ownership lookup reports the turn as
        #: already recorded (replay-dedupe scenarios).
        self.message_recorded = False

    def fetchone(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if self.message_recorded and "genie_messages" in sql:
            return {
                "conversation_id": (params or {}).get("conversation_id"),
                "message_id": (params or {}).get("message_id"),
            }
        return None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        _ = params
        self.executed.append(sql)


def _overrides(
    monkeypatch: Any,
    *,
    client: _FakeGenieClient,
    repo: _FakeRepo,
    audit: _FakeAudit,
    lakebase: _FakeLakebase,
) -> None:
    monkeypatch.setattr(genie_api, "get_genie_client", lambda: client)
    monkeypatch.setitem(app.dependency_overrides, get_genie_answer_repository, lambda: repo)
    monkeypatch.setitem(app.dependency_overrides, get_audit_store, lambda: audit)
    monkeypatch.setitem(app.dependency_overrides, get_lakebase_client, lambda: lakebase)


def test_submit_live_returns_ids_and_signed_token(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)

    res = TestClient(app).post(
        "/api/genie/message/submit", json={"question": QUESTION}, headers=HEADERS
    )

    assert res.status_code == 200
    body = res.json()
    assert body["completed"] is False
    assert body["conversation_id"] == "conv-async-1"
    assert body["message_id"] == "msg-async-1"
    assert body["question_hash"] == hashlib.sha256(QUESTION.encode()).hexdigest()[:16]
    assert body["progress_token"]
    assert body["response"] is None
    assert client.submitted == [(QUESTION, None)]
    # The live message creation is itself audited even before completion.
    assert any(w["action"] == "genie.message_submitted" for w in audit.writes)


def test_submit_deterministic_refusal_resolves_inline(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)

    res = TestClient(app).post(
        "/api/genie/message/submit",
        json={"question": "What is the phone number for borrower John Smith?"},
        headers=HEADERS,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["completed"] is True
    assert body["response"]["source"] == "refused"
    assert body["progress_token"] is None
    assert client.submitted == []
    assert any(w["action"] == "genie.refused_prompt" for w in audit.writes)


def test_progress_translates_status_thoughts_and_sql(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    client.message = {
        "status": "EXECUTING_QUERY",
        "attachments": [
            {
                "attachment_id": "att-1",
                "query": {
                    "query": "SELECT COUNT(*) FROM mip.gold.borrower_360",
                    "thoughts": [
                        {"thought_type": "THOUGHT_TYPE_FILTER_CONTEXT", "content": RAW_THOUGHT},
                        {"thought_type": "THOUGHT_TYPE_EXECUTE_QUERY", "content": RAW_THOUGHT},
                    ],
                },
            }
        ],
    }
    token = mint_genie_progress_token(
        actor=ACTOR,
        conversation_id="conv-async-1",
        message_id="msg-async-1",
        question_hash=genie_question_binding_hash(QUESTION),
    )

    res = TestClient(app).post(
        "/api/genie/message/progress",
        json={
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
            "progress_token": token,
        },
        headers=HEADERS,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "EXECUTING_QUERY"
    assert body["stage"] == "executing"
    assert body["terminal"] is False
    assert body["failed"] is False
    assert body["sql_preview"] == "SELECT COUNT(*) FROM mip.gold.borrower_360"
    assert len(body["reasoning_trace"]) == 2
    # Server-owned process copy only — the raw model thought must never
    # appear anywhere in the wire body.
    assert RAW_THOUGHT not in res.text
    assert client.peeked == [("conv-async-1", "msg-async-1")]


def test_progress_rejects_foreign_actor_and_tampered_token(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    token = mint_genie_progress_token(
        actor=ACTOR,
        conversation_id="conv-async-1",
        message_id="msg-async-1",
        question_hash=genie_question_binding_hash(QUESTION),
    )
    payload = {
        "conversation_id": "conv-async-1",
        "message_id": "msg-async-1",
        "progress_token": token,
    }

    foreign = TestClient(app).post(
        "/api/genie/message/progress",
        json=payload,
        headers={"X-Forwarded-Email": "someone-else@example.com"},
    )
    assert foreign.status_code == 403

    tampered = TestClient(app).post(
        "/api/genie/message/progress",
        json={**payload, "progress_token": token[:-4] + "AAAA"},
        headers=HEADERS,
    )
    assert tampered.status_code == 400
    assert client.peeked == []


def test_progress_token_expiry_is_enforced(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    expired = sign_genie_claims(
        {
            "kind": "genie_progress",
            "actor": ACTOR,
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
            "question_hash": genie_question_binding_hash(QUESTION),
            "exp": int(time.time()) - 5,
        }
    )

    res = TestClient(app).post(
        "/api/genie/message/progress",
        json={
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
            "progress_token": expired,
        },
        headers=HEADERS,
    )

    assert res.status_code == 400
    assert "expired" in res.json()["detail"]


def test_complete_runs_governed_tail_and_records_session(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    token = mint_genie_progress_token(
        actor=ACTOR,
        conversation_id="conv-async-1",
        message_id="msg-async-1",
        question_hash=genie_question_binding_hash(QUESTION),
    )

    res = TestClient(app).post(
        "/api/genie/message/complete",
        json={
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
            "progress_token": token,
            "question": QUESTION,
        },
        headers=HEADERS,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "genie"
    assert body["conversation_id"] == "conv-async-1"
    assert body["answer"].startswith("There are 124,946")
    assert repo.existing_calls == [
        {
            "question": QUESTION,
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
        }
    ]
    assert any(w["action"] == "genie.run_query" for w in audit.writes)
    # Session ownership recorded at completion (upsert + message insert).
    assert len(lakebase.executed) == 2


def test_complete_rejects_question_hash_mismatch(monkeypatch: Any) -> None:
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    token = mint_genie_progress_token(
        actor=ACTOR,
        conversation_id="conv-async-1",
        message_id="msg-async-1",
        question_hash=genie_question_binding_hash(QUESTION),
    )

    res = TestClient(app).post(
        "/api/genie/message/complete",
        json={
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
            "progress_token": token,
            "question": "Completely different question about equity?",
        },
        headers=HEADERS,
    )

    assert res.status_code == 400
    assert repo.existing_calls == []


def test_build_genie_progress_failure_uses_canned_hint_only() -> None:
    progress = build_genie_progress(
        {
            "status": "FAILED",
            "error": {"message": "raw warehouse text state=42 col=borrower_ssn"},
        }
    )

    assert progress.terminal is True
    assert progress.failed is True
    assert progress.stage == "failed"
    assert progress.error_hint is not None
    assert "warehouse text" not in progress.error_hint
    assert "borrower_ssn" not in progress.error_hint


def test_build_genie_progress_unknown_status_is_conservative() -> None:
    progress = build_genie_progress({"status": "SOME_NEW_PLATFORM_STATE"})

    assert progress.stage == "working"
    assert progress.terminal is False
    assert progress.failed is False

def test_progress_withholds_untrusted_sql_preview(monkeypatch: Any) -> None:
    """Mid-flight SQL that the completed answer would refuse to disclose
    (untrusted asset refs) must not appear in the progress body either."""
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    client.message = {
        "status": "EXECUTING_QUERY",
        "attachments": [
            {
                "attachment_id": "att-1",
                "query": {
                    "query": "SELECT ssn FROM other_catalog.secret.customers",
                    "thoughts": [],
                },
            }
        ],
    }
    token = mint_genie_progress_token(
        actor=ACTOR,
        conversation_id="conv-async-1",
        message_id="msg-async-1",
        question_hash=genie_question_binding_hash(QUESTION),
    )

    res = TestClient(app).post(
        "/api/genie/message/progress",
        json={
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
            "progress_token": token,
        },
        headers=HEADERS,
    )

    assert res.status_code == 200
    assert res.json()["sql_preview"] is None
    assert "other_catalog" not in res.text


def test_progress_status_vocabulary_is_closed() -> None:
    progress = build_genie_progress({"status": "SOME_NEW_PLATFORM_STATE payload<script>"})

    assert progress.status == "UNKNOWN"
    assert progress.stage == "working"


def test_complete_replay_serves_answer_without_duplicate_audit(
    monkeypatch: Any,
) -> None:
    """A re-sent complete (token valid for its TTL) re-serves the governed
    answer but must not write a second genie.run_query row."""
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    lakebase.message_recorded = True
    token = mint_genie_progress_token(
        actor=ACTOR,
        conversation_id="conv-async-1",
        message_id="msg-async-1",
        question_hash=genie_question_binding_hash(QUESTION),
    )

    res = TestClient(app).post(
        "/api/genie/message/complete",
        json={
            "conversation_id": "conv-async-1",
            "message_id": "msg-async-1",
            "progress_token": token,
            "question": QUESTION,
        },
        headers=HEADERS,
    )

    assert res.status_code == 200
    assert res.json()["answer"].startswith("There are 124,946")
    assert not any(w["action"] == "genie.run_query" for w in audit.writes)

def test_submit_genie_down_fallback_writes_run_query_audit(monkeypatch: Any) -> None:
    """QA H1 regression: a turn resolved through the Genie-down fallback must
    carry the same genie.run_query audit row the synchronous endpoint writes."""
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)

    def _down(question: str, conversation_id: str | None = None) -> tuple[str, str]:
        raise DependencyDownError(
            "genie", reason="submit failed", kind=DependencyDownError.KIND_RETRIES_EXHAUSTED
        )

    client.submit_message = _down  # type: ignore[method-assign]
    degraded = GenieMessageResponse(
        conversation_id="conv-degraded",
        message_id="msg-degraded",
        question=QUESTION,
        question_hash=genie_question_hash(QUESTION),
        answer="Reviewed canonical fallback answer.",
        source="trusted_sql",
        trusted_assets=["mip.gold.borrower_360"],
        row_count=1,
        table_rows=[{"in_the_money_borrowers": "124946"}],
    )
    repo.respond = lambda question, conversation_id=None: degraded  # type: ignore[method-assign]

    res = TestClient(app).post(
        "/api/genie/message/submit", json={"question": QUESTION}, headers=HEADERS
    )

    assert res.status_code == 200
    body = res.json()
    assert body["completed"] is True
    assert body["response"]["source"] == "trusted_sql"
    assert any(w["action"] == "genie.run_query" for w in audit.writes)


def test_submit_honors_legacy_interceptor_first_posture(monkeypatch: Any) -> None:
    """QA H2 regression: with mip_genie_live_first=False the async submit must
    resolve through the repository (canonical-first) and never create a live
    Genie message."""
    client, repo, audit, lakebase = _FakeGenieClient(), _FakeRepo(), _FakeAudit(), _FakeLakebase()
    _overrides(monkeypatch, client=client, repo=repo, audit=audit, lakebase=lakebase)
    monkeypatch.setattr(genie_api.settings, "mip_genie_live_first", False)
    canonical = GenieMessageResponse(
        conversation_id="conv-canonical",
        message_id="msg-canonical",
        question=QUESTION,
        question_hash=genie_question_hash(QUESTION),
        answer="Canonical catalog answer.",
        source="trusted_sql",
        trusted_assets=["mip.gold.borrower_360"],
        row_count=1,
        table_rows=[{"in_the_money_borrowers": "124946"}],
    )
    repo.respond = lambda question, conversation_id=None: canonical  # type: ignore[method-assign]

    res = TestClient(app).post(
        "/api/genie/message/submit", json={"question": QUESTION}, headers=HEADERS
    )

    assert res.status_code == 200
    body = res.json()
    assert body["completed"] is True
    assert body["response"]["source"] == "trusted_sql"
    assert client.submitted == []
    assert any(w["action"] == "genie.run_query" for w in audit.writes)


@pytest.mark.parametrize(
    "status", sorted(_GENIE_SUCCESS_STATES | _GENIE_TERMINAL_ERROR_STATES)
)
def test_progress_terminal_parity_with_client_states(status: str) -> None:
    """QA H3 regression: every state the polling client treats as terminal
    must be terminal to the live-progress map, or the browser polls a dead
    turn to its client-side ceiling."""
    progress = build_genie_progress({"status": status})

    assert progress.terminal is True
    if status in _GENIE_TERMINAL_ERROR_STATES:
        assert progress.failed is True
        assert progress.error_hint

