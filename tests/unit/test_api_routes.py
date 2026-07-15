from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.portfolio import PortfolioCreateResponse
from backend.services.repositories import get_portfolio_repository

client = TestClient(app)


class _CapturePortfolioCreateRepository:
    def __init__(self) -> None:
        self.idempotency_keys: list[str] = []

    def create(
        self,
        payload: Any,
        *,
        actor: str | None = None,
        idempotency_key: str,
    ) -> PortfolioCreateResponse:
        del actor
        self.idempotency_keys.append(idempotency_key)
        return PortfolioCreateResponse(
            portfolio_id="11111111-1111-4111-8111-111111111111",
            campaign_id="11111111-1111-4111-8111-111111111111",
            name=payload.name,
            marketable_population=1,
        )


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
        (
            "get",
            "/api/leads?portfolio_id=11111111-1111-4111-8111-111111111111&segment=itm",
            None,
            200,
        ),
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
        assert (
            response.status_code == expected
        ), f"{method.upper()} {path} returned {response.status_code}: {response.text}"


def test_portfolio_create_rejects_malformed_idempotency_key_as_input_error():
    response = client.post(
        "/api/portfolio/create",
        json={"name": "Malformed key"},
        headers={"Idempotency-Key": "not a public opaque id"},
    )

    assert response.status_code == 422
    assert "Idempotency-Key" not in response.text


def test_portfolio_create_generates_optional_idempotency_key_and_preserves_provided_key():
    repo = _CapturePortfolioCreateRepository()
    prior = app.dependency_overrides.get(get_portfolio_repository)
    app.dependency_overrides[get_portfolio_repository] = lambda: repo
    provided = "11111111-1111-4111-8111-111111111112"
    try:
        generated_response = client.post(
            "/api/v1/portfolio/create",
            json={"name": "Generated idempotency"},
        )
        provided_response = client.post(
            "/api/v1/portfolio/create",
            json={"name": "Provided idempotency"},
            headers={"Idempotency-Key": provided},
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_portfolio_repository, None)
        else:
            app.dependency_overrides[get_portfolio_repository] = prior

    assert generated_response.status_code == 200, generated_response.text
    assert provided_response.status_code == 200, provided_response.text
    generated = UUID(repo.idempotency_keys[0])
    assert generated.version == 4
    assert repo.idempotency_keys[1] == provided


def test_structured_mutation_routes_require_and_document_json_content_type():
    schema = app.openapi()
    missing: list[str] = []
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = str(getattr(route, "path", ""))
        structured_methods = sorted({"POST", "PATCH", "PUT"}.intersection(methods))
        if not structured_methods or not path.startswith("/api/"):
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None or not dependant.body_params:
            continue
        dependency_names = (
            [
                getattr(dependency.call, "__name__", str(dependency.call))
                for dependency in dependant.dependencies
            ]
        )
        schema_path = schema["paths"].get(getattr(route, "path_format", path), {})
        for method in structured_methods:
            responses = schema_path.get(method.lower(), {}).get("responses", {})
            if "require_json_content_type" not in dependency_names or "415" not in responses:
                missing.append(f"{method} {path}")

    assert missing == []


def test_structured_patch_and_put_routes_reject_non_json_before_binding() -> None:
    probes = [
        ("patch", "/api/campaigns/11111111-1111-4111-8111-111111111111"),
        ("patch", "/api/portfolio/11111111-1111-4111-8111-111111111111"),
        ("put", "/api/workspace/leads/B-48291"),
        ("put", "/api/workspace/drafts/B-48291"),
    ]
    for method, path in probes:
        response = getattr(client, method)(
            path,
            content='{"status":"pending_review"}',
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 415, f"{method.upper()} {path}: {response.text}"
        assert response.json() == {"detail": "Unsupported content type"}
