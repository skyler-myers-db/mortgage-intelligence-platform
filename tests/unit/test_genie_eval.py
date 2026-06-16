from __future__ import annotations

import email.message
import io
import json
import urllib.error
from pathlib import Path

import yaml

from tools import genie_eval


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _http_error(code: int, reason: str = "Service Unavailable") -> urllib.error.HTTPError:
    headers = email.message.Message()
    return urllib.error.HTTPError(
        "https://example.invalid/api/genie/message",
        code,
        reason,
        headers,
        io.BytesIO(b'{"retry_after_seconds":0.01}'),
    )


def test_ask_retries_transient_503(monkeypatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_urlopen(req, timeout):  # noqa: ANN001
        calls.append(timeout)
        if len(calls) == 1:
            raise _http_error(503)
        return _Response({"answer": "ok"})

    monkeypatch.setattr(genie_eval.urllib.request, "urlopen", fake_urlopen)

    payload, _elapsed = genie_eval._ask(
        "https://example.invalid",
        "token",
        "question",
        30,
        attempts=2,
        retry_backoff_s=20,
        sleep=sleeps.append,
    )

    assert payload == {"answer": "ok"}
    assert calls == [30, 30]
    assert sleeps == [0.01]


def test_ask_retries_degraded_response_body(monkeypatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_urlopen(req, timeout):  # noqa: ANN001
        calls.append(timeout)
        if len(calls) == 1:
            return _Response({"source": "degraded", "answer": "warming"})
        return _Response({"source": "genie", "answer": "ok"})

    monkeypatch.setattr(genie_eval.urllib.request, "urlopen", fake_urlopen)

    payload, _elapsed = genie_eval._ask(
        "https://example.invalid",
        "token",
        "question",
        30,
        attempts=2,
        retry_backoff_s=20,
        sleep=sleeps.append,
    )

    assert payload == {"source": "genie", "answer": "ok"}
    assert calls == [30, 30]
    assert sleeps == [20]


def test_ask_does_not_retry_non_transient_http_error(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(req, timeout):  # noqa: ANN001
        nonlocal calls
        calls += 1
        raise _http_error(400, "Bad Request")

    monkeypatch.setattr(genie_eval.urllib.request, "urlopen", fake_urlopen)

    payload, _elapsed = genie_eval._ask(
        "https://example.invalid",
        "token",
        "question",
        30,
        attempts=3,
        retry_backoff_s=20,
        sleep=lambda _seconds: None,
    )

    assert calls == 1
    assert payload["error"] == "HTTP 400: Bad Request"


def test_permit_gap_eval_accepts_roadmap_language() -> None:
    data = yaml.safe_load(Path("tools/genie_eval_questions.yml").read_text(encoding="utf-8"))
    permit = next(q for q in data["questions"] if q["id"] == "blocked_permit_question")
    assert "roadmap" in permit["require_keywords"]
