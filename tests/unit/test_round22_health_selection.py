"""Round 22 vocabulary-independent audience-selection governance proof."""

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
from backend.schemas._validators import contains_protected_class_marketing_text
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_UNREVIEWED_SELECTIONS = (
    "Borrowers showing zyrplax status are selected.",
    "Applicants whose moon-bloom result is current are targeted.",
    "Leads carrying nebula flag are prioritized.",
    "Recipients displaying quartz marker were chosen.",
    "Use zyrplax status when choosing borrowers.",
    "Apply lunar phase when ranking prospects.",
    "Consider cobalt index while selecting leads.",
    "Use solar index to select recipients.",
    "Choose leads according to abnormal urinalysis findings for this campaign.",
    "Please go ahead and select borrowers whose dental x-ray findings are abnormal.",
    "Kindly proceed to target prospects based on retinal scan findings.",
    "Move ahead and reserve leads with aurora status for this offer.",
    "Could you please choose leads according to zephyr findings.",
)

_SAFE_SELECTION_CONTROLS = (
    "Borrowers showing high equity are selected for this campaign.",
    "Applicants whose listed properties are active are selected for this offer.",
    "Use high equity when choosing borrowers.",
    "Apply current mortgage rates while ranking leads.",
    "Use high equity to select recipients.",
    "Please go ahead and select borrowers with high equity for this offer.",
    "Kindly proceed to choose leads according to current mortgage rates for this campaign.",
    "Could you please choose leads according to high equity for this offer.",
    "Chart the in-the-money borrowers by state.",
    "Prioritize the next outreach touches by state, segment, and offer lane.",
)


def test_closed_sales_ops_analytic_followup_remains_governance_safe() -> None:
    assert (
        contains_protected_class_marketing_text(
            "Show approved leads that have not been touched in 7 days."
        )
        is False
    )


def _variant(
    *, body: str, subject: str = "Mortgage options review"
) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_growth_dependencies() -> Iterator[tuple[MagicMock, ...]]:
    """Install inert dependencies so invalid requests cannot reach side effects."""

    sql = MagicMock(name="sql_client")
    lakebase = MagicMock(name="lakebase_client")
    audit_store = MagicMock(name="audit_store")
    dependencies = (get_sql_client, get_lakebase_client, get_audit_store)
    previous = {dependency: app.dependency_overrides.get(dependency) for dependency in dependencies}
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


@pytest.mark.parametrize("selection", _UNREVIEWED_SELECTIONS)
def test_unknown_selection_grammar_fails_every_public_copy_boundary(selection: str) -> None:
    assert contains_protected_class_marketing_text(selection) is True

    with pytest.raises(ValidationError, match="protected-class"):
        _variant(body=f"{selection} Contact us to review mortgage options.")
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(
            subject=selection,
            body="Contact us to review mortgage options.",
        )

    approved_copy = f"{selection} Contact us to review mortgage options. {_DISCLOSURE.body}"
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=approved_copy,
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_final_draft_subject(draft_subject=selection, channel="email")


@pytest.mark.parametrize("objective", _UNREVIEWED_SELECTIONS)
def test_unknown_selection_grammar_fails_both_growth_contracts(objective: str) -> None:
    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match="reviewed, non-PII mortgage-growth criteria"):
        ComposePlanRequest(objective=objective, execute=True)


@pytest.mark.parametrize(
    "objective",
    (
        "Borrowers showing a newly invented signal are selected.",
        "Please go ahead and select leads according to an unseen criterion.",
    ),
)
def test_unknown_selection_stops_before_planners_or_writes(
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
    assert objective not in run_response.text
    assert objective not in compose_response.text
    run_planner.assert_not_called()
    compose_planner.assert_not_called()
    for dependency in isolated_growth_dependencies:
        assert dependency.mock_calls == []


@pytest.mark.parametrize("safe_text", _SAFE_SELECTION_CONTROLS)
def test_closed_mortgage_analytics_and_strategy_controls_remain_available(
    safe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False
    assert GrowthAgentPromptRunRequest(prompt=safe_text).prompt == safe_text
    assert ComposePlanRequest(objective=safe_text).objective == safe_text
