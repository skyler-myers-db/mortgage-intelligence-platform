from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.schemas.lead import LeadSummary
from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachDraftRequest,
    OutreachRejectRequest,
)
from backend.schemas.portfolio import (
    CampaignStatusPatchRequest,
    CampaignSummary,
    PortfolioCreateRequest,
)
from backend.services.audit_store import AuditMetadataValueViolation
from backend.services.disclosures import MissingTenantDisclosureError, resolve_tenant_disclosure
from backend.services.repositories import (
    get_lead_repository,
    get_outreach_repository,
    get_portfolio_repository,
)
from tests.fixtures import mock_population
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore


class _SingleBorrowerOutreachRepo:
    def __init__(self, borrower: Any) -> None:
        self.borrower = borrower

    def find_borrower(self, borrower_id: str) -> Any | None:
        if borrower_id == self.borrower.borrower_id:
            return self.borrower
        return None


class _CaptureLeadRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> list[LeadSummary]:
        self.calls.append(kwargs)
        return []

    def count(self, **kwargs: Any) -> int:
        self.calls.append({"count": kwargs})
        return 0


class _OtherOwnerCampaignRepo:
    def preview(self, request: Any) -> Any:
        raise AssertionError("not used")

    def create(self, payload: Any, *, actor: str | None = None) -> Any:
        raise AssertionError("not used")

    def list_campaigns(
        self,
        *,
        owner_email: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> Any:
        _ = (owner_email, status, limit)
        return {"campaigns": []}

    def get(self, portfolio_id: str) -> dict[str, object]:
        return CampaignSummary(
            campaign_id=portfolio_id,
            name="Other owner campaign",
            owner_email="other@example.com",
            status="draft",
            criteria={"marketing_eligibility": "Eligible only"},
            suppression_policy={"default": "eligible_only"},
        ).model_dump()

    def patch_status(self, portfolio_id: str, payload: Any, *, actor: str | None = None) -> Any:
        _ = actor
        return CampaignSummary(**self.get(portfolio_id)).model_copy(update={"status": payload.status})


def _with_outreach_repo(repo: Any):
    prior = app.dependency_overrides.get(get_outreach_repository)
    app.dependency_overrides[get_outreach_repository] = lambda: repo
    return prior


def _restore_override(key: Any, prior: Any) -> None:
    if prior is None:
        app.dependency_overrides.pop(key, None)
    else:
        app.dependency_overrides[key] = prior


def test_outreach_draft_blocks_opt_out_borrower_before_copy_generation() -> None:
    borrower = mock_population.BORROWERS[0].model_copy(
        update={
            "marketing_eligible": False,
            "consent_status": "opt_out",
            "suppression_reason": "do_not_contact",
        },
    )
    prior = _with_outreach_repo(_SingleBorrowerOutreachRepo(borrower))
    try:
        response = TestClient(app).post(
            "/api/outreach/draft",
            json={"borrower_id": borrower.borrower_id, "channel": "email"},
        )
    finally:
        _restore_override(get_outreach_repository, prior)

    assert response.status_code == 422
    assert "not marketing-eligible" in response.json()["detail"]
    assert "opt_out" in response.json()["detail"]


def test_outreach_draft_blocks_recent_touch_frequency_cap() -> None:
    borrower = mock_population.BORROWERS[0].model_copy(
        update={
            "marketing_eligible": True,
            "consent_status": "opt_in",
            "suppression_reason": None,
            "last_touch_at": datetime.now(UTC) - timedelta(days=3),
        },
    )
    prior = _with_outreach_repo(_SingleBorrowerOutreachRepo(borrower))
    try:
        response = TestClient(app).post(
            "/api/outreach/draft",
            json={"borrower_id": borrower.borrower_id, "channel": "email"},
        )
    finally:
        _restore_override(get_outreach_repository, prior)

    assert response.status_code == 409
    assert "frequency cap" in response.json()["detail"]
    assert "earliest re-contact" in response.json()["detail"]


def test_outreach_draft_returns_configured_disclosure_not_placeholder() -> None:
    response = TestClient(app).post(
        "/api/outreach/draft",
        json={"borrower_id": "B-48291", "channel": "email"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["disclosure_version"] == "test-disclosure-v1"
    assert body["disclosure_state"]
    assert body["marketing_eligible"] is True
    assert "NMLS #123456" in body["body"]
    assert "Equal Housing" in body["body"]
    assert "Insert governed" not in body["body"]


@pytest.mark.parametrize(
    ("channel", "body"),
    [
        ("email", "Insert governed lender, NMLS, licensing, and Equal Housing disclosures."),
        ("email", "Summit Mortgage, Equal Housing Lender. Reply unsubscribe to opt out."),
        ("email", "Summit Mortgage, NMLS #123456. Reply unsubscribe to opt out."),
        ("sms", "Summit Mortgage NMLS #123456. Equal Housing Lender. Reply YES."),
        ("sms", "Summit Mortgage NMLS #123456. Equal Housing Lender. Reply 555-867-5309."),
    ],
)
def test_disclosure_resolver_rejects_unpublishable_active_rows(channel: str, body: str) -> None:
    lakebase = MagicMock()
    lakebase.fetchone.return_value = {
        "state": "_ALL",
        "channel": channel,
        "disclosure_version": "bad-disclosure",
        "body": body,
    }

    with pytest.raises(MissingTenantDisclosureError):
        resolve_tenant_disclosure(lakebase, state="IL", channel=channel)


def test_outreach_approve_requires_disclosure_backed_draft_body() -> None:
    client = TestClient(app)
    disclosure = "Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."

    missing = client.post(
        "/api/outreach/approve",
        json={"borrower_id": "B-48291", "offer_code": "refi_plus_heloc"},
    )
    assert missing.status_code == 422
    assert "draft_body is required" in missing.json()["detail"]

    no_disclosure = client.post(
        "/api/outreach/approve",
        json={"borrower_id": "B-48291", "draft_body": "Governed approval body."},
    )
    assert no_disclosure.status_code == 422
    assert "tenant disclosure" in no_disclosure.json()["detail"]

    placeholder = client.post(
        "/api/outreach/approve",
        json={"borrower_id": "B-48291", "draft_body": f"Hi [first name]. {disclosure}"},
    )
    assert placeholder.status_code == 422
    assert "placeholder" in placeholder.json()["detail"]


def test_outreach_campaign_metadata_ids_are_public_safe() -> None:
    assert (
        OutreachDraftRequest(
            borrower_id="B-48291",
            variant_name="Refi Pilot A",
        ).variant_name
        == "Refi Pilot A"
    )
    with pytest.raises(ValidationError, match="id must not contain"):
        OutreachDraftRequest(
            borrower_id="B-48291",
            campaign_id="jane@example.com",
        )
    with pytest.raises(ValidationError, match="variant_name"):
        OutreachDraftRequest(
            borrower_id="B-48291",
            variant_name="Call 212-555-1212",
        )
    with pytest.raises(ValidationError, match="human-name-shaped"):
        OutreachDraftRequest(
            borrower_id="B-48291",
            variant_name="Jane Smith",
        )
    with pytest.raises(ValidationError, match="variant_name"):
        OutreachApproveRequest(
            borrower_id="B-48291",
            campaign_id="11111111-1111-4111-8111-111111111111",
            variant_name="[first name]",
        )
    with pytest.raises(ValidationError, match="human-name-shaped"):
        OutreachApproveRequest(
            borrower_id="B-48291",
            draft_body="Reviewed body",
            variant_name="Jane Smith",
        )
    with pytest.raises(ValidationError, match="human-name-shaped"):
        OutreachRejectRequest(
            borrower_id="B-48291",
            rationale_code="low_intent",
            variant_name="Jane Smith",
        )


def test_audit_store_rejects_pii_like_campaign_and_variant_metadata() -> None:
    store = InMemoryAuditStore()
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="draft_outreach",
            entity_type="outreach_draft",
            entity_id="B-48291",
            payload_json={"campaign_id": "jane@example.com"},
        )
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="draft_outreach",
            entity_type="outreach_draft",
            entity_id="B-48291",
            payload_json={"variant_name": "Call 212-555-1212"},
        )
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="draft_outreach",
            entity_type="outreach_draft",
            entity_id="B-48291",
            payload_json={"variant_name": "Jane Smith"},
        )


def test_leads_default_to_eligible_only_contactability() -> None:
    repo = _CaptureLeadRepo()
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: repo
    try:
        response = TestClient(app).get("/api/leads")
    finally:
        _restore_override(get_lead_repository, prior)

    assert response.status_code == 200, response.text
    criteria = repo.calls[0]["portfolio_criteria"]
    assert criteria.marketing_eligibility == "Eligible only"


def test_leads_suppression_override_requires_admin_group() -> None:
    response = TestClient(app).get(
        "/api/leads?marketing_eligibility=Any",
        headers={"X-Forwarded-Groups": ""},
    )

    assert response.status_code == 403


def test_campaigns_can_be_listed_and_status_updated() -> None:
    client = TestClient(app)
    campaign_id = "11111111-1111-4111-8111-111111111111"

    listed = client.get("/api/campaigns")
    assert listed.status_code == 200, listed.text
    assert listed.json()["campaigns"]

    patched = client.patch(f"/api/campaigns/{campaign_id}", json={"status": "pending_review"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "pending_review"


def test_campaign_create_metadata_rejects_pii_and_bad_send_windows() -> None:
    with pytest.raises(ValidationError, match="variant subject"):
        PortfolioCreateRequest(
            name="Q3 CA recapture",
            message_variants=[
                {
                    "variant_name": "A",
                    "channel": "email",
                    "subject": "Call me at 555-867-5309",
                    "body": "Governed copy",
                    "weight_pct": 50,
                }
            ],
        )

    with pytest.raises(ValidationError, match="human-name-shaped"):
        CampaignStatusPatchRequest(status="pending_review", rationale="Reviewed by Jane Smith")

    with pytest.raises(ValidationError, match="send_window start_local"):
        PortfolioCreateRequest(
            name="Q3 CA recapture",
            send_window={
                "days": ["Tuesday"],
                "timezone": "borrower_local",
                "start_local": "16:00",
                "end_local": "09:00",
            },
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"suppression_policy": {"borrower_name": "Jane Smith"}},
        {"suppression_policy": {"customer_name": "Jane Smith"}},
        {"roi_assumptions": {"notes": "Jane Smith approved this"}},
        {
            "message_variants": [
                {
                    "variant_name": "Jane Smith",
                    "channel": "email",
                    "subject": "Summit Mortgage review",
                    "body": "Governed copy",
                    "weight_pct": 50,
                }
            ]
        },
    ],
)
def test_campaign_create_rejects_name_bearing_metadata(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="PII|human-name-shaped"):
        PortfolioCreateRequest(name="Q3 CA recapture", **kwargs)


def test_campaign_create_accepts_marketing_controls() -> None:
    payload = PortfolioCreateRequest(
        name="Q3 CA recapture",
        criteria={
            "states": ["CA"],
            "marketing_eligibility": "eligible_only",
            "consent_status": "opt_in",
            "recency": "untouched_30d",
        },
        suppression_policy={"default": "eligible_only", "frequency_cap_days": 30},
        message_variants=[
            {
                "variant_name": "A",
                "channel": "email",
                "subject": "Summit Mortgage review for your current loan options",
                "body": "Review current mortgage fit using the governed relationship-aware template.",
                "weight_pct": 45,
            }
        ],
        channel_cascade=[
            {"channel": "email", "step": 1},
            {"channel": "sms", "step": 2, "after_days": 3},
            {"channel": "direct_mail", "step": 3, "after_days": 10},
        ],
        send_window={
            "days": ["Tue", "Wed", "Thu"],
            "timezone": "borrower_local",
            "start": "09:00",
            "end": "16:00",
        },
        holdout={"method": "hash_modulo", "size_pct": 10},
        roi_assumptions={
            "budget_usd": 25000,
            "cost_per_contact_usd": {"email": 1.2, "sms": 0.08, "direct_mail": 0.86},
        },
    )

    assert payload.message_variants[0]["variant_name"] == "A"
    assert payload.criteria.marketing_eligibility == "Eligible only"
    assert payload.criteria.consent_status == "Opt-in"
    assert payload.criteria.recency == "Untouched 30d"
    assert payload.send_window["days"] == ["Tuesday", "Wednesday", "Thursday"]
    assert payload.send_window["start_local"] == "09:00"
    assert payload.channel_cascade[2]["channel"] == "direct_mail"
    assert payload.holdout == {"method": "hash_modulo", "size_pct": 10.0}


def test_campaign_owner_filters_are_fail_closed_for_non_admin() -> None:
    client = TestClient(app)
    response = client.get(
        "/api/campaigns?owner_email=other@example.com",
        headers={
            "X-Forwarded-Email": "analyst@example.com",
            "X-Forwarded-Groups": "",
        },
    )
    assert response.status_code == 403


def test_campaign_detail_is_hidden_from_non_owner_non_admin() -> None:
    prior = app.dependency_overrides.get(get_portfolio_repository)
    app.dependency_overrides[get_portfolio_repository] = lambda: _OtherOwnerCampaignRepo()
    try:
        response = TestClient(app).get(
            "/api/campaigns/11111111-1111-4111-8111-111111111111",
            headers={
                "X-Forwarded-Email": "analyst@example.com",
                "X-Forwarded-Groups": "",
            },
        )
    finally:
        _restore_override(get_portfolio_repository, prior)

    assert response.status_code == 404
