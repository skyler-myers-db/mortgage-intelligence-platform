"""Round 20 consent-withdrawal and response-transport governance proof."""

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
from backend.schemas.borrower_cta_actions import cta_channel_actions
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
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. "
    "Reply unsubscribe to opt out."
)

_ROUND21_UNSAFE_FACT_AND_CTA = (
    ("borrower insists we cease telephoning.", "Call us to review options."),
    ("customer prohibited further correspondence.", "Contact us to review options."),
    ("applicant placed do-not-disturb request.", "Contact us to review options."),
    ("customer declined further outreach.", "Contact us to review options."),
    (
        "borrower requested we desist from correspondence.",
        "Contact us to review options.",
    ),
    (
        "customer directed us to discontinue outreach.",
        "Contact us to review options.",
    ),
    ("borrower denied permission to call.", "Call us to review options."),
    ("applicant has no-contact preference.", "Contact us to review options."),
    ("gateway quarantines email indefinitely.", "Reply YES."),
    ("replies never surfaced to staff.", "Reply YES."),
    (
        "gateway archives inbound SMS where nobody reviews it.",
        "Reply YES.",
    ),
    ("Incoming email is swallowed before review.", "Reply YES."),
)

_UNSAFE_FACT_AND_CTA = (
    ("The customer asked to be left alone.", "Contact us to review mortgage options."),
    ("The customer wants to be left alone.", "Contact us to review mortgage options."),
    ("The recipients wish to be left alone.", "Message us to review options."),
    ("The applicant prefers to be left alone.", "Call us to review options."),
    ("The homeowner nullified email consent.", "Email us to review options."),
    ("The homeowner declared no more email.", "Email us to review options."),
    ("The client declared no further calls.", "Call us to review options."),
    ("The prospects declared no more messages.", "Message us to review options."),
    ("The recipient demanded radio silence.", "Message us to review options."),
    (
        "The borrower said this number is not to be called again.",
        "Call us to review options.",
    ),
    (
        "Incoming replies are overwritten by the gateway before staff can read them.",
        "Reply YES.",
    ),
    (
        "Incoming replies are overwritten before review then delivered to a monitored "
        "queue.",
        "Reply YES.",
    ),
    (
        "Incoming email is overwritten before human review then delivered to a staffed "
        "email inbox.",
        "Email us to review options.",
    ),
    (
        "Inbound SMS is replaced before staff review then routed to a staffed SMS queue.",
        "Text us to review options.",
    ),
    *_ROUND21_UNSAFE_FACT_AND_CTA,
)
_UNSAFE_COPY = tuple(
    body
    for fact, cta in _UNSAFE_FACT_AND_CTA
    for body in (f"{fact} {cta}", f"{cta} {fact}")
)

_SAFE_TRANSPORT_FACT_AND_CTA = (
    (
        "The gateway encrypts incoming email then delivers it to a staffed email inbox.",
        "Email us to review options.",
    ),
    (
        "The provider authenticates inbound SMS then routes it to a staffed SMS queue.",
        "Text us to review options.",
    ),
    (
        "The platform scans incoming replies then delivers them to a monitored queue.",
        "Reply YES.",
    ),
    (
        "The gateway normalizes incoming email then delivers it to a staffed email inbox.",
        "Email us to review options.",
    ),
    (
        "The provider parses inbound SMS then routes it to a staffed SMS queue.",
        "Text us to review options.",
    ),
    (
        "The platform logs incoming replies then delivers them to a monitored queue.",
        "Reply YES.",
    ),
    (
        "gateway encrypts incoming email before a human reviews it.",
        "Email us to review options.",
    ),
    (
        "provider authenticates inbound SMS before staff reads it.",
        "Text us to review options.",
    ),
    (
        "platform virus-scans incoming replies before our team reviews them.",
        "Reply YES.",
    ),
    (
        "gateway compresses inbound email and a human reads it.",
        "Email us to review options.",
    ),
    (
        "provider validates incoming texts and staff monitors them.",
        "Text us to review options.",
    ),
)
_SAFE_TRANSPORT_FACTS = tuple(fact for fact, _cta in _SAFE_TRANSPORT_FACT_AND_CTA)
_SAFE_COPY = tuple(
    f"{fact} {cta}" for fact, cta in _SAFE_TRANSPORT_FACT_AND_CTA
)

_UNRECOVERED_TRANSPORT_FACTS = (
    (
        "The gateway encrypts incoming email. "
        "It later delivers it to a staffed email inbox."
    ),
    (
        "The provider authenticates inbound SMS then routes the message to a staffed "
        "email inbox."
    ),
)

_SAFE_CONTEXTUAL_FACTS = (
    "The application was left alone while a self-service comparison ran.",
    "The branch recorded radio silence during a scheduled equipment test.",
    "The scheduler wants to be left alone during maintenance.",
    "A report declared no more email.",
)
_SAFE_CONTEXTUAL_COPY = tuple(
    f"{fact} Contact us to review mortgage options." for fact in _SAFE_CONTEXTUAL_FACTS
)

_DESTRUCTIVE_DELIVERY_FACT_AND_CTA = (
    (
        "The gateway erases incoming email then delivers it to a staffed email inbox.",
        "Email us to review options.",
    ),
    (
        "The provider deletes inbound SMS then routes it to a staffed SMS queue.",
        "Text us to review options.",
    ),
    (
        "The platform shreds incoming replies then delivers them to a monitored queue.",
        "Reply YES.",
    ),
    (
        "The gateway incinerates incoming email then delivers it to a staffed email inbox.",
        "Email us to review options.",
    ),
    (
        "The provider vaporizes inbound SMS then routes it to a staffed SMS queue.",
        "Text us to review options.",
    ),
    (
        "The platform annihilates incoming replies then delivers them to a monitored queue.",
        "Reply YES.",
    ),
    (
        "The gateway overwrites incoming email then delivers it to a staffed email inbox.",
        "Email us to review options.",
    ),
    (
        "The gateway replaces incoming SMS before review, then delivers it "
        "to a staffed SMS queue.",
        "Text us to review options.",
    ),
    (
        "Incoming replies are replaced by the platform before human review, then "
        "delivered to a monitored queue.",
        "Reply YES.",
    ),
    (
        "The provider drops inbound SMS then routes it to a staffed SMS queue.",
        "Text us to review options.",
    ),
    (
        "The platform discards incoming replies then delivers them to a monitored queue.",
        "Reply YES.",
    ),
    (
        "Incoming replies are vaporized by the platform then delivered to a monitored "
        "queue.",
        "Reply YES.",
    ),
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("body", _UNSAFE_COPY)
def test_round20_withdrawal_and_dead_response_copy_rejects_every_public_boundary(
    body: str,
) -> None:
    assert negative_borrower_cta_evidence(body)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=body)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=body)


@pytest.mark.parametrize(
    "subject",
    tuple(
        body
        for fact, cta in _ROUND21_UNSAFE_FACT_AND_CTA
        for body in (f"{fact} {cta}", f"{cta} {fact}")
    ),
)
def test_round21_withdrawal_and_dead_response_subjects_reject_before_approval(
    subject: str,
) -> None:
    with pytest.raises(ValidationError, match="call to action"):
        CampaignRecommendationVariant(
            variant_name="Guidance-led",
            subject=subject,
            body="Contact us to review mortgage options.",
            hypothesis="A reviewed invitation may support a response.",
        )
    with pytest.raises(HTTPException, match="call to action"):
        _assert_final_draft_subject(draft_subject=subject, channel="email")


@pytest.mark.parametrize("fact", _SAFE_TRANSPORT_FACTS)
def test_round20_staffed_same_clause_transport_recovery_has_no_negative_evidence(
    fact: str,
) -> None:
    assert negative_borrower_cta_evidence(fact) == []


@pytest.mark.parametrize("body", _SAFE_COPY)
def test_round20_staffed_transport_recovery_remains_valid_public_copy(body: str) -> None:
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)
    assert GrowthAgentPromptRunRequest(prompt=body).prompt == body
    assert ComposePlanRequest(objective=body).objective == body


@pytest.mark.parametrize(
    ("fact", "body"),
    zip(_SAFE_CONTEXTUAL_FACTS, _SAFE_CONTEXTUAL_COPY, strict=True),
)
def test_round20_contextual_left_alone_and_radio_silence_are_not_withdrawals(
    fact: str,
    body: str,
) -> None:
    assert negative_borrower_cta_evidence(fact) == []
    assert "contact" not in cta_channel_actions(fact)
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)
    assert GrowthAgentPromptRunRequest(prompt=body).prompt == body
    assert ComposePlanRequest(objective=body).objective == body


@pytest.mark.parametrize(("fact", "cta"), _DESTRUCTIVE_DELIVERY_FACT_AND_CTA)
def test_round20_destructive_transport_actions_cannot_be_recovered_by_delivery(
    fact: str,
    cta: str,
) -> None:
    body = f"{fact} {cta}"
    assert negative_borrower_cta_evidence(fact)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=body)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=body)


@pytest.mark.parametrize("fact", _UNRECOVERED_TRANSPORT_FACTS)
def test_round20_recovery_must_share_clause_and_response_transport(fact: str) -> None:
    assert negative_borrower_cta_evidence(fact)


@pytest.mark.parametrize("objective", _UNSAFE_COPY)
def test_round20_growth_endpoints_stop_before_planner_model_or_writes(
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
