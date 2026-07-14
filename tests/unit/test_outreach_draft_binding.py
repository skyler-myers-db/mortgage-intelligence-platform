from __future__ import annotations

from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app

client = TestClient(app)
BORROWER_EVIDENCE_IDS = ["ev-001"]


def _draft() -> dict[str, object]:
    response = client.post(
        "/api/outreach/draft",
        json={"borrower_id": "B-48291", "channel": "email"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approval(draft: dict[str, object], **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "borrower_id": "B-48291",
        "offer_code": draft["offer_code"],
        "channel": "email",
        "evidence_ids": BORROWER_EVIDENCE_IDS,
        "draft_subject": draft["subject"],
        "draft_body": draft["body"],
        "draft_generation_id": draft["generation_id"],
        "draft_response_hash": draft["response_hash"],
        "draft_source_refreshed_at": draft["source_refreshed_at"],
    }
    payload.update(updates)
    return payload


def test_production_approval_requires_persisted_draft_proof(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": draft["offer_code"],
            "channel": "email",
            "evidence_ids": BORROWER_EVIDENCE_IDS,
            "draft_subject": draft["subject"],
            "draft_body": draft["body"],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Approval requires the audited generated draft proof."


def test_production_approval_accepts_exact_draft_proof(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post("/api/outreach/approve", json=_approval(draft))

    assert response.status_code == 200, response.text
    assert response.json()["draft_generation_id"] == draft["generation_id"]
    assert response.json()["draft_edited"] is False


def test_production_approval_rejects_tampered_draft_proof(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft, draft_response_hash="f" * 64),
    )

    assert response.status_code == 409
    assert "does not match" in response.json()["detail"]


def test_production_approval_audits_human_edit_from_generated_draft(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")
    edited = f"Please review this updated option. {draft['body']}"

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft, draft_body=edited),
    )

    assert response.status_code == 200, response.text
    assert response.json()["draft_generation_id"] == draft["generation_id"]
    assert response.json()["draft_edited"] is True
