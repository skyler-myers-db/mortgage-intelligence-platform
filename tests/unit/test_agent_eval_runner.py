from __future__ import annotations

import json
import re
from typing import Any

from tools.databricks import run_agent_eval


def test_call_growth_agent_uses_json_content_type_and_uuid_request_id(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"workflow": {"id": "daily_refi_brief"}}

    class _Client:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            captured["timeout"] = timeout
            captured["follow_redirects"] = follow_redirects

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Response()

    monkeypatch.setattr(run_agent_eval.httpx, "Client", _Client)

    response = run_agent_eval._call_growth_agent(
        app_url="https://example.test/",
        token="redacted",
        case={"prompt": "Find prime refinance opportunities."},
        timeout_s=12,
    )

    assert response == {"workflow": {"id": "daily_refi_brief"}}
    assert captured["url"] == "https://example.test/api/growth-agent/agent/run"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["json"]["save_monitor"] is False
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        captured["json"]["request_id"],
    )


def test_log_eval_run_uses_positional_set_tag(monkeypatch) -> None:
    no_output_calls: list[list[str]] = []

    monkeypatch.setattr(run_agent_eval, "_experiment_id", lambda _name: "exp-1")
    monkeypatch.setattr(run_agent_eval, "_git_sha", lambda: "abcdef123456")

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        assert input_json is not None
        if args == ["experiments", "create-run"]:
            return {"run": {"info": {"run_id": "run-1"}}}
        raise AssertionError(f"unexpected _run call: {args}")

    def fake_run_no_output(args: list[str], *, input_json: dict[str, Any] | None = None) -> None:
        no_output_calls.append(args)
        if args[:2] == ["experiments", "set-tag"]:
            assert args[2] == "mip_eval_failures"
            assert json.loads(args[3])[0]["case_id"] == "case-a"
            assert args[4:] == ["--run-id", "run-1"]

    monkeypatch.setattr(run_agent_eval, "_run", fake_run)
    monkeypatch.setattr(run_agent_eval, "_run_no_output", fake_run_no_output)

    run_id = run_agent_eval._log_eval_run(
        experiment_name="/Shared/mip-agent-eval",
        app_url="https://mip.example.test",
        summary={
            "score": 0.0,
            "passed": 0,
            "total": 1,
            "results": [{"case_id": "case-a", "passed": False}],
        },
        responses_by_case_id={"case-a": {"error": "bad"}},
    )

    assert run_id == "run-1"
    assert any(call[:2] == ["experiments", "log-batch"] for call in no_output_calls)
    assert any(call[:3] == ["experiments", "set-tag", "mip_eval_failures"] for call in no_output_calls)
    assert any(call[:2] == ["experiments", "update-run"] for call in no_output_calls)
