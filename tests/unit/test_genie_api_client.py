"""Unit tests for the stdlib-only Databricks Genie Conversation API client.

All HTTP is stubbed via ``monkeypatch`` on ``urllib.request.urlopen`` so
these tests are hermetic -- no real workspace call ever fires.

Assertions:

- ``ask()`` drives the full ``start-conversation -> poll -> query-result``
  sequence and returns a structured ``GenieResponse`` with ``source="genie"``.
- Terminal error states (``FAILED``) raise ``GenieClientError`` with state
  metadata.
- Non-2xx responses raise ``GenieClientError`` with the status code and
  truncated response body.
- Malformed JSON raises ``GenieClientError`` with enough context to
  inspect the first 200 chars of the raw body.
- Polling respects the ``timeout_s`` budget when Genie never finishes.
"""
from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services.genie_client import (
    GenieClient,
    GenieClientError,
    GenieResponse,
)


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the poll sleep so tests don't actually wait."""
    monkeypatch.setattr("backend.services.genie_client.time.sleep", lambda _s: None)


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _make_response(body: Any) -> _FakeResponse:
    if isinstance(body, bytes):
        return _FakeResponse(body)
    return _FakeResponse(json.dumps(body).encode("utf-8"))


def _http_error(status: int, body: str = "error") -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(
        url="http://example/x",
        code=status,
        msg=f"HTTP {status}",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )
    return err


def _build_urlopen_sequence(
    responses: list[Any],
) -> tuple[MagicMock, list[tuple[str, Any]]]:
    """Return a urlopen mock that pops responses in order.

    Each entry is either a dict (JSON body) or an HTTPError/URLError to
    raise. Also returns a call log so tests can assert which endpoints
    were called.
    """
    calls: list[tuple[str, Any]] = []
    queue: Iterator[Any] = iter(responses)

    def _urlopen(req: Any, timeout: float = 30) -> _FakeResponse:  # noqa: ARG001
        try:
            nxt = next(queue)
        except StopIteration as exc:  # pragma: no cover -- test bug
            raise AssertionError("urlopen called more times than stubbed") from exc
        calls.append((req.full_url, req.get_method()))
        if isinstance(nxt, Exception):
            raise nxt
        return _make_response(nxt)

    return MagicMock(side_effect=_urlopen), calls


def _make_client() -> GenieClient:
    return GenieClient(
        host="https://workspace.example.cloud.databricks.com",
        token="dapi-test-token",
        space_id="sp-test-0001",
        timeout_s=2,
    )


# ---------------------------------------------------------------------------
# ask() -- happy path
# ---------------------------------------------------------------------------


def test_ask_drives_start_poll_query_and_returns_structured_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_body = {
        "conversation_id": "conv-123",
        "message": {"id": "msg-456", "status": "IN_PROGRESS"},
    }
    in_progress = {"status": "IN_PROGRESS", "id": "msg-456"}
    completed = {
        "status": "COMPLETED",
        "id": "msg-456",
        "attachments": [
            {"text": {"content": "12,840 borrowers are in the money."}},
            {
                "query": {
                    "query": "SELECT count(*) FROM mip.gold.lead_scores",
                    "description": "count of ITM borrowers",
                }
            },
        ],
    }
    query_result = {
        "statement_response": {
            "manifest": {"schema": {"columns": [{"name": "count"}]}},
            "result": {"data_array": [[12840]]},
        }
    }
    mock, calls = _build_urlopen_sequence(
        [start_body, in_progress, completed, query_result]
    )
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    client = _make_client()
    result = client.ask("How many borrowers are in the money?")

    assert isinstance(result, GenieResponse)
    assert result.source == "genie"
    assert result.conversation_id == "conv-123"
    assert result.message_id == "msg-456"
    assert "in the money" in result.answer_text
    assert result.sql_query is not None
    assert "mip.gold.lead_scores" in result.sql_query
    assert result.sql_result_rows == [{"count": 12840}]
    # Call shape: start, poll (IN_PROGRESS), poll (COMPLETED), query-result.
    assert len(calls) == 4
    assert "/start-conversation" in calls[0][0]
    assert calls[0][1] == "POST"
    assert calls[1][1] == "GET"
    assert "/query-result" in calls[3][0]


def test_ask_handles_text_only_responses_without_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_body = {
        "conversation_id": "conv-xy",
        "message": {"id": "msg-99"},
    }
    completed = {
        "status": "COMPLETED",
        "id": "msg-99",
        "attachments": [{"text": {"content": "I can help with that."}}],
    }
    mock, _ = _build_urlopen_sequence([start_body, completed])
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    result = _make_client().ask("Give me context")
    assert result.sql_query is None
    assert result.sql_result_rows is None
    assert result.answer_text == "I can help with that."


def test_ask_appends_message_when_conversation_id_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append_body = {"id": "msg-7"}
    completed = {
        "status": "COMPLETED",
        "id": "msg-7",
        "attachments": [{"text": {"content": "follow-up answer"}}],
    }
    mock, calls = _build_urlopen_sequence([append_body, completed])
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    result = _make_client().ask("follow-up", conversation_id="conv-existing")
    assert result.conversation_id == "conv-existing"
    assert result.message_id == "msg-7"
    # First call should hit the messages endpoint, NOT start-conversation.
    assert "/conversations/conv-existing/messages" in calls[0][0]
    assert calls[0][1] == "POST"


# ---------------------------------------------------------------------------
# ask() -- error paths
# ---------------------------------------------------------------------------


def test_non_2xx_raises_genie_client_error_with_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock, _ = _build_urlopen_sequence([_http_error(500, body="boom")])
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    with pytest.raises(GenieClientError) as exc:
        _make_client().ask("question")
    assert exc.value.status_code == 500
    assert "boom" in str(exc.value)


def test_malformed_json_raises_with_truncated_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock, _ = _build_urlopen_sequence([b"not-json-at-all"])
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    with pytest.raises(GenieClientError) as exc:
        _make_client().ask("question")
    assert "Malformed JSON" in str(exc.value)
    assert "not-json-at-all" in str(exc.value)


def test_terminal_error_state_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    start_body = {"conversation_id": "c1", "message": {"id": "m1"}}
    failed = {
        "status": "FAILED",
        "id": "m1",
        "error": {"message": "timeout on warehouse"},
    }
    mock, _ = _build_urlopen_sequence([start_body, failed])
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    with pytest.raises(GenieClientError) as exc:
        _make_client().ask("question")
    assert exc.value.state == "FAILED"
    assert "timeout on warehouse" in str(exc.value)


def test_polling_respects_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    start_body = {"conversation_id": "c1", "message": {"id": "m1"}}
    # Always return IN_PROGRESS -- the timeout should trip.
    responses: list[Any] = [start_body] + [{"status": "IN_PROGRESS", "id": "m1"}] * 50
    mock, _ = _build_urlopen_sequence(responses)
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)
    # Fake a monotonic clock that jumps past the timeout after a few calls.
    counter = {"n": 0}

    def _fake_mono() -> float:
        counter["n"] += 1
        return 100.0 if counter["n"] > 4 else 0.0

    monkeypatch.setattr(
        "backend.services.genie_client.time.monotonic", _fake_mono
    )

    with pytest.raises(GenieClientError) as exc:
        _make_client().ask("question")
    assert "polling timed out" in str(exc.value)


# ---------------------------------------------------------------------------
# ping()
# ---------------------------------------------------------------------------


def test_ping_returns_true_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    mock, _ = _build_urlopen_sequence([{"id": "sp-test-0001"}])
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    assert _make_client().ping() is True


def test_ping_returns_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock, _ = _build_urlopen_sequence([_http_error(404, body="no such space")])
    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", mock)

    assert _make_client().ping() is False
