"""Round 23 consent-prohibition and stored-response governance proof."""

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
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
    get_audit_store,
)
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. "
    "Reply unsubscribe to opt out."
)

_WITHDRAWAL_FACT_AND_CTA = (
    ("the borrower prohibits further calls.", "Call us to review options.", "call"),
    ("the customer forbade email.", "Email us to review options.", "email"),
    (
        "the applicant barred our team from calling.",
        "Call us to review options.",
        "call",
    ),
    (
        "the homeowner disallows correspondence.",
        "Contact us to review options.",
        "contact",
    ),
    (
        "the recipient forbids messaging.",
        "Reply YES to review options.",
        "message",
    ),
    (
        "the client explicitly retracted consent to calls.",
        "Call us to review options.",
        "call",
    ),
    (
        "the prospect repudiates email authorization.",
        "Email us to review options.",
        "email",
    ),
    (
        "the borrower has disavowed their permission for texts.",
        "Text us to review options.",
        "text",
    ),
    (
        "the customer disavowed authorization.",
        "Contact us to review options.",
        "contact",
    ),
    ("the applicant refuses to be called.", "Call us to review options.", "call"),
    (
        "the homeowner refuses to be messaged.",
        "Reply YES to review options.",
        "message",
    ),
    ("the recipient insists on no more emails.", "Email us to review options.", "email"),
    (
        "the client insisted on no further outreach.",
        "Contact us to review options.",
        "contact",
    ),
)

_DEAD_RESPONSE_FACT_AND_CTA = (
    (
        "replies are retained without anyone reading them.",
        "Reply YES to review options.",
        "reply",
    ),
    (
        "incoming emails were archived without staff reviewing them.",
        "Email us to review options.",
        "email",
    ),
    (
        "messages remain queued unseen by humans.",
        "Reply YES to review options.",
        "reply",
    ),
    (
        "incoming texts are stored with no human review.",
        "Text us to review options.",
        "text",
    ),
    ("responses are filed away.", "Reply YES to review options.", "reply"),
    ("incoming SMS will be archived forever.", "Reply YES to review options.", "reply"),
    (
        "replies languish permanently.",
        "Reply YES to review options.",
        "reply",
    ),
    (
        "incoming responses sit unreviewed.",
        "Reply YES to review options.",
        "reply",
    ),
)

_UNSAFE_FACT_AND_CTA = (*_WITHDRAWAL_FACT_AND_CTA, *_DEAD_RESPONSE_FACT_AND_CTA)
_UNSAFE_COPY = tuple(
    copy
    for fact, cta, _action in _UNSAFE_FACT_AND_CTA
    for copy in (f"{fact} {cta}", f"{cta} {fact}")
)

_SAFE_DETECTOR_CAMPAIGN_COPY = (
    "The borrower prohibits an automatic debit. Call us to review options.",
    "The applicant barred an unreviewed fee. Contact us to review options.",
    "The homeowner retracted a prior offer. Email us to review options.",
)

_SAFE_PUBLIC_COPY = (
    "Replies are retained for 30 days before compliance reviews them. Reply YES.",
    "incoming responses sit in a monitored queue and staff review them. Reply YES.",
    "Messages are filed away and then compliance reviews them. Reply YES.",
)

_UNRECOVERED_RESPONSE_FACTS = (
    "The gateway zorbifies incoming email before support monitors it.",
    "The gateway deletes incoming email before support monitors it.",
    "Incoming email sits unseen by staff then support reviews SMS.",
    "Replies are filed away. Compliance reviews them later.",
)


def _variant(*, subject: str = "Mortgage options", body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize(("fact", "cta", "action"), _UNSAFE_FACT_AND_CTA)
def test_round23_negative_evidence_has_canonical_conflicting_action(
    fact: str,
    cta: str,
    action: str,
) -> None:
    body = f"{fact} {cta}"
    negatives = negative_borrower_cta_evidence(body)
    positives = explicit_borrower_contact_actions(body)

    assert negatives
    assert positives
    assert any(
        action
        in negative_actions_for_positive(
            body,
            negative_match=negative,
            positive_match=positive,
        )
        for negative in negatives
        for positive, _positive_actions in positives
    )


@pytest.mark.parametrize("copy", _UNSAFE_COPY)
def test_round23_structural_families_reject_campaign_growth_and_compose(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        ComposePlanRequest(objective=copy)


@pytest.mark.parametrize("copy", _UNSAFE_COPY)
def test_round23_structural_families_reject_final_body_and_subject(copy: str) -> None:
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


@pytest.mark.parametrize("copy", _SAFE_DETECTOR_CAMPAIGN_COPY)
def test_round23_non_channel_predicates_remain_safe_for_campaign_copy(
    copy: str,
) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert _variant(body=copy).body == copy


@pytest.mark.parametrize("copy", _SAFE_PUBLIC_COPY)
def test_round23_staffed_same_clause_controls_remain_safe_at_public_boundaries(
    copy: str,
) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert _variant(body=copy).body == copy
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy


@pytest.mark.parametrize("fact", _UNRECOVERED_RESPONSE_FACTS)
def test_round23_unknown_destructive_mismatched_and_cross_clause_sinks_fail_closed(
    fact: str,
) -> None:
    assert negative_borrower_cta_evidence(fact)


@pytest.mark.parametrize("field", ("draft_body", "draft_subject"))
def test_round23_audit_metadata_rejects_contradictory_borrower_copy(field: str) -> None:
    with pytest.raises(AuditMetadataValueViolation, match="contradicts consent"):
        build_safe_audit_metadata(
            {field: "Borrower refuses to be called. Call us to review options."},
            action="outreach.approve",
        )


def test_round23_audit_metadata_preserves_explicit_channel_replacement() -> None:
    copy = "The borrower email opt-out is on file and instead call them."

    metadata = build_safe_audit_metadata(
        {"draft_body": copy},
        action="outreach.approve",
    )

    assert metadata["draft_body"] == copy


@pytest.mark.parametrize(
    "objective",
    (
        "the applicant barred our team from calling. Call us to review options.",
        "Reply YES. the homeowner refuses to be messaged.",
        "responses are filed away. Reply YES to review options.",
        "Email us to review options. incoming email sits unreviewed forever.",
    ),
)
def test_round23_growth_endpoints_stop_before_planners_models_or_writes(
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

    expected = "prompt must use reviewed, non-PII mortgage-growth criteria"
    assert run_response.status_code == 422, run_response.text
    assert compose_response.status_code == 422, compose_response.text
    assert expected in run_response.text
    assert expected in compose_response.text
    assert objective not in run_response.text
    assert objective not in compose_response.text
    run_planner.assert_not_called()
    compose_planner.assert_not_called()
    assert sql.mock_calls == []
    assert lakebase.mock_calls == []
    assert audit_store.mock_calls == []
