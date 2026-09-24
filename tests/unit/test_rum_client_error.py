"""`client_error` and `api_call` RUM contract (2026-09-21 UI/UX audit stack-01,
shell-01, states-01, quality-01, delivery-v3).

A client error reaches the server as four closed values and nothing else: an
error message, a stack line or a borrower-bearing path never validates, so it
can never reach the log sink. An `api_call` carries a templated API path whose
every segment is a literal route segment of this app or `:id`, plus the
Server-Timing fields. The sink stays log-only and off by default.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import telemetry as telemetry_mod
from backend.main import app
from backend.schemas.telemetry import (
    CLIENT_ERROR_BOUNDARIES,
    CLIENT_ERROR_NAMES,
    CLIENT_ERROR_SOURCES,
    RUM_API_ROUTE_SEGMENTS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUM_BRIDGE_TS = REPO_ROOT / "frontend" / "src" / "lib" / "rumBridge.ts"

RUM_PATH = "/api/telemetry/rum"

CLIENT_ERROR: dict[str, Any] = {
    "metric": "client_error",
    "value": 1,
    "rating": "info",
    "route": "/borrower-360/:id",
    "details": {
        "error_name": "TypeError",
        "error_kind": "render",
        "error_source": "caught",
        "boundary": "drawer",
    },
}

API_CALL: dict[str, Any] = {
    "metric": "api_call",
    "value": 912,
    "rating": "info",
    "route": "/borrower-360/:borrower_id",
    "details": {
        "api_route": "/api/borrowers/:id/proof",
        "cache": "miss",
        "warehouse_ms": 812.4,
        "lakebase_ms": 3,
        "total_ms": 840,
        "transfer_size": 1200,
    },
}

MESSAGE = "Cannot read properties of null (reading 'find')"
STACK_LINE = "    at EvidenceDrawer (https://host/assets/index-a1.js:1:2345)"
BORROWER_ID = "B-0123456789ABC"
BORROWER_PATH = "/borrower-360/B-0123456789ABC"
LEAKS = [MESSAGE, STACK_LINE, BORROWER_ID, BORROWER_PATH]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, event: dict[str, Any]) -> Any:
    return client.post(RUM_PATH, json={"events": [event]})


def _with_details(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    details = {**base["details"], **changes}
    return {**base, "details": {key: value for key, value in details.items() if value is not ...}}


def test_accepts_the_closed_client_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)
    assert _post(client, CLIENT_ERROR).json() == {"accepted": 1, "enabled": True}
    # The boundary is optional: an uncaught or window error has none.
    for boundary in (..., None):
        event = _with_details(CLIENT_ERROR, boundary=boundary, error_source="window")
        assert _post(client, event).status_code == 202


def test_accepts_an_api_call_with_its_server_timing_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)
    assert _post(client, API_CALL).json() == {"accepted": 1, "enabled": True}


def test_the_sink_stays_off_by_default(client: TestClient) -> None:
    response = _post(client, CLIENT_ERROR)
    assert response.status_code == 202
    assert response.json() == {"accepted": 0, "enabled": False}


@pytest.mark.parametrize("key", ["error_name", "error_kind", "error_source", "boundary"])
@pytest.mark.parametrize("leak", LEAKS, ids=["message", "stack", "borrower_id", "borrower_path"])
def test_rejects_free_text_in_every_error_key(client: TestClient, key: str, leak: str) -> None:
    response = _post(client, _with_details(CLIENT_ERROR, **{key: leak}))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "event",
    [
        {**CLIENT_ERROR, "value": 2},
        {**CLIENT_ERROR, "rating": "poor"},
        _with_details(CLIENT_ERROR, error_name=...),
        _with_details(CLIENT_ERROR, error_kind=...),
        _with_details(CLIENT_ERROR, error_source=...),
        _with_details(CLIENT_ERROR, from_route="/lead-queue"),
        _with_details(CLIENT_ERROR, transfer_size=12),
        {**CLIENT_ERROR, "metric": "lcp", "rating": "good"},
        {"metric": "lcp", "value": 1200, "rating": "good", "route": "/", "details": {"cache": "hit"}},
        {"metric": "lcp", "value": 1200, "rating": "good", "route": "/", "details": {"total_ms": 4}},
        {
            "metric": "route_change",
            "value": 30,
            "rating": "good",
            "route": "/",
            "details": {"api_route": "/api/leads"},
        },
    ],
    ids=[
        "value-2",
        "rating-poor",
        "missing-error-name",
        "missing-error-kind",
        "missing-error-source",
        "from-route-on-client-error",
        "transfer-size-on-client-error",
        "error-name-on-lcp",
        "cache-on-lcp",
        "total-ms-on-lcp",
        "api-route-on-route-change",
    ],
)
def test_rejects_a_client_error_outside_its_closed_shape(
    client: TestClient, event: dict[str, Any]
) -> None:
    assert _post(client, event).status_code == 422


@pytest.mark.parametrize(
    "api_route",
    [
        "/api/borrowers/B-0123456789ABC",
        "/api/leads?state=TX",
        "/api/analytics/funnel/loan-officers/lo-jdoe",
        "/lead-queue",
        "/api",
        "/api/v1/leads",
        "/api//leads",
        "/api/leads/:borrower_id",
        "/api/" + "/".join(["leads"] * 11),
        12,
    ],
)
def test_rejects_an_api_route_that_is_not_a_closed_template(
    client: TestClient, api_route: object
) -> None:
    assert _post(client, _with_details(API_CALL, api_route=api_route)).status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"cache": "warm"},
        {"cache": None},
        {"warehouse_ms": "12"},
        {"warehouse_ms": -1},
        {"lakebase_ms": 600_001},
        {"total_ms": True},
    ],
)
def test_rejects_unknown_cache_states_and_out_of_range_timings(
    client: TestClient, changes: dict[str, object]
) -> None:
    assert _post(client, _with_details(API_CALL, **changes)).status_code == 422


def _mounted_api_segments() -> set[str]:
    segments: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for segment in path.split("/"):
            if not segment or segment in {"api", "v1"} or segment.startswith("{"):
                continue
            segments.add(segment)
    return segments


def test_every_mounted_api_segment_is_in_the_rum_vocabulary() -> None:
    """Drift pin: a new /api route with a new literal segment must extend the
    vocabulary here AND in frontend/src/lib/rumApiRoute.ts, or its api_call
    events would template that segment to `:id`."""
    mounted = _mounted_api_segments()
    assert len(mounted) > 50, "the route walk found the mounted /api routes"
    missing = sorted(mounted - RUM_API_ROUTE_SEGMENTS)
    assert not missing, (
        "New literal /api path segments are missing from RUM_API_ROUTE_SEGMENTS "
        f"(backend/schemas/telemetry.py) and API_ROUTE_SEGMENTS "
        f"(frontend/src/lib/rumApiRoute.ts): {missing}"
    )


def _ts_const_array(path: Path, name: str) -> set[str]:
    """The string literals of `export const <name> = [...] as const;` in a TS file."""
    source = path.read_text(encoding="utf-8")
    match = re.search(
        rf"export const {name} = \[(?P<body>.*?)\] as const;", source, flags=re.DOTALL
    )
    assert match, f"{path.name} must declare `export const {name} = [...] as const;`"
    values = re.findall(r"'([^']*)'", match.group("body"))
    assert len(values) == len(set(values)), f"{name} in {path.name} has duplicates"
    return set(values)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("CLIENT_ERROR_NAMES", CLIENT_ERROR_NAMES),
        ("CLIENT_ERROR_SOURCES", CLIENT_ERROR_SOURCES),
        ("CLIENT_ERROR_BOUNDARIES", CLIENT_ERROR_BOUNDARIES),
    ],
)
def test_client_error_vocabularies_match_rum_bridge(name: str, server: frozenset[str]) -> None:
    """Parity pin (named in the rumBridge.ts header): a name, source or
    boundary the client can send is exactly one the server accepts."""
    assert _ts_const_array(RUM_BRIDGE_TS, name) == set(server)
