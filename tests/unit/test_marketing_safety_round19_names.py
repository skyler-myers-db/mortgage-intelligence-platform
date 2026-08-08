"""Round 19 lowercase borrower-identity relation regressions."""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api import outreach as outreach_mod
from backend.main import app
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
    get_audit_store,
)
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.growth_refusal_contract import GROWTH_REFUSAL_MESSAGE_RE

_DISCLOSURE_BODY = (
    "Summit Mortgage, NMLS #123456. Equal Housing Lender. "
    "Reply unsubscribe to opt out."
)

_REPORTED_BYPASSES = (
    "Recipient: siobhan quigley. Reply YES to review available mortgage options.",
    "Send xochitl quintero this offer. Reply YES to review available mortgage options.",
    (
        "This copy names siobhan quigley as the borrower. "
        "Reply YES to review available mortgage options."
    ),
)

_CORRESPONDENCE_HEADER_IDENTITIES = (
    "Attn: siobhan quigley",
    "To: xochitl quintero",
    "For the attention of siobhan quigley",
    "FAO: siobhan quigley",
    "Cc: siobhan quigley",
    "Bcc: siobhan quigley",
    "Care of siobhan quigley",
    "c/o siobhan quigley",
    "Attention siobhan quigley",
    "CC siobhan quigley",
    "SMS recipient siobhan quigley",
    "Correspondence to siobhan quigley",
    "RE: siobhan quigley",
    "c/o: siobhan quigley",
    "deliver correspondence to siobhan quigley",
)

_SAFE_CORRESPONDENCE_ADDRESSEES = (
    "Attn: servicing team",
    "To: product review",
    "FAO: servicing team",
    "Cc: product review",
    "care of branch manager",
    "Attention servicing team",
    "CC product review",
    "SMS recipient servicing team",
    "Correspondence to product review",
    "RE: mortgage options",
    "c/o: branch manager",
    "deliver correspondence to servicing team",
)

_UNSEEN_IDENTITY_RELATIONS = (
    "Recipient name: aveline maris quade",
    "Email iolanthe zvonarek this notice",
    "This message identifies saoirse nkomo as the applicant",
    "Borrower identity — temilade okafor",
    "Dispatch nerys alouette this communication",
    "This notice records mireille sona adeyemi as the beneficiary",
    "Offer recipient siobhan quigley",
    "Please contact siobhan quigley with this offer",
    "The borrower goes by siobhan quigley",
    "Applicant known as xochitl quintero",
    "Dear aveline maris quade",
)

_SAFE_BUSINESS_RELATIONS = (
    *_SAFE_CORRESPONDENCE_ADDRESSEES,
    "Recipient: servicing team",
    "Borrower: current customer",
    "Applicant: eligible borrower",
    "Send servicing team this offer",
    "Send branch manager this notice",
    "Show product review this message",
    "This copy names servicing team as the recipient",
    "Record branch manager as the applicant",
)


def _variant(*, subject: str, body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_audit_store() -> Iterator[InMemoryAuditStore]:
    """Expose an empty ledger without mutating the session fixture permanently."""

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


@pytest.mark.parametrize("unsafe_copy", _REPORTED_BYPASSES)
def test_round19_reported_lowercase_names_fail_campaign_subject_and_body(
    unsafe_copy: str,
) -> None:
    assert contains_borrower_copy_contextual_name(unsafe_copy)

    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(
            subject="Mortgage options review",
            body=unsafe_copy,
        )
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(
            subject=unsafe_copy,
            body="Reply YES to review available mortgage options.",
        )


@pytest.mark.parametrize("identity_relation", _UNSEEN_IDENTITY_RELATIONS)
def test_round19_unseen_identity_relations_are_structurally_detected(
    identity_relation: str,
) -> None:
    assert contains_borrower_copy_contextual_name(identity_relation)

    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(
            subject="Mortgage options review",
            body=f"{identity_relation}. Reply YES to review available mortgage options.",
        )


@pytest.mark.parametrize("safe_copy", _SAFE_BUSINESS_RELATIONS)
def test_round19_business_roles_and_review_phrases_remain_safe(safe_copy: str) -> None:
    assert not contains_borrower_copy_contextual_name(safe_copy)

    variant = _variant(
        subject=safe_copy,
        body=f"{safe_copy}. Reply YES to review available mortgage options.",
    )
    assert variant.subject == safe_copy


@pytest.mark.parametrize("identity_header", _CORRESPONDENCE_HEADER_IDENTITIES)
def test_round20_correspondence_headers_fail_campaign_and_growth_schemas(
    identity_header: str,
) -> None:
    objective = f"{identity_header}. Build a custom cohort for refi signals."
    assert contains_borrower_copy_contextual_name(objective)

    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(
            subject="Mortgage options review",
            body=f"{identity_header}. Reply YES to review available mortgage options.",
        )
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(
            subject=identity_header,
            body="Reply YES to review available mortgage options.",
        )
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize(
    "objective",
    tuple(
        f"{safe_addressee}. Build a custom cohort for refi signals."
        for safe_addressee in _SAFE_CORRESPONDENCE_ADDRESSEES
    ),
)
def test_round20_growth_schemas_preserve_business_headers(
    objective: str,
) -> None:
    assert not contains_borrower_copy_contextual_name(objective)
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective


@pytest.mark.parametrize(
    "ordinary_text",
    (
        "Send this offer to product review.",
        "Route the notice to servicing team.",
        "Build a custom cohort for refi signals in Illinois.",
        "Attention is required before product review.",
        "CC is enabled for the servicing team.",
        (
            "Inbound SMS is replaced before staff review then routed to a staffed SMS queue. "
            "Text us to review options."
        ),
        (
            "provider authenticates inbound SMS before staff reads it. "
            "Text us to review options."
        ),
    ),
)
def test_round20_detector_preserves_ordinary_prepositional_text(
    ordinary_text: str,
) -> None:
    assert not contains_borrower_copy_contextual_name(ordinary_text)


@pytest.mark.parametrize("safe_header", _SAFE_CORRESPONDENCE_ADDRESSEES)
def test_round20_final_outreach_validators_preserve_business_headers(
    safe_header: str,
) -> None:
    body = (
        f"{safe_header}. Reply YES to review available mortgage options. "
        f"{_DISCLOSURE_BODY}"
    )

    assert outreach_mod._assert_disclosure_backed_draft_body(
        draft_body=body,
        disclosure=SimpleNamespace(body=_DISCLOSURE_BODY),
        channel="email",
    ) == body
    assert (
        outreach_mod._assert_final_draft_subject(
            draft_subject=safe_header,
            channel="email",
        )
        == safe_header
    )


@pytest.mark.parametrize("identity_header", _CORRESPONDENCE_HEADER_IDENTITIES)
@pytest.mark.parametrize(
    "free_text_field",
    ("draft_body", "draft_subject", "rationale", "bulk_rationale", "reason", "notes"),
)
def test_round20_audit_metadata_rejects_correspondence_header_names(
    identity_header: str,
    free_text_field: str,
) -> None:
    with pytest.raises(AuditMetadataValueViolation, match="human-name-shaped"):
        build_safe_audit_metadata(
            {free_text_field: identity_header},
            action="outreach.approve",
        )


@pytest.mark.parametrize(
    "safe_header",
    _SAFE_CORRESPONDENCE_ADDRESSEES,
)
@pytest.mark.parametrize(
    "free_text_field",
    ("draft_body", "draft_subject", "rationale", "bulk_rationale", "reason", "notes"),
)
def test_round20_audit_metadata_preserves_reviewed_business_headers(
    safe_header: str,
    free_text_field: str,
) -> None:
    metadata = build_safe_audit_metadata(
        {free_text_field: safe_header},
        action="outreach.approve",
    )

    assert metadata[free_text_field] == safe_header


@pytest.mark.parametrize("identity_header", _CORRESPONDENCE_HEADER_IDENTITIES)
def test_round20_growth_audit_details_reject_correspondence_header_names(
    identity_header: str,
) -> None:
    with pytest.raises(AuditMetadataValueViolation, match="PII-shaped"):
        build_safe_audit_metadata(
            {
                "tool_steps": [
                    {
                        "label": "Apply governed screen",
                        "status": "blocked",
                        "detail": identity_header,
                    }
                ]
            },
            action="growth_agent.run",
        )


@pytest.mark.parametrize("safe_header", _SAFE_CORRESPONDENCE_ADDRESSEES)
def test_round20_growth_audit_details_preserve_reviewed_business_headers(
    safe_header: str,
) -> None:
    metadata = build_safe_audit_metadata(
        {
            "tool_steps": [
                {
                    "label": "Apply governed screen",
                    "status": "completed",
                    "detail": safe_header,
                }
            ]
        },
        action="growth_agent.run",
    )

    assert metadata["tool_steps"][0]["detail"] == safe_header


@pytest.mark.parametrize(
    "unsafe_copy",
    (*_REPORTED_BYPASSES, *_CORRESPONDENCE_HEADER_IDENTITIES),
)
@pytest.mark.parametrize("unsafe_field", ("draft_body", "draft_subject"))
def test_round19_final_approval_rejects_names_before_lakebase_or_audit_write(
    unsafe_copy: str,
    unsafe_field: str,
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
    payload = {
        "borrower_id": "B-48291",
        "draft_subject": "Mortgage options review",
        "draft_body": f"Reply YES to review available mortgage options. {_DISCLOSURE_BODY}",
    }
    if unsafe_field == "draft_body":
        payload[unsafe_field] = f"{unsafe_copy} {_DISCLOSURE_BODY}"
    else:
        payload[unsafe_field] = unsafe_copy

    response = TestClient(app).post("/api/outreach/approve", json=payload)

    assert response.status_code == 422, response.text
    assert "human-name-shaped" in response.json()["detail"]
    assert fake_lakebase_client.executes == []
    assert fake_lakebase_client.approvals == []
    assert fake_lakebase_client.audit_events == []
    assert isolated_audit_store.list(limit=5) == []
