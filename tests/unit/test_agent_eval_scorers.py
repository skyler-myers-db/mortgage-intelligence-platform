from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.growth_agent_workflows import build_growth_agent_route, planned_workflow
from tests.eval.scorers import load_cases, score_batch, score_growth_agent_response
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)


def _response_for(case_id: str) -> dict:
    if case_id == "pii_prompt_is_rejected_before_planning":
        return {"error": "prompt must use reviewed, non-PII mortgage-growth criteria"}
    if case_id == "custom_all_mode_preserves_intersection_semantics":
        return {
            "workflow": {"id": "custom_segment_watch"},
            "execution_mode": "deterministic",
            "route": "/lead-queue?segment_codes=itm%2Clisted&segment_mode=all&marketing_eligibility=Eligible+only",
            "criteria": {
                "lead_queue_filters": {
                    "segment_codes": ["itm", "listed"],
                    "segment_mode": "all",
                }
            },
        }
    if case_id == "custom_any_mode_preserves_or_semantics":
        return {
            "workflow": {"id": "custom_segment_watch"},
            "execution_mode": "deterministic",
            "route": "/lead-queue?segment_codes=itm%2Cequity&segment_mode=any&marketing_eligibility=Eligible+only",
            "criteria": {
                "lead_queue_filters": {
                    "segment_codes": ["itm", "equity"],
                    "segment_mode": "any",
                }
            },
        }
    if case_id == "listed_objective_routes_to_purchase_watch":
        return {
            "workflow": {"id": "listing_watch"},
            "execution_mode": "deterministic",
            "route": "/lead-queue?segment=listed&marketing_eligibility=Eligible+only",
            "criteria": {
                "lead_queue_filters": {
                    "segment_codes": ["listed"],
                    "segment_mode": "any",
                }
            },
        }
    return {
        "workflow": {"id": "daily_refi_brief"},
        "execution_mode": "deterministic",
        "route": "/lead-queue?segment=itm&marketing_eligibility=Eligible+only",
        "criteria": {
            "lead_queue_filters": {
                "segment_codes": ["itm"],
                "segment_mode": "any",
            }
        },
    }


def test_golden_agent_cases_load() -> None:
    cases = load_cases()
    assert {case["id"] for case in cases} == {
        "refi_objective_routes_to_daily_refi",
        "listed_objective_routes_to_purchase_watch",
        "custom_any_mode_preserves_or_semantics",
        "custom_all_mode_preserves_intersection_semantics",
        "pii_prompt_is_rejected_before_planning",
    }


def test_growth_agent_eval_scorer_passes_expected_response() -> None:
    case = next(case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi")
    result = score_growth_agent_response(_response_for(str(case["id"])), case)
    assert result["passed"] is True
    assert result["score"] == 1.0


def test_growth_agent_eval_scorer_fails_segment_mode_drift() -> None:
    case = next(case for case in load_cases() if case["id"] == "custom_any_mode_preserves_or_semantics")
    response = _response_for(str(case["id"]))
    response["criteria"]["lead_queue_filters"]["segment_mode"] = "all"
    result = score_growth_agent_response(response, case)
    assert result["passed"] is False
    assert result["checks"]["segment_mode"] is False


def test_growth_agent_eval_batch_summary() -> None:
    cases = load_cases()
    responses = {str(case["id"]): _response_for(str(case["id"])) for case in cases}
    summary = score_batch(responses, cases)
    assert summary["passed"] == 5
    assert summary["total"] == 5
    assert summary["score"] == 1.0


def test_growth_agent_eval_requires_full_case_floor() -> None:
    with pytest.raises(ValueError, match="at least 5 cases"):
        score_batch({}, [])


def test_golden_cases_score_real_reviewed_planner_outputs() -> None:
    responses: dict[str, dict] = {}
    for case in load_cases():
        is_custom_case = case["id"] == "custom_any_mode_preserves_or_semantics"
        try:
            payload = GrowthAgentPromptRunRequest(
                prompt=str(case["prompt"]),
                segment_codes=case.get("expected_segment_codes", []) if is_custom_case else [],
                segment_mode=str(case.get("expected_segment_mode", "any")),
            )
        except ValidationError as exc:
            responses[str(case["id"])] = {"error": str(exc)}
            continue
        workflow, _intent = planned_workflow(payload)
        responses[str(case["id"])] = {
            "workflow": {"id": workflow.id},
            "execution_mode": "deterministic",
            "route": build_growth_agent_route(workflow.route_filters, path=workflow.route_path),
            "criteria": {
                "lead_queue_filters": {
                    "segment_codes": list(workflow.route_filters.get("segment_codes", workflow.route_filters.get("segment", "")).split(",")),
                    "segment_mode": workflow.route_filters.get("segment_mode", "any"),
                }
            },
        }

    summary = score_batch(responses)
    assert summary["passed"] == summary["total"] == 5


def test_golden_cases_score_real_agent_endpoint_outputs() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    responses: dict[str, dict] = {}
    try:
        for case in load_cases():
            response = client.post(
                "/api/growth-agent/agent/run",
                json={"prompt": case["prompt"]},
                headers={"X-Forwarded-Email": "operator@example.com"},
            )
            responses[str(case["id"])] = (
                response.json() if response.status_code < 400 else {"error": str(response.json())}
            )
    finally:
        _clear_overrides()

    summary = score_batch(responses)
    assert summary["passed"] == summary["total"] == 5
