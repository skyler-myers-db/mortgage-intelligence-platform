"""Round 22 structural withdrawal and staffed-response governance proof."""

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
from backend.schemas.borrower_cta_actions import (
    explicit_borrower_contact_actions,
    negative_actions_for_positive,
)
from backend.schemas.borrower_cta_evidence import negative_borrower_cta_evidence
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client
from tests.unit.growth_refusal_contract import (
    GROWTH_REFUSAL_MESSAGE_RE,
    assert_only_refusal_audit_writes,
    assert_refused_with_audit,
)

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out."
)

_WITHDRAWAL_FACT_AND_CTA = (
    ("Borrower demands no calls.", "Call us to review options.", "call"),
    (
        "Borrower requested cessation of calls.",
        "Call us to review options.",
        "call",
    ),
    ("Borrower insists calls stop.", "Call us to review options.", "call"),
    ("Borrower demanded calling stop.", "Call us to review options.", "call"),
    (
        "Borrower expressly declined calls.",
        "Call us to review options.",
        "call",
    ),
    (
        "Borrower wants outreach ended.",
        "Contact us to review options.",
        "contact",
    ),
    (
        "The applicant objects to further contact.",
        "Contact us to review options.",
        "contact",
    ),
    (
        "The homeowner does not permit telephone calls.",
        "Call us to review options.",
        "call",
    ),
    (
        "The recipient refuses permission for messages.",
        "Reply YES to review options.",
        "reply",
    ),
)
_DEAD_RESPONSE_FACT_AND_CTA = (
    (
        "Replies are archived and not looked at by anyone.",
        "Reply YES to review options.",
        "reply",
    ),
    (
        "Incoming emails were stored but never reviewed by staff.",
        "Email us to review options.",
        "email",
    ),
    (
        "Messages are queued where no employee opens them.",
        "Reply YES to review options.",
        "reply",
    ),
    (
        "Responses are retained and support never processes them.",
        "Reply YES to review options.",
        "reply",
    ),
)
_UNSAFE_FACT_AND_CTA = (*_WITHDRAWAL_FACT_AND_CTA, *_DEAD_RESPONSE_FACT_AND_CTA)
_UNSAFE_COPY = tuple(
    body
    for fact, cta, _action in _UNSAFE_FACT_AND_CTA
    for body in (f"{fact} {cta}", f"{cta} {fact}")
)

_SAFE_OPERATION_FACT_AND_CTA = (
    (
        "The gateway encrypts incoming email before an employee opens it.",
        "Email us to review options.",
    ),
    (
        "The provider authenticates inbound SMS before support processes it.",
        "Text us to review options.",
    ),
    (
        "The platform virus-scans incoming replies before compliance reviews them.",
        "Reply YES to review options.",
    ),
    (
        "The gateway compresses incoming email before an agent reads it.",
        "Email us to review options.",
    ),
    (
        "The provider validates incoming texts before support monitors them.",
        "Text us to review options.",
    ),
)
_SAFE_DETECTOR_CAMPAIGN_CONTROLS = (
    "The borrower demands a fee review. Call us to review options.",
    "The recipient refuses permission for a credit pull. Reply YES to review options.",
)
_SAFE_PUBLIC_NON_CONSENT_COPY = (
    "The applicant objects to the loan terms. Contact us to review options.",
    "The homeowner does not permit automatic payments. Call us to review options.",
    "Replies are archived and then compliance reviews them. Reply YES to review options.",
)


def _variant(*, subject: str = "Mortgage options", body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize(("fact", "cta", "action"), _UNSAFE_FACT_AND_CTA)
def test_round22_negative_evidence_infers_the_conflicting_action(
    fact: str,
    cta: str,
    action: str,
) -> None:
    body = f"{fact} {cta}"
    negative = negative_borrower_cta_evidence(body)
    positive = explicit_borrower_contact_actions(body)

    assert negative
    assert positive
    assert any(
        action
        in negative_actions_for_positive(
            body,
            negative_match=negative_match,
            positive_match=positive_match,
        )
        for negative_match in negative
        for positive_match, _positive_actions in positive
    )


@pytest.mark.parametrize("body", _UNSAFE_COPY)
def test_round22_withdrawals_and_dead_responses_reject_every_schema_boundary(
    body: str,
) -> None:
    assert negative_borrower_cta_evidence(body)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body=body)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=body)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=body)


@pytest.mark.parametrize("copy", _UNSAFE_COPY)
def test_round22_withdrawals_and_dead_responses_reject_final_body_and_subject(
    copy: str,
) -> None:
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{copy} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(ValidationError, match="call to action"):
        _variant(subject=copy, body="Contact us to review mortgage options.")
    with pytest.raises(HTTPException, match="call to action"):
        _assert_final_draft_subject(draft_subject=copy, channel="email")


@pytest.mark.parametrize(("fact", "cta"), _SAFE_OPERATION_FACT_AND_CTA)
def test_round22_reviewed_provider_operations_with_staffed_delivery_remain_safe(
    fact: str,
    cta: str,
) -> None:
    body = f"{fact} {cta}"
    assert negative_borrower_cta_evidence(fact) == []
    assert _variant(body=body).body == body
    assert GrowthAgentPromptRunRequest(prompt=body).prompt == body
    assert ComposePlanRequest(objective=body).objective == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)


@pytest.mark.parametrize("body", _SAFE_DETECTOR_CAMPAIGN_CONTROLS)
def test_round22_non_channel_predicates_are_not_consent_evidence(body: str) -> None:
    assert negative_borrower_cta_evidence(body) == []
    assert _variant(body=body).body == body


@pytest.mark.parametrize("body", _SAFE_PUBLIC_NON_CONSENT_COPY)
def test_round22_non_consent_business_language_remains_valid_public_input(
    body: str,
) -> None:
    assert negative_borrower_cta_evidence(body) == []
    assert _variant(body=body).body == body
    assert GrowthAgentPromptRunRequest(prompt=body).prompt == body
    assert ComposePlanRequest(objective=body).objective == body


@pytest.mark.parametrize(
    "fact",
    (
        "The gateway zorbifies incoming email before support monitors it.",
        "The gateway deletes incoming email before support monitors it.",
        "The provider validates incoming email before support monitors SMS.",
        "The platform encrypts incoming replies. Support reviews them later.",
        "Replies are archived and support never opens them.",
    ),
)
def test_round22_unknown_destructive_mismatched_and_dead_transports_fail_closed(
    fact: str,
) -> None:
    assert negative_borrower_cta_evidence(fact)


@pytest.mark.parametrize("objective", _UNSAFE_COPY)
def test_round22_growth_endpoints_stop_before_planner_model_or_writes(
    objective: str,
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
            json={"prompt": objective, "save_monitor": True, "cadence": "daily"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        compose_response = client.post(
            "/api/growth-agent/agent/compose",
            json={"objective": objective, "execute": True},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        app.dependency_overrides.pop(get_audit_store, None)

    assert_refused_with_audit(run_response)
    assert_refused_with_audit(compose_response)
    assert objective not in run_response.text
    assert objective not in compose_response.text
    run_planner.assert_not_called()
    compose_planner.assert_not_called()
    assert sql.mock_calls == []
    assert lakebase.mock_calls == []
    # The refusal is recorded; no run/monitor/draft write happens.
    assert_only_refusal_audit_writes(audit_store)
