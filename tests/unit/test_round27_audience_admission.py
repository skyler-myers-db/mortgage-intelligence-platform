"""Round 27 structural proof for audience-admission relation ordering."""

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
from backend.schemas.marketing_audience_admission import audience_admission_criterion
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
    get_audit_store,
)
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client
from tests.unit.growth_refusal_contract import (
    GROWTH_REFUSAL_MESSAGE_RE,
    assert_refusal_isolation,
)

_DISCLOSURE = MagicMock(
    body=("Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out.")
)

# Mutate the predicate inside each independently proved relation. Admission is
# determined by population/destination order and the conditional connector,
# never by membership in a closed action-synonym list.
_UNSAFE_ADMISSIONS = (
    "Schedule borrowers for the campaign when scleroderma is present.",
    "Channel borrowers for the campaign when scleroderma is present.",
    "Bundle borrowers for the campaign when scleroderma is present.",
    "Borrowers are scheduled for the campaign when scleroderma is present.",
    "Borrowers have been channeled for the campaign when scleroderma is present.",
    "Borrowers will be bundled for the campaign when scleroderma is present.",
    "Borrowers enter the campaign when scleroderma is present.",
    "Borrowers flow into the campaign when scleroderma is present.",
    "Borrowers join the campaign when scleroderma is present.",
    "Borrowers gain campaign membership when scleroderma is present.",
    "Borrowers obtain campaign membership when scleroderma is present.",
    "Borrowers secure campaign membership when scleroderma is present.",
    "The campaign receives borrowers when scleroderma is present.",
    "The campaign admits borrowers when scleroderma is present.",
    "The campaign takes borrowers when scleroderma is present.",
    "The campaign is populated with borrowers when scleroderma is present.",
    "Borrowers become campaign members when scleroderma is present.",
    "Borrowers end up in the campaign when scleroderma is present.",
    "Borrowers wind up in the campaign when scleroderma is present.",
    "Borrowers make it into the campaign when scleroderma is present.",
    "Borrowers find themselves in the campaign when scleroderma is present.",
    "Borrowers come to rest in the campaign when scleroderma is present.",
    "Borrowers make their way into the campaign when scleroderma is present.",
    "Borrowers become part of the campaign when scleroderma is present.",
    "The campaign is made up of borrowers when scleroderma is present.",
    "Borrowers flow into this campaign provided scleroderma is present.",
    "Borrowers' placement in the campaign based on scleroderma is present.",
)

_SAFE_ADMISSIONS = (
    "Schedule borrowers for the campaign when current LTV is present.",
    "Channel borrowers for the campaign when high equity is present.",
    "Bundle borrowers for the campaign when current LTV is documented.",
    "Borrowers are scheduled for the campaign when high equity is present.",
    "Borrowers have been channeled for the campaign when current LTV is present.",
    "Borrowers will be bundled for the campaign when high equity is documented.",
    "Borrowers enter the campaign when current LTV is present.",
    "Borrowers flow into the campaign when high equity is present.",
    "Borrowers join the campaign when current LTV is documented.",
    "Borrowers gain campaign membership when high equity is present.",
    "Borrowers obtain campaign membership when current LTV is present.",
    "Borrowers secure campaign membership when high equity is documented.",
    "The campaign receives applicants when current LTV is present.",
    "The campaign admits homeowners when high equity is present.",
    "The campaign takes applicants when current LTV is documented.",
    "The campaign is populated with applicants when high equity is documented.",
    "Borrowers become campaign members when current LTV is present.",
    "Borrowers end up in the campaign when high equity is present.",
    "Borrowers wind up in the campaign when current LTV is documented.",
    "Borrowers make it into the campaign when high equity is documented.",
    "Borrowers find themselves in the campaign when high equity is present.",
    "Borrowers come to rest in the campaign when current LTV is documented.",
    "Borrowers make their way into the campaign when high equity is documented.",
    "Borrowers become part of the campaign when current LTV is documented.",
    "The campaign is made up of borrowers when high equity is present.",
    "Borrowers flow into this campaign provided current LTV is documented.",
    "Borrowers' placement in the campaign based on high equity.",
)

_ENDPOINT_OBJECTIVES = (
    _UNSAFE_ADMISSIONS[0],
    _UNSAFE_ADMISSIONS[6],
    _UNSAFE_ADMISSIONS[9],
    _UNSAFE_ADMISSIONS[12],
)


def _variant(*, subject: str, body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_growth_dependencies() -> Iterator[tuple[MagicMock, ...]]:
    """Install inert dependencies so request rejection precedes side effects."""

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


@pytest.mark.parametrize("copy", _UNSAFE_ADMISSIONS)
def test_relation_order_mutations_reject_every_shared_boundary(copy: str) -> None:
    clause = copy.removesuffix(".")
    assert audience_admission_criterion(clause) == "scleroderma is present"
    assert contains_protected_class_marketing_text(copy)

    with pytest.raises(ValidationError, match="protected-class"):
        _variant(
            subject="Mortgage options",
            body=f"{copy} Contact us to review mortgage options.",
        )
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(
            subject=copy,
            body="Contact us to review mortgage options.",
        )
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=copy)
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{copy} Contact us to review options. {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_final_draft_subject(draft_subject=copy, channel="email")
    for field in ("draft_body", "draft_subject"):
        with pytest.raises(AuditMetadataValueViolation, match="protected-class"):
            build_safe_audit_metadata({field: copy}, action="outreach.approve")


@pytest.mark.parametrize("copy", _SAFE_ADMISSIONS)
def test_closed_mortgage_counterparts_pass_every_shared_boundary(copy: str) -> None:
    criterion = audience_admission_criterion(copy.removesuffix("."))
    assert criterion is not None
    assert not contains_protected_class_marketing_text(copy)

    variant = _variant(
        subject=copy,
        body=f"{copy} Contact us to review mortgage options.",
    )
    assert variant.subject == copy
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy

    body = f"{copy} Contact us to review options. {_DISCLOSURE.body}"
    assert (
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
        == body
    )
    assert _assert_final_draft_subject(draft_subject=copy, channel="email") == copy
    for field in ("draft_body", "draft_subject"):
        assert build_safe_audit_metadata({field: copy}, action="outreach.approve")[field] == copy


@pytest.mark.parametrize("objective", _ENDPOINT_OBJECTIVES)
def test_each_new_relation_order_stops_before_planners_and_writes(
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


@pytest.mark.parametrize(
    "copy",
    (
        "How has the in-the-money population moved recently?",
        "Show HELOC candidates with recent permits and strong equity.",
        "Review priority distribution and assign the next owner.",
    ),
)
def test_analytic_source_gap_and_internal_owner_controls_remain_safe(copy: str) -> None:
    assert not contains_protected_class_marketing_text(copy)


@pytest.mark.parametrize(
    "copy",
    (
        "How has the in-the-money population moved recently?",
        "Review priority distribution and assign the next owner.",
    ),
)
def test_reviewed_analytic_and_internal_owner_controls_reach_growth_schemas(copy: str) -> None:
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy
