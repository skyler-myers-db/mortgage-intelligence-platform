"""One warehouse keep-warm policy (2026-09-21 audit ``delivery-v1``, critic fix 21).

Health no longer runs ``SELECT 1`` (see test_warehouse_state_probe.py), which
removes the accidental keep-warm. What keeps the warehouse warm is now one
explicit policy, ``MIP_WAREHOUSE_KEEP_WARM`` = off | activity | scheduled,
resolved by ``keep_warm.effective_policy``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend.main as main_module
from backend.config.settings import Settings, settings
from backend.main import app
from backend.services import health_probes, keep_warm

REPO = Path(__file__).resolve().parents[2]
AUTHENTICATED = {"X-Forwarded-Email": "ops@example.com"}
ADMIN = {"X-Forwarded-Email": "ops@example.com", "X-Forwarded-Groups": "mip-admin"}


class _RecordingExecutor(Executor):
    def __init__(self) -> None:
        self.jobs: list[Callable[[], Any]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.jobs.append(lambda: fn(*args, **kwargs))
        return Future()


class _Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def pings(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> Iterator[_RecordingExecutor]:
    executor = _RecordingExecutor()
    keep_warm._reset_for_tests(now=clock, executor=executor)
    for name in ("probe_warehouse", "probe_lakebase", "probe_genie"):
        monkeypatch.setattr(health_probes, name, lambda: True)
    health_probes._probe_cache.clear()
    yield executor
    keep_warm._reset_for_tests()
    health_probes._probe_cache.clear()


def _activity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_warehouse_keep_warm", "activity")
    monkeypatch.setattr(settings, "mip_warehouse_keep_warm_activity_window_min", 15)


def test_policy_is_off_by_default_and_off_never_pings(pings: _RecordingExecutor) -> None:
    assert Settings(_env_file=None).mip_warehouse_keep_warm == "off"
    assert settings.mip_warehouse_keep_warm == "off"

    response = TestClient(app).get("/api/health?idle_s=0", headers=AUTHENTICATED)

    assert response.status_code == 200
    assert pings.jobs == []


def test_an_active_authenticated_tab_sends_one_select_1_ping(
    monkeypatch: pytest.MonkeyPatch, pings: _RecordingExecutor
) -> None:
    _activity(monkeypatch)
    statements: list[str] = []

    class _Client:
        def execute_one(self, sql: str) -> dict[str, int]:
            statements.append(sql)
            return {"keep_warm": 1}

    monkeypatch.setattr("backend.services.databricks_sql.get_sql_client", lambda: _Client())

    assert TestClient(app).get("/api/health?idle_s=30", headers=AUTHENTICATED).status_code == 200
    assert len(pings.jobs) == 1

    pings.jobs[0]()
    assert statements == ["SELECT 1 AS keep_warm"]


def test_a_second_actor_or_tab_inside_240_s_sends_no_second_ping(
    monkeypatch: pytest.MonkeyPatch, pings: _RecordingExecutor, clock: _Clock
) -> None:
    _activity(monkeypatch)

    class _Client:
        def execute_one(self, sql: str) -> None:
            raise TimeoutError("warehouse still waking")

    monkeypatch.setattr("backend.services.databricks_sql.get_sql_client", lambda: _Client())
    client = TestClient(app)
    client.get("/api/health?idle_s=0", headers=AUTHENTICATED)
    pings.jobs.pop()()  # the ping ran and failed quietly (logged, never raised)

    clock.now += keep_warm.KEEP_WARM_PING_INTERVAL_S - 1
    client.get("/api/health?idle_s=0", headers={"X-Forwarded-Email": "second@example.com"})
    client.get("/api/health?idle_s=3", headers=AUTHENTICATED)
    assert pings.jobs == [], "process-wide: one ping per 240 s whoever polls"

    clock.now += 1
    client.get("/api/health?idle_s=0", headers=AUTHENTICATED)
    assert len(pings.jobs) == 1


def test_no_ping_is_submitted_while_one_is_in_flight(
    monkeypatch: pytest.MonkeyPatch, pings: _RecordingExecutor, clock: _Clock
) -> None:
    _activity(monkeypatch)
    assert keep_warm.note_activity(0) is True
    clock.now += keep_warm.KEEP_WARM_PING_INTERVAL_S * 2

    assert keep_warm.note_activity(0) is False, "the first ping has not finished"
    assert len(pings.jobs) == 1


def test_a_tab_idle_beyond_the_window_sends_no_ping(
    monkeypatch: pytest.MonkeyPatch, pings: _RecordingExecutor
) -> None:
    _activity(monkeypatch)

    TestClient(app).get(f"/api/health?idle_s={15 * 60 + 1}", headers=AUTHENTICATED)

    assert pings.jobs == []


def test_anonymous_and_admin_health_never_ping(
    monkeypatch: pytest.MonkeyPatch, pings: _RecordingExecutor
) -> None:
    _activity(monkeypatch)
    client = TestClient(app)

    anonymous = client.get("/api/health?idle_s=0")
    admin = client.get("/api/admin/health?idle_s=0", headers=ADMIN)

    assert anonymous.status_code == 200
    assert set(anonymous.json()) == {"status", "mode"}
    assert admin.status_code == 200
    assert pings.jobs == []


@pytest.mark.parametrize("raw", ["abc", "-1", "1e3", "86401", "123456", "", "%EF%BC%91", "1.5", "0x10"])
def test_a_malformed_idle_hint_is_ignored_and_health_never_422s(
    monkeypatch: pytest.MonkeyPatch, pings: _RecordingExecutor, raw: str
) -> None:
    _activity(monkeypatch)

    response = TestClient(app).get(f"/api/health?idle_s={raw}", headers=AUTHENTICATED)

    assert response.status_code == 200
    assert pings.jobs == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", 0), (" 42 ", 42), ("86400", 86400), ("86401", None), ("4.2", None), ("٣", None), (None, None)],
)
def test_parse_idle_hint(raw: str | None, expected: int | None) -> None:
    assert keep_warm.parse_idle_hint(raw) == expected


def test_scheduled_without_an_interval_resolves_to_off_with_an_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "mip_warehouse_keep_warm", "scheduled")
    monkeypatch.setattr(settings, "mip_leads_warm_interval_s", 0.0)

    with caplog.at_level(logging.INFO, logger=keep_warm.log.name):
        assert keep_warm.log_startup_policy() == "off"

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == ["warehouse_keep_warm_scheduled_without_interval"]


def test_the_ping_interval_is_well_inside_the_bundle_auto_stop() -> None:
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")
    auto_stop_mins = int(re.search(r"auto_stop_mins:\s*(\d+)", bundle).group(1))  # type: ignore[union-attr]

    assert auto_stop_mins == 10
    assert 60 * auto_stop_mins > keep_warm.KEEP_WARM_PING_INTERVAL_S


# --- the startup wiring, through the real lifespan ---------------------------


@pytest.fixture
def live_lifespan(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Run ``backend.main._lifespan`` down its non-pytest branch with every
    dependency warm stubbed; returns the intervals the rewarm loop started with."""
    started: list[float] = []

    async def fake_loop(interval_s: float) -> None:
        started.append(interval_s)
        await asyncio.Event().wait()

    monkeypatch.setattr(main_module, "_running_under_pytest", lambda: False)
    monkeypatch.setattr(type(settings), "require_databricks_creds", lambda _self: None)
    for name in (
        "check_trust_boundary_at_startup",
        "_warm_warehouse",
        "_warm_lakebase",
        "_warm_hot_lead_cache",
        "warm_governed_place_dimension",
    ):
        monkeypatch.setattr(main_module, name, lambda: None)
    monkeypatch.setattr(main_module, "_lead_cache_rewarm_loop", fake_loop)
    return started


def test_a_positive_interval_under_off_warns_and_starts_no_loop(
    monkeypatch: pytest.MonkeyPatch, live_lifespan: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "mip_warehouse_keep_warm", "off")
    monkeypatch.setattr(settings, "mip_leads_warm_interval_s", 120.0)

    with caplog.at_level(logging.INFO, logger=keep_warm.log.name), TestClient(app):
        pass

    assert live_lifespan == []
    messages = [r.getMessage() for r in caplog.records if r.name == keep_warm.log.name]
    assert messages.count("warehouse_keep_warm_interval_ignored") == 1
    assert messages.count("warehouse_keep_warm_policy") == 1


def test_scheduled_starts_the_refresh_ahead_loop(
    monkeypatch: pytest.MonkeyPatch, live_lifespan: list[float]
) -> None:
    monkeypatch.setattr(settings, "mip_warehouse_keep_warm", "scheduled")
    monkeypatch.setattr(settings, "mip_leads_warm_interval_s", 120.0)

    with TestClient(app):
        pass

    assert live_lifespan == [120.0]
