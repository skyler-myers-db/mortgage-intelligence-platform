from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

import backend.agents.mortgage_growth_copilot as copilot_module
from backend.config.settings import Settings
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)

_TEST_GATEWAY_SHA = "75ea6680b7f04bbaa6d0bbf38d7676218ae6c1cc"


class _ReadyWorkspace:
    def __init__(self, *, ready: bool = True, task: str = "agent/v1/responses") -> None:
        self.serving_endpoints = _ReadyServingEndpoints(ready=ready, task=task)
        self.api_client = _SupervisorMetadataApi()


class _SupervisorMetadataApi:
    def do(self, method: str, path: str) -> dict[str, str]:
        assert method == "GET"
        assert path == "/api/2.1/supervisor-agents/supervisor-1"
        return {
            "supervisor_agent_id": "supervisor-1",
            "endpoint_name": "mip-supervisor-endpoint",
        }


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
            mip_git_sha=_TEST_GATEWAY_SHA,
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor-endpoint",
            mip_agent_supervisor_id="supervisor-1",
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-supervisor-endpoint",
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
        return {
            "id": "resp-supervisor-1",
            "output": [
                {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "workflow_id": "listing_watch",
                                    "confidence": 0.91,
                                    "reason": "purchase intent from validated objective",
                                }
                            )
                        }
                    ]
                }
            ],
        }

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
    assert calls[0]["client_request_id"].startswith(f"mip-agent-run-{_TEST_GATEWAY_SHA}-")
    assert "Reviewed objective signals:" in calls[0]["prompt"]
    assert prompt_text not in calls[0]["prompt"]
    assert "Objective hash:" in calls[0]["prompt"]
    assert "Reviewed workflow allowlist:" in calls[0]["prompt"]
    assert "daily_refi_brief" in calls[0]["prompt"]
    assert "listing_watch" in calls[0]["prompt"]
    assert body["execution_mode"] == "agent_framework"
    assert body["trace_kind"] == "agent_framework"
    assert body["planner_label"] == "Databricks Agent Responses endpoint"
    assert body["workflow"]["id"] == "listing_watch"
    assert body["route"] == "/lead-queue?segment=listed&marketing_eligibility=Eligible+only&states=IL"
    assert "Agent Responses endpoint selected reviewed workflow" in body["interpreted_intent"]
    assert "deterministic fallback candidate" in body["interpreted_intent"]
    assert "Daily Refi Opportunity Brief" in body["agent_reasoning"]
    selection_check = next(
        check for check in body["policy_checks"] if check["label"] == "Supervisor workflow selection"
    )
    assert selection_check["status"] == "review_required"
    assert "listing_watch" in selection_check["detail"]
    assert "daily_refi_brief" in selection_check["detail"]
    assert body["genie_trusted_assets"] == [
        "databricks.serving_endpoint.mip-supervisor-endpoint",
    ]
    framework_chip = next(
        chip for chip in body["governance_chips"] if chip["label"] == "Multi-agent framework"
    )
    assert framework_chip["status"] == "review_required"
    assert "different reviewed workflow" in framework_chip["detail"]
    assert framework_chip["evidence_ref"] == body["genie_question_hash"]
    gateway_chip = next(chip for chip in body["governance_chips"] if chip["label"] == "AI Gateway")
    assert gateway_chip["status"] == "review_required"
    assert "Supervisor call was routed through the configured AI Gateway endpoint" in gateway_chip["detail"]
    assert "does not claim per-run row landing" in gateway_chip["detail"]
    assert str(calls[0]["client_request_id"]) not in json.dumps(body)
    assert len(body["genie_question_hash"]) == 64
    evidence = json.loads(lakebase.runs[0]["agent_evidence"])
    assert evidence["supervisor_workflow_id"] == "listing_watch"
    assert evidence["deterministic_workflow_id"] == "daily_refi_brief"
    assert evidence["workflow_override_review_required"] is True
    assert evidence["gateway_client_request_id"] == calls[0]["client_request_id"]
    assert evidence["serving_endpoint"] == "mip-supervisor-endpoint"
    assert evidence["serving_task"] == "agent/v1/responses"
    assert prompt_text.lower() not in json.dumps(body).lower()
    assert prompt_text.lower() not in json.dumps(lakebase.runs, default=str).lower()
    assert prompt_text.lower() not in json.dumps(lakebase.audit_events, default=str).lower()


def test_prompt_agent_replay_preserves_supervisor_divergence_review_flag(
    monkeypatch: pytest.MonkeyPatch,
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
        _ = workspace_client, prompt
        calls.append(
            {
                "endpoint": endpoint,
                "client_request_id": client_request_id,
                "task": task,
            }
        )
        return {
            "id": "resp-supervisor-1",
            "output": [{"content": '{"workflow_id":"listing_watch","reason":"purchase intent"}'}],
        }

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    payload = {
        "prompt": "show borrowers in the money for review",
        "states": ["IL"],
        "request_id": str(uuid4()),
    }
    try:
        first = client.post(
            "/api/growth-agent/agent/run",
            json=payload,
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        replay = client.post(
            "/api/growth-agent/agent/run",
            json=payload,
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["run_id"] == first.json()["run_id"]
    selection_check = next(
        check for check in replay.json()["policy_checks"] if check["label"] == "Supervisor workflow selection"
    )
    assert selection_check["status"] == "review_required"
    framework_chip = next(
        chip for chip in replay.json()["governance_chips"] if chip["label"] == "Multi-agent framework"
    )
    assert framework_chip["status"] == "review_required"
    assert len(calls) == 2
    assert calls[0]["client_request_id"] == calls[1]["client_request_id"]
    assert str(calls[0]["client_request_id"]).startswith(f"mip-agent-run-{_TEST_GATEWAY_SHA}-")
    evidence = json.loads(lakebase.runs[0]["agent_evidence"])
    assert evidence["workflow_override_review_required"] is True
    assert len(lakebase.runs) == 1


def test_prompt_agent_supervisor_prompt_uses_inferred_state_and_refi_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_text = "Find prime refinance opportunities in Illinois for branch review."
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
        return {
            "id": "resp-supervisor-1",
            "output": [{"content": '{"workflow_id":"daily_refi_brief"}'}],
        }

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": prompt_text},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(calls) == 1
    assert "State scope: IL." in calls[0]["prompt"]
    assert "Reviewed objective signals: refinance economics." in calls[0]["prompt"]
    assert "branch capacity" not in calls[0]["prompt"]
    assert prompt_text not in calls[0]["prompt"]
    assert body["execution_mode"] == "agent_framework"
    assert body["workflow"]["id"] == "daily_refi_brief"
    selection_check = next(
        check for check in body["policy_checks"] if check["label"] == "Supervisor workflow selection"
    )
    assert selection_check["status"] == "passed"
    assert "agreed on daily_refi_brief" in selection_check["detail"]
    assert body["criteria"]["states"] == ["IL"]
    assert body["route"] == "/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL"


def test_prompt_agent_rejects_unreviewed_supervisor_workflow_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_query_serving_endpoint(*args: object, **kwargs: object) -> dict[str, Any]:
        _ = args, kwargs
        return {"id": "resp-supervisor-1", "output": [{"content": '{"workflow_id":"send_email"}'}]}

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "find listed-for-sale borrowers for review", "states": ["IL"]},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "deterministic"
    assert body["workflow"]["id"] == "listing_watch"
    evidence = json.loads(lakebase.runs[0]["agent_evidence"])
    assert evidence["fallback_reason"] == "agent_orchestrator_unavailable"


def test_prompt_agent_discards_adversarial_supervisor_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adversarial_reason = "<script>alert(1)</script> John Smith SELECT * FROM mip.raw.secret"

    def fake_query_serving_endpoint(*args: object, **kwargs: object) -> dict[str, Any]:
        _ = args, kwargs
        return {
            "id": "resp-supervisor-1",
            "output": [
                {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "workflow_id": "high_equity_heloc_watch",
                                    "confidence": 1.0,
                                    "reason": adversarial_reason,
                                }
                            )
                        }
                    ]
                }
            ],
        }

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "find home equity line opportunities", "states": ["TX"]},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "agent_framework"
    assert body["workflow"]["id"] == "high_equity_heloc_watch"
    persisted_text = " ".join(
        [
            json.dumps(body, default=str),
            json.dumps(lakebase.runs, default=str),
            json.dumps(lakebase.audit_events, default=str),
        ]
    ).lower()
    for forbidden in ("<script", "john smith", "select *", "mip.raw.secret"):
        assert forbidden not in persisted_text


def test_prompt_agent_does_not_let_supervisor_override_explicit_custom_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_query_serving_endpoint(*args: object, **kwargs: object) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {"id": "resp-supervisor-1", "output": [{"content": '{"workflow_id":"listing_watch"}'}]}

    monkeypatch.setattr(copilot_module, "_workspace_client", lambda: _ReadyWorkspace())
    monkeypatch.setattr(copilot_module, "query_serving_endpoint", fake_query_serving_endpoint)
    _enable_orchestrator(monkeypatch)
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={
                "prompt": "find borrowers in both prime refi and listed segments",
                "states": ["IL"],
                "segment_codes": ["itm", "listed"],
                "segment_mode": "all",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == []
    assert body["execution_mode"] == "deterministic"
    assert body["workflow"]["id"] == "custom_segment_watch"
    assert body["criteria"]["lead_queue_filters"]["segment_codes"] == ["itm", "listed"]
    assert body["criteria"]["lead_queue_filters"]["segment_mode"] == "all"
    assert body["route"] == (
        "/lead-queue?segment_codes=itm%2Clisted&segment_mode=all&"
        "marketing_eligibility=Eligible+only&states=IL"
    )


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


@pytest.mark.parametrize(
    "prompt",
    [
        "find refi opportunities at 742 evergreen terrace",
        "Find elderly borrowers in Illinois for a mortgage offer.",
        "target older homeowners in Illinois for a mortgage offer",
        "find borrowers over 65 in Illinois",
    ],
)
def test_prompt_agent_rejects_pii_and_protected_proxies_before_supervisor_call(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
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
            json={"prompt": prompt},
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
        return {"id": "resp-supervisor-1", "output": [{"content": '{"workflow_id":"daily_refi_brief"}'}]}

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
