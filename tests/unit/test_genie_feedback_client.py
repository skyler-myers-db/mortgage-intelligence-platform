"""Wire-contract tests for native Genie message feedback."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.services.genie_client import GenieClient


class _Response:
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


def test_send_message_feedback_uses_native_endpoint_and_positive_rating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []

    def _urlopen(request: Any, *, timeout: float) -> _Response:
        assert timeout == 50.0
        requests.append(request)
        return _Response()

    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", _urlopen)
    genie = GenieClient(
        "https://workspace.example.com",
        "token",
        "space-1",
        timeout_s=45,
    )

    genie.send_message_feedback("conversation-1", "message-1", "POSITIVE")

    assert len(requests) == 1
    request = requests[0]
    assert request.get_method() == "POST"
    assert request.full_url == (
        "https://workspace.example.com/api/2.0/genie/spaces/space-1/"
        "conversations/conversation-1/messages/message-1/feedback"
    )
    assert json.loads(request.data.decode("utf-8")) == {"rating": "POSITIVE"}


def test_send_message_feedback_rejects_non_native_rating_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _urlopen(_request: Any, *, timeout: float) -> _Response:
        nonlocal called
        called = True
        return _Response()

    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", _urlopen)
    genie = GenieClient("workspace.example.com", "token", "space-1")

    with pytest.raises(ValueError, match="POSITIVE or NEGATIVE"):
        genie.send_message_feedback("conversation-1", "message-1", "NONE")  # type: ignore[arg-type]

    assert called is False
