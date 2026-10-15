"""The per-request actor binding (D-identity-review-a4; audit genie-02, bundle-04).

``backend/services/actor_binding.py`` refuses an ``/api`` request whose
``X-MIP-Actor-Key`` no longer names the forwarded identity: a 409
``actor_changed`` sent before routing, a dependency, the visit ledger or a
backpressure slot. These tests pin:

  (i)    the refusal: the exact body, nothing inside the middleware runs (the
         router, the audit writer, the visit tracker);
  (ii)   what passes: a matching key, an alias, no header at all;
  (iii)  the closed exemption set (health, session, telemetry), as a literal;
  (iv)   every routed /api path is covered, filled generically;
  (v)    the aliases: only under a live previous secret;
  (vi)   the log line carries the method and nothing that names a person, a
         borrower or a key; the 409 still carries the outer middlewares'
         headers; the registration order.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.config.settings import settings
from backend.main import app
from backend.services import actor_identity, visit_tracking
from backend.services.actor_binding import (
    EXEMPT_PATHS,
    EXEMPT_PREFIXES,
    bound_request_refused,
    is_exempt,
)
from backend.services.actor_identity import actor_cache_key, actor_cache_key_aliases
from backend.services.audit_store import get_audit_store

client = TestClient(app)

ACTOR = "lo.binding@summit.example"
OTHER = "lo.other@summit.example"
STALE_KEY = "actor_0000000000000000"
CURRENT_SECRET = "test-secret-0123456789abcdef"
PREVIOUS_SECRET = "previous-action-secret"
REFUSAL = {
    "detail": "The signed-in user changed in this browser. Nothing was recorded.",
    "reason": "actor_changed",
}
BORROWER = "B-0000000000000"
_PARAM = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _headers(key: str | None, identity: str | None = ACTOR) -> dict[str, str]:
    headers: dict[str, str] = {}
    if identity is not None:
        headers["X-Forwarded-Email"] = identity
    if key is not None:
        headers["X-MIP-Actor-Key"] = key
    return headers


class _Spies:
    def __init__(self) -> None:
        self.router: list[str] = []
        self.audit: list[str] = []
        self.claims: list[str | None] = []
        self.visits: list[str] = []


@pytest.fixture()
def spies(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Spies]:
    spy = _Spies()
    inner = app.router.middleware_stack

    async def router_spy(scope: Any, receive: Any, send: Any) -> None:
        spy.router.append(str(scope.get("path")))
        await inner(scope, receive, send)

    monkeypatch.setattr(app.router, "middleware_stack", router_spy)
    store = app.dependency_overrides[get_audit_store]()
    original_write = store.write

    def audit_spy(**kwargs: Any) -> Any:
        spy.audit.append(str(kwargs.get("event_type") or kwargs.get("action")))
        return original_write(**kwargs)

    monkeypatch.setattr(store, "write", audit_spy)

    def claim_spy(_self: Any, actor: str | None) -> bool:
        spy.claims.append(actor)
        return False

    def visit_spy(_self: Any, actor: str) -> None:
        spy.visits.append(actor)

    monkeypatch.setattr(visit_tracking.VisitTracker, "maybe_claim", claim_spy)
    monkeypatch.setattr(visit_tracking.VisitTracker, "record_visit", visit_spy)
    yield spy


@pytest.fixture()
def rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr(CURRENT_SECRET))
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", SecretStr(PREVIOUS_SECRET))


# ---------------------------------------------------------------- refusal


def test_a_stale_key_is_refused_before_anything_inside_runs(spies: _Spies) -> None:
    response = client.get(f"/api/v1/borrowers/{BORROWER}", headers=_headers(STALE_KEY))

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/json"
    assert response.json() == REFUSAL
    assert response.content == json.dumps(REFUSAL).encode("utf-8")
    assert spies.router == []
    assert spies.audit == []
    assert spies.claims == []
    assert spies.visits == []


def test_the_spies_see_a_request_the_binding_lets_through(spies: _Spies) -> None:
    """Non-vacuity: the same read under the right key reaches all three."""

    response = client.get("/api/v1/borrowers/B-48291", headers=_headers(actor_cache_key(ACTOR)))

    assert response.status_code == 200
    assert spies.router == ["/api/v1/borrowers/B-48291"]
    assert spies.audit == ["VIEW_BORROWER"]
    assert spies.claims == [ACTOR]


def test_a_key_minted_for_another_actor_is_refused() -> None:
    response = client.get("/api/v1/workspace", headers=_headers(actor_cache_key(OTHER)))

    assert response.status_code == 409
    assert response.json() == REFUSAL


def test_a_duplicated_header_is_refused_even_when_one_copy_matches() -> None:
    key = actor_cache_key(ACTOR)
    response = client.get(
        "/api/v1/workspace",
        headers=[("X-Forwarded-Email", ACTOR), ("X-MIP-Actor-Key", key), ("X-MIP-Actor-Key", key)],
    )

    assert response.status_code == 409


def test_a_key_without_a_forwarded_identity_is_refused() -> None:
    response = client.get("/api/v1/workspace", headers=_headers(actor_cache_key(ACTOR), identity=None))

    assert response.status_code == 409
    assert response.json() == REFUSAL


def test_a_key_behind_an_untrusted_edge_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)

    bound = client.get("/api/v1/workspace", headers=_headers(actor_cache_key(ACTOR)))
    unbound = client.get("/api/v1/workspace", headers=_headers(None))

    assert bound.status_code == 409
    assert unbound.status_code == 200


def test_a_non_ascii_value_is_a_mismatch() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/workspace",
        "query_string": b"",
        "headers": [
            (b"x-forwarded-email", ACTOR.encode("latin-1")),
            (b"x-mip-actor-key", actor_cache_key(ACTOR).encode("ascii") + b"\xe9"),
        ],
    }

    assert bound_request_refused(scope) is True


# ---------------------------------------------------------------- passes


@pytest.mark.parametrize("label", ["matching key", "padded key", "no header"])
def test_matching_padded_and_absent_keys_pass(label: str, spies: _Spies) -> None:
    key = {"matching key": actor_cache_key(ACTOR), "padded key": f"  {actor_cache_key(ACTOR)} ", "no header": None}[label]

    response = client.get("/api/v1/workspace", headers=_headers(key))

    assert response.status_code == 200
    assert spies.router == ["/api/v1/workspace"]


def test_the_key_the_binding_accepts_is_the_session_key() -> None:
    session = client.get("/api/v1/session", headers=_headers(None)).json()

    accepted = client.get("/api/v1/workspace", headers=_headers(session["actor_cache_key"]))

    assert session["actor_cache_key"] == actor_cache_key(ACTOR)
    assert accepted.status_code == 200


def test_an_alias_under_a_live_previous_secret_passes(rotation: None) -> None:
    health = client.get("/api/v1/health", headers=_headers(None)).json()
    [alias] = health["actor_cache_key_aliases"]

    current = client.get("/api/v1/workspace", headers=_headers(health["actor_cache_key"]))
    previous = client.get("/api/v1/workspace", headers=_headers(alias))

    assert alias != health["actor_cache_key"]
    assert current.status_code == 200
    assert previous.status_code == 200


def test_the_alias_stops_passing_once_the_previous_secret_is_retired(
    rotation: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    [alias] = actor_cache_key_aliases(ACTOR)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)

    response = client.get("/api/v1/workspace", headers=_headers(alias))

    assert response.status_code == 409


# ---------------------------------------------------------------- aliases


def test_the_alias_is_the_key_under_the_previous_secret(rotation: None) -> None:
    rotated = actor_cache_key_aliases(" Lo.Binding@Summit.example ")

    assert rotated == [actor_identity._derive(PREVIOUS_SECRET, ACTOR)]
    assert rotated != [actor_cache_key(ACTOR)]


@pytest.mark.parametrize(
    "previous",
    [
        None,
        "",
        "changeme",
        "<redacted>",
        "disabled." + "0" * 64,
        CURRENT_SECRET,
    ],
    ids=["unset", "blank", "placeholder", "bracketed", "retired", "same as current"],
)
def test_no_alias_without_a_live_distinct_previous_secret(
    previous: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr(CURRENT_SECRET))
    monkeypatch.setattr(
        settings,
        "mip_genie_action_secret_previous",
        None if previous is None else SecretStr(previous),
    )

    assert actor_cache_key_aliases(ACTOR) == []


def test_the_retired_prefix_is_the_provisioning_tools_prefix() -> None:
    from tools.databricks.provision_runtime_secrets import DISABLED_PREVIOUS_PREFIX

    assert actor_identity._DISABLED_PREVIOUS_PREFIX == DISABLED_PREVIOUS_PREFIX


# ---------------------------------------------------------------- exemptions


def test_the_exemption_set_is_a_closed_literal() -> None:
    assert frozenset({"/api/health", "/api/v1/health", "/api/session", "/api/v1/session"}) == EXEMPT_PATHS
    assert EXEMPT_PREFIXES == ("/api/telemetry/", "/api/v1/telemetry/")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        (method, f"{prefix}{route}{slash}")
        for prefix in ("/api", "/api/v1")
        for method, route in (("GET", "/health"), ("GET", "/session"), ("POST", "/telemetry/rum"))
        for slash in ("", "/")
    ],
)
def test_a_wrong_key_on_an_exempt_route_is_not_refused(method: str, path: str) -> None:
    response = client.request(method, path, headers=_headers(STALE_KEY), json={})

    assert response.status_code != 409


@pytest.mark.parametrize("path", ["/api/v1/genie/sessions", "/api/healthz", "/api/v1/sessions", "/api/admin/health"])
def test_near_misses_of_the_exemptions_are_bound(path: str) -> None:
    assert is_exempt(path) is False


def _filled(path: str) -> str:
    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "borrower_id":
            return BORROWER
        if name.endswith("_id"):
            return "00000000-0000-4000-8000-000000000000"
        return "x"

    return _PARAM.sub(fill, path)


def _api_routes() -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (route.path == "/api" or route.path.startswith("/api/")):
            continue
        methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        routes.append((methods[0], route.path))
    return routes


def test_every_routed_api_path_is_bound_except_the_exemptions(spies: _Spies) -> None:
    routes = _api_routes()
    exempt = sorted(path for _method, path in routes if is_exempt(_filled(path)))
    bound = [(method, path) for method, path in routes if not is_exempt(_filled(path))]

    assert exempt == sorted(
        ["/api/health", "/api/v1/health", "/api/session", "/api/v1/session", "/api/telemetry/rum", "/api/v1/telemetry/rum"]
    )
    assert len(bound) > 200
    refused_other = []
    for method, path in bound:
        response = client.request(method, _filled(path), headers=_headers(STALE_KEY))
        if response.status_code != 409 or response.json() != REFUSAL:
            refused_other.append((method, path, response.status_code))
    assert refused_other == []
    assert spies.router == []
    assert spies.audit == []


# ---------------------------------------------------------------- log + stack


def test_the_refusal_logs_the_method_and_nothing_that_names_anyone(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    client.get(f"/api/v1/borrowers/{BORROWER}", headers=_headers(STALE_KEY))

    binding = [record for record in caplog.records if record.name == "mip.actor_binding"]
    assert [record.getMessage() for record in binding] == ["actor_binding_mismatch"]
    assert binding[0].levelno == logging.WARNING
    assert binding[0].__dict__["mip_extras"] == {"method": "GET"}
    assert binding[0].__dict__["mip_correlation_id"]
    server_records = [record for record in caplog.records if record.name != "httpx"]  # the test client's own line
    assert any(record.name == "mip.http" for record in server_records)
    for record in server_records:
        text = record.getMessage() + json.dumps(
            {key: value for key, value in record.__dict__.items() if key.startswith("mip_")}, default=str
        )
        assert "B-" not in text
        assert STALE_KEY not in text
        assert ACTOR not in text


def test_the_refusal_still_carries_the_outer_middleware_headers() -> None:
    response = client.get("/api/v1/workspace", headers=_headers(STALE_KEY))

    assert response.status_code == 409
    assert response.headers["X-Correlation-ID"]
    assert response.headers["Server-Timing"].startswith("total;dur=")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_the_binding_sits_inside_server_timing_and_outside_visits() -> None:
    assert [middleware.cls.__name__ for middleware in app.user_middleware] == [
        "GZipMiddleware",
        "SecurityHeadersMiddleware",
        "CorrelationIdMiddleware",
        "ServerTimingMiddleware",
        "ActorBindingMiddleware",
        "VisitTrackingMiddleware",
        "BackpressureMiddleware",
    ]
