"""Warehouse health from the lifecycle state, not SQL (audit delivery-01 / -v1).

A routine serverless resume used to read as an outage: health ran ``SELECT 1``
inside a 3 s budget, so the first visit after an auto-stop showed a red
Degraded pill. The probe now reads ``GET /api/2.0/sql/warehouses/{id}``:
``STARTING`` is ``resuming`` (not an outage), and any read failure falls back
to the old ``SELECT 1`` probe, which never yields ``resuming``.
"""
from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.sql import State
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services import health_probes, resilience

AUTHENTICATED = {"X-Forwarded-Email": "ops@example.com"}


class _Warehouses:
    def __init__(self, state: Any = None, error: BaseException | None = None) -> None:
        self._state = state
        self._error = error
        self.calls: list[str] = []

    def get(self, *, id: str) -> Any:  # noqa: A002 -- SDK keyword
        self.calls.append(id)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(state=self._state)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "databricks_warehouse_id", "wh-fixture")
    resilience._reset_breakers_for_tests()
    health_probes._probe_cache.clear()
    yield
    resilience._reset_breakers_for_tests()
    health_probes._probe_cache.clear()


def _use_state(monkeypatch: pytest.MonkeyPatch, warehouses: _Warehouses) -> list[bool]:
    monkeypatch.setattr(
        health_probes, "_warehouse_state_client", lambda: SimpleNamespace(warehouses=warehouses)
    )
    select_one_calls: list[bool] = []

    def select_one() -> bool:
        select_one_calls.append(True)
        return True

    monkeypatch.setattr(health_probes, "_probe_warehouse_select_one", select_one)
    return select_one_calls


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (State.RUNNING, "up"),
        (State.STARTING, "resuming"),
        (State.STOPPED, "up"),
        (State.STOPPING, "up"),
        (State.DELETING, "down"),
        (State.DELETED, "down"),
    ],
)
def test_every_warehouse_state_maps_without_running_sql(
    monkeypatch: pytest.MonkeyPatch, state: State, expected: str
) -> None:
    warehouses = _Warehouses(state=state)
    select_one = _use_state(monkeypatch, warehouses)

    assert health_probes.probe_warehouse() == expected
    assert warehouses.calls == ["wh-fixture"]
    assert select_one == [], "a readable state never issues SELECT 1 (no accidental keep-warm)"


@pytest.mark.parametrize(
    "warehouses",
    [
        _Warehouses(error=TimeoutError("state read timed out")),
        _Warehouses(state=None),
        _Warehouses(state=SimpleNamespace(value="HIBERNATING")),
    ],
    ids=["read-error", "no-state", "unknown-state"],
)
@pytest.mark.parametrize("select_one_up", [True, False])
def test_an_unreadable_state_falls_back_to_select_one_and_never_resumes(
    monkeypatch: pytest.MonkeyPatch, warehouses: _Warehouses, select_one_up: bool
) -> None:
    monkeypatch.setattr(
        health_probes, "_warehouse_state_client", lambda: SimpleNamespace(warehouses=warehouses)
    )
    monkeypatch.setattr(health_probes, "_probe_warehouse_select_one", lambda: select_one_up)

    assert health_probes.probe_warehouse() == ("up" if select_one_up else "down")


def test_the_state_read_failure_logs_the_exception_type_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret_text = "token=dapi-should-never-be-logged"
    _use_state(monkeypatch, _Warehouses(error=PermissionError(secret_text)))

    with caplog.at_level("WARNING", logger=health_probes.log.name):
        health_probes.probe_warehouse()

    failures = [r for r in caplog.records if r.getMessage() == "warehouse_state_read_failed"]
    assert failures, "the fallback is announced"
    assert secret_text not in caplog.text


def _stub_other_dependencies(monkeypatch: pytest.MonkeyPatch, warehouse: object) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: warehouse)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)


def test_resuming_with_lakebase_and_genie_up_is_not_an_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_other_dependencies(monkeypatch, "resuming")

    body = TestClient(app).get("/api/health", headers=AUTHENTICATED).json()

    assert body["status"] == "ok"
    assert body["dependencies"] == {"warehouse": "resuming", "lakebase": "up", "genie": "up"}


def test_an_open_breaker_turns_resuming_into_down_and_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_other_dependencies(monkeypatch, "resuming")
    breaker = resilience.get_breaker("warehouse", failure_threshold=1, cooldown_s=60)
    breaker.record_failure()
    assert breaker.state == "open"

    body = TestClient(app).get("/api/health", headers=AUTHENTICATED).json()

    assert body["status"] == "degraded"
    assert body["dependencies"]["warehouse"] == "down"


def test_legacy_boolean_probes_still_normalise(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_other_dependencies(monkeypatch, False)

    status, deps = health_probes.probe_snapshot()

    assert (status, deps["warehouse"]) == ("degraded", "down")


def test_anonymous_health_stays_status_and_mode_and_runs_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> bool:
        raise AssertionError("anonymous liveness must not probe a billable dependency")

    monkeypatch.setattr(health_probes, "probe_warehouse", forbidden)
    monkeypatch.setattr(health_probes, "probe_lakebase", forbidden)
    monkeypatch.setattr(health_probes, "probe_genie", forbidden)

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert set(response.json()) == {"status", "mode"}
