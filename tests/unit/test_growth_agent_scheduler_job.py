from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from jobs import run_growth_agent_monitors


class _FakeResponse:
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"due_count": 1, "actor_count": 1, "runs": [], "drafts": []}).encode(
            "utf-8"
        )


class _FakeWorkspace:
    config = SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer job-token"})
    apps = SimpleNamespace(get=lambda _name: SimpleNamespace(url="https://mip.example.app/"))


def test_growth_agent_scheduler_posts_json_with_workspace_bearer(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float) -> _FakeResponse:  # noqa: ANN001 - urllib request type
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["content_type"] = request.get_header("Content-type")
        captured["accept"] = request.get_header("Accept")
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr(run_growth_agent_monitors, "_workspace_client", lambda: _FakeWorkspace())
    monkeypatch.setattr(run_growth_agent_monitors.urllib.request, "urlopen", fake_urlopen)

    rc = run_growth_agent_monitors.main(
        [
            "--app-name=mip-app",
            "--limit=7",
            "--channels=slack",
            "--timeout-s=12",
        ]
    )

    assert rc == 0
    assert captured["url"] == "https://mip.example.app/api/v1/growth-agent/monitors/run-due-all"
    assert captured["timeout"] == 12
    assert captured["content_type"] == "application/json"
    assert captured["accept"] == "application/json"
    assert captured["authorization"] == "Bearer job-token"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["limit"] == 7
    assert payload["channels"] == ["slack"]
    assert isinstance(payload["request_id"], str)
    assert payload["request_id"] == run_growth_agent_monitors._request_id_for_bucket(
        limit=7,
        channels=["slack"],
    )


def test_growth_agent_scheduler_request_id_is_stable_per_cadence_bucket() -> None:
    now = datetime(2026, 6, 27, 13, 30, tzinfo=UTC)

    left = run_growth_agent_monitors._request_id_for_bucket(
        limit=20,
        channels=["teams", "slack"],
        now=now,
    )
    right = run_growth_agent_monitors._request_id_for_bucket(
        limit=20,
        channels=["slack", "teams"],
        now=now,
    )
    different_limit = run_growth_agent_monitors._request_id_for_bucket(
        limit=7,
        channels=["slack", "teams"],
        now=now,
    )

    assert left == right
    assert left != different_limit
