"""Round 17 structural protected-selection governance regressions."""

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
from backend.services.lakebase import get_lakebase_client

_UNSAFE_STRUCTURAL_SELECTION_TEXT = (
    "Select homeowners with high equity. Their eligibility hinges on COPD.",
    "Build a refi cohort. Their eligibility hinges on COPD.",
    "Select homeowners with high equity. Their eligibility is contingent on zephyrax.",
    "Select homeowners with high equity. Their eligibility hinges upon quorvax.",
    "Build a HELOC cohort. Their eligibility relies on velnora.",
    "Borrowers were selected for high equity. Each of them carries zephyrax.",
    "Applicants were selected for low LTV. All of them possess velnora.",
    "Borrowers were selected for high equity. Every one of them carries quorvax.",
    "Borrowers were selected for high equity. Each of them lives with zephyrax.",
    "Borrowers were selected for high equity. Each of them is marked by zephyrax.",
    "Borrowers were selected for high equity. They suffer from zephyrax.",
    "Borrowers were selected for high equity. Every one of them takes zephyrax.",
    "Borrowers were selected for high equity. Their qualification turns on zephyrax.",
    "Borrowers were selected for high equity. Eligibility is subject to zephyrax.",
    (
        "Health status indicates homeowners with COPD should receive this offer "
        "but is not used for selection."
    ),
)

_SAFE_REVIEWED_SELECTION_TEXT = (
    "Health status is not used for selection.",
    "Health information is excluded from campaign eligibility.",
    "Select homeowners with high equity. Their eligibility hinges on high equity.",
    "Borrowers were selected for high equity. Each of them carries high equity.",
    "Borrowers were selected for high equity. They may contact us to review options.",
    "Borrowers were selected for high equity. They are prepared for human review.",
    "Borrowers were selected for high equity. Their eligibility does not depend on health status.",
    "Borrowers were selected for high equity. Their qualification turns on high equity.",
    "Borrowers were selected for high equity. Eligibility is subject to high equity.",
)


def _campaign_variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options review",
        body=f"{body} Contact us to review options.",
        hypothesis="Guidance framing may support a review request.",
    )


def _disclosure() -> MagicMock:
    return MagicMock(
        body=(
            "Summit Mortgage, NMLS #123456. Equal Housing Lender. "
            "Reply unsubscribe to opt out."
        )
    )


@pytest.mark.parametrize("unsafe_text", _UNSAFE_STRUCTURAL_SELECTION_TEXT)
def test_structural_selection_is_rejected_at_copy_and_objective_boundaries(
    unsafe_text: str,
) -> None:
    """Every canonical copy/objective boundary shares the same fail-closed policy."""

    assert contains_protected_class_marketing_text(unsafe_text) is True
    with pytest.raises(ValidationError, match="protected-class"):
        _campaign_variant(unsafe_text)

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


@pytest.mark.parametrize("safe_text", _SAFE_REVIEWED_SELECTION_TEXT)
def test_safe_exclusions_and_reviewed_mortgage_criteria_remain_available(
    safe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False
    _campaign_variant(safe_text)
    assert GrowthAgentPromptRunRequest(prompt=safe_text).prompt == safe_text
    assert ComposePlanRequest(objective=safe_text).objective == safe_text


@pytest.mark.parametrize("unsafe_text", _UNSAFE_STRUCTURAL_SELECTION_TEXT)
def test_structural_selection_is_rejected_before_planners_or_storage(
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
            json={
                "prompt": unsafe_text,
                "save_monitor": True,
                "cadence": "daily",
            },
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

    assert run_response.status_code == 422, run_response.text
    assert compose_response.status_code == 422, compose_response.text
    expected_detail = "prompt must use reviewed, non-PII mortgage-growth criteria"
    assert expected_detail in run_response.text
    assert expected_detail in compose_response.text
    assert unsafe_text not in run_response.text
    assert unsafe_text not in compose_response.text
    run_planner.assert_not_called()
    compose_planner.assert_not_called()
    assert sql.mock_calls == []
    assert lakebase.mock_calls == []
    assert audit_store.mock_calls == []
