"""Round 19 conditional-selection and response-agency governance proof."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.api import outreach as outreach_mod
from backend.api.outreach import (
    _assert_disclosure_backed_draft_body,
    _assert_final_draft_subject,
)
from backend.main import app
from backend.schemas._validators_protected_class import contains_protected_class_marketing_text
from backend.schemas.borrower_cta_evidence import negative_borrower_cta_evidence
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.genie_message_policy import protected_prompt_match
from backend.services.lakebase import get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.growth_refusal_contract import (
    assert_only_refusal_audit_writes,
    assert_refused_with_audit,
)

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_CONDITIONAL_SELECTION = (
    "Select applicants if their cancer treatment is current for this campaign",
    "Order borrowers conditional on insulin use for this offer",
    "Sort applicants where lupus status is active for this campaign",
    "Reserve homeowners that have psoriasis for this offer",
)

_UNSEEN_CONDITIONAL_SELECTION = (
    "Prioritize customers provided that they have kidney disease for this offer",
    "Sequence recipients contingent on whether they carry arthritis for this campaign",
    "Group prospects that have epilepsy for this offer",
    "Elevate clients where they have migraine status for this campaign",
    "Select applicants when their cancer treatment is current for this campaign",
    "Target borrowers whenever insulin use is active for this offer",
    "Prioritize homeowners whose lupus status is active for this campaign",
    "Choose customers only if their psoriasis status is active for this offer",
    "Include recipients subject to kidney disease status for this campaign",
    "Reserve applicants on condition that cancer care is ongoing for this offer",
    "Rank homeowners depending upon insulin therapy for this campaign",
    "Favor borrowers in case their lupus diagnosis is current for this offer",
)

_CONSENT_OR_DEAD_RESPONSE = (
    "The borrower asked us to abstain from messaging. Message us to review options.",
    "The borrower requested a moratorium on calls. Call us to review options.",
    "Further contact is unwelcome. Contact us to review options.",
    "The borrower vetoed any future email. Email us to review options.",
    "Replies expire before staff read them. Reply YES to review options.",
    "The gateway shreds inbound SMS. Text us to review options.",
    "The platform erases incoming email. Email us to review options.",
)

_UNSEEN_CONSENT_OR_DEAD_RESPONSE = (
    "The customer demanded a freeze on texts. Text us to review options.",
    "Any further messages are unwanted. Message us to review options.",
    "Incoming replies evaporate before an employee can see them. Reply YES.",
    "The carrier incinerates incoming texts. Text us to review options.",
    "The service wipes inbound emails. Email us to review options.",
    "The borrower requested a hiatus from calls. Call us to review options.",
    "The borrower said future email is forbidden. Email us to review options.",
    "The borrower asked that messaging stop indefinitely. Message us to review options.",
    "The borrower imposed a blackout on texts. Text us to review options.",
    "The borrower renounced future contact. Contact us to review options.",
    "The gateway vaporizes inbound SMS. Text us to review options.",
    "Incoming replies time out before staff can open them. Reply YES.",
    "The platform annihilates incoming email. Email us to review options.",
)

_SAFE_CONDITIONAL_SELECTION = (
    "Select applicants if they have high equity for this campaign",
    "Order borrowers conditional on high equity for this offer",
    "Sort applicants where they have current mortgage rates for this campaign",
    "Reserve homeowners that have listed properties for this offer",
)

_SAFE_RESPONSE_COPY = (
    "The gateway relays inbound SMS to a staffed SMS queue. Text us to review options.",
    "Replies expire after staff archive them. Reply YES to review options.",
    (
        "The SMS gateway intercepts inbound SMS then routes it to a staffed SMS queue. "
        "Text us to review options."
    ),
    (
        "The email provider filters incoming email then delivers it to a staffed email "
        "inbox. Email us to review options."
    ),
)

_SAFE_ANALYTICS = (
    "Show me the top 10 borrowers by lead score in Illinois.",
    "Count listed borrowers by county.",
)


def _variant(body: str, *, subject: str = "Mortgage options") -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_audit_store() -> Iterator[InMemoryAuditStore]:
    """Expose an empty audit ledger for final-approval no-write assertions."""

    previous = app.dependency_overrides.get(get_audit_store)
    audit = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        yield audit
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous


@pytest.mark.parametrize(
    "selection",
    (*_CONDITIONAL_SELECTION, *_UNSEEN_CONDITIONAL_SELECTION),
)
def test_conditional_protected_selection_rejects_public_and_final_copy(
    selection: str,
) -> None:
    body = f"{selection}. Contact us to review mortgage options."
    assert contains_protected_class_marketing_text(selection)
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(body)
    with pytest.raises(ValidationError, match="protected-class"):
        _variant("Contact us to review mortgage options.", subject=selection)
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_final_draft_subject(draft_subject=selection, channel="email")


@pytest.mark.parametrize(
    "body",
    (*_CONSENT_OR_DEAD_RESPONSE, *_UNSEEN_CONSENT_OR_DEAD_RESPONSE),
)
def test_withdrawal_and_dead_response_synonyms_reject_public_and_final_copy(
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


@pytest.mark.parametrize(
    ("unsafe_copy", "expected_detail"),
    (
        *((value, "protected-class") for value in _CONDITIONAL_SELECTION),
        *((value, "call to action") for value in _CONSENT_OR_DEAD_RESPONSE),
    ),
)
def test_final_approval_rejects_governance_bypasses_before_lakebase_or_audit_write(
    unsafe_copy: str,
    expected_detail: str,
    fake_lakebase_client,
    isolated_audit_store: InMemoryAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outreach_mod,
        "ensure_approval_idempotency_column",
        lambda lakebase: None,
    )
    monkeypatch.setattr(
        outreach_mod,
        "ensure_approval_followup_columns",
        lambda lakebase: None,
    )
    response = TestClient(app).post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "draft_subject": "Mortgage options review",
            "draft_body": f"{unsafe_copy} {_DISCLOSURE.body}",
        },
    )

    assert response.status_code == 422, response.text
    assert expected_detail in response.json()["detail"]
    assert fake_lakebase_client.executes == []
    assert fake_lakebase_client.approvals == []
    assert fake_lakebase_client.audit_events == []
    assert isolated_audit_store.list(limit=5) == []


@pytest.mark.parametrize("selection", _SAFE_CONDITIONAL_SELECTION)
def test_reviewed_mortgage_conditions_remain_available(selection: str) -> None:
    body = f"{selection}. Contact us to review mortgage options."
    assert contains_protected_class_marketing_text(selection) is False
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)


@pytest.mark.parametrize("body", _SAFE_RESPONSE_COPY)
def test_legitimate_delivery_and_same_transport_staffing_remain_available(body: str) -> None:
    assert negative_borrower_cta_evidence(body) == []
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)


@pytest.mark.parametrize("question", _SAFE_ANALYTICS)
def test_reviewed_mortgage_analytics_remain_available(question: str) -> None:
    assert contains_protected_class_marketing_text(question) is False
    assert protected_prompt_match(question) is None
    assert GrowthAgentPromptRunRequest(prompt=question).prompt == question


@pytest.mark.parametrize(
    "objective",
    (
        *_CONDITIONAL_SELECTION,
        *_UNSEEN_CONDITIONAL_SELECTION,
        *_CONSENT_OR_DEAD_RESPONSE,
        *_UNSEEN_CONSENT_OR_DEAD_RESPONSE,
    ),
)
def test_unsafe_objectives_stop_before_planners_sql_lakebase_or_audit(
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
