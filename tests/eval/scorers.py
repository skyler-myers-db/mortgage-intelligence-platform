"""Pure scorers for Mortgage Growth Agent golden eval cases.

These scorers are intentionally dependency-light so CI can verify the contract
without a live MLflow workspace. Live MLflow Agent Evaluation imports the same
functions and wraps them in ``mlflow.genai.scorers.scorer`` so the live run and
fast tests score the same behavioral contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CASE_PATH = Path(__file__).with_name("golden_agent_cases.jsonl")
MIN_GOLDEN_CASES = 5
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UC_THREE_PART = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$"
)
_TRUSTED_ASSET_SUFFIXES = (
    ".gold.lead_population",
    ".gold.segment_population",
    ".gold.lead_scores",
    ".gold.borrower_360",
    ".gold.borrower_dossier",
    ".gold.evidence_events",
    ".gold.source_readiness",
    ".gold.lockin_cohort",
    ".gold.funnel_snapshot_daily",
    ".gold.county_rollup",
    ".gold.zip_rollup",
    ".semantics.lead_generation_metric_view",
    ".semantics.segment_performance_metric_view",
    ".semantics.borrower_opportunity_metric_view",
)


def load_cases(path: Path = CASE_PATH) -> list[dict[str, Any]]:
    """Load JSONL golden cases with stable ids."""

    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            case = json.loads(text)
            if not isinstance(case, dict) or not case.get("id"):
                raise ValueError(f"{path}:{line_number} missing case id")
            cases.append(case)
    return cases


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _policy_check_passed(response: dict[str, Any], label: str) -> bool:
    checks = response.get("policy_checks")
    if not isinstance(checks, list):
        return False
    for item in checks:
        if not isinstance(item, dict):
            continue
        item_label = str(item.get("label") or "").strip().lower()
        item_status = str(item.get("status") or "").strip().lower()
        if item_label == label.lower() and item_status == "passed":
            return True
    return False


def _trusted_source_asset(value: Any) -> bool:
    asset = str(value or "").strip()
    return bool(_UC_THREE_PART.fullmatch(asset)) and asset.endswith(_TRUSTED_ASSET_SUFFIXES)


def score_count_reconciliation(response: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Score the broad-vs-actionable count contract for a Growth Agent answer.

    Equal counts are necessary but insufficient. A passing answer must also
    prove that the source/action cohort and the complete Lead Queue destination
    contain the same borrower IDs under one source snapshot, with the
    destination fingerprint bound to the answer's tool-result hash.
    """

    if case.get("expected_error"):
        return {
            "passed": True,
            "checks": {"not_applicable_expected_error": True},
        }

    broad_total = _number(response.get("broad_total"))
    actionable_total = _number(response.get("actionable_total"))
    destination_total = _number(response.get("destination_total"))
    route = str(response.get("route") or "")
    trace_id = str(response.get("trace_id") or "")
    tool_hash = str(response.get("tool_result_hash") or "")
    source_fingerprint = str(
        response.get("actionable_cohort_fingerprint")
        or response.get("source_cohort_fingerprint")
        or ""
    )
    destination_fingerprint = str(response.get("destination_cohort_fingerprint") or "")
    fingerprint_tool_hash = str(response.get("destination_fingerprint_tool_result_hash") or "")
    source_snapshot = str(
        response.get("actionable_snapshot_id") or response.get("source_snapshot_id") or ""
    )
    destination_snapshot = str(response.get("destination_snapshot_id") or "")
    source_assets = response.get("source_assets")
    checks = {
        "broad_total_present": broad_total is not None,
        "actionable_total_present": actionable_total is not None,
        "destination_total_present": destination_total is not None,
        "counts_ordered": (
            broad_total is not None
            and actionable_total is not None
            and broad_total >= actionable_total >= 0
        ),
        "destination_matches_actionable": (
            destination_total is not None
            and actionable_total is not None
            and destination_total == actionable_total
        ),
        "source_cohort_fingerprint_present": bool(_HEX64.fullmatch(source_fingerprint)),
        "destination_cohort_fingerprint_present": bool(_HEX64.fullmatch(destination_fingerprint)),
        "cohort_identity_matches": (
            bool(source_fingerprint)
            and bool(destination_fingerprint)
            and source_fingerprint == destination_fingerprint
        ),
        "destination_fingerprint_bound_to_tool_result": (
            bool(_HEX64.fullmatch(tool_hash)) and fingerprint_tool_hash == tool_hash
        ),
        "common_snapshot": (
            bool(source_snapshot)
            and bool(destination_snapshot)
            and source_snapshot == destination_snapshot
        ),
        "destination_identity_complete": not bool(response.get("destination_identity_error")),
        "eligible_handoff": "marketing_eligibility=Eligible+only" in route,
        "trace_id_present": trace_id.startswith("agent-trace-"),
        "tool_result_hash_present": bool(_HEX64.fullmatch(tool_hash)),
        "source_assets_present": (
            isinstance(source_assets, list)
            and bool(source_assets)
            and all(_trusted_source_asset(item) for item in source_assets)
        ),
        "reconciliation_policy_check": _policy_check_passed(
            response,
            "Broad vs actionable reconciliation",
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "facts": {
            "broad_total": broad_total,
            "actionable_total": actionable_total,
            "destination_total": destination_total,
            "source_cohort_fingerprint": source_fingerprint,
            "destination_cohort_fingerprint": destination_fingerprint,
            "source_snapshot_id": source_snapshot,
            "destination_snapshot_id": destination_snapshot,
            "route": route,
            "trace_id": trace_id,
        },
    }


def count_reconciles(
    *,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    expectations: dict[str, Any] | None = None,
    trace: Any | None = None,
) -> bool:
    """MLflow-compatible custom scorer for reconciled Growth Agent counts."""

    _ = trace
    response = outputs if isinstance(outputs, dict) else {}
    case = expectations if isinstance(expectations, dict) else {}
    if not case and isinstance(inputs, dict):
        input_case = inputs.get("case")
        if isinstance(input_case, dict):
            case = input_case
        elif isinstance(input_case, str):
            try:
                parsed_case = json.loads(input_case)
            except json.JSONDecodeError:
                parsed_case = {}
            if isinstance(parsed_case, dict):
                case = parsed_case
    return bool(score_count_reconciliation(response, case)["passed"])


def score_growth_agent_response(response: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Score one Growth Agent response against a golden case.

    The score is binary by dimension and intentionally strict:
    workflow routing, segment semantics, route handoff, and PII-safe text must
    all pass for an eval case to be considered successful.
    """

    workflow_value = response.get("workflow")
    workflow: dict[str, Any] = workflow_value if isinstance(workflow_value, dict) else {}
    criteria_value = response.get("criteria")
    criteria: dict[str, Any] = criteria_value if isinstance(criteria_value, dict) else {}
    filters_value = criteria.get("lead_queue_filters")
    filters = filters_value if isinstance(filters_value, dict) else {}
    route = str(response.get("route") or "")
    serialized = json.dumps(response, sort_keys=True, default=str).lower()
    expected_error = case.get("expected_error")
    if expected_error:
        error_text = str(response.get("error") or response.get("detail") or "")
        checks = {
            "expected_error": str(expected_error) in error_text,
            "no_success_workflow": not workflow.get("id"),
            "no_forbidden_terms": not any(
                str(term).lower() in serialized for term in case.get("forbidden_terms", [])
            ),
        }
        passed = all(checks.values())
        return {
            "case_id": case.get("id"),
            "passed": passed,
            "score": 1.0 if passed else 0.0,
            "checks": checks,
        }

    expected_segments = [str(item) for item in case.get("expected_segment_codes", [])]
    actual_segments = [str(item) for item in filters.get("segment_codes", [])]
    expected_route_parts = [str(item) for item in case.get("expected_route_contains", [])]
    forbidden_terms = [str(item).lower() for item in case.get("forbidden_terms", [])]

    count_score = score_count_reconciliation(response, case)
    checks = {
        "workflow_id": workflow.get("id") == case.get("expected_workflow_id"),
        "segment_codes": actual_segments == expected_segments,
        "segment_mode": filters.get("segment_mode") == case.get("expected_segment_mode"),
        "route_handoff": all(part in route for part in expected_route_parts),
        "no_forbidden_terms": not any(term in serialized for term in forbidden_terms),
        "no_outbound_activation": response.get("execution_mode") != "outbound_activation",
        "count_reconciles": count_score["passed"],
    }
    passed = all(checks.values())
    return {
        "case_id": case.get("id"),
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "checks": checks,
        "count_reconciliation": count_score,
    }


def score_batch(
    responses_by_case_id: dict[str, dict[str, Any]], cases: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Score multiple responses and return a compact eval summary."""

    loaded_cases = cases if cases is not None else load_cases()
    if len(loaded_cases) < MIN_GOLDEN_CASES:
        raise ValueError(
            f"Growth Agent golden eval requires at least {MIN_GOLDEN_CASES} cases; "
            f"got {len(loaded_cases)}."
        )
    results = [
        score_growth_agent_response(responses_by_case_id.get(str(case["id"]), {}), case)
        for case in loaded_cases
    ]
    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    return {
        "passed": passed,
        "total": total,
        "score": (passed / total) if total else 0.0,
        "results": results,
    }
