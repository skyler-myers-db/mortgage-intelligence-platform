"""Unit tests for the Growth Agent plan composer + plan schema validation."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.config.settings import Settings
from backend.schemas.agent_plan import ComposePlanRequest
from backend.services.growth_agent_composer import (
    build_validated_plan,
    compose_growth_agent_plan,
    composer_prompt,
)


def _compose_settings() -> Settings:
    return Settings(
        databricks_host="dbc-test.cloud.databricks.com",
        databricks_warehouse_id="wh-123",
        genie_space_id="space-abc",
        lakebase_host="lb-test",
        lakebase_user="mip_app",
        mip_agent_orchestrator=True,
        mip_agent_supervisor_id="supervisor-123",
        mip_agent_serving_endpoint="mas-supervisor-endpoint",
    )


class _State:
    def __init__(self, ready: str) -> None:
        self.ready = ready


class _Details:
    def __init__(self, ready: str, task: str) -> None:
        self.state = _State(ready)
        self.task = task


class _Endpoints:
    def __init__(self, ready: str, task: str) -> None:
        self._details = _Details(ready, task)

    def get(self, _endpoint: str) -> _Details:
        return self._details


class _ApiClient:
    def __init__(self, body: Any, raise_on_query: bool) -> None:
        self._body = body
        self._raise = raise_on_query

    def do(self, _method: str, _path: str, body: dict[str, Any] | None = None) -> Any:
        if self._raise:
            raise RuntimeError("serving endpoint unreachable")
        return self._body


class _FakeServingClient:
    def __init__(
        self,
        *,
        ready: str = "READY",
        task: str = "agent/v1/responses",
        body: Any = None,
        raise_on_query: bool = False,
    ) -> None:
        self.serving_endpoints = _Endpoints(ready, task)
        self.api_client = _ApiClient(body, raise_on_query)


def _responses_body(plan_json: str) -> dict[str, Any]:
    return {"output": [{"content": [{"text": plan_json}]}]}


def _request(objective: str = "Compose a refi and equity growth plan for branch review.") -> ComposePlanRequest:
    return ComposePlanRequest(objective=objective, execute=False)


# ----------------------------------------------------------------------------
# build_validated_plan — pure schema/registry validation
# ----------------------------------------------------------------------------


def test_valid_plan_composes_with_registry_tools() -> None:
    parsed = {
        "objective_summary": "Screen refi economics then gate to eligible leads.",
        "steps": [
            {"step_id": "step-1", "tool": "fn_build_cohort", "params": {}, "rationale": "broad screen"},
            {
                "step_id": "step-2",
                "tool": "fn_segment_counts",
                "params": {"segment_codes": ["itm"], "segment_mode": "any"},
                "rationale": "apply eligibility gates",
            },
        ],
        "expected_outcome": "A reconciled eligible subset.",
        "risk_notes": "None.",
    }
    outcome = build_validated_plan(parsed, _request(), endpoint="mas-supervisor-endpoint")
    assert outcome.status == "composed"
    assert outcome.plan is not None
    assert [step.tool for step in outcome.plan.steps] == ["fn_build_cohort", "fn_segment_counts"]
    # No state-writing / handoff tool -> approval not forced.
    assert outcome.plan.requires_approval is False


def test_unknown_tool_is_rejected_without_fallback() -> None:
    parsed = {
        "steps": [
            {"step_id": "s1", "tool": "fn_build_cohort", "params": {}},
            {"step_id": "s2", "tool": "fn_totally_made_up", "params": {}},
        ]
    }
    outcome = build_validated_plan(parsed, _request(), endpoint="ep")
    assert outcome.status == "invalid"
    assert outcome.plan is None
    assert "unregistered tool" in (outcome.message or "")


def test_bad_params_are_rejected() -> None:
    parsed = {"steps": [{"step_id": "s1", "tool": "fn_build_cohort", "params": {"bogus": 1}}]}
    outcome = build_validated_plan(parsed, _request(), endpoint="ep")
    assert outcome.status == "invalid"
    assert "does not accept params" in (outcome.message or "")


def test_out_of_domain_state_param_is_rejected() -> None:
    parsed = {"steps": [{"step_id": "s1", "tool": "fn_build_cohort", "params": {"states": ["ZZ"]}}]}
    outcome = build_validated_plan(parsed, _request(), endpoint="ep")
    assert outcome.status == "invalid"


def test_step_cap_is_enforced() -> None:
    parsed = {"steps": [{"step_id": f"s{i}", "tool": "fn_build_cohort", "params": {}} for i in range(9)]}
    outcome = build_validated_plan(parsed, _request(), endpoint="ep")
    assert outcome.status == "invalid"
    assert "step limit" in (outcome.message or "")


def test_empty_steps_are_rejected() -> None:
    outcome = build_validated_plan({"steps": []}, _request(), endpoint="ep")
    assert outcome.status == "invalid"


def test_requires_approval_forced_server_side_for_handoff_tool() -> None:
    # Model omits requires_approval, but a handoff tool must force it True.
    parsed = {
        "objective_summary": "Prep a lead queue handoff.",
        "steps": [
            {"step_id": "s1", "tool": "fn_build_cohort", "params": {}},
            {"step_id": "s2", "tool": "fn_lead_queue_url", "params": {"segment_codes": ["itm"]}},
        ],
    }
    outcome = build_validated_plan(parsed, _request(), endpoint="ep")
    assert outcome.status == "composed"
    assert outcome.plan is not None
    assert outcome.plan.requires_approval is True


def test_rationale_is_scrubbed_and_clamped() -> None:
    parsed = {
        "steps": [
            {
                "step_id": "s1",
                "tool": "fn_build_cohort",
                "params": {},
                "rationale": "call 415-555-1234 or email a@b.com " + "x" * 400,
            }
        ]
    }
    outcome = build_validated_plan(parsed, _request(), endpoint="ep")
    assert outcome.status == "composed"
    assert outcome.plan is not None
    rationale = outcome.plan.steps[0].rationale
    assert "415-555-1234" not in rationale
    assert "a@b.com" not in rationale
    assert "[PHONE-REDACTED]" in rationale
    assert len(rationale) <= 300


# ----------------------------------------------------------------------------
# compose_growth_agent_plan — end-to-end with a fake serving client
# ----------------------------------------------------------------------------


def test_compose_with_valid_model_json() -> None:
    plan_json = json.dumps(
        {
            "objective_summary": "Refi screen then gate.",
            "steps": [
                {"step_id": "step-1", "tool": "fn_build_cohort", "params": {}, "rationale": "screen"},
                {"step_id": "step-2", "tool": "fn_lead_queue_url", "params": {"segment_codes": ["itm"]}},
            ],
        }
    )
    client = _FakeServingClient(body=_responses_body(plan_json))
    outcome = compose_growth_agent_plan(_request(), settings=_compose_settings(), serving_client=client)
    assert outcome.status == "composed"
    assert outcome.plan is not None
    assert outcome.plan.requires_approval is True
    assert outcome.endpoint == "mas-supervisor-endpoint"


def test_compose_with_malformed_json_is_invalid() -> None:
    client = _FakeServingClient(body=_responses_body("this is not json {{{"))
    outcome = compose_growth_agent_plan(_request(), settings=_compose_settings(), serving_client=client)
    assert outcome.status == "invalid"
    assert outcome.plan is None


def test_compose_degrades_when_endpoint_not_ready() -> None:
    client = _FakeServingClient(ready="NOT_READY", body=_responses_body("{}"))
    outcome = compose_growth_agent_plan(_request(), settings=_compose_settings(), serving_client=client)
    assert outcome.status == "degraded"
    assert outcome.degraded_reason == "orchestrator_not_ready"


def test_compose_degrades_when_orchestrator_disabled() -> None:
    settings = Settings(
        databricks_host="dbc-test.cloud.databricks.com",
        databricks_warehouse_id="wh-123",
        genie_space_id="space-abc",
        lakebase_host="lb-test",
        lakebase_user="mip_app",
    )
    outcome = compose_growth_agent_plan(_request(), settings=settings, serving_client=_FakeServingClient())
    assert outcome.status == "degraded"
    assert outcome.degraded_reason == "orchestrator_disabled"


def test_compose_degrades_when_call_raises() -> None:
    client = _FakeServingClient(raise_on_query=True)
    outcome = compose_growth_agent_plan(_request(), settings=_compose_settings(), serving_client=client)
    assert outcome.status == "degraded"
    assert outcome.degraded_reason == "orchestrator_call_failed"


def test_objective_rejects_pii_at_request_boundary() -> None:
    with pytest.raises(ValueError):
        ComposePlanRequest(objective="Run this for John Smith refi opportunities.")


def test_composer_prompt_lists_registry_and_demands_json() -> None:
    prompt = composer_prompt(_request())
    assert "fn_build_cohort" in prompt
    assert "REQUIRES HUMAN APPROVAL" in prompt
    assert "STRICT JSON" in prompt
    assert "never invent a tool" in prompt
