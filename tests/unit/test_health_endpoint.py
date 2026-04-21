"""Unit tests for the Slice-6 health endpoint contract.

Body shape::

    {
      "status": "ok" | "degraded",
      "mode": "live",
      "app_env": "<env>",
      "warehouse_id": "<id>",
      "dependencies": {"warehouse": "up"|"down", "lakebase": "up"|"down"},
      "circuit_breakers": {"warehouse": "closed"|"open"|"half_open", ...}
    }

We stub the probe helpers so the test never opens a warehouse / Lakebase
connection. Health must return HTTP 200 even when degraded so the
Databricks App load-balancer doesn't yank the container.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import health as health_mod
from backend.main import app
from backend.services import resilience

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    resilience._reset_breakers_for_tests()


def test_health_returns_ok_when_both_deps_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)

    res = client.get("/api/health")
    assert res.status_code == 200
    payload: dict[str, Any] = res.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "live"
    assert payload["dependencies"] == {"warehouse": "up", "lakebase": "up", "genie": "up"}
    assert set(payload["circuit_breakers"].keys()) >= {"warehouse", "lakebase", "genie"}


def test_health_returns_degraded_when_warehouse_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: False)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)

    res = client.get("/api/health")
    # Degraded state STILL returns 200 so load balancers don't pull
    # the container.
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"] == {
        "warehouse": "down",
        "lakebase": "up",
        "genie": "up",
    }


def test_health_reports_open_breaker_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)

    # Register the warehouse breaker and force it open.
    cb = resilience.get_breaker("warehouse", failure_threshold=1, cooldown_s=60)
    cb.record_failure()
    assert cb.state == "open"

    res = client.get("/api/health")
    payload = res.json()
    assert payload["circuit_breakers"]["warehouse"] == "open"


def test_health_reports_genie_down_and_degrades_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice-7: Genie is the third dependency. When its ping fails the
    body flips to degraded even if warehouse + Lakebase are up. Status
    is still HTTP 200 so the LB doesn't pull the container."""
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: False)

    res = client.get("/api/health")
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"]["genie"] == "down"


def test_dependency_down_exception_translates_to_structured_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app-level exception handler wraps DependencyDownError as a
    503 JSON body with ``retryable: true``.

    Strategy: monkey-patch the segment repository override to raise a
    DependencyDownError and hit the existing ``/api/segments`` route
    through TestClient. This exercises the live exception-handler
    chain end-to-end (router -> handler -> JSONResponse) exactly as
    the browser would see it, without invoking a warehouse.
    """
    from backend.services.repositories import get_segment_repository
    from backend.services.resilience import DependencyDownError

    class _BoomSegmentRepo:
        def list(self, portfolio_id: str | None = None) -> list:
            raise DependencyDownError("warehouse", reason="circuit breaker is open")

    # Layer a one-off override on top of the session-scoped stub.
    previous = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: _BoomSegmentRepo()
    try:
        local = TestClient(app, raise_server_exceptions=False)
        res = local.get("/api/segments")
        assert res.status_code == 503
        body = res.json()
        assert body["retryable"] is True
        assert body["dependency"] == "warehouse"
        assert "warehouse" in body["detail"]
    finally:
        if previous is None:
            del app.dependency_overrides[get_segment_repository]
        else:
            app.dependency_overrides[get_segment_repository] = previous
