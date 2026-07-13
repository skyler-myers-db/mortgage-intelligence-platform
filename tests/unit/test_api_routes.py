from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_required_routes_exist_and_respond():
    checks = [
        ("get", "/api/health", None, 200),
        ("get", "/api/config/options", None, 200),
        ("get", "/api/analytics/executive", None, 200),
        ("get", "/api/analytics/geography", None, 200),
        ("get", "/api/analytics/economics", None, 200),
        ("get", "/api/analytics/segments", None, 200),
        ("get", "/api/analytics/signals", None, 200),
        ("post", "/api/portfolio/preview", {"criteria": {}}, 200),
        ("post", "/api/portfolio/create", {"name": "Sample"}, 200),
        ("get", "/api/portfolio/11111111-1111-4111-8111-111111111111", None, 200),
        (
            "patch",
            "/api/portfolio/11111111-1111-4111-8111-111111111111",
            {"status": "pending_review"},
            200,
        ),
        ("get", "/api/segments?portfolio_id=11111111-1111-4111-8111-111111111111", None, 200),
        ("get", "/api/leads?portfolio_id=11111111-1111-4111-8111-111111111111&segment=itm", None, 200),
        ("get", "/api/leads?segment_codes=itm,equity&segment_mode=all", None, 200),
        ("get", "/api/geo/state-rollups?segment_codes=itm,equity&segment_mode=all", None, 200),
        ("get", "/api/borrowers/B-48291", None, 200),
        ("get", "/api/borrowers/B-48291/evidence", None, 200),
        ("post", "/api/offers/recommend", {"borrower_id": "B-48291"}, 200),
        ("post", "/api/outreach/draft", {"borrower_id": "B-48291", "channel": "email"}, 200),
        (
            "post",
            "/api/outreach/approve",
            {
                "borrower_id": "B-48291",
                "actor": "anonymous",
                "draft_subject": "Your mortgage review",
                "draft_body": "Governed approval body. Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out.",
            },
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
                    "action": "view.custom",
                    "entity_type": "lead_queue",
                    "entity_id": "itm",
                },
            200,
        ),
        ("get", "/api/admin/rules", None, 200),
        # App-local threshold edits are rejected; rules are UC-governed.
        ("put", "/api/admin/rules", {"attempted_change": {"x": "y"}}, 410),
        ("get", "/api/not-a-real-route", None, 404),
    ]

    for method, path, payload, expected in checks:
        call = getattr(client, method)
        headers = (
            {"Idempotency-Key": "11111111-1111-4111-8111-111111111112"}
            if path == "/api/portfolio/create"
            else None
        )
        response = call(path, json=payload, headers=headers) if payload is not None else call(path)
        assert response.status_code == expected, (
            f"{method.upper()} {path} returned {response.status_code}: {response.text}"
        )


def test_structured_post_routes_require_and_document_json_content_type():
    schema = app.openapi()
    missing: list[str] = []
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = str(getattr(route, "path", ""))
        if "POST" not in methods or not path.startswith("/api/"):
            continue
        dependant = getattr(route, "dependant", None)
        dependency_names = [] if dependant is None else [
            getattr(dependency.call, "__name__", str(dependency.call))
            for dependency in dependant.dependencies
        ]
        route_schema = schema["paths"].get(getattr(route, "path_format", path), {}).get("post", {})
        responses = route_schema.get("responses", {})
        if "require_json_content_type" not in dependency_names or "415" not in responses:
            missing.append(path)

    assert missing == []
