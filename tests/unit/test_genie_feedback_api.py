"""Contract tests for ``POST /api/genie/feedback``.

Covered:

* Happy path -> ``{accepted: true, audit_event_id}`` + a ``GENIE_FEEDBACK``
  audit row written in a transaction + a best-effort Genie comment.
* 415 on non-JSON content type.
* 422 on a PII / human-name comment (rejected without echo).
* 403 when the conversation is not owned by the actor.
* A failing Genie comment post does NOT fail the request (best-effort).
* The Genie comment text is governed and length-capped-note only.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.genie_client import get_genie_client
from backend.services.lakebase import get_lakebase_client

client = TestClient(app)
ACTOR_HEADERS = {"X-Forwarded-Email": "lo@example.com"}
_OWNED_CONVERSATION = "01f13d4968af1b249dc388fd5b18b195"
_MESSAGE_ID = "msg-feedback-1"


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
    def __init__(self, *, owned: bool = True) -> None:
        self.owned = owned
        self.audit_events: list[dict[str, Any]] = []
        self.ownership_queries: list[dict[str, Any]] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        params = params or {}
        if "FROM mip_app.genie_sessions" in sql:
            self.ownership_queries.append(params)
            if self.owned and params.get("conversation_id") == _OWNED_CONVERSATION:
                return {"conversation_id": _OWNED_CONVERSATION}
            return None
        raise AssertionError(f"unexpected fetchone SQL: {sql}")

    @contextmanager
    def transaction(self) -> Any:
        yield _FakeConn(self)

    def handle_execute(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "INSERT INTO mip_app.action_audit" in sql:
            row = {"audit_id": uuid4(), "event_at": None, **params}
            self.audit_events.append(row)
            return row
        raise AssertionError(f"unexpected execute SQL: {sql}")


class _FakeGenie:
    def __init__(self, *, ok: bool = True, raises: bool = False) -> None:
        self.ok = ok
        self.raises = raises
        self.comments: list[tuple[str, str, str]] = []

    def post_message_comment(self, conversation_id: str, message_id: str, content: str) -> bool:
        if self.raises:
            raise RuntimeError("comment endpoint down")
        self.comments.append((conversation_id, message_id, content))
        return self.ok


def _install(lakebase: _FakeLakebase, genie: _FakeGenie) -> None:
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_genie_client] = lambda: genie


def _clear() -> None:
    app.dependency_overrides.pop(get_lakebase_client, None)
    app.dependency_overrides.pop(get_genie_client, None)


def teardown_function(_func: object) -> None:
    _clear()


def _body(**overrides: Any) -> dict[str, Any]:
    body = {
        "conversation_id": _OWNED_CONVERSATION,
        "message_id": _MESSAGE_ID,
        "helpful": True,
        "comment": None,
    }
    body.update(overrides)
    return body


def test_feedback_happy_path_writes_audit_and_posts_comment() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie()
    _install(lakebase, genie)

    resp = client.post("/api/genie/feedback", json=_body(helpful=True), headers=ACTOR_HEADERS)

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["accepted"] is True
    assert payload["audit_event_id"]
    # Audit row written in-transaction with the governed event type.
    assert len(lakebase.audit_events) == 1
    audit = lakebase.audit_events[0]
    assert audit["event_type"] == "GENIE_FEEDBACK"
    # Comment posted best-effort with the governed prefix.
    assert len(genie.comments) == 1
    assert genie.comments[0][2] == "MIP feedback: helpful"


def test_feedback_not_helpful_uses_governed_negative_comment() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie()
    _install(lakebase, genie)

    resp = client.post("/api/genie/feedback", json=_body(helpful=False), headers=ACTOR_HEADERS)

    assert resp.status_code == 200, resp.text
    assert genie.comments[0][2] == "MIP feedback: not helpful"


def test_feedback_appends_sanitized_comment_to_governed_prefix() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie()
    _install(lakebase, genie)

    resp = client.post(
        "/api/genie/feedback",
        json=_body(helpful=True, comment="the ranking was clear and useful"),
        headers=ACTOR_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    posted = genie.comments[0][2]
    assert posted.startswith("MIP feedback: helpful")
    assert "clear and useful" in posted
    # comment_present recorded, but the note text is NOT stored in audit metadata.
    audit_meta = lakebase.audit_events[0]["metadata"]
    assert "comment_present" in audit_meta
    assert "clear and useful" not in audit_meta


def test_feedback_requires_json_content_type() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie()
    _install(lakebase, genie)

    resp = client.post(
        "/api/genie/feedback",
        data="conversation_id=x",
        headers={**ACTOR_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )

    assert resp.status_code == 415
    assert lakebase.audit_events == []


def test_feedback_rejects_pii_comment_without_echo() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie()
    _install(lakebase, genie)

    resp = client.post(
        "/api/genie/feedback",
        json=_body(comment="call me at 415-555-0199"),
        headers=ACTOR_HEADERS,
    )

    assert resp.status_code == 422
    assert "415-555-0199" not in resp.text
    assert lakebase.audit_events == []
    assert genie.comments == []


def test_feedback_rejects_human_name_comment() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie()
    _install(lakebase, genie)

    resp = client.post(
        "/api/genie/feedback",
        json=_body(comment="please contact Jane Doe about this"),
        headers=ACTOR_HEADERS,
    )

    assert resp.status_code == 422
    assert "Jane Doe" not in resp.text


def test_feedback_rejects_unowned_conversation() -> None:
    lakebase = _FakeLakebase(owned=False)
    genie = _FakeGenie()
    _install(lakebase, genie)

    resp = client.post("/api/genie/feedback", json=_body(), headers=ACTOR_HEADERS)

    assert resp.status_code == 403
    assert lakebase.audit_events == []
    assert genie.comments == []


def test_feedback_survives_comment_post_failure() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(raises=True)
    _install(lakebase, genie)

    resp = client.post("/api/genie/feedback", json=_body(), headers=ACTOR_HEADERS)

    # Best-effort comment: a failure must NOT fail the request; audit still lands.
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] is True
    assert len(lakebase.audit_events) == 1
