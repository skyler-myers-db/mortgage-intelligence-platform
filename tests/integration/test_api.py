from fastapi.testclient import TestClient

from backend.main import app
from tests.fixtures.reviewed_approval import reviewed_approval

client = TestClient(app)


def test_health():
    assert client.get("/api/health").status_code == 200


def test_segments():
    res = client.get("/api/segments")
    assert res.status_code == 200
    assert any(s["code"] == "itm" for s in res.json())


def test_approve_writes_audit():
    # W5c: a reviewed approval certifies a real draft (review_mode required).
    res = client.post("/api/outreach/approve", json=reviewed_approval(client, "B-48291", actor="test"))
    assert res.status_code == 200
    assert res.json()["approved"] is True
