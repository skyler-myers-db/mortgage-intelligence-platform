"""Round 24 structural audience, identity-slot, and consent proof."""

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
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
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
from tests.unit.growth_refusal_contract import (
    GROWTH_REFUSAL_MESSAGE_RE,
    assert_refusal_isolation,
)

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out."
)

_UNREVIEWED_ADMISSION = (
    "Move borrowers into the campaign when scleroderma is present.",
    "Transfer applicants to the audience based on an unreviewed health indicator.",
    "Admit homeowners to the cohort if a hidden clinical status is present.",
    "Borrowers are moved into the campaign when an unknown clinical marker is present.",
    "Borrowers were admitted into the campaign due to a hidden clinical status.",
    "Borrowers get moved into the campaign when scleroderma is present.",
    "Recipients with an unseen medical status are transferred into the audience.",
    "Hidden treatment-status borrowers are admitted to the campaign.",
    "The admission of borrowers into the campaign based on scleroderma.",
)

_SAFE_ADMISSION = (
    "Move borrowers into the campaign when high equity is present.",
    "Transfer applicants to the audience based on current mortgage rates.",
    "Admit homeowners to the cohort if they have strong equity.",
    "Borrowers are moved into the campaign when current LTV is documented.",
    "Recipients with high equity are transferred into the audience.",
    "High equity borrowers are admitted to the campaign.",
    "The admission of borrowers into the campaign based on high equity.",
)

_IDENTITY_SLOTS = (
    "Recipient: Siobhan Q. Quigley",
    "Recipient: Siobhan Q Quigley",
    "Mail to: Siobhan Q. Quigley",
    "Send to: Siobhan Q Quigley",
    "Email to: Siobhan Q. Quigley",
    "For delivery to: Siobhan Q Quigley",
    "Issued to Siobhan Q. Quigley",
    "Add Siobhan Q Quigley to correspondence",
)

_SAFE_BUSINESS_SLOTS = (
    "Mail to: branch office",
    "Send to: compliance team",
    "Email to: customer support team",
    "For delivery to: regional branch office",
    "Issued to legal counsel",
    "Add underwriting manager to correspondence",
)

_CONSENT_CONTRADICTIONS = (
    "The borrower rejects all future calls. Call us to review options.",
    "The customer revoked calling permission. Call us to review options.",
)

_DEAD_RESPONSE_CONTRADICTIONS = (
    "Replies are left unread. Reply YES to review options.",
    "Replies go unread. Reply YES to review options.",
    "Replies are never seen. Reply YES to review options.",
    "Incoming replies never seen. Reply YES to review options.",
)

_SAFE_CONSENT_CONTROLS = (
    "The applicant rejected an unreviewed fee. Call us to review options.",
    "Replies are seen by staff. Reply YES to review options.",
    "Replies are left unread at first, then staff reviews them. Reply YES to review options.",
)


def _variant(*, body: str, subject: str = "Mortgage options") -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_growth_dependencies() -> Iterator[tuple[MagicMock, ...]]:
    """Install inert dependencies so request validation must precede all work."""

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


@pytest.mark.parametrize("copy", _UNREVIEWED_ADMISSION)
def test_admission_morphology_rejects_every_shared_boundary(copy: str) -> None:
    assert contains_protected_class_marketing_text(copy)
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(body=f"{copy} Contact us to review mortgage options.")
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
    with pytest.raises(AuditMetadataValueViolation, match="protected-class"):
        build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")


@pytest.mark.parametrize("copy", _SAFE_ADMISSION)
def test_closed_mortgage_admission_controls_remain_safe(copy: str) -> None:
    assert not contains_protected_class_marketing_text(copy)
    assert _variant(body=f"{copy} Contact us to review mortgage options.")
    assert (
        build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")["draft_body"]
        == copy
    )


@pytest.mark.parametrize("field", ("draft_body", "draft_subject"))
def test_exact_health_admission_fails_audit_draft_fields(field: str) -> None:
    unsafe = "Move borrowers into the campaign when scleroderma is present."
    with pytest.raises(AuditMetadataValueViolation, match="protected-class"):
        build_safe_audit_metadata({field: unsafe}, action="outreach.approve")


@pytest.mark.parametrize("copy", _SAFE_ADMISSION[:-2])
def test_closed_mortgage_admission_controls_remain_safe_for_growth(copy: str) -> None:
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy


def test_read_only_population_movement_question_is_not_audience_admission() -> None:
    question = "How has the in-the-money population moved recently?"
    assert not contains_protected_class_marketing_text(question)


@pytest.mark.parametrize("copy", _IDENTITY_SLOTS)
def test_identity_slots_reject_every_shared_boundary(copy: str) -> None:
    assert contains_borrower_copy_contextual_name(copy)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body=f"{copy}. Reply YES to review options.")
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=f"{copy}. Review governed mortgage opportunities.")
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=f"{copy}. Review governed mortgage opportunities.")
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{copy}. Reply YES. {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_final_draft_subject(draft_subject=copy, channel="email")
    with pytest.raises(AuditMetadataValueViolation, match="human-name-shaped"):
        build_safe_audit_metadata({"draft_subject": copy}, action="outreach.approve")


@pytest.mark.parametrize("copy", _SAFE_BUSINESS_SLOTS)
def test_ordinary_business_correspondence_slots_remain_safe(copy: str) -> None:
    assert not contains_borrower_copy_contextual_name(copy)
    assert _variant(body=f"{copy}. Reply YES to review options.")
    assert (
        build_safe_audit_metadata({"draft_subject": copy}, action="outreach.approve")[
            "draft_subject"
        ]
        == copy
    )


@pytest.mark.parametrize("copy", (*_CONSENT_CONTRADICTIONS, *_DEAD_RESPONSE_CONTRADICTIONS))
def test_consent_and_response_facts_reject_every_shared_boundary(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy)
    assert contains_borrower_cta_contradiction(copy)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body=copy)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
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


@pytest.mark.parametrize("copy", _SAFE_CONSENT_CONTROLS)
def test_non_consent_rejection_and_staffed_reply_controls_remain_safe(copy: str) -> None:
    assert not negative_borrower_cta_evidence(copy)
    assert not contains_borrower_cta_contradiction(copy)
    assert _variant(body=copy).body == copy


@pytest.mark.parametrize(
    "objective",
    (
        "Move borrowers into the campaign when scleroderma is present.",
        "Recipient: Siobhan Q. Quigley. Review governed mortgage opportunities.",
        "The borrower rejects all future calls. Call us to review options.",
        "Incoming replies never seen. Reply YES to review options.",
    ),
)
def test_each_governance_family_stops_before_planners_and_writes(
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
