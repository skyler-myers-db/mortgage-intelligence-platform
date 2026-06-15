from __future__ import annotations

import io
import json
import urllib.error

from tools import wait_app_ready


class _Response:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _http_error(code: int, payload: dict[str, object]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/api/admin/health",
        code,
        "Forbidden",
        {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def test_wait_ready_falls_back_to_authenticated_health_when_admin_forbidden(monkeypatch) -> None:
    calls: list[str] = []

    def fake_urlopen(req, timeout):  # noqa: ANN001
        calls.append(req.full_url)
        if req.full_url.endswith("/api/admin/health"):
            raise _http_error(403, {"detail": "forbidden"})
        return _Response(
            {
                "status": "ok",
                "dependencies": {"warehouse": "up", "lakebase": "up", "genie": "up"},
                "circuit_breakers": {"warehouse": "closed", "lakebase": "closed", "genie": "closed"},
            }
        )

    monkeypatch.setattr(wait_app_ready.urllib.request, "urlopen", fake_urlopen)

    body = wait_app_ready.wait_ready(
        base="https://example.invalid",
        token="token",
        timeout_s=10,
        interval_s=1,
        request_timeout_s=3,
        sleep=lambda _seconds: None,
    )

    assert body["status"] == "ok"
    assert calls == [
        "https://example.invalid/api/admin/health",
        "https://example.invalid/api/health",
    ]


def test_wait_ready_waits_for_closed_breakers(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def fake_urlopen(req, timeout):  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        breaker = "open" if attempts == 1 else "closed"
        return _Response(
            {
                "status": "ok",
                "dependencies": {"warehouse": "up", "lakebase": "up", "genie": "up"},
                "circuit_breakers": {"warehouse": "closed", "lakebase": "closed", "genie": breaker},
            }
        )

    monkeypatch.setattr(wait_app_ready.urllib.request, "urlopen", fake_urlopen)

    body = wait_app_ready.wait_ready(
        base="https://example.invalid",
        token="token",
        timeout_s=10,
        interval_s=1,
        request_timeout_s=3,
        sleep=sleeps.append,
    )

    assert body["circuit_breakers"]["genie"] == "closed"
    assert attempts == 2
    assert sleeps == [1]


def test_wait_ready_can_warm_up_half_open_genie_breaker(monkeypatch) -> None:
    health_attempts = 0
    probe_calls = 0

    def fake_urlopen(req, timeout):  # noqa: ANN001
        nonlocal health_attempts, probe_calls
        if req.full_url.endswith("/api/genie/message"):
            probe_calls += 1
            return _Response({"source": "genie"})
        health_attempts += 1
        breaker = "half_open" if health_attempts == 1 else "closed"
        return _Response(
            {
                "status": "ok",
                "dependencies": {"warehouse": "up", "lakebase": "up", "genie": "up"},
                "circuit_breakers": {"warehouse": "closed", "lakebase": "closed", "genie": breaker},
            }
        )

    monkeypatch.setattr(wait_app_ready.urllib.request, "urlopen", fake_urlopen)

    body = wait_app_ready.wait_ready(
        base="https://example.invalid",
        token="token",
        timeout_s=10,
        interval_s=1,
        request_timeout_s=3,
        genie_probe_question="Warm Genie",
        sleep=lambda _seconds: None,
    )

    assert body["circuit_breakers"]["genie"] == "closed"
    assert probe_calls == 1


def test_wait_ready_times_out_with_last_diagnostics(monkeypatch) -> None:
    def fake_urlopen(req, timeout):  # noqa: ANN001
        return _Response(
            {
                "status": "degraded",
                "dependencies": {"warehouse": "up", "lakebase": "up", "genie": "down"},
                "circuit_breakers": {"warehouse": "closed", "lakebase": "closed", "genie": "open"},
            }
        )

    monkeypatch.setattr(wait_app_ready.urllib.request, "urlopen", fake_urlopen)

    try:
        wait_app_ready.wait_ready(
            base="https://example.invalid",
            token="token",
            timeout_s=0,
            interval_s=1,
            request_timeout_s=3,
            sleep=lambda _seconds: None,
        )
    except TimeoutError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected timeout")

    assert "status='degraded'" in message
    assert "dependencies.genie='down'" in message
    assert "circuit_breakers.genie='open'" in message
