from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.schemas.campaign_status import (
    CampaignTransitionEvidence,
    validate_campaign_status_transition,
)
from backend.schemas.portfolio import CampaignStatusPatchRequest, CampaignSummary
from backend.services.repositories import get_portfolio_repository

client = TestClient(app)


class _GovernedTransitionRepo:
    def __init__(
        self,
        *,
        owner_email: str,
        current_status: str = "pending_review",
        treatment_state: str = "ready",
        validation_actor: str | None = None,
        validation_now: datetime | None = None,
    ) -> None:
        self.owner_email = owner_email
        self.current_status = current_status
        self.treatment_state = treatment_state
        self.validation_actor = validation_actor
        self.validation_now = validation_now
        self.patch_calls = 0
        self.evidence: CampaignTransitionEvidence | None = None

    def _campaign(self, campaign_id: str, *, status: str | None = None) -> CampaignSummary:
        return CampaignSummary(
            campaign_id=campaign_id,
            name="Governed compatibility campaign",
            owner_email=self.owner_email,
            status=status or self.current_status,  # type: ignore[arg-type]
            treatment_state=self.treatment_state,  # type: ignore[arg-type]
            criteria={"marketing_eligibility": "Eligible only"},
            suppression_policy={"default": "eligible_only"},
            message_variants=[],
            channel_cascade=[],
            send_window={},
        )

    def get(self, campaign_id: str) -> dict[str, object]:
        return self._campaign(campaign_id).model_dump()

    def patch_status(
        self,
        campaign_id: str,
        payload: CampaignStatusPatchRequest,
        *,
        actor: str | None = None,
    ) -> CampaignSummary:
        self.patch_calls += 1
        self.evidence = validate_campaign_status_transition(
            payload,
            campaign_id=campaign_id,
            current_status=self.current_status,
            actor=self.validation_actor or actor or "",
            now=self.validation_now,
        )
        return self._campaign(campaign_id, status=payload.status)


def _headers(actor: str) -> dict[str, str]:
    return {
        "X-Forwarded-Email": actor,
        "X-Forwarded-Groups": "",
    }


def test_campaigns_router_lists_and_patches_visible_campaign() -> None:
    listed = client.get("/api/campaigns")
    assert listed.status_code == 200
    campaigns = listed.json()["campaigns"]
    assert campaigns

    campaign_id = campaigns[0]["campaign_id"]
    detail = client.get(f"/api/campaigns/{campaign_id}")
    assert detail.status_code == 200
    assert detail.json()["campaign_id"] == campaign_id

    patched = client.patch(f"/api/campaigns/{campaign_id}", json={"status": "pending_review"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "pending_review"


def test_campaigns_router_rejects_invalid_campaign_id() -> None:
    response = client.get("/api/campaigns/not a valid id")
    assert response.status_code == 422


def test_building_campaign_quarantine_requires_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = "campaign-owner@example.com"
    repo = _GovernedTransitionRepo(owner_email=owner, treatment_state="building")
    monkeypatch.setitem(app.dependency_overrides, get_portfolio_repository, lambda: repo)
    monkeypatch.setattr(settings, "admin_emails", "admin@example.com")

    denied = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers(owner),
        json={"status": "archived", "rationale": "Quarantine abandoned build"},
    )
    assert denied.status_code == 403
    assert repo.patch_calls == 0

    repo.owner_email = "admin@example.com"
    allowed = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers("admin@example.com"),
        json={"status": "archived", "rationale": "Quarantine abandoned build"},
    )
    assert allowed.status_code == 200
    assert repo.patch_calls == 1


@pytest.mark.parametrize("target_status", ["approved", "live", "active"])
def test_campaigns_alias_rejects_direct_protected_transition_without_approver(
    monkeypatch: pytest.MonkeyPatch,
    target_status: str,
) -> None:
    actor = "campaign-owner@example.com"
    repo = _GovernedTransitionRepo(owner_email=actor)
    monkeypatch.setitem(app.dependency_overrides, get_portfolio_repository, lambda: repo)
    monkeypatch.setattr(settings, "approver_emails", "authorized-approver@example.com")

    response = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers(actor),
        json={
            "status": target_status,
            "rationale": "Governance and legal review completed",
        },
    )

    assert response.status_code == 403
    assert repo.patch_calls == 0


@pytest.mark.parametrize("target_status", ["live", "active"])
def test_campaigns_alias_rejects_illegal_pending_review_skip_for_approver(
    monkeypatch: pytest.MonkeyPatch,
    target_status: str,
) -> None:
    actor = "authorized-approver@example.com"
    repo = _GovernedTransitionRepo(owner_email=actor)
    monkeypatch.setitem(app.dependency_overrides, get_portfolio_repository, lambda: repo)
    monkeypatch.setattr(settings, "approver_emails", actor)

    response = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers(actor),
        json={
            "status": target_status,
            "rationale": "Governance and legal review completed",
        },
    )

    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]


def test_campaigns_alias_rejects_stale_approval_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = "authorized-approver@example.com"
    repo = _GovernedTransitionRepo(
        owner_email=actor,
        validation_now=datetime.now(UTC) + timedelta(minutes=6),
    )
    monkeypatch.setitem(app.dependency_overrides, get_portfolio_repository, lambda: repo)
    monkeypatch.setattr(settings, "approver_emails", actor)

    response = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers(actor),
        json={
            "status": "approved",
            "rationale": "Governance and legal review completed",
        },
    )

    assert response.status_code == 422
    assert "approval evidence is stale" in response.json()["detail"]


def test_campaigns_alias_rejects_mismatched_approval_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = "authorized-approver@example.com"
    repo = _GovernedTransitionRepo(
        owner_email=actor,
        validation_actor="different-approver@example.com",
    )
    monkeypatch.setitem(app.dependency_overrides, get_portfolio_repository, lambda: repo)
    monkeypatch.setattr(settings, "approver_emails", actor)

    response = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers(actor),
        json={
            "status": "approved",
            "rationale": "Governance and legal review completed",
        },
    )

    assert response.status_code == 422
    assert "approval evidence does not match" in response.json()["detail"]


def test_campaigns_alias_rejects_client_supplied_private_approval_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = "authorized-approver@example.com"
    repo = _GovernedTransitionRepo(owner_email=actor)
    monkeypatch.setitem(app.dependency_overrides, get_portfolio_repository, lambda: repo)
    monkeypatch.setattr(settings, "approver_emails", actor)

    response = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers(actor),
        json={
            "status": "approved",
            "rationale": "Governance and legal review completed",
            "_transition_evidence": {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "approver_email": actor,
            },
        },
    )

    assert response.status_code == 422
    assert repo.patch_calls == 0


def test_campaigns_alias_accepts_valid_authorized_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = "authorized-approver@example.com"
    repo = _GovernedTransitionRepo(owner_email=actor)
    monkeypatch.setitem(app.dependency_overrides, get_portfolio_repository, lambda: repo)
    monkeypatch.setattr(settings, "approver_emails", actor)

    response = client.patch(
        "/api/campaigns/11111111-1111-4111-8111-111111111111",
        headers=_headers(actor),
        json={
            "status": "approved",
            "rationale": "Governance and legal review completed",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert repo.patch_calls == 1
    assert repo.evidence is not None
    assert repo.evidence.approver_email == actor
