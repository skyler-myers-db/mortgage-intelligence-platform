"""Round 27 structural consent-withdrawal and dead-response proof."""

from collections.abc import Iterator
from types import SimpleNamespace
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
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_cta_evidence import (
    contains_borrower_cta_contradiction,
    negative_borrower_cta_evidence,
)
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
    get_audit_store,
)
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client

_DISCLOSURE = SimpleNamespace(
    body=("Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out.")
)

_WITHDRAWAL_FACT_AND_CTA = (
    ("The borrower said no to future calls.", "Call us to review options."),
    ("The customer stated no more emails.", "Email us to review options."),
    ("The recipient declared no additional texts.", "Text us to review options."),
    ("The applicant no longer permits calls.", "Call us to review options."),
    ("The customer no longer allows email.", "Email us to review options."),
    ("The homeowner no longer authorizes text messages.", "Text us to review options."),
    ("The borrower has stopped allowing calls.", "Call us to review options."),
    ("The client stopped permitting email.", "Email us to review options."),
    ("The prospect had stopped authorizing texts.", "Text us to review options."),
    ("The borrower permits no more calls.", "Call us to review options."),
    ("The customer allows no further email.", "Email us to review options."),
    ("The recipient authorizes no additional texts.", "Text us to review options."),
    ("The borrower discontinued future calls.", "Call us to review options."),
    ("The customer abandons additional emails.", "Email us to review options."),
    ("The client relinquished consent for future calls.", "Call us to review options."),
    ("The applicant disclaims permission for email.", "Email us to review options."),
    ("The homeowner gave up authorization for text messages.", "Text us to review options."),
    ("The prospect discontinued consent.", "Call us to review options."),
    ("The borrower abandoned permission.", "Contact us to review options."),
    ("Future calls were discontinued by the borrower.", "Call us to review options."),
    ("Future emails were abandoned by the customer.", "Email us to review options."),
    ("Consent for future calls was relinquished by the client.", "Call us to review options."),
    ("Permission for email was disclaimed by the applicant.", "Email us to review options."),
    ("Authorization for texts was given up by the homeowner.", "Text us to review options."),
    ("The borrower does not consent to future calls.", "Call us to review options."),
    ("The customer revoked our ability to email.", "Email us to review options."),
    ("The borrower stopped giving permission for calls.", "Call us to review options."),
    ("The customer will permit no more email.", "Email us to review options."),
    ("The borrower has given up consent to calls.", "Call us to review options."),
)

_DEAD_RESPONSE_FACT_AND_CTA = (
    ("Replies fall through the cracks.", "Reply YES to review options."),
    ("Messages fell through cracks.", "Reply YES to review options."),
    ("Nobody responds to replies.", "Reply YES to review options."),
    ("No one responds to messages.", "Reply YES to review options."),
    ("No staff monitors replies.", "Reply YES to review options."),
    ("Replies are disregarded.", "Reply YES to review options."),
    ("Responses receive no reply.", "Reply YES to review options."),
    ("Emails received zero staff response.", "Email us to review options."),
    ("Replies are overlooked.", "Reply YES to review options."),
    ("Messages were buried.", "Reply YES to review options."),
    ("Replies await review indefinitely.", "Reply YES to review options."),
    ("Messages are awaiting staff review indefinitely.", "Reply YES to review options."),
    ("Replies never reach staff.", "Reply YES to review options."),
    ("Responses never reached a human.", "Reply YES to review options."),
    ("Replies go unanswered.", "Reply YES to review options."),
    ("Calls went unanswered.", "Call us to review options."),
    ("Replies are not monitored.", "Reply YES to review options."),
    ("Replies remain unanswered.", "Reply YES to review options."),
    ("No employee answers replies.", "Reply YES to review options."),
    ("Responses lack monitoring.", "Reply YES to review options."),
    ("Replies are left without a response.", "Reply YES to review options."),
    ("Messages disappear into a void.", "Reply YES to review options."),
    ("Nobody ever sees inbound emails.", "Email us to review options."),
)

_UNSAFE_COPY = tuple(
    copy
    for fact, cta in (*_WITHDRAWAL_FACT_AND_CTA, *_DEAD_RESPONSE_FACT_AND_CTA)
    for copy in (f"{fact} {cta}", f"{cta} {fact}")
)

_SAFE_COPY = (
    "The applicant rejected an unreviewed fee. Call us to review options.",
    "Future calls are permitted. Call us to review options.",
    "We are permitted to make future calls. Call us to review options.",
    "The lender discontinued a deprecated call-routing service. Call us to review options.",
    "Our operations team abandoned the telephone migration. Call us to review options.",
    "Replies fall through the cracks during triage, but staff reviews them. Reply YES.",
    "Replies are buried during indexing, then staff reviews them. Reply YES.",
    "Replies receive no reply from automation, but staff responds to them. Reply YES.",
    "Replies await review briefly, then staff reviews them. Reply YES.",
    "Replies wait in a monitored queue before staff reviews them. Reply YES.",
    "No staff monitors the old dashboard, but staff reviews replies. Reply YES.",
    "Replies remain answered by staff. Reply YES.",
)

_SAFE_EXPLICIT_CONSENT_COPY = (
    "The borrower permits future calls. Call us to review options.",
    "The borrower has consented to future calls. Call us to review options.",
)

_SAFE_ANSWERED_RECOVERY_COPY = (
    "Calls go unanswered during triage, but staff answers them. Call us to review options.",
    "Replies go unanswered during triage, but staff responds to them. Reply YES.",
)


def _variant(*, body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_growth_dependencies() -> Iterator[tuple[MagicMock, ...]]:
    """Install inert dependencies so validation must precede every side effect."""

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


@pytest.mark.parametrize("copy", _UNSAFE_COPY)
def test_new_structural_families_reject_every_governed_copy_boundary(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy)
    assert contains_borrower_cta_contradiction(copy)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        ComposePlanRequest(objective=copy)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{copy} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="call to action"):
        _assert_final_draft_subject(draft_subject=copy, channel="email")
    with pytest.raises(AuditMetadataValueViolation, match="contradicts consent"):
        build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")


@pytest.mark.parametrize("copy", _SAFE_COPY)
def test_positive_permission_operations_and_staffed_controls_remain_safe(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert not contains_borrower_cta_contradiction(copy)
    assert _variant(body=copy).body == copy
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{copy} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(copy)
    assert _assert_final_draft_subject(draft_subject=copy, channel="email") == copy
    assert (
        build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")["draft_body"]
        == copy
    )


@pytest.mark.parametrize("copy", _SAFE_EXPLICIT_CONSENT_COPY)
def test_positive_borrower_consent_is_not_negative_cta_evidence(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert not contains_borrower_cta_contradiction(copy)
    assert _variant(body=copy).body == copy
    assert _assert_final_draft_subject(draft_subject=copy, channel="email") == copy
    assert (
        build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")["draft_body"]
        == copy
    )


@pytest.mark.parametrize("copy", _SAFE_ANSWERED_RECOVERY_COPY)
def test_same_clause_staff_answer_reconciles_unanswered_state(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert not contains_borrower_cta_contradiction(copy)


@pytest.mark.parametrize(
    "objective",
    (
        "The borrower said no to future calls. Call us to review options.",
        "The homeowner gave up authorization for calls. Call us to review options.",
        "Replies fall through the cracks. Reply YES to review options.",
        "No staff monitors replies. Reply YES to review options.",
    ),
)
def test_representative_families_stop_endpoints_before_planners_and_writes(
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
