from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def _headers(actor: str) -> dict[str, str]:
    return {"X-Forwarded-Email": actor}


def test_workspace_saved_leads_are_actor_scoped_and_deletable() -> None:
    borrower_id = f"B-WS-{uuid4().hex[:8]}"
    actor = f"lo-{uuid4().hex[:8]}@example.com"
    other_actor = f"lo-{uuid4().hex[:8]}@example.com"

    payload = {
        "borrower_id": borrower_id,
        "city": "Seattle",
        "state": "WA",
        "zip": "98118",
        "recommended_offer": "Refinance + HELOC",
        "opportunity_score": 86,
        "confidence": 81,
    }
    res = client.put(
        f"/api/workspace/leads/{borrower_id}",
        json=payload,
        headers=_headers(actor),
    )
    assert res.status_code == 200
    saved = res.json()
    assert saved["borrower_id"] == borrower_id
    assert saved["recommended_offer"] == "Refinance + HELOC"

    mine = client.get("/api/workspace", headers=_headers(actor)).json()
    assert [row["borrower_id"] for row in mine["saved_leads"]] == [borrower_id]

    theirs = client.get("/api/workspace", headers=_headers(other_actor)).json()
    assert theirs["saved_leads"] == []

    deleted = client.delete(
        f"/api/workspace/leads/{borrower_id}",
        headers=_headers(actor),
    )
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert client.get("/api/workspace", headers=_headers(actor)).json()["saved_leads"] == []


def test_workspace_drafts_are_persisted_and_pii_scrubbed() -> None:
    borrower_id = f"B-DR-{uuid4().hex[:8]}"
    actor = f"lo-{uuid4().hex[:8]}@example.com"
    body = (
        "Hi [first name], call 212-555-1212 or email person@example.com. "
        "We can discuss 123 Main St."
    )

    res = client.put(
        f"/api/workspace/drafts/{borrower_id}",
        json={
            "borrower_id": borrower_id,
            "offer_code": "OFFER-123",
            "channel": "email",
            "body": body,
        },
        headers=_headers(actor),
    )
    assert res.status_code == 200
    saved = res.json()
    assert saved["borrower_id"] == borrower_id
    assert "[PHONE-REDACTED]" in saved["body"]
    assert "[EMAIL-REDACTED]" in saved["body"]
    assert "[ADDRESS-REDACTED]" in saved["body"]

    state = client.get("/api/workspace", headers=_headers(actor)).json()
    assert state["saved_drafts"][0]["body"] == saved["body"]

    deleted = client.delete(
        f"/api/workspace/drafts/{borrower_id}?channel=email",
        headers=_headers(actor),
    )
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert client.get("/api/workspace", headers=_headers(actor)).json()["saved_drafts"] == []


def test_workspace_path_body_mismatch_is_rejected() -> None:
    res = client.put(
        "/api/workspace/leads/B-ONE",
        json={"borrower_id": "B-TWO"},
        headers=_headers("lo@example.com"),
    )
    assert res.status_code == 400


def test_workspace_requires_actor_identity() -> None:
    res = client.get("/api/workspace")
    assert res.status_code == 401
    assert res.json()["detail"] == "workspace identity required"
