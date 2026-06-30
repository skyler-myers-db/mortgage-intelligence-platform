from __future__ import annotations

import json
from typing import Any

import pytest

import backend.agents.mortgage_growth_copilot as copilot_module
from backend.config.settings import Settings
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)


class _ReadyWorkspace:
    def __init__(self, *, ready: bool = True, task: str = "agent/v1/responses") -> None:
        self.serving_endpoints = _ReadyServingEndpoints(ready=ready, task=task)


class _ReadyServingEndpoints:
    def __init__(self, *, ready: bool, task: str) -> None:
        self.ready = ready
        self.task = task

    def get(self, endpoint: str) -> object:
        _ = endpoint
        return type(
            "EndpointDetails",
            (),
            {
                "state": type("EndpointState", (), {"ready": "READY" if self.ready else "NOT_READY"})(),
                "task": self.task,
            },
        )()


def _enable_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        copilot_module,
        "get_settings",
        lambda: Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
            mip_agent_supervisor_id="supervisor-1",
        ),
    )


def test_prompt_agent_invokes_supervisor_endpoint_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_text = "show borrowers in the money for review"
    calls: list[dict[str, Any]] = []

    def fake_query_serving_endpoint(
        workspace_client: object,
        endpoint: str,
        *,
        prompt: str,
        client_request_id: str | None = None,
        task: str | None = None,
    ) -> dict[str, Any]:
        calls.append(
            {
                "workspace_client": workspace_client,
                "endpoint": endpoint,
                "prompt": prompt,
                "client_request_id": client_request_id,
                "task": task,
            }
        )
        return {"id": "resp-supervisor-1", "output": [{"content": "ack"}]}

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": prompt_text, "states": ["IL"]},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(calls) == 1
    assert calls[0]["endpoint"] == "mip-supervisor-endpoint"
    assert calls[0]["task"] == "agent/v1/responses"
    assert calls[0]["client_request_id"].startswith("mip-growth-agent-")
    assert prompt_text not in calls[0]["prompt"]
    assert "Objective hash:" in calls[0]["prompt"]
    assert "Reviewed workflow selected by the app contract: Daily Refi Opportunity Brief" in calls[0]["prompt"]
    assert body["execution_mode"] == "agent_framework"
    assert body["trace_kind"] == "agent_framework"
    assert body["planner_label"] == "Databricks Supervisor Agent"
    assert body["workflow"]["id"] == "daily_refi_brief"
    assert body["route"] == "/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL"
    assert body["genie_trusted_assets"] == [
        "databricks.serving_endpoint.mip-supervisor-endpoint",
        "databricks.supervisor_agent.supervisor-1",
    ]
    framework_chip = next(
        chip for chip in body["governance_chips"] if chip["label"] == "Multi-agent framework"
    )
    assert framework_chip["status"] == "passed"
    assert framework_chip["evidence_ref"] == body["genie_question_hash"]
    assert len(body["genie_question_hash"]) == 64
    assert prompt_text.lower() not in json.dumps(body).lower()
    assert prompt_text.lower() not in json.dumps(lakebase.runs, default=str).lower()
    assert prompt_text.lower() not in json.dumps(lakebase.audit_events, default=str).lower()


def test_prompt_agent_falls_back_when_supervisor_endpoint_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_query(*args: object, **kwargs: object) -> object:
        _ = args, kwargs
        raise RuntimeError("serving endpoint unavailable")

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fail_query)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "find refinance opportunities"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "deterministic"
    assert body["trace_kind"] == "local_hash"
    assert body["planner_label"] == "Reviewed deterministic planner"
    evidence = json.loads(lakebase.runs[0]["agent_evidence"])
    assert evidence["fallback_reason"] == "agent_orchestrator_unavailable"


@pytest.mark.parametrize(
    "settings",
    [
        Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint=None,
            mip_agent_supervisor_id="supervisor-1",
        ),
        Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
            mip_agent_supervisor_id=None,
        ),
    ],
)
def test_prompt_agent_falls_back_when_supervisor_config_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(copilot_module, "get_settings", lambda: settings)
    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(
        copilot_module,
        "query_serving_endpoint",
        lambda *args, **kwargs: calls.append({"args": args, "kwargs": kwargs}) or {"id": "bad"},
    )
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "find refinance opportunities"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "deterministic"
    assert body["trace_kind"] == "local_hash"
    assert calls == []
    evidence = json.loads(lakebase.runs[0]["agent_evidence"])
    assert evidence["fallback_reason"] == "agent_orchestrator_not_configured"


@pytest.mark.parametrize(
    ("workspace", "response_payload", "expected_calls"),
    [
        (_ReadyWorkspace(ready=False), {"id": "not-called"}, 0),
        (_ReadyWorkspace(task="llm/v1/chat"), {"id": "not-called"}, 0),
        (_ReadyWorkspace(task="not_agent"), {"id": "not-called"}, 0),
        (_ReadyWorkspace(task="agentless-chat"), {"id": "not-called"}, 0),
        (_ReadyWorkspace(), {}, 1),
    ],
)
def test_prompt_agent_falls_back_when_supervisor_is_not_proven_ready(
    monkeypatch: pytest.MonkeyPatch,
    workspace: object,
    response_payload: dict[str, Any],
    expected_calls: int,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_query_serving_endpoint(*args: object, **kwargs: object) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return response_payload

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: workspace)
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "find refinance opportunities"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "deterministic"
    assert len(calls) == expected_calls


def test_prompt_agent_rejects_pii_before_supervisor_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(
        copilot_module,
        "query_serving_endpoint",
        lambda *args, **kwargs: calls.append({"args": args, "kwargs": kwargs}) or {"id": "bad"},
    )
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "find refi opportunities at 742 evergreen terrace"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 422
    assert calls == []
    assert lakebase.runs == []


@pytest.mark.parametrize(
    "prompt",
    [
        "top 10 prime refi candidates",
        "show 10 high equity borrowers",
    ],
)
def test_prompt_agent_allows_numeric_rank_prompts_with_supervisor_enabled(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_query_serving_endpoint(
        workspace_client: object,
        endpoint: str,
        *,
        prompt: str,
        client_request_id: str | None = None,
        task: str | None = None,
    ) -> dict[str, Any]:
        calls.append(
            {
                "workspace_client": workspace_client,
                "endpoint": endpoint,
                "prompt": prompt,
                "client_request_id": client_request_id,
                "task": task,
            }
        )
        return {"id": "resp-supervisor-1", "output": [{"content": "ack"}]}

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": prompt},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert calls
