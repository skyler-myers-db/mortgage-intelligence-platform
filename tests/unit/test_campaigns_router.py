from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


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
