"""Pure scorers for Growth Agent plan-composition golden cases.

These mirror ``tests/eval/scorers.py`` but score the *composition* contract:
a model-proposed plan JSON, once validated against the governed tool registry,
must (a) reach the expected honest status, (b) name only registered tools, and
(c) carry only scrubbed, non-PII rationale/summary text. They are dependency-
light so CI can verify the contract without a live Supervisor endpoint. The
deploy gate's five ``golden_agent_cases.jsonl`` cases are untouched; these are an
additive, offline compose suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.schemas.agent_plan import ComposePlanRequest
from backend.services.agent_tools import registered_agent_tool_names
from backend.services.growth_agent_composer import build_validated_plan

CASE_PATH = Path(__file__).with_name("golden_compose_cases.jsonl")
MIN_COMPOSE_CASES = 2


def load_compose_cases(path: Path = CASE_PATH) -> list[dict[str, Any]]:
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


def _plan_text_blobs(plan: Any) -> list[str]:
    blobs = [plan.objective_summary, plan.expected_outcome, plan.risk_notes]
    blobs.extend(step.rationale for step in plan.steps)
    return [str(b) for b in blobs]


def score_compose_case(case: dict[str, Any]) -> dict[str, Any]:
    """Validate one case's model plan and score it against expectations."""

    # The objective itself must survive the shared PII/injection gate.
    ComposePlanRequest(objective=str(case["objective"]))
    request = ComposePlanRequest(objective=str(case["objective"]))
    outcome = build_validated_plan(dict(case["model_plan"]), request, endpoint="eval-endpoint")

    expected_status = str(case.get("expected_status") or "composed")
    checks: dict[str, bool] = {"status_matches": outcome.status == expected_status}

    known = registered_agent_tool_names()
    if outcome.status == "composed" and outcome.plan is not None:
        tools = [step.tool for step in outcome.plan.steps]
        checks["all_tools_registered"] = all(tool in known for tool in tools)
        expected_tools = case.get("expected_tools")
        if isinstance(expected_tools, list) and expected_tools:
            checks["expected_tools_match"] = tools == list(expected_tools)
        expected_approval = case.get("expected_requires_approval")
        if isinstance(expected_approval, bool):
            checks["requires_approval_matches"] = (
                outcome.plan.requires_approval == expected_approval
            )
        forbidden = [str(term).lower() for term in case.get("forbidden_terms") or []]
        blob = " ".join(_plan_text_blobs(outcome.plan)).lower()
        checks["no_forbidden_terms"] = all(term not in blob for term in forbidden)
    else:
        # Rejected plans expose no plan surface at all.
        checks["no_plan_on_reject"] = outcome.plan is None

    return {"id": case["id"], "passed": all(checks.values()), "checks": checks, "status": outcome.status}


def score_compose_batch(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = [score_compose_case(case) for case in cases]
    passed = sum(1 for row in results if row["passed"])
    return {
        "passed": passed,
        "total": len(results),
        "score": passed / len(results) if results else 0.0,
        "results": results,
    }


__all__ = [
    "MIN_COMPOSE_CASES",
    "load_compose_cases",
    "score_compose_batch",
    "score_compose_case",
]
