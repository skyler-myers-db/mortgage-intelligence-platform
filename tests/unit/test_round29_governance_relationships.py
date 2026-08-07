"""Round 29 governance relationship ordering and safe-control proof."""

from __future__ import annotations

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
from backend.schemas._validators_protected_class import contains_protected_class_marketing_text
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.borrower_cta_evidence import (
    contains_borrower_cta_contradiction,
    negative_borrower_cta_evidence,
)
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

_DISCLOSURE = SimpleNamespace(
    body=("Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out.")
)

_UNSAFE_AUDIENCE_RELATIONSHIPS = (
    "Borrowers living with scleroderma are funneled toward the campaign.",
    "Borrowers affected by lupus are routed towards the audience.",
    "Scleroderma is why borrowers enter the campaign.",
    "Because of lupus, borrowers enter the campaign.",
    "Lupus causes borrowers to enter the campaign.",
    "Borrowers enter the campaign due to lupus.",
    "The campaign is for borrowers living with lupus.",
    "Lupus determines whether borrowers join the campaign.",
    "The campaign receives borrowers because of lupus.",
    "Campaign entry depends on lupus.",
    "Lupus drives campaign membership for borrowers.",
    "On account of lupus, borrowers join the campaign.",
    "Borrowers join the campaign on account of lupus.",
    "Campaign admission stems from lupus.",
    "Lupus led borrowers into the campaign.",
    "Lupus puts borrowers in the campaign.",
    "Lupus sends borrowers toward the campaign.",
    "Borrowers enter the campaign because they have lupus.",
    "Borrowers enter the campaign owing to lupus.",
    "The campaign takes borrowers on the basis of lupus.",
    "Campaign membership hinges upon lupus.",
    "Lupus is the basis for borrowers entering the campaign.",
    "Lupus explains why borrowers enter the campaign.",
    "Borrowers with lupus end up in the campaign.",
    "Lupus brought borrowers into the campaign.",
    "Lupus steered borrowers toward the campaign.",
    "Lupus moved borrowers onto the list.",
    "Lupus accounts for why borrowers enter the campaign.",
    "Lupus resulted in borrowers entering the campaign.",
    "Borrowers enter the campaign by reason of lupus.",
    "Borrowers enter the campaign on grounds of eczema.",
    "Borrowers enter the campaign by virtue of eczema.",
    "Borrowers enter the campaign in light of eczema.",
    "Eczema is the grounds for borrowers entering the campaign.",
    "Borrowers coping with eczema receive this offer.",
    "Campaign membership rests upon lupus.",
    "The audience enrolls borrowers because they have lupus.",
    "Borrowers with lupus wind up in the campaign.",
    "Lupus is the cause for borrowers joining the audience.",
    "Cancer puts borrowers on the offer.",
    "Cancer is putting borrowers on the offer.",
)

_SAFE_AUDIENCE_RELATIONSHIPS = (
    "Borrowers living with high equity are funneled toward the campaign.",
    "Borrowers affected by current LTV are routed towards the audience.",
    "High equity is why borrowers enter the campaign.",
    "Because of current LTV, borrowers enter the campaign.",
    "High equity causes borrowers to enter the campaign.",
    "Borrowers enter the campaign due to current LTV.",
    "The campaign is for borrowers living with high equity.",
    "High equity determines whether borrowers join the campaign.",
    "The campaign receives borrowers because of high equity.",
    "Campaign entry depends on current LTV.",
    "High equity drives campaign membership for borrowers.",
    "On account of high equity, borrowers join the campaign.",
    "Borrowers join the campaign on account of current LTV.",
    "Campaign admission stems from high equity.",
    "High equity led borrowers into the campaign.",
    "High equity puts borrowers in the campaign.",
    "Current LTV sends borrowers toward the campaign.",
    "Borrowers enter the campaign because they have high equity.",
    "Borrowers enter the campaign owing to current LTV.",
    "The campaign takes borrowers on the basis of high equity.",
    "Campaign membership hinges upon current LTV.",
    "High equity is the basis for borrowers entering the campaign.",
    "Current LTV explains why borrowers enter the campaign.",
    "Borrowers with high equity end up in the campaign.",
    "High equity brought borrowers into the campaign.",
    "Current LTV steered borrowers toward the campaign.",
    "High equity moved borrowers onto the list.",
    "High equity accounts for why borrowers enter the campaign.",
    "Current LTV resulted in borrowers entering the campaign.",
    "Borrowers enter the campaign by reason of high equity.",
    "Borrowers enter the campaign on grounds of high equity.",
    "Borrowers enter the campaign by virtue of high equity.",
    "Borrowers enter the campaign in light of high equity.",
    "High equity is the grounds for borrowers entering the campaign.",
    "Borrowers coping with high equity receive this offer.",
    "Campaign membership rests upon current LTV.",
    "The audience enrolls borrowers because they have high equity.",
    "Borrowers with high equity wind up in the campaign.",
    "High equity is the cause for borrowers joining the audience.",
    "High equity puts borrowers on the offer.",
    "High equity is putting borrowers on the offer.",
)

_UNSAFE_IDENTITY_RELATIONSHIPS = (
    "Prepared by xavier montoya",
    "Drafted by xavier montoya",
    "Reviewed by xavier montoya",
    "Composed by xavier montoya",
    "Written by xavier montoya",
    "Your reply goes to Jordan",
    "Your response is routed to Jordan",
    "The message will be sent to Jordan",
    "Jordan receives your reply",
    "Reply to Jordan about this offer",
    "Attn: Jordan",
    "Send this reply to Jordan",
    "Recipient: Jordan",
    "Jordan is the recipient",
    "Edited by xavier montoya",
    "Assembled by xavier montoya",
    "Presented by xavier montoya",
    "Your reply will reach Jordan",
    "Jordan will get your reply",
    "Send Jordan this reply",
    "Cc: Jordan",
    "For Jordan: mortgage review",
    "Prepared for Jordan",
    "Signed by Jordan",
    "Proofread by xavier montoya",
    "Finalized by xavier montoya",
    "Issued by xavier montoya",
    "Your reply arrives with Jordan",
    "Your response lands with Jordan",
    "Jordan gets the message",
    "Deliver Jordan the message",
    "Bcc: Jordan",
    "Attention Jordan",
    "To: Jordan",
    "Message recipient: Jordan",
    "The addressee is Jordan",
    "Created for Jordan",
    "Authorized by Jordan",
    "Your reply is handled by Jordan",
    "Your reply is read by Jordan",
    "Your reply is reviewed by Jordan",
    "Jordan handles your reply",
    "Verified by xavier montoya",
    "Checked by xavier montoya",
    "Coauthored by xavier montoya",
    "Your response was answered by zora quill",
    "zora quill will receive this message",
    "This message will be prepared by zora quill",
    "Your response shall be answered by zora quill",
    "zora quill is going to receive this message",
    "This message is going to be prepared by zora quill",
)

_SAFE_IDENTITY_RELATIONSHIPS = (
    "Prepared by compliance team",
    "Drafted by legal counsel",
    "Reviewed by product review",
    "Composed by servicing team",
    "Written by compliance team",
    "Your reply goes to Support",
    "Your response is routed to Compliance",
    "The message will be sent to Servicing",
    "Support receives your reply",
    "Reply to Compliance about this offer",
    "Attn: Support",
    "Send this reply to Compliance",
    "Recipient: Support",
    "Support is the recipient",
    "Edited by compliance team",
    "Assembled by product review",
    "Presented by servicing team",
    "Your reply will reach Support",
    "Compliance will get your reply",
    "Send Support this reply",
    "Cc: Compliance",
    "For Support: mortgage review",
    "Prepared for Compliance",
    "Signed by Support",
    "Proofread by compliance team",
    "Finalized by product review",
    "Issued by servicing team",
    "Your reply arrives with Support",
    "Your response lands with Compliance",
    "Support gets the message",
    "Deliver Support the message",
    "Bcc: Support",
    "Attention Compliance",
    "To: Support",
    "Message recipient: Compliance",
    "The addressee is Support",
    "Created for Compliance",
    "Authorized by Support",
    "Reviewed by the operator",
    "Your reply is handled by Support",
    "Your reply is read by Compliance",
    "Your reply is reviewed by Support",
    "Support handles your reply",
    "Verified by compliance team",
    "Checked by product review",
    "Coauthored by servicing team",
    "Your response was answered by support team",
    "Support will receive this message",
    "This message will be prepared by compliance team",
    "Your response shall be answered by support team",
    "support team is going to receive this message",
    "This message is going to be prepared by compliance team",
)

_UNSAFE_CTA_RELATIONSHIPS = (
    "You instructed us to cease telephone contact. Call us to review mortgage options.",
    "You asked us to cease phone contact. Call us to review mortgage options.",
    "You told us no more telephone outreach. Call us to review mortgage options.",
    "Reply YES, even though you said no to being contacted.",
    "Email us, although you said no to being emailed.",
    "Reply YES, after you asked not to be contacted.",
    "Call us, though you objected to being called.",
    "No personnel handle replies. Reply YES to review mortgage options.",
    "No associate handles replies. Reply YES to review mortgage options.",
    "No representative processes responses. Reply YES to review mortgage options.",
    "Reply YES, but nobody will read it.",
    "Reply YES, but nobody can open it.",
    "Reply YES. It will not be read.",
    "No one is there to read responses. Reply YES to review mortgage options.",
    "There is no human to read responses. Reply YES to review mortgage options.",
    "Replies will not be read. Reply YES to review mortgage options.",
    "You requested that we cease email contact. Email us to review options.",
    "You asked for no more phone contact. Call us to review options.",
    "You declined being contacted. Contact us now.",
    "No contractor reads replies. Reply YES to review mortgage options.",
    "No operator handles replies. Reply YES to review mortgage options.",
    "No staffer monitors responses. Reply YES to review mortgage options.",
    "Replies lack an owner. Reply YES to review mortgage options.",
    "Replies have nobody assigned. Reply YES to review mortgage options.",
    "Reply YES, but there is nobody to read it.",
    "Replies won't be read. Reply YES to review mortgage options.",
    "Responses receive no human follow-up. Reply YES to review mortgage options.",
    "You opted not to be contacted. Contact us now.",
    "You turned down being contacted. Contact us now.",
    "No supervisor reads replies. Reply YES to review mortgage options.",
    "No reviewer handles responses. Reply YES to review mortgage options.",
    "Nobody owns replies. Reply YES to review mortgage options.",
    "Replies are ownerless. Reply YES to review mortgage options.",
    "Replies have no assigned staff. Reply YES to review mortgage options.",
    "There isn't anyone to read replies. Reply YES to review mortgage options.",
    "Responses get zero human follow-up. Reply YES to review mortgage options.",
    "You prefer not to be called. Call us to review mortgage options.",
    "You asked us to quit phoning. Call us to review mortgage options.",
    "You refuse telephone contact. Call us to review mortgage options.",
    "Responses are thrown away. Reply YES to review mortgage options.",
    "Discard every reply. Reply YES to review mortgage options.",
    "There is nobody checking replies. Reply YES to review mortgage options.",
    "You withheld consent for texts. Please text us to review mortgage options.",
    "You did not grant permission for calls. Call us to review mortgage options.",
    "You asked not to hear from us again. Contact us to review mortgage options.",
    "Replies are disposed of. Please reply to review mortgage options.",
    "Responses are expunged. Please reply to review mortgage options.",
    "Replies are swept away. Please reply to review mortgage options.",
    "Every response is doomed to remain unread. Reply to review mortgage options.",
    "You have withheld consent for texts. Please text us to review mortgage options.",
    "You have not granted permission for calls. Call us to review mortgage options.",
    "You have asked not to hear from us again. Contact us to review mortgage options.",
    "Replies face disposal. Please reply to review mortgage options.",
    "Replies faced deletion. Please reply to review mortgage options.",
    "Every response is destined to remain unread. Reply to review mortgage options.",
    "Every response is fated to remain unreviewed. Reply to review mortgage options.",
    "Every response was condemned to remain unanswered. Reply to review mortgage options.",
)

_SAFE_CTA_RELATIONSHIPS = (
    "You instructed us to continue telephone contact. Call us to review mortgage options.",
    "You asked us to allow phone contact. Call us to review mortgage options.",
    "You told us telephone outreach remains permitted. Call us to review mortgage options.",
    "You consented to being emailed. Email us to review mortgage options.",
    "Personnel handle replies. Reply YES to review mortgage options.",
    "An associate handles replies. Reply YES to review mortgage options.",
    "A representative processes responses. Reply YES to review mortgage options.",
    "Staff will read replies. Reply YES to review mortgage options.",
    "Replies will be read by staff. Reply YES to review mortgage options.",
    "No associate handles replies, but staff handles them. Reply YES to review mortgage options.",
    "Nobody will read replies at first, but staff will read them. Reply YES to review options.",
    "You requested that we continue email contact. Email us to review options.",
    "You asked us to permit phone contact. Call us to review options.",
    "You accepted being contacted. Contact us now.",
    "A contractor reads replies. Reply YES to review mortgage options.",
    "An operator handles replies. Reply YES to review mortgage options.",
    "A staffer monitors responses. Reply YES to review mortgage options.",
    "Replies have an owner. Reply YES to review mortgage options.",
    "Replies have staff assigned. Reply YES to review mortgage options.",
    "There is staff to read it. Reply YES to review mortgage options.",
    "Replies will be read. Reply YES to review mortgage options.",
    "Responses receive human follow-up. Reply YES to review mortgage options.",
    "You opted to be contacted. Contact us now.",
    "You welcomed being contacted. Contact us now.",
    "A supervisor reads replies. Reply YES to review mortgage options.",
    "A reviewer handles responses. Reply YES to review mortgage options.",
    "Staff owns replies. Reply YES to review mortgage options.",
    "Replies have an owner. Reply YES to review mortgage options.",
    "Replies have assigned staff. Reply YES to review mortgage options.",
    "There is someone to read replies. Reply YES to review mortgage options.",
    "Responses get human follow-up. Reply YES to review mortgage options.",
    "You prefer to be called. Call us to review mortgage options.",
    "You asked us to continue phoning. Call us to review mortgage options.",
    "You accept telephone contact. Call us to review mortgage options.",
    "Responses are handled by staff. Reply YES to review mortgage options.",
    "There is staff checking replies. Reply YES to review mortgage options.",
    "You granted consent for texts. Please text us to review mortgage options.",
    "You granted permission for calls. Call us to review mortgage options.",
    "You asked to hear from us again. Contact us to review mortgage options.",
    "You have granted consent for texts. Please text us to review mortgage options.",
    "You have granted permission for calls. Call us to review mortgage options.",
    "You have asked to hear from us again. Contact us to review mortgage options.",
    "Replies face staff review. Please reply to review mortgage options.",
    (
        "Replies are filtered, then delivered to a staffed reply inbox. "
        "Please reply to discuss your mortgage options."
    ),
)


def _variant(*, subject: str = "Mortgage options", body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_growth_dependencies() -> Iterator[tuple[MagicMock, ...]]:
    """Install inert dependencies so schema rejection precedes side effects."""

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


@pytest.mark.parametrize("copy", _UNSAFE_AUDIENCE_RELATIONSHIPS)
def test_health_relations_reject_every_governed_boundary(copy: str) -> None:
    criterion = audience_admission_criterion(copy.removesuffix("."))
    assert criterion is not None
    assert contains_protected_class_marketing_text(copy)
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(body=f"{copy} Contact us to review mortgage options.")
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(subject=copy, body="Contact us to review mortgage options.")
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
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


@pytest.mark.parametrize("copy", _SAFE_AUDIENCE_RELATIONSHIPS)
def test_reviewed_mortgage_relations_pass_every_governed_boundary(copy: str) -> None:
    assert audience_admission_criterion(copy.removesuffix(".")) is not None
    assert not contains_protected_class_marketing_text(copy)
    assert _variant(subject=copy, body=f"{copy} Contact us to review mortgage options.")
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
    assert build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")[
        "draft_body"
    ] == copy


@pytest.mark.parametrize("relationship", _UNSAFE_IDENTITY_RELATIONSHIPS)
def test_identity_relations_reject_every_governed_boundary(relationship: str) -> None:
    objective = f"{relationship}. Review governed mortgage opportunities."
    body = f"{relationship}. Reply YES to review mortgage options. {_DISCLOSURE.body}"
    assert contains_borrower_copy_contextual_name(relationship)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body=body)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(subject=relationship, body="Reply YES to review mortgage options.")
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        ComposePlanRequest(objective=objective)
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_final_draft_subject(draft_subject=relationship, channel="email")
    with pytest.raises(AuditMetadataValueViolation, match="human-name-shaped"):
        build_safe_audit_metadata({"draft_subject": relationship}, action="outreach.approve")


@pytest.mark.parametrize("relationship", _SAFE_IDENTITY_RELATIONSHIPS)
def test_organization_and_content_destinations_remain_safe(relationship: str) -> None:
    objective = f"{relationship}. Review governed mortgage opportunities."
    body = f"{relationship}. Reply YES to review mortgage options. {_DISCLOSURE.body}"
    assert not contains_borrower_copy_contextual_name(relationship)
    assert _variant(body=body)
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective
    assert (
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
        == body
    )
    assert (
        _assert_final_draft_subject(draft_subject="Review mortgage options", channel="email")
        == "Review mortgage options"
    )
    assert build_safe_audit_metadata(
        {"draft_subject": relationship},
        action="outreach.approve",
    )["draft_subject"] == relationship


@pytest.mark.parametrize("copy", _UNSAFE_CTA_RELATIONSHIPS)
def test_consent_and_dead_response_relations_reject_every_boundary(copy: str) -> None:
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


@pytest.mark.parametrize("copy", _SAFE_CTA_RELATIONSHIPS)
def test_positive_and_staffed_response_controls_remain_safe(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert not contains_borrower_cta_contradiction(copy)
    assert _variant(body=copy).body == copy
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy
    body = f"{copy} {_DISCLOSURE.body}"
    assert (
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
        == body
    )
    assert _assert_final_draft_subject(draft_subject=copy, channel="email") == copy
    assert build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")[
        "draft_body"
    ] == copy


def test_staffed_follow_up_reconciles_contracted_unread_state() -> None:
    copy = "Replies won't be read automatically, but staff will read them."

    assert negative_borrower_cta_evidence(copy) == []
    assert not contains_borrower_cta_contradiction(f"{copy} Reply YES.")


@pytest.mark.parametrize(
    "objective",
    (
        _UNSAFE_AUDIENCE_RELATIONSHIPS[4],
        f"{_UNSAFE_IDENTITY_RELATIONSHIPS[-1]}. Review governed mortgage opportunities.",
        _UNSAFE_CTA_RELATIONSHIPS[7],
    ),
)
def test_each_relationship_family_stops_before_planners_and_writes(
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
