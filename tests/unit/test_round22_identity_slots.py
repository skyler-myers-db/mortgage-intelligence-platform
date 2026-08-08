"""Round 22 structural lowercase borrower identity-slot regressions."""

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

_DISCLOSURE = (
    "Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out."
)
_IDENTITY_SLOTS = (
    "Subject: siobhan quigley",
    "Regarding: siobhan quigley",
    "Reference: siobhan quigley",
    "Addressee siobhan quigley",
    "Copy to siobhan quigley",
    "Courtesy copy: siobhan quigley",
    "Addressed to siobhan quigley",
    "Intended recipient siobhan quigley",
    "Notification recipient siobhan quigley",
    "Please deliver to siobhan quigley",
    "For delivery to siobhan quigley",
    # Morphological cousins exercise grammar categories, not one-off strings.
    "Concerning xochitl quintero",
    "Carbon copies to xochitl quintero",
    "Copied for xochitl quintero",
    "Designated addressee xochitl quintero",
    "Directed to xochitl quintero",
    "Distribution for xochitl quintero",
    "Please forward this notice to xochitl quintero",
)
_SAFE_SLOTS = (
    "Subject: mortgage options",
    "Regarding compliance review",
    "Reference: product review",
    "Addressee servicing team",
    "Copy to servicing team",
    "Courtesy copy: compliance review",
    "Addressed to branch manager",
    "Intended recipient product review",
    "Notification recipient servicing team",
    "Please deliver to servicing team",
    "For delivery to product review",
    "Subject: Summit Mortgage",
    "Subject: summit mortgage",
)


def _variant(*, subject: str = "Mortgage options review", body: str) -> object:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.fixture
def isolated_audit_store() -> Iterator[InMemoryAuditStore]:
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


@pytest.mark.parametrize("identity_slot", _IDENTITY_SLOTS)
def test_round22_identity_slots_fail_all_shared_text_boundaries(identity_slot: str) -> None:
    objective = f"{identity_slot}. Build a custom cohort for refi signals."
    assert contains_borrower_copy_contextual_name(identity_slot)

    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body=f"{identity_slot}. Reply YES to review mortgage options.")
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(subject=identity_slot, body="Reply YES to review mortgage options.")
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=objective)
    with pytest.raises(AuditMetadataValueViolation, match="human-name-shaped"):
        build_safe_audit_metadata({"draft_subject": identity_slot}, action="outreach.approve")


@pytest.mark.parametrize("safe_slot", _SAFE_SLOTS)
def test_round22_reviewed_business_and_public_slots_remain_safe(safe_slot: str) -> None:
    objective = f"{safe_slot}. Build a custom cohort for refi signals."
    assert not contains_borrower_copy_contextual_name(safe_slot)
    assert _variant(body=f"{safe_slot}. Reply YES to review mortgage options.")
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective
    assert (
        build_safe_audit_metadata({"draft_subject": safe_slot}, action="outreach.approve")[
            "draft_subject"
        ]
        == safe_slot
    )


@pytest.mark.parametrize("identity_slot", _IDENTITY_SLOTS[:11])
@pytest.mark.parametrize("unsafe_field", ("draft_body", "draft_subject"))
def test_round22_final_approval_fails_before_any_write(
    identity_slot: str,
    unsafe_field: str,
    fake_lakebase_client,
    isolated_audit_store: InMemoryAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outreach_mod, "ensure_approval_idempotency_column", lambda lakebase: None)
    monkeypatch.setattr(outreach_mod, "ensure_approval_followup_columns", lambda lakebase: None)
    payload = {
        "borrower_id": "B-48291",
        "draft_subject": "Mortgage options review",
        "draft_body": f"Reply YES to review mortgage options. {_DISCLOSURE}",
    }
    payload[unsafe_field] = (
        f"{identity_slot}. {_DISCLOSURE}" if unsafe_field == "draft_body" else identity_slot
    )

    response = TestClient(app).post("/api/outreach/approve", json=payload)

    assert response.status_code == 422, response.text
    assert "human-name-shaped" in response.json()["detail"]
    assert fake_lakebase_client.executes == []
    assert fake_lakebase_client.approvals == []
    assert fake_lakebase_client.audit_events == []
    assert isolated_audit_store.list(limit=5) == []


@pytest.mark.parametrize("safe_slot", _SAFE_SLOTS)
def test_round22_final_copy_validator_preserves_safe_slots(safe_slot: str) -> None:
    body = f"{safe_slot}. Reply YES to review mortgage options. {_DISCLOSURE}"
    assert (
        outreach_mod._assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=SimpleNamespace(body=_DISCLOSURE),
            channel="email",
        )
        == body
    )
    assert (
        outreach_mod._assert_final_draft_subject(
            draft_subject=safe_slot,
            channel="email",
        )
        == safe_slot
    )


@pytest.mark.parametrize(
    "safe_transport_copy",
    (
        "The gateway encrypts incoming email before an employee opens it.",
        "The gateway compresses incoming email before an agent reads it.",
    ),
)
def test_round22_incoming_email_transport_is_not_misread_as_a_person(
    safe_transport_copy: str,
) -> None:
    body = f"{safe_transport_copy} Reply YES to review mortgage options."
    assert not contains_borrower_copy_contextual_name(safe_transport_copy)
    assert _variant(body=body)
