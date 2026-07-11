"""Visit-tracking contracts: dedupe window, unauthenticated skip, resilience.

The tracker is exercised directly with fake clocks/clients; the middleware
is exercised on a minimal FastAPI app so the assertions cover the actual
request-path wiring (claim on /api traffic only, header-derived actor,
fire-and-forget write).
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.services.visit_tracking import (
    DEFAULT_VISIT_DEDUPE_WINDOW_S,
    VisitTracker,
    VisitTrackingMiddleware,
)


class _RecordingClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        if self.fail:
            raise RuntimeError("lakebase down")
        self.calls.append((sql, params or {}))


class _FakeClock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


def _wait_until(predicate: Any, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


# ---------------------------------------------------------------------------
# Tracker throttle contract
# ---------------------------------------------------------------------------


def test_visit_claim_dedupes_within_window() -> None:
    clock = _FakeClock()
    client = _RecordingClient()
    tracker = VisitTracker(lambda: client, window_s=600.0, now=clock)

    assert tracker.maybe_claim("lo@summit.example") is True
    # A browsing session inside the window never claims again.
    clock.t += 599.0
    assert tracker.maybe_claim("lo@summit.example") is False
    # Past the window a new claim (one new row) is allowed.
    clock.t += 2.0
    assert tracker.maybe_claim("lo@summit.example") is True


def test_visit_claims_are_per_actor() -> None:
    clock = _FakeClock()
    tracker = VisitTracker(lambda: _RecordingClient(), window_s=600.0, now=clock)

    assert tracker.maybe_claim("a@summit.example") is True
    assert tracker.maybe_claim("b@summit.example") is True
    assert tracker.maybe_claim("a@summit.example") is False


def test_unauthenticated_actor_never_claims() -> None:
    tracker = VisitTracker(lambda: _RecordingClient(), window_s=600.0)
    assert tracker.maybe_claim(None) is False
    assert tracker.maybe_claim("") is False


def test_default_factory_tracker_refuses_claims_under_pytest() -> None:
    """The default-factory path must never open a background Postgres
    connection from a pytest process (the conftest Lakebase override is a
    FastAPI dependency seam this service-level call would bypass)."""
    tracker = VisitTracker()
    assert tracker.maybe_claim("lo@summit.example") is False


def test_record_visit_inserts_with_sql_side_window_guard() -> None:
    client = _RecordingClient()
    tracker = VisitTracker(lambda: client, window_s=450.0)

    assert tracker.maybe_claim("lo@summit.example") is True
    tracker.record_visit("lo@summit.example")

    assert len(client.calls) == 1
    sql, params = client.calls[0]
    assert "INSERT INTO mip_app.user_visits" in sql
    assert "WHERE NOT EXISTS" in sql  # replica-safe dedupe re-check in SQL
    assert params == {"actor_email": "lo@summit.example", "window_s": 450.0}


def test_failed_write_releases_claim_and_never_raises() -> None:
    clock = _FakeClock()
    client = _RecordingClient(fail=True)
    tracker = VisitTracker(lambda: client, window_s=600.0, now=clock)

    assert tracker.maybe_claim("lo@summit.example") is True
    tracker.record_visit("lo@summit.example")  # swallows the failure
    # The slot was released, so the very next request retries the write
    # instead of silently losing the visit for a whole window.
    assert tracker.maybe_claim("lo@summit.example") is True


def test_window_must_be_positive() -> None:
    with pytest.raises(ValueError):
        VisitTracker(lambda: _RecordingClient(), window_s=0)


def test_default_window_is_minutes_not_seconds() -> None:
    """The 'at most one row per actor per N minutes' contract: the default
    window is a multi-minute span, so a browsing session can never write a
    row per request."""
    assert DEFAULT_VISIT_DEDUPE_WINDOW_S >= 300.0


# ---------------------------------------------------------------------------
# Middleware wiring
# ---------------------------------------------------------------------------


def _make_app(tracker: VisitTracker) -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(VisitTrackingMiddleware, tracker=tracker)

    @test_app.get("/api/ping")
    def _ping() -> dict[str, str]:
        return {"status": "ok"}

    @test_app.get("/assets/app.js")
    def _asset() -> dict[str, str]:
        return {"status": "ok"}

    return test_app


def test_middleware_records_one_visit_per_actor_per_window() -> None:
    client_fake = _RecordingClient()
    tracker = VisitTracker(lambda: client_fake, window_s=600.0)
    http = TestClient(_make_app(tracker))

    for _ in range(5):
        response = http.get(
            "/api/ping", headers={"X-Forwarded-Email": "lo@summit.example"}
        )
        assert response.status_code == 200

    assert _wait_until(lambda: len(client_fake.calls) == 1)
    time.sleep(0.05)  # would catch a stray second write racing in
    assert len(client_fake.calls) == 1
    assert client_fake.calls[0][1]["actor_email"] == "lo@summit.example"


def test_middleware_records_distinct_actors_separately() -> None:
    client_fake = _RecordingClient()
    tracker = VisitTracker(lambda: client_fake, window_s=600.0)
    http = TestClient(_make_app(tracker))

    http.get("/api/ping", headers={"X-Forwarded-Email": "a@summit.example"})
    http.get("/api/ping", headers={"X-Forwarded-Email": "b@summit.example"})

    assert _wait_until(lambda: len(client_fake.calls) == 2)
    actors = {params["actor_email"] for _, params in client_fake.calls}
    assert actors == {"a@summit.example", "b@summit.example"}


def test_middleware_skips_unauthenticated_requests() -> None:
    client_fake = _RecordingClient()
    tracker = VisitTracker(lambda: client_fake, window_s=600.0)
    http = TestClient(_make_app(tracker))

    response = http.get("/api/ping")  # no forwarded identity headers
    assert response.status_code == 200

    time.sleep(0.05)
    assert client_fake.calls == []


def test_middleware_skips_non_api_paths() -> None:
    client_fake = _RecordingClient()
    tracker = VisitTracker(lambda: client_fake, window_s=600.0)
    http = TestClient(_make_app(tracker))

    http.get("/assets/app.js", headers={"X-Forwarded-Email": "lo@summit.example"})

    time.sleep(0.05)
    assert client_fake.calls == []


def test_middleware_accepts_forwarded_user_fallback_header() -> None:
    client_fake = _RecordingClient()
    tracker = VisitTracker(lambda: client_fake, window_s=600.0)
    http = TestClient(_make_app(tracker))

    http.get("/api/ping", headers={"X-Forwarded-User": "user-id-42"})

    assert _wait_until(lambda: len(client_fake.calls) == 1)
    assert client_fake.calls[0][1]["actor_email"] == "user-id-42"


def test_middleware_skips_when_forwarded_headers_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With trust disabled the identity headers are attacker-writable, so
    no visit is attributed at all (mirrors resolve_actor's R5-09 posture)."""
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)
    client_fake = _RecordingClient()
    tracker = VisitTracker(lambda: client_fake, window_s=600.0)
    http = TestClient(_make_app(tracker))

    http.get("/api/ping", headers={"X-Forwarded-Email": "spoof@evil.example"})

    time.sleep(0.05)
    assert client_fake.calls == []
