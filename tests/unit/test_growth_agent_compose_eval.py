"""Runs the additive Growth Agent plan-composition golden cases offline."""

from __future__ import annotations

from tests.eval.compose_scorers import (
    MIN_COMPOSE_CASES,
    load_compose_cases,
    score_compose_batch,
    score_compose_case,
)


def test_compose_golden_case_file_has_minimum_cases() -> None:
    cases = load_compose_cases()
    assert len(cases) >= MIN_COMPOSE_CASES


def test_all_compose_golden_cases_pass() -> None:
    cases = load_compose_cases()
    summary = score_compose_batch(cases)
    failures = [row for row in summary["results"] if not row["passed"]]
    assert summary["passed"] == summary["total"], failures


def test_injection_case_scrubs_rationale_and_keeps_allowlist() -> None:
    case = next(
        c for c in load_compose_cases() if c["id"] == "compose_injection_rationale_is_scrubbed"
    )
    result = score_compose_case(case)
    assert result["status"] == "composed"
    assert result["checks"]["all_tools_registered"] is True
    assert result["checks"]["no_forbidden_terms"] is True


def test_unregistered_tool_case_is_rejected() -> None:
    case = next(
        c for c in load_compose_cases() if c["id"] == "compose_unregistered_tool_is_rejected"
    )
    result = score_compose_case(case)
    assert result["status"] == "invalid"
    assert result["checks"]["no_plan_on_reject"] is True
