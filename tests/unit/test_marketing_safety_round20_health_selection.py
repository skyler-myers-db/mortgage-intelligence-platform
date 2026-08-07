"""Round 20 protected-health selection governance proof."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.api.outreach import (
    _assert_disclosure_backed_draft_body,
    _assert_final_draft_subject,
)
from backend.main import app
from backend.schemas._validators_protected_class import contains_protected_class_marketing_text
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.genie_message_policy import protected_prompt_match
from backend.services.lakebase import get_lakebase_client

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_PROTECTED_HEALTH_SELECTIONS = (
    (
        "Choose borrowers whose prescriptions are active for this offer. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Rank applicants according to elevated blood glucose readings. "
        "Build a custom cohort for HELOC signals."
    ),
    "Select recipients whose hospice enrollment is current for this offer.",
    "Choose customers whose organ transplant history is documented for this campaign.",
    "Choose borrowers whose laboratory results are abnormal for this offer.",
    "Rank applicants according to cholesterol readings for this campaign.",
    "Select recipients whose hospitalization history is documented for this offer.",
    "Reserve borrowers whose tumor marker levels are elevated for this offer.",
    (
        "Select applicants whose mammogram result is recent for this campaign. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Choose borrowers based on whether they have a pacemaker for this offer. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Order homeowners according to oncology visit frequency for this campaign. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Rank recipients by CPAP use for this offer. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Target customers whose genetic test results are documented for this campaign. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Prioritize applicants based on vaccination status for this offer. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Reserve homeowners according to ER visit history for this campaign. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Favor borrowers whose blood type is O negative for this offer. "
        "Build a custom cohort for refi signals."
    ),
    (
        "Prioritize outreach touches by blood type, segment, and offer lane. "
        "Build a custom cohort for refi signals."
    ),
)

_PROTECTED_HEALTH_FAMILY_NEIGHBORS = (
    "Prioritize homeowners whose prescriptions remain current for this campaign.",
    "Order recipients based on abnormal A1C results for this offer.",
    "Reserve customers whose palliative care status is active for this campaign.",
    "Rank borrowers according to documented kidney transplant history for this offer.",
    "Order homeowners based on lab values for this campaign.",
    "Prioritize applicants whose cholesterol levels are high for this offer.",
    "Choose recipients whose inpatient status is current for this campaign.",
    "Rank customers according to tumor marker results for this offer.",
    "Target customers based on their medical test results for this campaign.",
)

_SAFE_GROWTH_CONTROLS = (
    "Choose borrowers with high equity for this offer.",
    "Rank applicants according to current mortgage rates for this campaign.",
    "Select recipients whose listed properties are active for this offer.",
    "Include high equity borrowers in a reviewed cohort.",
    "Prioritize the next outreach touches by state, segment, and offer lane.",
    "Health information is excluded from campaign selection.",
    "Organ transplant history is documented for compliance and not used for targeting.",
)

_SAFE_NONSELECTION_DOCUMENTATION = (
    "Document aggregate hospice enrollment counts for governance review.",
    "Blood glucose readings are retained only for compliance documentation.",
    "Prescription data is excluded from campaign selection.",
    "Laboratory results are retained only for compliance documentation.",
    "Document aggregate cholesterol readings for compliance review.",
    "Hospitalization history is documented for compliance and not used for targeting.",
    "Tumor marker levels are excluded from campaign selection.",
    "A standing email opt-out remains active. Instead, call us to review options.",
    "Audit phone suppression records and report aggregate counts.",
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options review",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_growth_dependencies() -> Iterator[tuple[MagicMock, ...]]:
    """Install inert dependencies so rejected objectives cannot reach side effects."""

    sql = MagicMock(name="sql_client")
    lakebase = MagicMock(name="lakebase_client")
    audit_store = MagicMock(name="audit_store")
    previous = {
        dependency: app.dependency_overrides.get(dependency)
        for dependency in (get_sql_client, get_lakebase_client, get_audit_store)
    }
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_audit_store] = lambda: audit_store
    try:
        yield sql, lakebase, audit_store
    finally:
        for dependency, override in previous.items():
            if override is None:
                app.dependency_overrides.pop(dependency, None)
            else:
                app.dependency_overrides[dependency] = override


@pytest.mark.parametrize("unsafe_text", _PROTECTED_HEALTH_SELECTIONS)
def test_protected_health_selection_rejects_public_campaign_and_final_copy(
    unsafe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(unsafe_text) is True
    assert protected_prompt_match(unsafe_text) == "protected_class_language"

    body = f"{unsafe_text} Contact us to review mortgage options."
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(body)
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_final_draft_subject(draft_subject=unsafe_text, channel="email")


@pytest.mark.parametrize("objective", _PROTECTED_HEALTH_SELECTIONS)
def test_protected_health_selection_rejects_both_growth_request_contracts(
    objective: str,
) -> None:
    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        ComposePlanRequest(objective=objective, execute=True)


@pytest.mark.parametrize("objective", _PROTECTED_HEALTH_SELECTIONS)
def test_protected_health_selection_stops_before_planners_models_or_writes(
    objective: str,
    isolated_growth_dependencies: tuple[MagicMock, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_planner = MagicMock(name="plan_growth_agent_prompt")
    compose_planner = MagicMock(name="compose_growth_agent_plan")
    monkeypatch.setattr(growth_agent_api, "plan_growth_agent_prompt", run_planner)
    monkeypatch.setattr(
        growth_agent_compose_api,
        "compose_growth_agent_plan",
        compose_planner,
    )

    client = TestClient(app)
    run_response = client.post(
        "/api/growth-agent/agent/run",
        json={"prompt": objective, "save_monitor": True, "cadence": "daily"},
        headers={"X-Forwarded-Email": "operator@example.com"},
    )
    compose_response = client.post(
        "/api/growth-agent/agent/compose",
        json={"objective": objective, "execute": True},
        headers={"X-Forwarded-Email": "operator@example.com"},
    )

    assert run_response.status_code == 422, run_response.text
    assert compose_response.status_code == 422, compose_response.text
    assert "reviewed, non-PII mortgage-growth criteria" in run_response.text
    assert "reviewed, non-PII mortgage-growth criteria" in compose_response.text
    assert objective not in run_response.text
    assert objective not in compose_response.text
    run_planner.assert_not_called()
    compose_planner.assert_not_called()
    for dependency in isolated_growth_dependencies:
        assert dependency.mock_calls == []


@pytest.mark.parametrize("unsafe_text", _PROTECTED_HEALTH_FAMILY_NEIGHBORS)
def test_protected_health_semantic_families_do_not_depend_on_exact_reproductions(
    unsafe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(unsafe_text) is True
    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        GrowthAgentPromptRunRequest(prompt=unsafe_text)


@pytest.mark.parametrize("safe_text", _SAFE_GROWTH_CONTROLS)
def test_reviewed_mortgage_and_explicit_exclusion_controls_remain_available(
    safe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False
    assert GrowthAgentPromptRunRequest(prompt=safe_text).prompt == safe_text
    assert ComposePlanRequest(objective=safe_text).objective == safe_text


@pytest.mark.parametrize("safe_text", _SAFE_NONSELECTION_DOCUMENTATION)
def test_nonselection_health_documentation_remains_public_safe(safe_text: str) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False
