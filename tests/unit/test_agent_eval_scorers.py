from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.growth_agent_workflows import build_growth_agent_route, planned_workflow
from tests.eval.scorers import (
    count_reconciles,
    load_cases,
    score_batch,
    score_count_reconciliation,
    score_growth_agent_response,
)
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)


def _response_for(case_id: str) -> dict:
    if case_id == "pii_prompt_is_rejected_before_planning":
        return {"error": "prompt must use reviewed, non-PII mortgage-growth criteria"}
    common = {
        "broad_total": 117404,
        "actionable_total": 5394,
        "destination_total": 5394,
        "actionable_cohort_fingerprint": "c" * 64,
        "destination_cohort_fingerprint": "c" * 64,
        "destination_fingerprint_tool_result_hash": "a" * 64,
        "actionable_snapshot_id": "snapshot-eval-1",
        "destination_snapshot_id": "snapshot-eval-1",
        "source_assets": ["mip.gold.borrower_360", "mip.gold.lead_population"],
        "trace_id": "agent-trace-evalcase",
        "tool_result_hash": "a" * 64,
        "policy_checks": [
            {"label": "Broad vs actionable reconciliation", "status": "passed"},
            {"label": "Approval gate required", "status": "passed"},
        ],
    }
    if case_id == "custom_all_mode_preserves_intersection_semantics":
        return {
            **common,
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
            **common,
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
            **common,
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
        **common,
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
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    result = score_growth_agent_response(_response_for(str(case["id"])), case)
    assert result["passed"] is True
    assert result["score"] == 1.0


def test_growth_agent_eval_scorer_fails_segment_mode_drift() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "custom_any_mode_preserves_or_semantics"
    )
    response = _response_for(str(case["id"]))
    response["criteria"]["lead_queue_filters"]["segment_mode"] = "all"
    result = score_growth_agent_response(response, case)
    assert result["passed"] is False
    assert result["checks"]["segment_mode"] is False


def test_growth_agent_eval_scorer_fails_count_reconciliation_drift() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))
    response["actionable_total"] = response["broad_total"] + 1

    result = score_growth_agent_response(response, case)

    assert result["passed"] is False
    assert result["checks"]["count_reconciles"] is False
    assert result["count_reconciliation"]["checks"]["counts_ordered"] is False


def test_count_reconciliation_rejects_ordered_but_wrong_destination_total() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))
    response["destination_total"] = response["actionable_total"] - 1

    result = score_count_reconciliation(response, case)

    assert result["checks"]["counts_ordered"] is True
    assert result["checks"]["destination_matches_actionable"] is False
    assert result["passed"] is False


def test_count_reconciliation_rejects_equal_counts_with_wrong_destination_identity() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))
    response["destination_cohort_fingerprint"] = "d" * 64

    result = score_count_reconciliation(response, case)

    assert result["checks"]["destination_matches_actionable"] is True
    assert result["checks"]["cohort_identity_matches"] is False
    assert result["passed"] is False


def test_count_reconciliation_fails_closed_without_common_snapshot() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))
    response.pop("destination_snapshot_id")
    response["destination_identity_error"] = "snapshot unsupported"

    result = score_count_reconciliation(response, case)

    assert result["checks"]["common_snapshot"] is False
    assert result["checks"]["destination_identity_complete"] is False
    assert result["passed"] is False


def test_count_reconciles_mlflow_scorer_signature() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))

    assert count_reconciles(inputs={"prompt": case["prompt"]}, outputs=response, expectations=case)


def test_count_reconciles_reads_case_from_inputs_for_traced_replay() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))

    assert count_reconciles(
        inputs={"prompt": case["prompt"], "case_id": case["id"], "case": case},
        outputs=response,
        expectations=None,
    )


def test_count_reconciles_reads_json_case_from_inputs_for_mlflow_serialization() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))

    assert count_reconciles(
        inputs={"prompt": case["prompt"], "case_id": case["id"], "case": json.dumps(case)},
        outputs=response,
        expectations=None,
    )


def test_count_reconciliation_accepts_configured_catalog_trusted_assets() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))
    response["source_assets"] = [
        "customer_mip.gold.borrower_360",
        "customer_mip.semantics.lead_generation_metric_view",
    ]

    result = score_count_reconciliation(response, case)

    assert result["passed"] is True


def test_count_reconciliation_rejects_untrusted_source_assets() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))
    response["source_assets"] = ["customer_mip.raw.borrower_360"]

    result = score_count_reconciliation(response, case)

    assert result["passed"] is False
    assert result["checks"]["source_assets_present"] is False


def test_count_reconciliation_requires_trace_and_policy_evidence() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "refi_objective_routes_to_daily_refi"
    )
    response = _response_for(str(case["id"]))
    response["policy_checks"] = []
    response["trace_id"] = ""

    result = score_count_reconciliation(response, case)

    assert result["passed"] is False
    assert result["checks"]["trace_id_present"] is False
    assert result["checks"]["reconciliation_policy_check"] is False


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
            "broad_total": 117404,
            "actionable_total": 5394,
            "destination_total": 5394,
            "actionable_cohort_fingerprint": "d" * 64,
            "destination_cohort_fingerprint": "d" * 64,
            "destination_fingerprint_tool_result_hash": "b" * 64,
            "actionable_snapshot_id": "snapshot-planner-1",
            "destination_snapshot_id": "snapshot-planner-1",
            "source_assets": ["mip.gold.borrower_360", "mip.gold.lead_population"],
            "trace_id": "agent-trace-planner",
            "tool_result_hash": "b" * 64,
            "policy_checks": [
                {"label": "Broad vs actionable reconciliation", "status": "passed"},
            ],
            "criteria": {
                "lead_queue_filters": {
                    "segment_codes": list(
                        workflow.route_filters.get(
                            "segment_codes", workflow.route_filters.get("segment", "")
                        ).split(",")
                    ),
                    "segment_mode": workflow.route_filters.get("segment_mode", "any"),
                }
            },
        }

    summary = score_batch(responses)
    assert summary["passed"] == summary["total"] == 5


def test_agent_endpoint_exposes_source_proof_but_fails_without_destination_proof() -> None:
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
            if response.status_code < 400:
                responses[str(case["id"])]["destination_total"] = responses[str(case["id"])][
                    "actionable_total"
                ]
    finally:
        _clear_overrides()

    summary = score_batch(responses)
    assert summary["passed"] == 1
    successful_results = [
        row
        for row in summary["results"]
        if row["case_id"] != "pii_prompt_is_rejected_before_planning"
    ]
    assert all(
        row["count_reconciliation"]["checks"]["source_cohort_fingerprint_present"] is True
        and row["count_reconciliation"]["checks"]["destination_cohort_fingerprint_present"]
        is False
        for row in successful_results
    )
