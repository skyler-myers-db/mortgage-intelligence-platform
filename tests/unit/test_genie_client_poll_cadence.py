"""Server-side Genie polling stays inside Databricks' 1-5 s guidance (delivery-04).

The Conversation API documents polling a message every 1-5 seconds. The
client used to start at 0.5 s. This drives the real ``_poll_message`` loop
with a scripted ``_get`` and records every sleep it asks for.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services import genie_client
from backend.services.genie_client import GenieClient

_GUIDANCE_MIN_S = 1.0
_GUIDANCE_MAX_S = 5.0


def _client() -> GenieClient:
    return GenieClient(host="https://example.cloud.databricks.com", token="t", space_id="space-1")


def test_every_poll_interval_is_inside_the_documented_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    states = iter(["SUBMITTED", "FETCHING_METADATA", "ASKING_AI", "EXECUTING_QUERY", "EXECUTING_QUERY",
                   "EXECUTING_QUERY", "EXECUTING_QUERY", "COMPLETED"])
    sleeps: list[float] = []

    def scripted_get(url: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        _ = url, timeout_s
        return {"status": next(states)}

    monkeypatch.setattr(client, "_get", scripted_get)
    monkeypatch.setattr(genie_client.time, "sleep", sleeps.append)

    body = client._poll_message("conv-1", "msg-1", timeout_s=600)

    assert body["status"] == "COMPLETED"
    assert len(sleeps) == 7
    assert sleeps[0] == _GUIDANCE_MIN_S
    assert all(_GUIDANCE_MIN_S <= interval <= _GUIDANCE_MAX_S for interval in sleeps), sleeps
    assert sleeps == sorted(sleeps), "the cadence only ever backs off"
    assert max(sleeps) == GenieClient._POLL_MAX_S == 2.0
