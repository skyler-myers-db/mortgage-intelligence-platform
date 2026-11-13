"""Health surfaces read the last real Genie turn; they never run one.

Contract (2026-10-28 budget finding):

- ``/api/admin/health`` carries ``genie_last_turn: {outcome, error_class, at}``
  (null before the first turn since boot).
- A ping-up Genie whose last turn failed on BUDGET_EXCEEDED / QUOTA_EXCEEDED
  inside the window reads ``degraded`` with ``dependency_reasons``, and
  ``status`` reads ``degraded``. RATE_LIMITED and older failures do not.
- The anonymous liveness body stays ``{status, mode}`` and does not reveal it.
- No health read starts a Genie turn.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api import health as health_mod
from backend.main import app
from backend.schemas.health import AdminHealthResponse, GenieLastTurn
from backend.services import genie_client, genie_turn_outcome, health_probes
from backend.services.forced_degraded import (
    FORCED_DEGRADED_COOKIE_NAME,
    _reset_forced_degraded_for_tests,
)
from backend.services.genie_client import GenieClientError

client = TestClient(app)
ADMIN = {"X-Forwarded-Email": "ops@example.com", "X-Forwarded-Groups": "mip-admin"}
USER = {"X-Forwarded-Email": "operator@summit.example", "X-Forwarded-Groups": ""}
_REAL_PROBE_GENIE = health_probes.probe_genie


@pytest.fixture(autouse=True)
def _all_pings_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_forced_degraded_for_tests()
    client.cookies.clear()
    health_probes._probe_cache.clear()
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    monkeypatch.setattr(health_mod.settings, "databricks_host", None)


def _fail(error_class: str) -> None:
    genie_turn_outcome.record_genie_turn_failure(GenieClientError("x", error_class=error_class))


def test_admin_health_reports_no_turn_before_the_first_one() -> None:
    body = client.get("/api/admin/health", headers=ADMIN).json()
    assert body["genie_last_turn"] is None
    assert body["dependencies"]["genie"] == "up"
    assert body["dependency_reasons"] == {}
    assert body["status"] == "ok"


def test_a_recent_budget_failure_degrades_genie_with_a_reason() -> None:
    _fail("BUDGET_EXCEEDED")

    admin = client.get("/api/admin/health", headers=ADMIN).json()
    assert admin["status"] == "degraded"
    assert admin["dependencies"] == {"warehouse": "up", "lakebase": "up", "genie": "degraded"}
    assert admin["dependency_reasons"] == {"genie": "BUDGET_EXCEEDED"}
    last = admin["genie_last_turn"]
    assert set(last) == {"outcome", "error_class", "at"}
    assert (last["outcome"], last["error_class"]) == ("failed", "BUDGET_EXCEEDED")
    at = datetime.fromisoformat(last["at"].replace("Z", "+00:00"))
    assert at.utcoffset() is not None and at.utcoffset().total_seconds() == 0
    assert abs((datetime.now(UTC) - at).total_seconds()) < 60
    # The breaker is untouched: this is a turn signal, not a ping/breaker one.
    assert admin["circuit_breakers"]["genie"] == "closed"

    user = client.get("/api/health", headers=USER).json()
    assert user["status"] == "degraded"
    assert user["dependencies"]["genie"] == "degraded"
    assert user["dependency_reasons"] == {"genie": "BUDGET_EXCEEDED"}
    assert "genie_last_turn" not in user


def test_anonymous_liveness_does_not_reveal_the_turn_state() -> None:
    _fail("BUDGET_EXCEEDED")
    anon = client.get("/api/health", headers={"X-Forwarded-Email": "", "X-Forwarded-Groups": ""})
    assert anon.json() == {"status": "ok", "mode": "live"}


def test_the_authenticated_body_omits_reasons_when_nothing_is_degraded() -> None:
    genie_turn_outcome.record_genie_turn_success()
    user = client.get("/api/health", headers=USER).json()
    assert user["status"] == "ok"
    assert user["dependencies"]["genie"] == "up"
    assert "dependency_reasons" not in user


@pytest.mark.parametrize("error_class", ["RATE_LIMITED", "TIMEOUT", "FAILED", "AUTH"])
def test_non_quota_failures_are_reported_but_do_not_degrade(error_class: str) -> None:
    _fail(error_class)
    admin = client.get("/api/admin/health", headers=ADMIN).json()
    assert admin["status"] == "ok"
    assert admin["dependencies"]["genie"] == "up"
    assert admin["dependency_reasons"] == {}
    assert admin["genie_last_turn"]["error_class"] == error_class


def test_a_stale_budget_failure_stops_degrading(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail("BUDGET_EXCEEDED")
    record = genie_turn_outcome.last_genie_turn()
    assert record is not None
    later = record.monotonic_at + genie_turn_outcome.QUOTA_DEGRADED_WINDOW_S + 1.0
    monkeypatch.setattr(genie_turn_outcome, "time", SimpleNamespace(monotonic=lambda: later))
    admin = client.get("/api/admin/health", headers=ADMIN).json()
    assert admin["status"] == "ok"
    assert admin["dependencies"]["genie"] == "up"
    # The record itself is still reported for the operator.
    assert admin["genie_last_turn"]["error_class"] == "BUDGET_EXCEEDED"


def test_a_later_success_clears_the_degraded_state() -> None:
    _fail("BUDGET_EXCEEDED")
    genie_turn_outcome.record_genie_turn_success()
    admin = client.get("/api/admin/health", headers=ADMIN).json()
    assert admin["status"] == "ok"
    assert admin["dependencies"]["genie"] == "up"
    assert admin["genie_last_turn"]["outcome"] == "success"


def test_a_down_genie_stays_down_without_a_degraded_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_probes, "probe_genie", lambda: False)
    _fail("BUDGET_EXCEEDED")
    admin = client.get("/api/admin/health", headers=ADMIN).json()
    assert admin["dependencies"]["genie"] == "down"
    assert admin["dependency_reasons"] == {}


def test_a_forced_genie_drill_drops_the_degraded_reason() -> None:
    _fail("BUDGET_EXCEEDED")
    forced = client.post(
        "/api/admin/force-degraded",
        headers=ADMIN,
        json={"state": "on", "dependency": "genie", "ttl_s": 30},
    )
    assert forced.status_code == 200, forced.text
    assert client.cookies.get(FORCED_DEGRADED_COOKIE_NAME)
    user = client.get("/api/health", headers=USER).json()
    assert user["dependencies"]["genie"] == "down"
    assert "dependency_reasons" not in user


def test_health_reads_never_start_a_genie_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real ping probe runs; every turn method would blow up if called."""

    calls: list[str] = []
    pings: list[int] = []

    def _turn(name: str) -> Any:
        def _blocked(*_a: Any, **_k: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"health started a Genie turn via {name}")

        return _blocked

    stub = SimpleNamespace(
        ping=lambda: pings.append(1) is None,
        ask=_turn("ask"),
        submit_message=_turn("submit_message"),
        resume_message=_turn("resume_message"),
        peek_message=_turn("peek_message"),
    )
    monkeypatch.setattr(health_probes, "probe_genie", _REAL_PROBE_GENIE)
    monkeypatch.setattr(health_probes.settings, "genie_space_id", "space-1")
    monkeypatch.setattr(genie_client, "get_genie_client", lambda: stub)

    for _ in range(3):
        assert client.get("/api/health", headers=USER).status_code == 200
        assert client.get("/api/admin/health", headers=ADMIN).status_code == 200
    assert pings, "the real ping probe must have run (non-vacuity)"
    assert calls == []
    assert genie_turn_outcome.last_genie_turn() is None


def test_the_admin_schema_rejects_free_text_in_the_turn_record() -> None:
    at = datetime.now(UTC)
    GenieLastTurn(outcome="failed", error_class="BUDGET_EXCEEDED", at=at)
    with pytest.raises(ValidationError):
        GenieLastTurn(outcome="failed", error_class="RESOURCE_EXHAUSTED: You've reached your budget.", at=at)
    with pytest.raises(ValidationError):
        GenieLastTurn(outcome="degraded", error_class=None, at=at)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AdminHealthResponse(
            status="ok",
            mode="live",
            genie_last_turn={"outcome": "failed", "error_class": "SELECT * FROM leads", "at": at},
        )


def test_the_openapi_contract_names_the_closed_vocabulary() -> None:
    schemas = app.openapi()["components"]["schemas"]
    admin_props = schemas["AdminHealthResponse"]["properties"]
    assert "genie_last_turn" in admin_props
    turn = schemas["GenieLastTurn"]
    assert set(turn["properties"]) == {"outcome", "error_class", "at"}
    classes = next(
        option["enum"]
        for option in turn["properties"]["error_class"]["anyOf"]
        if "enum" in option
    )
    assert set(classes) == genie_turn_outcome.GENIE_TURN_ERROR_CLASSES
    assert "dependency_reasons" in schemas["HealthResponse"]["properties"]
