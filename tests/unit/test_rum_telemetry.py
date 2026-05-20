from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import telemetry as telemetry_mod
from backend.main import app


def test_rum_endpoint_is_disabled_by_default() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/telemetry/rum",
        json={
            "events": [
                {
                    "metric": "lcp",
                    "value": 1234.5,
                    "rating": "good",
                    "route": "/borrower-360/:borrower_id",
                    "navigation_type": "navigate",
                    "details": {"ttfb_ms": 120},
                }
            ]
        },
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 0, "enabled": False}


def test_rum_endpoint_accepts_sanitized_batch_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)

    response = client.post(
        "/api/telemetry/rum",
        json={
            "events": [
                {
                    "metric": "lcp",
                    "value": 1234.5,
                    "rating": "good",
                    "route": "/borrower-360/:borrower_id",
                    "navigation_type": "navigate",
                    "details": {"ttfb_ms": 120},
                }
            ]
        },
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 1, "enabled": True}


def test_rum_endpoint_rejects_borrower_ids_and_query_strings() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    for route in [
        "/borrower-360/B-102FL7THC6Q3L",
        "/borrower-360/B-Abc_123-extra_456",
        "/borrower-360/B-a2345678901234567890123456789012345678901234567890",
        "/lead-queue?state=WA",
    ]:
        response = client.post(
            "/api/telemetry/rum",
            json={
                "events": [
                    {
                        "metric": "route_change",
                        "value": 25,
                        "rating": "good",
                        "route": route,
                    }
                ]
            },
        )
        assert response.status_code == 422


def test_rum_endpoint_rejects_other_identifier_and_pii_shapes() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    bad_values = [
        {"route": "/clip/CL-1234567890"},
        {"route": "/audit/123456789"},
        {"route": "/search"},
        {"route": "/search"},
        {"route": "/search"},
        {"route": "/search"},
    ]
    detail_values = [
        {},
        {},
        {"from_route": "555-212-3456"},
        {"from_route": "123-45-6789"},
        {"from_route": "123 Main St"},
        {"from_route": "Alice Smith"},
    ]

    for base, details in zip(bad_values, detail_values, strict=True):
        response = client.post(
            "/api/telemetry/rum",
            json={
                "events": [
                    {
                        "metric": "route_change",
                        "value": 25,
                        "rating": "good",
                        "route": base["route"],
                        "details": details,
                    }
                ]
            },
        )
        assert response.status_code == 422


def test_rum_endpoint_rejects_pii_in_details() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/telemetry/rum",
        json={
            "events": [
                {
                    "metric": "api_call",
                    "value": 42,
                    "rating": "info",
                    "route": "/lead-queue",
                    "details": {"bad": "alice@example.com"},
                }
            ]
        },
    )

    assert response.status_code == 422


def test_rum_endpoint_rejects_unapproved_or_nested_details() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    for details in [
        {"bad": "public"},
        {"from_route": {"nested": "/lead-queue"}},
        {"from_route": ["/lead-queue"]},
    ]:
        response = client.post(
            "/api/telemetry/rum",
            json={
                "events": [
                    {
                        "metric": "api_call",
                        "value": 42,
                        "rating": "info",
                        "route": "/lead-queue",
                        "details": details,
                    }
                ]
            },
        )
        assert response.status_code == 422


def test_rum_endpoint_rejects_arbitrary_text_and_oversized_detail_strings() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    for details in [
        {"from_route": "this is arbitrary operational note text"},
        {"dependency": "not-a-real-dependency but arbitrary text"},
        {"from_route": "/" + "a" * 5000},
        {"ttfb_ms": "123"},
        {"attempt": 0},
        {"retryable": "true"},
    ]:
        response = client.post(
            "/api/telemetry/rum",
            json={
                "events": [
                    {
                        "metric": "api_call",
                        "value": 42,
                        "rating": "info",
                        "route": "/lead-queue",
                        "details": details,
                    }
                ]
            },
        )
        assert response.status_code == 422


def test_rum_endpoint_actor_class_honors_trust_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    emitted: list[dict[str, object]] = []

    def _emit(_log, _event, **kwargs):
        emitted.append(kwargs)

    monkeypatch.setattr(telemetry_mod, "emit", _emit)
    monkeypatch.setattr(telemetry_mod.settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)

    payload = {
        "events": [
            {
                "metric": "lcp",
                "value": 123,
                "rating": "good",
                "route": "/lead-queue",
            }
        ]
    }

    authed = client.post(
        "/api/telemetry/rum",
        headers={"X-Forwarded-Email": "ops@example.com"},
        json=payload,
    )
    assert authed.status_code == 202
    assert emitted[-1]["actor_class"] == "authenticated"

    anon = client.post("/api/telemetry/rum", json=payload)
    assert anon.status_code == 202
    assert emitted[-1]["actor_class"] == "anonymous"

    monkeypatch.setattr(telemetry_mod.settings, "trust_forwarded_headers", False)
    spoofed = client.post(
        "/api/telemetry/rum",
        headers={"X-Forwarded-Email": "spoof@example.com"},
        json=payload,
    )
    assert spoofed.status_code == 202
    assert emitted[-1]["actor_class"] == "anonymous"


def test_rum_endpoint_rejects_pii_in_navigation_type() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    for navigation_type in ["B-Abc_123-extra_456", "alice@example.com", "unexpected"]:
        response = client.post(
            "/api/telemetry/rum",
            json={
                "events": [
                    {
                        "metric": "navigation_load",
                        "value": 42,
                        "rating": "good",
                        "route": "/lead-queue",
                        "navigation_type": navigation_type,
                    }
                ]
            },
        )
        assert response.status_code == 422
