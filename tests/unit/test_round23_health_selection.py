"""Round 23 structural audience-formation governance proof."""

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
from backend.schemas.marketing_selection_criteria import (
    is_reviewed_campaign_audience_description_text,
    is_reviewed_campaign_audience_summary_text,
)
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import get_audit_store
from backend.services.campaign_intelligence import _OFFER_AUDIENCE
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client
from tests.unit.growth_refusal_contract import (
    GROWTH_REFUSAL_MESSAGE_RE,
    assert_refusal_isolation,
)

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_UNREVIEWED_AUDIENCE_FORMATION = (
    "Screen borrowers displaying quasar-response status for this campaign.",
    "Queue leads according to irregular marrow-scan findings for this offer.",
    "Advance applicants whose prism assay findings are elevated.",
    "Shortlist homeowners displaying a newly invented eligibility signal.",
    "Nominate recipients according to unreviewed biosensor readings.",
    "Enroll customers whose comet-panel result is current.",
    "The scoring workflow screens prospects displaying aurora status.",
    "Operations queued leads according to lunar-panel findings.",
    "The workflow is advancing applicants whose nebula result is abnormal.",
    "Borrowers displaying quartz-marker status are screened.",
    "Homeowners whose orbital assay is current were shortlisted.",
    "Recipients according to an unseen clinical index made the cut.",
    "Applicants displaying cobalt-test findings make the cut.",
    "Borrowers are screened according to hidden assay findings.",
    "Applicants will be nominated based on an unseen marker.",
    "Leads were queued using a secret biosensor index.",
    "The model screens for hidden assay findings among borrowers.",
    "The workflow nominates only hidden-marker borrowers.",
    "Hidden-marker borrowers are queued.",
    # Interrogative framing does not launder a criterion out of the
    # pre-population slot or a trailing co-reference clause.
    "Which zyrplax borrowers are eligible for a HELOC?",
    "Which of the zyrplax borrowers are eligible for a refinance?",
    "Which borrowers are eligible because they carry zyrplax?",
)

_SAFE_GOVERNED_CONTROLS = (
    "Screen borrowers with high equity for this campaign.",
    "Queue leads according to current mortgage rates for this offer.",
    "Advance applicants whose listed properties are active for this offer.",
    "Shortlist high equity homeowners in a reviewed cohort.",
    "Nominate reviewed recipients for this campaign.",
    "Enroll eligible customers for this offer.",
    "Borrowers displaying high equity make the cut.",
    "Leads according to current mortgage rates are queued.",
    "Borrowers are screened according to current mortgage rates.",
    "Applicants will be nominated based on high equity.",
    "Leads were queued using current LTV.",
    "Operations queue high equity borrowers.",
    "Chart the in-the-money borrowers by state.",
    "Prioritize the next outreach touches by state, segment, and offer lane.",
    "Create a refi campaign for a borrower whose email opt-out is on file and instead call them.",
)

_SAFE_NONSELECTION_BUSINESS_TEXT = (
    "Display portfolio totals according to state.",
    "The screen displays current filter values.",
    "The queue report lists volume by state.",
    "Applicants displaying documents in the portal can contact us.",
    "Advance quarterly business planning according to reviewed targets.",
    "Enrollment operations report aggregate counts by state.",
)

_SAFE_POLICY_ONLY_CONTROLS = (
    "High equity borrowers are queued.",
    "Reviewed borrowers are queued.",
    # Interrogative determiners are audience-state questions, not criteria
    # (live turn 2026-08-07: protected_prompt_match false positive).
    "Which borrowers are eligible for a HELOC?",
    "Which borrowers are eligible for a refinance?",
    "Which of the borrowers are eligible for a HELOC?",
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
    """Install inert dependencies so validation precedes planners and writes."""

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


@pytest.mark.parametrize("selection", _UNREVIEWED_AUDIENCE_FORMATION)
def test_formation_morphology_fails_every_public_copy_boundary(selection: str) -> None:
    assert contains_protected_class_marketing_text(selection) is True

    with pytest.raises(ValidationError, match="protected-class"):
        _variant(body=f"{selection} Contact us to review mortgage options.")
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(subject=selection, body="Contact us to review mortgage options.")

    final_body = f"{selection} Contact us to review mortgage options. {_DISCLOSURE.body}"
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=final_body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_final_draft_subject(draft_subject=selection, channel="email")


@pytest.mark.parametrize("objective", _UNREVIEWED_AUDIENCE_FORMATION)
def test_formation_morphology_fails_both_growth_contracts(objective: str) -> None:
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=objective, execute=True)


@pytest.mark.parametrize(
    "objective",
    (
        "The model nominates borrowers displaying a never-before-seen flag.",
        "Leads whose invented assay is current make the cut.",
    ),
)
def test_unseen_formation_stops_before_planners_models_or_audit_writes(
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
    # The refusal is recorded; SQL and Lakebase stay untouched.
    assert_refusal_isolation(isolated_growth_dependencies)


@pytest.mark.parametrize("safe_text", _SAFE_GOVERNED_CONTROLS)
def test_closed_mortgage_analytics_and_strategy_controls_remain_available(
    safe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False
    assert GrowthAgentPromptRunRequest(prompt=safe_text).prompt == safe_text
    assert ComposePlanRequest(objective=safe_text).objective == safe_text


@pytest.mark.parametrize("safe_text", _SAFE_NONSELECTION_BUSINESS_TEXT)
def test_nonselection_business_wording_is_not_treated_as_audience_formation(
    safe_text: str,
) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False


def test_closed_sales_ops_read_only_followup_remains_available() -> None:
    assert (
        contains_protected_class_marketing_text(
            "Show approved leads that have not been touched in 7 days."
        )
        is False
    )


@pytest.mark.parametrize(
    ("offer_code", "audience_description"),
    tuple(_OFFER_AUDIENCE.items()),
)
def test_every_server_owned_offer_audience_summary_remains_public_safe(
    offer_code: str,
    audience_description: str,
) -> None:
    summary = (
        f"The selected audience is led by {audience_description} "
        "and is ready for a controlled message test."
    )

    assert offer_code
    assert is_reviewed_campaign_audience_description_text(audience_description) is True
    assert contains_protected_class_marketing_text(audience_description) is False
    assert is_reviewed_campaign_audience_summary_text(summary) is True
    assert contains_protected_class_marketing_text(summary) is False


@pytest.mark.parametrize("safe_text", _SAFE_POLICY_ONLY_CONTROLS)
def test_passive_closed_audience_controls_remain_available(safe_text: str) -> None:
    assert contains_protected_class_marketing_text(safe_text) is False
