"""Pure scorers for Mortgage Growth Agent golden eval cases.

These scorers are intentionally dependency-light so CI can verify the contract
without a live MLflow workspace. Live MLflow Agent Evaluation can import the
same functions and attach the returned facts to an experiment run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CASE_PATH = Path(__file__).with_name("golden_agent_cases.jsonl")


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


def score_growth_agent_response(response: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Score one Growth Agent response against a golden case.

    The score is binary by dimension and intentionally strict:
    workflow routing, segment semantics, route handoff, and PII-safe text must
    all pass for an eval case to be considered successful.
    """

    workflow = response.get("workflow") if isinstance(response.get("workflow"), dict) else {}
    criteria = response.get("criteria") if isinstance(response.get("criteria"), dict) else {}
    filters = criteria.get("lead_queue_filters") if isinstance(criteria.get("lead_queue_filters"), dict) else {}
    route = str(response.get("route") or "")
    serialized = json.dumps(response, sort_keys=True, default=str).lower()
    expected_error = case.get("expected_error")
    if expected_error:
        error_text = str(response.get("error") or response.get("detail") or "")
        checks = {
            "expected_error": str(expected_error) in error_text,
            "no_success_workflow": not workflow.get("id"),
            "no_forbidden_terms": not any(
                str(term).lower() in serialized
                for term in case.get("forbidden_terms", [])
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

    checks = {
        "workflow_id": workflow.get("id") == case.get("expected_workflow_id"),
        "segment_codes": actual_segments == expected_segments,
        "segment_mode": filters.get("segment_mode") == case.get("expected_segment_mode"),
        "route_handoff": all(part in route for part in expected_route_parts),
        "no_forbidden_terms": not any(term in serialized for term in forbidden_terms),
        "no_outbound_activation": response.get("execution_mode") != "outbound_activation",
    }
    passed = all(checks.values())
    return {
        "case_id": case.get("id"),
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "checks": checks,
    }


def score_batch(responses_by_case_id: dict[str, dict[str, Any]], cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Score multiple responses and return a compact eval summary."""

    loaded_cases = cases if cases is not None else load_cases()
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
