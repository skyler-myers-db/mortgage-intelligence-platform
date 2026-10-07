"""The admin SSE ingress probe through the REAL middleware stack (delivery-04).

An ASGI harness calls ``backend.main.app`` directly with a recording
``send``, so every message the four BaseHTTPMiddleware layers and GZip pass
on is observed as the server would emit it: each event is its own body
message with ``more_body=True`` and GZip leaves ``text/event-stream`` alone;
a non-admin gets 403; out-of-bound parameters get 422; the probe ends at its
bound; keep-alive comments fill a gap; the registry records the run.

The in-process disconnect observation (does ``http.disconnect`` reach the
probe through those layers?) is RECORDED by
``test_a_client_disconnect_is_observed_and_recorded`` and written into
docs/load-baseline.md; it is deliberately not asserted either way: a test
that pins a transport defect is worse than none.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from backend.api import admin_sse_probe as probe
from backend.main import app

_ADMIN = [(b"x-forwarded-groups", b"mip-admin"), (b"x-forwarded-email", b"admin@example.com")]
log = logging.getLogger("test-sse-probe")


def _probe_id() -> str:
    return secrets.token_hex(8)


async def _drive(query: str, *, headers: list[tuple[bytes, bytes]] | None = None,
                 disconnect_after: int | None = None, timeout: float = 15.0) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    disconnect = anyio.Event()
    state = {"requested": False, "ticks": 0}

    async def receive() -> dict[str, Any]:
        if not state["requested"]:
            state["requested"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and bytes(message.get("body", b"")).startswith(b"id: "):
            state["ticks"] += 1
            if disconnect_after is not None and state["ticks"] >= disconnect_after:
                disconnect.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/api/v1/admin/sse-probe", "raw_path": b"/api/v1/admin/sse-probe", "root_path": "",
        "query_string": query.encode(), "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
        "headers": [(b"host", b"testserver"), (b"accept", b"text/event-stream"), (b"accept-encoding", b"gzip"),
                    *(_ADMIN if headers is None else headers)],
    }
    with anyio.fail_after(timeout):
        await app(scope, receive, send)
    return messages


def _start(messages: list[dict[str, Any]]) -> dict[bytes, bytes]:
    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 200, start
    return {key.lower(): value for key, value in start["headers"]}


def _ticks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if m["type"] == "http.response.body" and bytes(m.get("body", b"")).startswith(b"id: ")]


def test_each_event_is_its_own_unbuffered_body_message_and_gzip_leaves_it_alone() -> None:
    probe_id = _probe_id()
    messages = anyio.run(_drive, f"probe_id={probe_id}&events=5&interval_ms=100&pad_bytes=2048")

    headers = _start(messages)
    assert headers[b"content-type"].startswith(b"text/event-stream")
    assert b"content-encoding" not in headers
    assert headers[b"cache-control"] == b"no-cache, no-transform"
    assert headers[b"x-accel-buffering"] == b"no"
    ticks = _ticks(messages)
    assert len(ticks) == 5, "the probe ends at its bound"
    assert all(m.get("more_body") is True for m in ticks)
    assert all(b"\nevent: tick\n" in bytes(m["body"]) and b": xxxx" in bytes(m["body"]) for m in ticks)
    assert bytes(ticks[0]["body"]).startswith(b"id: 1\n") and bytes(ticks[-1]["body"]).startswith(b"id: 5\n")
    done = [m for m in messages if b"event: done" in bytes(m.get("body", b""))]
    assert len(done) == 1
    outcome = TestClient(app).get(f"/api/v1/admin/sse-probe/{probe_id}").json()
    assert outcome == {"events_sent": 5, "disconnected_at_seq": None, "completed": True}


def test_keepalive_comments_fill_a_gap() -> None:
    messages = anyio.run(_drive, f"probe_id={_probe_id()}&events=2&interval_ms=2200&keepalive_ms=1000")

    keepalives = [m for m in messages if bytes(m.get("body", b"")) == b": keepalive\n\n"]
    assert len(keepalives) >= 2
    assert len(_ticks(messages)) == 2


def test_a_non_admin_is_refused() -> None:
    messages = anyio.run(lambda: _drive(f"probe_id={_probe_id()}&events=1", headers=[(b"x-forwarded-groups", b"")]))

    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 403
    assert TestClient(app).get(f"/api/v1/admin/sse-probe/{_probe_id()}",
                               headers={"X-Forwarded-Groups": ""}).status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        "probe_id=NOTHEX0000000000&events=1",
        "probe_id={id}&events=0",
        "probe_id={id}&events=121",
        "probe_id={id}&interval_ms=99",
        "probe_id={id}&pad_bytes=4097",
        "probe_id={id}&keepalive_ms=500",
        "probe_id={id}&events=120&interval_ms=3000",
    ],
)
def test_out_of_bound_parameters_are_a_422(query: str) -> None:
    response = TestClient(app).get(f"/api/v1/admin/sse-probe?{query.format(id=_probe_id())}")

    assert response.status_code == 422


def test_an_unknown_probe_is_a_404() -> None:
    assert TestClient(app).get(f"/api/v1/admin/sse-probe/{_probe_id()}").status_code == 404


def test_the_registry_is_bounded() -> None:
    ids = [_probe_id() for _ in range(probe.REGISTRY_SIZE + 3)]
    for probe_id in ids:
        probe.REGISTRY.start(probe_id)

    assert probe.REGISTRY.get(ids[0]) is None
    assert probe.REGISTRY.get(ids[-1]) is not None


def test_a_client_disconnect_is_observed_and_recorded() -> None:
    """Deliver http.disconnect after 3 events; RECORD what the probe saw.

    Not asserted either way (see the module docstring): the observation is
    logged and copied into docs/load-baseline.md by hand.
    """
    probe_id = _probe_id()
    messages = anyio.run(lambda: _drive(f"probe_id={probe_id}&events=12&interval_ms=150", disconnect_after=3))

    run = probe.REGISTRY.get(probe_id)
    assert run is not None, "the run was registered"
    log.warning(
        "sse_probe_in_process_disconnect ticks_sent=%s events_sent=%s disconnected_at_seq=%s completed=%s",
        len(_ticks(messages)), run.events_sent, run.disconnected_at_seq, run.completed,
    )
