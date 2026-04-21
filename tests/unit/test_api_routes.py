from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_required_routes_exist_and_respond():
    checks = [
        ("get", "/api/health", None, 200),
        ("get", "/api/config/options", None, 200),
        ("post", "/api/portfolio/preview", {"criteria": {}}, 200),
        ("post", "/api/portfolio/create", {"name": "Sample"}, 200),
        ("get", "/api/portfolio/p1", None, 200),
        ("get", "/api/segments?portfolio_id=p1", None, 200),
        ("get", "/api/leads?portfolio_id=p1&segment=itm", None, 200),
        ("get", "/api/borrowers/B-48291", None, 200),
        ("get", "/api/borrowers/B-48291/evidence", None, 200),
        ("post", "/api/offers/recommend", {"borrower_id": "B-48291"}, 200),
        ("post", "/api/outreach/draft", {"borrower_id": "B-48291", "channel": "email"}, 200),
        (
            "post",
            "/api/outreach/approve",
            {"borrower_id": "B-48291", "actor": "anonymous"},
            200,
        ),
        ("post", "/api/genie/start", {"context": {}}, 200),
        (
            "post",
            "/api/genie/message",
            {"conversation_id": "c1", "question": "Which zips are in the money?"},
            200,
        ),
        ("get", "/api/audit/events", None, 200),
        (
            "post",
            "/api/audit/event",
            {
                "actor": "anonymous",
                "action": "view.leads",
                "entity_type": "lead_queue",
                "entity_id": "itm",
            },
            200,
        ),
        ("get", "/api/admin/rules", None, 200),
        ("put", "/api/admin/rules", {"x": "y"}, 200),
    ]

    for method, path, payload, expected in checks:
        call = getattr(client, method)
        response = call(path, json=payload) if payload is not None else call(path)
        assert response.status_code == expected, (
            f"{method.upper()} {path} returned {response.status_code}: {response.text}"
        )
