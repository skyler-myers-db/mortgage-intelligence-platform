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
        if _method == "GET":
            assert _path == "/api/2.1/supervisor-agents/supervisor-123"
            return {
                "supervisor_agent_id": "supervisor-123",
                "endpoint_name": "mas-supervisor-endpoint",
            }
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
    assert outcome.degraded_reason == "supervisor_endpoint_not_ready:NOT_READY"


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


def test_reviewed_objective_accepts_natural_compose_phrasings() -> None:
    """Live false refusal (2026-07-07): 'for those signals' was flagged as a
    human name by the lowercase-name heuristic and 422'd a legitimate compose
    objective. Closed-class English words never begin personal names."""
    from backend.schemas.growth_agent import assert_reviewed_growth_objective

    objectives = [
        (
            "Find investor owners in Illinois with high equity whose properties "
            "recently listed, check our data freshness for those signals, size "
            "the opportunity, and stage a lead queue handoff for the strongest cohort"
        ),
        "Review retention risk for these segments and rank counties by recapture upside",
        "Compare equity distributions for all cohorts and flag any stale sources",
        "Build a briefing for our strongest markets using every trusted signal",
        "Check freshness for several feeds and summarize which segments grew most",
    ]
    for objective in objectives:
        assert assert_reviewed_growth_objective(objective)


def test_reviewed_objective_still_rejects_human_names() -> None:
    from backend.schemas.growth_agent import assert_reviewed_growth_objective

    # NOTE: lowercase names after verbs outside the action list (e.g.
    # "prioritize maria garcia") are a known pre-existing gap documented at
    # _PROMPT_LOWERCASE_NAME_AFTER_ACTION_RE -- widening the verb list
    # false-refuses ordinary analytics phrasings; a real name-detection pass
    # is the follow-up, not more regex.
    for bad in [
        "Pull everything for john smith in Chicago",
        "Call Robert Johnson about his rate",
    ]:
        try:
            assert_reviewed_growth_objective(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have refused: {bad}")


class _SequentialApiClient:
    """Returns queued responses-route bodies in order and captures prompts."""

    def __init__(self, bodies: list[Any]) -> None:
        self._bodies = list(bodies)
        self.prompts: list[str] = []

    def do(self, _method: str, _path: str, body: dict[str, Any] | None = None) -> Any:
        if _method == "GET":
            assert _path == "/api/2.1/supervisor-agents/supervisor-123"
            return {
                "supervisor_agent_id": "supervisor-123",
                "endpoint_name": "mas-supervisor-endpoint",
            }
        messages = (body or {}).get("input") or []
        self.prompts.append(str((messages[0] if messages else {}).get("content", "")))
        return self._bodies.pop(0)


def _sequential_client(bodies: list[Any]) -> _FakeServingClient:
    client = _FakeServingClient()
    client.api_client = _SequentialApiClient(bodies)
    return client


_BAD_PARAMS_PLAN = (
    '{"objective_summary":"s","steps":[{"step_id":"step-1","tool":"fn_build_cohort",'
    '"params":{"segment_codes":["itm"]},"rationale":"r"}],'
    '"expected_outcome":"o","risk_notes":"n"}'
)
_GOOD_PLAN = (
    '{"objective_summary":"s","steps":[{"step_id":"step-1","tool":"fn_build_cohort",'
    '"params":{"states":["IL"]},"rationale":"r"}],'
    '"expected_outcome":"o","risk_notes":"n"}'
)


def test_composer_repairs_invalid_plan_once_with_error_feedback() -> None:
    """Live finding (2026-07-08): the Supervisor composed a plan putting
    fn_segment_counts params on fn_build_cohort; hard validation rejected it
    honestly but the endpoint gave up. One bounded repair turn (mirroring the
    Genie SQL-repair precedent) feeds the validation error back; a corrected
    second plan composes."""
    client = _sequential_client(
        [_responses_body(_BAD_PARAMS_PLAN), _responses_body(_GOOD_PLAN)]
    )
    outcome = compose_growth_agent_plan(
        _request(), settings=_compose_settings(), serving_client=client
    )
    assert outcome.status == "composed", outcome.message
    assert outcome.plan is not None
    assert outcome.plan.steps[0].params == {"states": ["IL"]}
    assert len(client.api_client.prompts) == 2
    assert "failed validation" in client.api_client.prompts[1]
    assert "does not accept params" in client.api_client.prompts[1]
    # First prompt carries no repair block.
    assert "failed validation" not in client.api_client.prompts[0]


def test_composer_still_invalid_after_repair_stays_invalid() -> None:
    client = _sequential_client(
        [_responses_body(_BAD_PARAMS_PLAN), _responses_body(_BAD_PARAMS_PLAN)]
    )
    outcome = compose_growth_agent_plan(
        _request(), settings=_compose_settings(), serving_client=client
    )
    assert outcome.status == "invalid"
    assert "does not accept params" in (outcome.message or "")
    assert len(client.api_client.prompts) == 2


def test_composer_prompt_carries_the_validated_objective_text() -> None:
    """External audit blocker (2026-07-08): the planner received only the
    objective's HASH, so composed plans were generic tool-chains, never
    grounded in the operator's actual ask. The validated (PII-free) objective
    text must be in the prompt."""
    from backend.services.growth_agent_composer import composer_prompt

    req = _request(
        "Find investor owners in Illinois with high equity and stage a handoff"
    )
    prompt = composer_prompt(req)
    assert "investor owners in Illinois with high equity" in prompt
    assert "Objective hash:" in prompt  # correlation id retained


def test_planner_catalog_excludes_pii_param_tools_and_composer_rejects_them() -> None:
    """External audit blocker (2026-07-08): fn_property_loan_lookup's
    address_line was a composed-plan PII egress path. Objectives are validated
    address-free, so any address in a plan is hallucinated or injected — the
    tool is planner-hidden and any plan naming it is invalid, while remaining
    fully available to the dossier specialist surface."""
    from backend.services.agent_tools import get_agent_tool, tool_catalog_for_planner

    assert "fn_property_loan_lookup" not in tool_catalog_for_planner()
    assert get_agent_tool("fn_property_loan_lookup").specialists == (
        "borrower_dossier_agent",
    )

    parsed = {
        "objective_summary": "s",
        "steps": [
            {
                "step_id": "step-1",
                "tool": "fn_property_loan_lookup",
                "params": {"address_line": "742 Evergreen Terrace", "zip5": "62704"},
                "rationale": "r",
            }
        ],
        "expected_outcome": "o",
        "risk_notes": "n",
    }
    outcome = build_validated_plan(parsed, _request(), endpoint="mas-x")
    assert outcome.status == "invalid"
    assert "not available to the plan composer" in (outcome.message or "")
