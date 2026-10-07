"""The RUM ingestion contract (audit 2026-09-21 stack-08, runtime-09, quality-07;
D-platform-process-d1 / d2).

The endpoint accepts only closed shapes: a route-registry template, the
closed metric and detail vocabularies, and values that pass the public-value
scan. An accepted event is folded into the Lakebase day aggregates
(backend/services/rum_rollup.py); nothing is logged per event. The code
default is off; the deploy payload turns it on.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.api import telemetry as telemetry_mod
from backend.main import app
from backend.services import rum_rollup


@pytest.fixture(autouse=True)
def _rollup() -> Iterator[rum_rollup.RumRollup]:
    """A fresh accumulator with no flusher thread, for every test."""
    fresh = rum_rollup._reset_for_tests()
    try:
        yield fresh
    finally:
        rum_rollup._reset_for_tests()


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
                    "route": "/borrower-360/:id",
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
                    "route": "/borrower-360/:id",
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

    # The detail cases ride a member route, so the detail scan is what refuses them.
    bad_values = [
        {"route": "/clip/CL-1234567890"},
        {"route": "/audit/123456789"},
        {"route": "/lead-queue"},
        {"route": "/lead-queue"},
        {"route": "/lead-queue"},
        {"route": "/lead-queue"},
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


def test_record_rum_never_resolves_the_actor_and_emits_no_per_event_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, _rollup: rum_rollup.RumRollup
) -> None:
    """D-platform-process-d1 envelope: no actor, user or session reaches the
    sink. The handler no longer resolves the actor (it used to log an
    actor_class per event) and logs nothing per event: the accumulator gets
    the event, and the per-flush counts line is the only RUM log."""
    source = inspect.getsource(telemetry_mod)
    assert "actor_key_for_request" not in source
    assert "emit(" not in source
    client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)

    with caplog.at_level("DEBUG"):
        response = client.post(
            "/api/telemetry/rum",
            headers={"X-Forwarded-Email": "ops@example.com"},
            json={"events": [{"metric": "lcp", "value": 123, "rating": "good", "route": "/lead-queue"}]},
        )

    assert response.json() == {"accepted": 1, "enabled": True}
    events = {getattr(record, "mip_event", None) for record in caplog.records}
    assert "rum_metric" not in events
    assert "ops@example.com" not in caplog.text
    assert len(_rollup.pending_keys()) == 1, "non-vacuity: the event reached the accumulator"


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


# --- 2026-09-21 audit shell-03: the /ask-genie/:conversationId deep link ----


def _route_change(route: str, details: dict[str, object] | None = None) -> dict[str, object]:
    event: dict[str, object] = {"metric": "route_change", "value": 25, "rating": "good", "route": route}
    if details is not None:
        event["details"] = details
    return {"events": [event]}


@pytest.mark.parametrize(
    "route",
    [
        # A raw conversation id, dashless or dashed, and a malformed id (it can
        # hold typed text) never reach telemetry in the deep link.
        "/ask-genie/0123456789abcdef0123456789abcdef",
        "/ask-genie/0123456789ABCDEF0123456789ABCDEF",
        "/ask-genie/01234567-89ab-cdef-0123-456789abcdef",
        "/ask-genie/not-an-id",
        "/ask-genie/typed",
        # A bare 32-hex token anywhere in a route.
        "/audit/0123456789abcdef0123456789abcdef",
    ],
)
def test_rum_rejects_an_untemplated_conversation_route_and_from_route(route: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)

    as_route = client.post("/api/telemetry/rum", json=_route_change(route))
    as_from_route = client.post(
        "/api/telemetry/rum",
        json=_route_change("/lead-queue", {"from_route": route}),
    )

    assert (as_route.status_code, as_from_route.status_code) == (422, 422)


@pytest.mark.parametrize(
    ("route", "details"),
    [
        ("/ask-genie/:conversationId", {"from_route": "/x/0123456789abcdef0123456789abcdef"}),
        ("/ask-genie/:conversationId", {"dependency": "0123456789abcdef0123456789abcdef"}),
        ("/audit/0123456789abcdef0123456789abcdef", {}),
    ],
)
def test_rum_rejects_a_bare_hex_token_in_the_route_and_every_detail(
    route: str, details: dict[str, object]
) -> None:
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/telemetry/rum", json=_route_change(route, details))

    assert response.status_code == 422
    # The public-value scan is what refuses it, before any per-key rule.
    assert "raw hex identifiers" in response.text


def test_rum_accepts_the_one_registry_spelling_of_the_conversation_route(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)

    for payload in [
        _route_change("/ask-genie/:conversationId", {"from_route": "/ask-genie"}),
        _route_change("/ask-genie", {"from_route": "/ask-genie/:conversationId"}),
    ]:
        response = client.post("/api/telemetry/rum", json=payload)
        assert response.status_code == 202, payload
        assert response.json() == {"accepted": 1, "enabled": True}


# --- D-platform-process-d1 / d2: templates, attribution keys, soft navigations


def _post_one(event: dict[str, object]) -> int:
    client = TestClient(app, raise_server_exceptions=False)
    return client.post("/api/telemetry/rum", json={"events": [event]}).status_code


def _inp(details: dict[str, object]) -> dict[str, object]:
    return {"metric": "inp", "value": 180, "rating": "good", "route": "/lead-queue", "details": details}


def _lcp(details: dict[str, object]) -> dict[str, object]:
    return {"metric": "lcp", "value": 1800, "rating": "good", "route": "/lead-queue", "details": details}


@pytest.mark.parametrize(
    "route",
    ["/borrower-360/:borrower_id", "/ask-genie/:conversation_id", "/ask-genie/", "/borrower-360/abc", "/lower case"],
)
def test_a_route_outside_the_registry_templates_is_422(route: str) -> None:
    assert _post_one({"metric": "route_change", "value": 25, "rating": "good", "route": route}) == 422
    assert _post_one({
        "metric": "route_change", "value": 25, "rating": "good", "route": "/lead-queue",
        "details": {"from_route": route},
    }) == 422


def test_every_registry_template_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.schemas.telemetry import RUM_ROUTE_TEMPLATES

    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)
    for route in sorted(RUM_ROUTE_TEMPLATES):
        event = {"metric": "route_change", "value": 25, "rating": "good", "route": route,
                 "details": {"from_route": route}}
        assert _post_one(event) == 202, route


def test_soft_navigation_is_an_accepted_navigation_type() -> None:
    event = {"metric": "lcp", "value": 900, "rating": "good", "route": "/lead-queue",
             "navigation_type": "soft_navigation"}
    assert _post_one(event) == 202


@pytest.mark.parametrize(
    "event",
    [
        _inp({"interaction_target": "lead-row", "input_delay_ms": 12, "processing_ms": 80.5, "presentation_ms": 40}),
        _inp({"interaction_target": "other"}),
        _lcp({"lcp_element": "h1"}),
        _lcp({"lcp_element": "other"}),
    ],
    ids=["inp-all-keys", "inp-other", "lcp-h1", "lcp-other"],
)
def test_the_attribution_keys_are_accepted_on_their_metric(event: dict[str, object]) -> None:
    assert _post_one(event) == 202


@pytest.mark.parametrize(
    "event",
    [
        _inp({"interaction_target": "#lead-row > td:nth-child(2)"}),
        _inp({"interaction_target": "button.btn"}),
        _inp({"interaction_target": "B-0123456789ABC"}),
        _inp({"input_delay_ms": 600_001}),
        _inp({"processing_ms": -1}),
        _inp({"presentation_ms": "40"}),
        _inp({"lcp_element": "h1"}),
        _lcp({"lcp_element": "section"}),
        _lcp({"lcp_element": "IMG"}),
        _lcp({"interaction_target": "lead-row"}),
        _lcp({"input_delay_ms": 3}),
        {"metric": "cls", "value": 0.02, "rating": "good", "route": "/", "details": {"interaction_target": "nav"}},
        {"metric": "api_call", "value": 5, "rating": "info", "route": "/", "details": {"lcp_element": "img"}},
    ],
    ids=[
        "inp-selector", "inp-class-selector", "inp-borrower-id", "inp-delay-over", "inp-processing-negative",
        "inp-presentation-string", "lcp-element-on-inp", "lcp-unknown-tag", "lcp-uppercase-tag",
        "target-on-lcp", "phase-on-lcp", "target-on-cls", "lcp-element-on-api-call",
    ],
)
def test_the_attribution_keys_are_closed_and_scoped(event: dict[str, object]) -> None:
    assert _post_one(event) == 422
