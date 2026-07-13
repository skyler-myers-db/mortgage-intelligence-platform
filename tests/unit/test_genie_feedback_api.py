"""Contract tests for durable native Genie feedback delivery."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.genie_client import GenieClientError, get_genie_client
from backend.services.genie_feedback import _mark_retryable_failure
from backend.services.lakebase import get_lakebase_client

client = TestClient(app)
ACTOR_HEADERS = {"X-Forwarded-Email": "lo@example.com"}
_OWNED_CONVERSATION = "01f13d4968af1b249dc388fd5b18b195"
_MESSAGE_ID = "msg-feedback-1"
_REQUEST_ID = "11111111-1111-4111-8111-111111111111"


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
        self.intents: dict[tuple[str, str], dict[str, Any]] = {}
        self.ownership_queries: list[dict[str, Any]] = []
        self.order: list[str] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        params = params or {}
        if "FROM mip_app.genie_sessions AS sessions" in sql:
            self.ownership_queries.append(params)
            if (
                self.owned
                and params.get("conversation_id") == _OWNED_CONVERSATION
                and params.get("message_id") == _MESSAGE_ID
            ):
                return {
                    "conversation_id": _OWNED_CONVERSATION,
                    "message_id": _MESSAGE_ID,
                }
            return None
        raise AssertionError(f"unexpected fetchone SQL: {sql}")

    @contextmanager
    def transaction(self) -> Any:
        yield _FakeConn(self)

    def _intent_for_id(self, feedback_request_id: object) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.intents.values()
                if str(row["feedback_request_id"]) == str(feedback_request_id)
            ),
            None,
        )

    def handle_execute(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "INSERT INTO mip_app.genie_feedback_requests" in sql:
            key = (str(params["actor_email"]), str(params["request_id"]))
            if key in self.intents:
                return None
            row = {
                "feedback_request_id": uuid4(),
                **params,
                "status": "PENDING",
                "attempt_count": 0,
                "intent_audit_event_id": None,
                "audit_event_id": None,
                "last_error_code": None,
            }
            self.intents[key] = row
            self.order.append("intent")
            return row.copy()
        if "FROM mip_app.genie_feedback_requests" in sql:
            row = self.intents.get((str(params["actor_email"]), str(params["request_id"])))
            return row.copy() if row is not None else None
        if "SET intent_audit_event_id" in sql:
            row = self._intent_for_id(params["feedback_request_id"])
            assert row is not None
            row["intent_audit_event_id"] = params["intent_audit_event_id"]
            return {"feedback_request_id": row["feedback_request_id"]}
        if "SET status = 'IN_FLIGHT'" in sql:
            row = self._intent_for_id(params["feedback_request_id"])
            assert row is not None
            if row["status"] not in {"PENDING", "RETRYABLE_FAILED"}:
                return None
            row["status"] = "IN_FLIGHT"
            row["attempt_count"] += 1
            row["last_error_code"] = None
            self.order.append("claim")
            return {
                "feedback_request_id": row["feedback_request_id"],
                "attempt_count": row["attempt_count"],
            }
        if "SET status = 'RETRYABLE_FAILED'" in sql:
            row = self._intent_for_id(params["feedback_request_id"])
            assert row is not None
            if row["status"] != "IN_FLIGHT" or row["attempt_count"] != params["attempt_count"]:
                return None
            row["status"] = "RETRYABLE_FAILED"
            row["last_error_code"] = params["last_error_code"]
            self.order.append("failed")
            return {
                "feedback_request_id": row["feedback_request_id"],
                "attempt_count": row["attempt_count"],
            }
        if "SET status = 'SUCCEEDED'" in sql:
            row = self._intent_for_id(params["feedback_request_id"])
            assert row is not None
            row["status"] = "SUCCEEDED"
            row["audit_event_id"] = params["audit_event_id"]
            row["last_error_code"] = None
            self.order.append("succeeded")
            return {"feedback_request_id": row["feedback_request_id"]}
        if "INSERT INTO mip_app.action_audit" in sql:
            row = {"audit_id": uuid4(), "event_at": None, **params}
            self.audit_events.append(row)
            return row
        raise AssertionError(f"unexpected execute SQL: {sql}")


class _FakeGenie:
    def __init__(
        self,
        lakebase: _FakeLakebase,
        *,
        feedback_failures: int = 0,
        comment_raises: bool = False,
    ) -> None:
        self.lakebase = lakebase
        self.feedback_failures = feedback_failures
        self.comment_raises = comment_raises
        self.ratings: list[tuple[str, str, str]] = []
        self.comments: list[tuple[str, str, str]] = []

    def send_message_feedback(
        self,
        conversation_id: str,
        message_id: str,
        rating: str,
    ) -> None:
        assert any(row["status"] == "IN_FLIGHT" for row in self.lakebase.intents.values())
        self.ratings.append((conversation_id, message_id, rating))
        self.lakebase.order.append("native_rating")
        if self.feedback_failures:
            self.feedback_failures -= 1
            raise GenieClientError(
                "raw upstream detail must stay private",
                status_code=500,
            )

    def post_message_comment(self, conversation_id: str, message_id: str, content: str) -> bool:
        assert any(row["status"] == "SUCCEEDED" for row in self.lakebase.intents.values())
        if self.comment_raises:
            raise RuntimeError("comment endpoint down")
        self.comments.append((conversation_id, message_id, content))
        self.lakebase.order.append("comment")
        return True


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


def _feedback_headers(request_id: str = _REQUEST_ID) -> dict[str, str]:
    return {**ACTOR_HEADERS, "Idempotency-Key": request_id}


def _single_intent(lakebase: _FakeLakebase) -> dict[str, Any]:
    assert len(lakebase.intents) == 1
    return next(iter(lakebase.intents.values()))


def test_feedback_sets_native_positive_after_durable_intent_and_audits_success() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)

    response = client.post("/api/genie/feedback", json=_body(), headers=_feedback_headers())

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True
    assert response.json()["audit_event_id"]
    assert genie.ratings == [(_OWNED_CONVERSATION, _MESSAGE_ID, "POSITIVE")]
    assert genie.comments == []
    assert lakebase.order.index("intent") < lakebase.order.index("native_rating")
    assert lakebase.order.index("claim") < lakebase.order.index("native_rating")
    assert lakebase.order.index("native_rating") < lakebase.order.index("succeeded")
    intent = _single_intent(lakebase)
    assert intent["status"] == "SUCCEEDED"
    assert intent["attempt_count"] == 1
    assert [event["event_type"] for event in lakebase.audit_events] == [
        "GENIE_FEEDBACK_INTENT",
        "GENIE_FEEDBACK",
    ]
    assert lakebase.ownership_queries == [
        {
            "actor_email": "lo@example.com",
            "conversation_id": _OWNED_CONVERSATION,
            "message_id": _MESSAGE_ID,
        }
    ]


def test_feedback_maps_false_to_native_negative() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)

    response = client.post(
        "/api/genie/feedback",
        json=_body(helpful=False),
        headers=_feedback_headers(),
    )

    assert response.status_code == 200, response.text
    assert genie.ratings[0][2] == "NEGATIVE"


def test_optional_comment_is_not_stored_and_posts_only_after_rating_durability() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)
    note = "the ranking was clear and useful"

    response = client.post(
        "/api/genie/feedback",
        json=_body(comment=note),
        headers=_feedback_headers(),
    )

    assert response.status_code == 200, response.text
    assert len(genie.comments) == 1
    assert genie.comments[0][2] == f"MIP feedback: helpful - {note}"
    assert lakebase.order.index("succeeded") < lakebase.order.index("comment")
    serialized_state = json.dumps(list(lakebase.intents.values()), default=str)
    serialized_audit = json.dumps(lakebase.audit_events, default=str)
    assert note not in serialized_state
    assert note not in serialized_audit
    assert _single_intent(lakebase)["comment_present"] is True


def test_same_request_id_retry_returns_prior_result_without_second_side_effect() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)
    payload = _body(comment="useful grouping")

    first = client.post("/api/genie/feedback", json=payload, headers=_feedback_headers())
    second = client.post("/api/genie/feedback", json=payload, headers=_feedback_headers())

    assert first.status_code == second.status_code == 200
    assert first.json()["audit_event_id"] == second.json()["audit_event_id"]
    assert len(genie.ratings) == 1
    assert len(genie.comments) == 1
    assert len(lakebase.audit_events) == 2


def test_legacy_request_without_key_uses_stable_retry_key() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)
    payload = _body()

    first = client.post("/api/genie/feedback", json=payload, headers=ACTOR_HEADERS)
    second = client.post("/api/genie/feedback", json=payload, headers=ACTOR_HEADERS)

    assert first.status_code == second.status_code == 200
    assert len(genie.ratings) == 1
    assert str(_single_intent(lakebase)["request_id"]).startswith("genie-")


def test_failed_native_delivery_is_retryable_with_same_request_id() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase, feedback_failures=1)
    _install(lakebase, genie)

    first = client.post("/api/genie/feedback", json=_body(), headers=_feedback_headers())
    assert first.status_code == 503
    assert "raw upstream" not in first.text
    intent = _single_intent(lakebase)
    assert intent["status"] == "RETRYABLE_FAILED"
    assert intent["last_error_code"] == "http_500"
    assert len(lakebase.audit_events) == 1

    second = client.post("/api/genie/feedback", json=_body(), headers=_feedback_headers())
    assert second.status_code == 200, second.text
    assert len(genie.ratings) == 2
    assert intent["status"] == "SUCCEEDED"
    assert intent["attempt_count"] == 2
    assert len(lakebase.audit_events) == 2


def test_stale_failure_cannot_overwrite_a_newer_delivery_attempt() -> None:
    lakebase = _FakeLakebase()
    feedback_request_id = uuid4()
    row = {
        "feedback_request_id": feedback_request_id,
        "actor_email": "lo@example.com",
        "request_id": _REQUEST_ID,
        "conversation_id": _OWNED_CONVERSATION,
        "message_id": _MESSAGE_ID,
        "rating": "POSITIVE",
        "comment_present": False,
        "status": "IN_FLIGHT",
        "attempt_count": 2,
        "last_error_code": None,
        "audit_event_id": None,
    }
    lakebase.intents[("lo@example.com", _REQUEST_ID)] = row

    _mark_retryable_failure(
        lakebase,
        feedback_request_id=str(feedback_request_id),
        attempt_count=1,
        error_code="http_500",
    )

    assert row["status"] == "IN_FLIGHT"
    assert row["attempt_count"] == 2
    assert row["last_error_code"] is None


def test_request_id_reuse_for_different_rating_is_conflict() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)

    first = client.post("/api/genie/feedback", json=_body(), headers=_feedback_headers())
    second = client.post(
        "/api/genie/feedback",
        json=_body(helpful=False),
        headers=_feedback_headers(),
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(genie.ratings) == 1


def test_feedback_rejects_unowned_message_before_intent_or_native_call() -> None:
    lakebase = _FakeLakebase(owned=False)
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)

    response = client.post("/api/genie/feedback", json=_body(), headers=_feedback_headers())

    assert response.status_code == 403
    assert lakebase.intents == {}
    assert lakebase.audit_events == []
    assert genie.ratings == []


def test_feedback_rejects_pii_comment_without_echo_or_side_effect() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)
    pii = "call me at 415-555-0199"

    response = client.post(
        "/api/genie/feedback",
        json=_body(comment=pii),
        headers=_feedback_headers(),
    )

    assert response.status_code == 422
    assert pii not in response.text
    assert lakebase.ownership_queries == []
    assert lakebase.intents == {}
    assert genie.ratings == []


def test_feedback_rejects_invalid_request_id_without_echo() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)
    invalid = "jane@example.com"

    response = client.post(
        "/api/genie/feedback",
        json=_body(),
        headers=_feedback_headers(invalid),
    )

    assert response.status_code == 422
    assert invalid not in response.text
    assert lakebase.intents == {}


def test_feedback_requires_json_content_type() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase)
    _install(lakebase, genie)

    response = client.post(
        "/api/genie/feedback",
        data="conversation_id=x",
        headers={**_feedback_headers(), "Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 415
    assert lakebase.intents == {}


def test_comment_failure_does_not_undo_durable_native_rating() -> None:
    lakebase = _FakeLakebase()
    genie = _FakeGenie(lakebase, comment_raises=True)
    _install(lakebase, genie)

    response = client.post(
        "/api/genie/feedback",
        json=_body(comment="useful answer"),
        headers=_feedback_headers(),
    )

    assert response.status_code == 200, response.text
    assert len(genie.ratings) == 1
    assert _single_intent(lakebase)["status"] == "SUCCEEDED"
