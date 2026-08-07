"""Ask Genie conversation history: persistence, listing, and replay.

Covers ``GET /api/genie/sessions`` and
``GET /api/genie/sessions/{conversation_id}`` plus the write-path extension
in ``_record_genie_session`` that makes replay possible. Router tests follow
the stubbed-Lakebase / snapshot-and-restore override pattern in
``tests/unit/test_genie_actions_api.py``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from backend.api.genie import _record_genie_session
from backend.main import app
from backend.services.genie_answers import (
    GenieActionSuggestion,
    GenieMessageResponse,
    GenieProof,
    GenieReasoningStep,
)
from backend.services.genie_history import (
    GENIE_HISTORY_PAYLOAD_MAX_BYTES,
    GENIE_HISTORY_SESSION_LIMIT,
    GENIE_HISTORY_TITLE_MAX,
    GENIE_HISTORY_TRIMMED_ROWS,
    history_payload_json,
)
from backend.services.lakebase import LakebaseError, get_lakebase_client

client = TestClient(app)
ACTOR_HEADERS = {"X-Forwarded-Email": "lo@example.com"}


class _Lakebase:
    """Minimal Lakebase stand-in that records writes and serves fixed reads."""

    def __init__(
        self,
        *,
        session_rows: list[dict[str, Any]] | None = None,
        turn_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.executes: list[tuple[str, dict[str, Any]]] = []
        self.fetchall_calls: list[tuple[str, dict[str, Any]]] = []
        self._session_rows = session_rows or []
        self._turn_rows = turn_rows or []

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.executes.append((sql, params))

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.fetchall_calls.append((sql, dict(params or {})))
        rows = self._turn_rows if "response_json" in sql else self._session_rows
        return rows[:limit]


class _BrokenLakebase(_Lakebase):
    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        raise LakebaseError("lakebase unavailable")


def _with_lakebase(lakebase: object, call):  # type: ignore[no-untyped-def]
    prior = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        return call()
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior


def _response(
    *,
    conversation_id: str = "conv-1",
    message_id: str = "msg-1",
    question: str = "How many borrowers are in the money?",
    table_rows: list[dict[str, Any]] | None = None,
    actions: list[GenieActionSuggestion] | None = None,
) -> GenieMessageResponse:
    return GenieMessageResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        question=question,
        question_hash="hash-1",
        answer="117,404 borrowers.\n\nSource: mip.gold.borrower_360",
        source="genie",
        genie_status="COMPLETED",
        trusted_assets=["mip.gold.borrower_360"],
        row_count=len(table_rows or []),
        table_rows=table_rows if table_rows is not None else [{"borrowers": 117404}],
        actions=actions or [],
        reasoning_trace=[
            GenieReasoningStep(kind="guardrails", content="Prompt cleared the guardrails."),
        ],
        proof=GenieProof(source_assets=["mip.gold.borrower_360"], row_count=1, trusted=True),
    )


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def test_recorded_turn_persists_question_and_response_payload() -> None:
    lakebase = _Lakebase()
    response = _response()

    _record_genie_session(lakebase, actor="analyst@example.com", response=response)  # type: ignore[arg-type]

    assert len(lakebase.executes) == 2
    _, params = lakebase.executes[1]
    assert params["question_text"] == "How many borrowers are in the money?"
    stored = json.loads(params["response_json"])
    assert stored["conversation_id"] == "conv-1"
    assert stored["answer"] == response.answer
    assert stored["table_rows"] == [{"borrowers": 117404}]


def test_persisted_payload_never_carries_signed_action_tokens() -> None:
    response = _response(
        actions=[
            GenieActionSuggestion(
                id="open-cohort",
                label="Open cohort",
                action_type="open_cohort",
                description="Open the matching borrowers in Lead Queue.",
                confirmation_token="signed.token.value",
                request_id="req-1",
            )
        ]
    )

    stored = json.loads(history_payload_json(response) or "{}")

    assert stored["actions"] == []
    assert "signed.token.value" not in json.dumps(stored)


def test_oversized_payload_is_trimmed_and_discloses_the_trim() -> None:
    wide_row = {f"col_{index}": "x" * 200 for index in range(20)}
    response = _response(table_rows=[dict(wide_row) for _ in range(400)])

    encoded = history_payload_json(response)

    assert encoded is not None
    assert len(encoded.encode("utf-8")) <= GENIE_HISTORY_PAYLOAD_MAX_BYTES
    stored = json.loads(encoded)
    assert len(stored["table_rows"]) <= GENIE_HISTORY_TRIMMED_ROWS
    assert any("trimmed" in gap or "not\nshown" in gap for gap in stored["proof"]["known_data_gaps"])


def test_refused_turn_is_never_persisted() -> None:
    lakebase = _Lakebase()
    refused = _response().model_copy(update={"source": "refused"})

    _record_genie_session(lakebase, actor="analyst@example.com", response=refused)  # type: ignore[arg-type]

    assert lakebase.executes == []


# ---------------------------------------------------------------------------
# GET /api/genie/sessions
# ---------------------------------------------------------------------------


def test_sessions_list_returns_actor_scoped_titles_newest_first() -> None:
    long_question = "Which borrowers " + ("should we contact " * 10)
    lakebase = _Lakebase(
        session_rows=[
            {
                "conversation_id": "conv-2",
                "last_activity_at": "2026-08-06T12:00:00+00:00",
                "turn_count": 3,
                "first_question": long_question,
            },
            {
                "conversation_id": "conv-1",
                "last_activity_at": "2026-08-05T09:30:00+00:00",
                "turn_count": 1,
                "first_question": "How many borrowers are in the money?",
            },
        ]
    )

    res = _with_lakebase(
        lakebase,
        lambda: client.get("/api/genie/sessions", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 200
    body = res.json()
    assert [session["conversation_id"] for session in body["sessions"]] == ["conv-2", "conv-1"]
    first = body["sessions"][0]
    assert first["turn_count"] == 3
    assert first["last_activity_at"] == "2026-08-06T12:00:00+00:00"
    assert first["title"] == long_question[:GENIE_HISTORY_TITLE_MAX]
    assert len(first["title"]) == GENIE_HISTORY_TITLE_MAX
    _, params = lakebase.fetchall_calls[0]
    assert params["actor_email"] == "lo@example.com"
    assert params["limit"] == GENIE_HISTORY_SESSION_LIMIT


def test_sessions_list_is_empty_when_the_actor_has_no_history() -> None:
    res = _with_lakebase(
        _Lakebase(),
        lambda: client.get("/api/genie/sessions", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 200
    assert res.json() == {"sessions": []}


def test_sessions_list_returns_503_when_lakebase_is_down() -> None:
    res = _with_lakebase(
        _BrokenLakebase(),
        lambda: client.get("/api/genie/sessions", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/genie/sessions/{conversation_id}
# ---------------------------------------------------------------------------


def _turn_row(question: str, response: GenieMessageResponse) -> dict[str, Any]:
    return {
        "question_text": question,
        "response_json": json.loads(history_payload_json(response) or "{}"),
    }


def test_session_detail_replays_turns_in_ask_order() -> None:
    first = _response(message_id="msg-1", question="How many borrowers are in the money?")
    second = _response(message_id="msg-2", question="Break that down by state.")
    lakebase = _Lakebase(
        turn_rows=[
            _turn_row("How many borrowers are in the money?", first),
            _turn_row("Break that down by state.", second),
        ]
    )

    res = _with_lakebase(
        lakebase,
        lambda: client.get("/api/genie/sessions/conv-1", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 200
    body = res.json()
    assert body["conversation_id"] == "conv-1"
    assert [turn["question"] for turn in body["turns"]] == [
        "How many borrowers are in the money?",
        "Break that down by state.",
    ]
    replayed = body["turns"][0]["response"]
    assert replayed["answer"] == first.answer
    assert replayed["source"] == "genie"
    assert replayed["table_rows"] == [{"borrowers": 117404}]
    assert replayed["reasoning_trace"][0]["kind"] == "guardrails"
    _, params = lakebase.fetchall_calls[0]
    assert params["actor_email"] == "lo@example.com"
    assert params["conversation_id"] == "conv-1"


def test_session_detail_accepts_a_json_encoded_payload_column() -> None:
    response = _response()
    lakebase = _Lakebase(
        turn_rows=[
            {
                "question_text": response.question,
                "response_json": history_payload_json(response),
            }
        ]
    )

    res = _with_lakebase(
        lakebase,
        lambda: client.get("/api/genie/sessions/conv-1", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 200
    assert res.json()["turns"][0]["response"]["answer"] == response.answer


def test_session_detail_404s_for_a_conversation_owned_by_another_actor() -> None:
    # The read is actor-scoped, so another actor's conversation yields no rows
    # and is indistinguishable from one that never existed.
    res = _with_lakebase(
        _Lakebase(turn_rows=[]),
        lambda: client.get("/api/genie/sessions/conv-someone-else", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 404


def test_session_detail_skips_turns_recorded_before_payload_persistence() -> None:
    lakebase = _Lakebase(
        turn_rows=[
            {"question_text": None, "response_json": None},
            _turn_row("How many borrowers are in the money?", _response()),
        ]
    )

    res = _with_lakebase(
        lakebase,
        lambda: client.get("/api/genie/sessions/conv-1", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 200
    assert len(res.json()["turns"]) == 1


def test_session_detail_returns_503_when_lakebase_is_down() -> None:
    res = _with_lakebase(
        _BrokenLakebase(),
        lambda: client.get("/api/genie/sessions/conv-1", headers=ACTOR_HEADERS),
    )

    assert res.status_code == 503
