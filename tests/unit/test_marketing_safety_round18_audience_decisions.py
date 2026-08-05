"""Round 18 fail-closed audience-decision governance regressions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.main import app
from backend.schemas._validators import contains_protected_class_marketing_text
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.genie_message_policy import protected_prompt_match
from backend.services.lakebase import get_lakebase_client
from backend.services.state_footprint import (
    FootprintState,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
)

_UNSAFE_AUDIENCE_DECISIONS = (
    "This offer gives priority to homeowners carrying zyrplax.",
    "Applicants carrying zyrplax receive preferential consideration.",
    "Select borrowers with high equity. Recipients have zyrplax.",
    (
        "Health status is not used for selection while recipients with zyrplax "
        "receive this offer."
    ),
    "Give priority to recipients carrying zyrplax.",
    "Favor applicants carrying zyrplax.",
    (
        "Health information is excluded from eligibility while recipients with zyrplax "
        "receive priority."
    ),
    "Rank applicants according to whether they carry zyrplax.",
    "Rank applicants by high equity. Everyone in the resulting list has zyrplax.",
    "Elevate applicants carrying zyrplax.",
    "Tier homeowners by whether they carry zyrplax.",
    "Reserve the best treatment for recipients with zyrplax.",
    "Select applicants with high equity. Everyone in the resulting list is marked by zyrplax.",
)

_UNSAFE_SUBJECT_DECISIONS = (
    "This offer gives priority to homeowners carrying zyrplax.",
    "Applicants carrying zyrplax receive preferential consideration.",
    "Give priority to recipients carrying zyrplax.",
    "Favor applicants carrying zyrplax.",
    "Elevate applicants carrying zyrplax.",
    "Tier homeowners by whether they carry zyrplax.",
)

_SAFE_AUDIENCE_DECISIONS = (
    "This offer gives priority to homeowners carrying high equity.",
    "Applicants carrying high equity receive preferential consideration.",
    "Give priority to recipients carrying high equity.",
    "Favor applicants carrying high equity.",
    "Rank applicants according to whether they carry high equity.",
    "Elevate applicants carrying high equity.",
    "Tier homeowners by whether they carry high equity.",
    "Reserve the best treatment for recipients with high equity.",
    "Rank applicants by high equity. Everyone in the resulting list has high equity.",
    "Select borrowers with high equity. Recipients have high equity.",
    "Select borrowers with high equity. Recipients may contact us to review options.",
    "Select borrowers with high equity. Recipients are prepared for human review.",
    "Health status is not used for selection.",
)

_SAFE_REVIEWED_MORTGAGE_OBJECTIVES = (
    "Review residents dealing with high mortgage rates for refinance options.",
    "Review homeowners with current loan balances for refinance options.",
    "Review residents dealing with high mortgage rates and report aggregate trends.",
    "Review homeowners with current loan balances and report aggregate trends.",
)

_SAFE_GENIE_ANALYTICS = (
    "Chart the in-the-money borrowers by state.",
    "Show me the top 10 borrowers by lead score.",
    "Show me the top 10 borrowers by lead score in Illinois.",
    "What is the average listing time on market for listed borrowers by state?",
    "Count listed borrowers by county.",
    "Rank borrowers by opportunity score in IL.",
    "What is the median lead score for refinance-ready borrowers by county?",
)

_UNSAFE_ANALYTIC_TARGETING_NEIGHBORS = (
    "Show borrowers by zyrplax.",
    "Chart borrowers carrying zyrplax by state for outreach.",
    "Show the top 10 borrowers by zyrplax for campaign priority.",
    "Rank applicants according to whether they carry zyrplax.",
    "Rank applicants by whether they carry zyrplax.",
    "Group homeowners with zyrplax by state for offer priority.",
    "Show borrowers by lead score and give them offer priority.",
    "Correlate borrowers by zyrplax for outreach.",
)

_SAFE_REVIEWED_SEGMENT_SIGNAL_OBJECTIVES = (
    "Build a custom cohort for borrowers with refi as well as HELOC signals.",
    "Build a custom cohort for borrowers with refi together with HELOC signals.",
    "Build a custom cohort for borrowers with refi plus HELOC signals.",
    (
        "Build a custom cohort for borrowers with refi or HELOC signals and prepare it "
        "for review."
    ),
    "Build a custom cohort for borrowers with high equity together with retention signals.",
    "Build a custom cohort for borrowers with listed plus investor signals.",
)

_UNSAFE_SEGMENT_SIGNAL_NEIGHBORS = (
    "Build a custom cohort for borrowers with refi as well as zyrplax signals.",
    "Build a custom cohort for borrowers with zyrplax together with HELOC signals.",
    "Build a custom cohort for borrowers with refi plus zyrplax signals.",
    (
        "Build a custom cohort for borrowers with refi or zyrplax signals and prepare it "
        "for review."
    ),
    "Build a custom cohort for borrowers with refi plus HELOC signals for offer priority.",
)


def _variant(*, subject: str = "Mortgage options review", body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="Guidance framing may support a review request.",
    )


def _disclosure() -> MagicMock:
    return MagicMock(
        body=(
            "Summit Mortgage, NMLS #123456. Equal Housing Lender. "
            "Reply unsubscribe to opt out."
        )
    )


@pytest.mark.parametrize("unsafe_text", _UNSAFE_AUDIENCE_DECISIONS)
def test_audience_decisions_fail_every_body_and_objective_boundary(unsafe_text: str) -> None:
    assert contains_protected_class_marketing_text(unsafe_text) is True
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(body=f"{unsafe_text} Contact us to review options.")

    disclosure = _disclosure()
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{unsafe_text} Contact us to review options. {disclosure.body}",
            disclosure=disclosure,
            channel="email",
        )

    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        GrowthAgentPromptRunRequest(prompt=unsafe_text)
    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        ComposePlanRequest(objective=unsafe_text, execute=True)


@pytest.mark.parametrize("unsafe_text", _UNSAFE_SUBJECT_DECISIONS)
def test_audience_decisions_fail_campaign_subject_boundary(unsafe_text: str) -> None:
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(
            subject=f"{unsafe_text} Review options.",
            body="Contact us to review mortgage options.",
        )


@pytest.mark.parametrize("safe_text", _SAFE_AUDIENCE_DECISIONS)
def test_reviewed_decisions_exclusions_and_post_selection_actions_remain_safe(
    safe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False


@pytest.mark.parametrize(
    "safe_text",
    (
        "Applicants carrying high equity receive preferential consideration.",
        "Homeowners carrying high equity receive this offer.",
        "Health status is not used for selection.",
    ),
)
def test_reviewed_borrower_copy_decisions_and_exclusions_remain_safe(safe_text: str) -> None:
    _variant(body=f"{safe_text} Contact us to review mortgage options.")


@pytest.mark.parametrize("safe_text", _SAFE_REVIEWED_MORTGAGE_OBJECTIVES)
def test_reviewed_mortgage_attributes_remain_safe_at_every_boundary(safe_text: str) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False
    assert GrowthAgentPromptRunRequest(prompt=safe_text).prompt == safe_text
    assert ComposePlanRequest(objective=safe_text).objective == safe_text
    _variant(body=f"{safe_text} Contact us to review mortgage options.")

    disclosure = _disclosure()
    approved_body = f"{safe_text} Contact us to review mortgage options. {disclosure.body}"
    assert (
        _assert_disclosure_backed_draft_body(
            draft_body=approved_body,
            disclosure=disclosure,
            channel="email",
        )
        == approved_body
    )


@pytest.mark.parametrize("question", _SAFE_GENIE_ANALYTICS)
def test_read_only_reviewed_analytics_reach_genie(question: str) -> None:
    assert contains_protected_class_marketing_text(question) is False
    assert protected_prompt_match(question) is None


@pytest.mark.parametrize("question", _UNSAFE_ANALYTIC_TARGETING_NEIGHBORS)
def test_analytic_wording_cannot_launder_unreviewed_targeting(question: str) -> None:
    assert contains_protected_class_marketing_text(question) is True
    assert protected_prompt_match(question) == "protected_class_language"


@pytest.mark.parametrize("objective", _SAFE_REVIEWED_SEGMENT_SIGNAL_OBJECTIVES)
def test_reviewed_segment_signal_criteria_reach_the_authoritative_growth_parser(
    objective: str,
) -> None:
    assert contains_protected_class_marketing_text(objective) is False
    assert protected_prompt_match(objective) is None
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective


@pytest.mark.parametrize("objective", _UNSAFE_SEGMENT_SIGNAL_NEIGHBORS)
def test_known_connectors_cannot_launder_an_invented_segment_criterion(
    objective: str,
) -> None:
    assert contains_protected_class_marketing_text(objective) is True
    assert protected_prompt_match(objective) == "protected_class_language"
    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        GrowthAgentPromptRunRequest(prompt=objective)


def test_shared_policy_does_not_resolve_mixed_reviewed_segment_relationships() -> None:
    objective = (
        "Build a custom cohort for borrowers with refi plus HELOC or listed signals."
    )
    assert contains_protected_class_marketing_text(objective) is False
    with pytest.raises(HTTPException, match="one explicit relationship"):
        GrowthAgentPromptRunRequest(prompt=objective)


def test_reviewed_ranked_analytics_preserves_genie_action_affordances() -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: [  # type: ignore[method-assign]
        FootprintState("IL", "Illinois", 1, True),
    ]
    _reset_state_footprint_resolver_for_tests(resolver)
    try:
        response = TestClient(app).post(
            "/api/genie/message",
            json={"question": "Show me the top 10 borrowers by lead score in Illinois."},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _reset_state_footprint_resolver_for_tests()
    body = response.json()
    assert response.status_code == 200, body
    assert body["source"] == "genie", body
    assert body["actions"], body


@pytest.mark.parametrize("unsafe_text", _UNSAFE_AUDIENCE_DECISIONS)
def test_audience_decisions_stop_before_planners_sql_or_storage(
    unsafe_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_planner = MagicMock(name="plan_growth_agent_prompt")
    compose_planner = MagicMock(name="compose_growth_agent_plan")
    sql = MagicMock(name="sql_client")
    lakebase = MagicMock(name="lakebase_client")
    audit_store = MagicMock(name="audit_store")
    monkeypatch.setattr(growth_agent_api, "plan_growth_agent_prompt", run_planner)
    monkeypatch.setattr(
        growth_agent_compose_api,
        "compose_growth_agent_plan",
        compose_planner,
    )
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_audit_store] = lambda: audit_store
    try:
        client = TestClient(app)
        run_response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": unsafe_text, "save_monitor": True, "cadence": "daily"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        compose_response = client.post(
            "/api/growth-agent/agent/compose",
            json={"objective": unsafe_text, "execute": True},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        app.dependency_overrides.pop(get_audit_store, None)

    expected_detail = "prompt must use reviewed, non-PII mortgage-growth criteria"
    assert run_response.status_code == 422, run_response.text
    assert compose_response.status_code == 422, compose_response.text
    assert expected_detail in run_response.text
    assert expected_detail in compose_response.text
    assert unsafe_text not in run_response.text
    assert unsafe_text not in compose_response.text
    run_planner.assert_not_called()
    compose_planner.assert_not_called()
    assert sql.mock_calls == []
    assert lakebase.mock_calls == []
    assert audit_store.mock_calls == []
