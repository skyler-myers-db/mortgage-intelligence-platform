"""Real scheduler state for saved watchlists (audit 2026-09-21 flow-08 slice 2, wow-ai-4).

The UI used to hard-code "scheduler paused". The App now reads the
``mip_growth_agent_monitor_scheduler`` job's schedule through the Jobs API,
read-only: it is never a managed job, so Admin Operations can neither list
nor run it. These tests pin every state mapping, the honest failure modes,
the no-client path when the job is unbound, the process cache, the GET
/growth-agent field and the capability row.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.services.growth_agent_scheduler as scheduler
from backend.config.settings import Settings
from backend.main import app
from backend.schemas.admin import AdminOperationJobKey, AdminOperationRunRequest
from backend.services.capabilities import (
    LiveCapabilityStatus,
    collect_live_capability_statuses,
    probe_capabilities,
)
from backend.services.capabilities_models import CapabilityStatus
from backend.services.databricks_jobs import (
    GROWTH_AGENT_SCHEDULER_JOB,
    MANAGED_JOBS,
    DatabricksJobOperations,
)
from backend.services.lakebase import get_lakebase_client
from backend.services.observability import emit as _real_emit

ENV = GROWTH_AGENT_SCHEDULER_JOB.env_var
JOB_NAME = GROWTH_AGENT_SCHEDULER_JOB.job_name


class _Jobs:
    def __init__(self, job: Any = None, *, error: Exception | None = None) -> None:
        self.job = job
        self.error = error
        self.gets: list[int] = []

    def get(self, *, job_id: int) -> Any:
        self.gets.append(job_id)
        if self.error is not None:
            raise self.error
        return self.job

    def run_now(self, **_: Any) -> Any:  # pragma: no cover - must never be reached
        raise AssertionError("the scheduler job is read-only")


def _job(*, name: str = f"[dev mip] {JOB_NAME}", schedule: str | None = None, trigger: str | None = None) -> Any:
    def holder(status: str | None) -> Any:
        return None if status is None else SimpleNamespace(pause_status=SimpleNamespace(value=status))

    return SimpleNamespace(settings=SimpleNamespace(name=name, schedule=holder(schedule), trigger=holder(trigger)))


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(ENV, raising=False)
    scheduler.reset_growth_agent_scheduler_cache()
    yield
    scheduler.reset_growth_agent_scheduler_cache()


def _status_with(jobs: _Jobs, monkeypatch: pytest.MonkeyPatch, *, job_id: str = "4242") -> Any:
    monkeypatch.setenv(ENV, job_id)
    return scheduler.growth_agent_scheduler_status(workspace_factory=lambda: SimpleNamespace(jobs=jobs))


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (_job(schedule="UNPAUSED"), ("active", "job_schedule")),
        (_job(schedule="PAUSED"), ("paused", "job_schedule")),
        (_job(trigger="UNPAUSED"), ("active", "job_schedule")),
        (_job(trigger="PAUSED"), ("paused", "job_schedule")),
        (_job(schedule="PAUSED", trigger="UNPAUSED"), ("paused", "job_schedule")),
        (_job(), ("paused", "no_schedule")),
    ],
    ids=["schedule-on", "schedule-off", "trigger-on", "trigger-off", "schedule-wins", "no-schedule"],
)
def test_every_pause_status_maps_to_a_state(job: Any, expected: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = _Jobs(job)
    status = _status_with(jobs, monkeypatch)
    assert (status.state, status.reason) == expected
    assert jobs.gets == [4242]


@pytest.mark.parametrize(
    "jobs",
    [
        _Jobs(_job(name="mip_refresh_scores", schedule="UNPAUSED")),
        _Jobs(_job(schedule="SOMETHING_NEW")),
        _Jobs(error=RuntimeError("PERMISSION_DENIED")),
    ],
    ids=["wrong-job", "unknown-pause-status", "jobs-api-error"],
)
def test_a_failed_or_mismatched_lookup_is_unavailable_and_logged_by_type_only(
    jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    def record(logger: Any, event: str, **fields: Any) -> None:
        events.append((event, fields))
        _real_emit(logger, event, **fields)

    monkeypatch.setattr(scheduler, "emit", record)
    status = _status_with(jobs, monkeypatch)
    assert (status.state, status.reason) == ("unavailable", "lookup_failed")
    assert [event for event, _ in events] == ["growth_agent_scheduler_state_unavailable"]
    assert set(events[0][1]) == {"level", "exc_type"}


def test_an_unbound_job_builds_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_client() -> Any:
        raise AssertionError("no WorkspaceClient may be built without a binding")

    status = scheduler.growth_agent_scheduler_status(workspace_factory=no_client)
    assert (status.state, status.reason) == ("unavailable", "not_configured")


def test_resolved_states_are_cached_with_one_jobs_api_read(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = _Jobs(_job(schedule="PAUSED"))
    for _ in range(5):
        assert _status_with(jobs, monkeypatch).state == "paused"
    assert jobs.gets == [4242]
    assert scheduler.RESOLVED_TTL_S == 300 and scheduler.LOOKUP_FAILED_TTL_S == 60


def test_a_failed_lookup_is_retried_after_the_short_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
    jobs = _Jobs(error=RuntimeError("timeout"))
    assert _status_with(jobs, monkeypatch).reason == "lookup_failed"
    clock[0] += 59
    assert _status_with(jobs, monkeypatch).reason == "lookup_failed"
    assert len(jobs.gets) == 1
    clock[0] += 2
    jobs.error = None
    jobs.job = _job(schedule="UNPAUSED")
    assert _status_with(jobs, monkeypatch).state == "active"
    assert len(jobs.gets) == 2


def test_the_sdk_client_is_bounded_by_names_the_pinned_sdk_accepts() -> None:
    from databricks.sdk.core import Config

    assert {"http_timeout_seconds", "retry_timeout_seconds"} <= set(dir(Config))
    assert scheduler.HTTP_TIMEOUT_S <= 3 and scheduler.RETRY_TIMEOUT_S <= 5


def test_the_scheduler_job_is_never_a_managed_or_runnable_job() -> None:
    assert JOB_NAME not in {definition.job_name for definition in MANAGED_JOBS.values()}
    assert GROWTH_AGENT_SCHEDULER_JOB.key not in MANAGED_JOBS
    assert GROWTH_AGENT_SCHEDULER_JOB.key not in get_args(AdminOperationJobKey)
    with pytest.raises(ValidationError):
        AdminOperationRunRequest.model_validate({"job_key": GROWTH_AGENT_SCHEDULER_JOB.key, "confirm": True})
    with pytest.raises(KeyError):
        DatabricksJobOperations(SimpleNamespace(jobs=_Jobs())).run_now(
            GROWTH_AGENT_SCHEDULER_JOB.key,  # type: ignore[arg-type]
            idempotency_token="t",
        )


class _EmptyLakebase:
    def fetchall(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        return []


@pytest.mark.parametrize(("pause", "state"), [("UNPAUSED", "active"), ("PAUSED", "paused")])
def test_get_growth_agent_serves_the_scheduler_state(
    pause: str, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = _Jobs(_job(schedule=pause))
    _status_with(jobs, monkeypatch)  # warm the process cache with this fake workspace
    app.dependency_overrides[get_lakebase_client] = lambda: _EmptyLakebase()
    try:
        response = TestClient(app).get("/api/growth-agent", headers={"X-Forwarded-Email": "lo@example.com"})
    finally:
        app.dependency_overrides.pop(get_lakebase_client, None)
    assert response.status_code == 200, response.text
    assert response.json()["scheduler_state"] == state


def test_get_growth_agent_says_unavailable_when_unbound() -> None:
    app.dependency_overrides[get_lakebase_client] = lambda: _EmptyLakebase()
    try:
        response = TestClient(app).get("/api/growth-agent", headers={"X-Forwarded-Email": "lo@example.com"})
    finally:
        app.dependency_overrides.pop(get_lakebase_client, None)
    assert response.status_code == 200, response.text
    assert response.json()["scheduler_state"] == "unavailable"


def _capability(settings: Settings | None = None, **live: LiveCapabilityStatus) -> Any:
    rows = probe_capabilities(settings or Settings(), live_statuses=live or None)
    return next(row for row in rows if row.key == "growth_agent_scheduler")


def test_the_capability_row_is_claimable_only_after_a_live_unpaused_read(monkeypatch: pytest.MonkeyPatch) -> None:
    unbound = _capability()
    assert unbound.status == CapabilityStatus.NOT_PROVISIONED and not unbound.claimable
    assert unbound.label == "Scheduled watchlist refresh" and unbound.ga is True

    monkeypatch.setenv(ENV, "4242")
    bound = _capability()
    assert bound.status == CapabilityStatus.CONFIGURED and not bound.claimable

    paused = collect_live_capability_statuses(
        settings=Settings(), workspace_client=SimpleNamespace(jobs=_Jobs(_job(schedule="PAUSED")))
    )["growth_agent_scheduler"]
    assert paused.available is False
    assert _capability(growth_agent_scheduler=paused).claimable is False

    live = collect_live_capability_statuses(
        settings=Settings(), workspace_client=SimpleNamespace(jobs=_Jobs(_job(schedule="UNPAUSED")))
    )["growth_agent_scheduler"]
    assert live.available is True
    available = _capability(growth_agent_scheduler=live)
    assert available.status == CapabilityStatus.AVAILABLE and available.claimable


def test_an_unbound_job_is_never_live_probed() -> None:
    statuses = collect_live_capability_statuses(
        settings=Settings(), workspace_client=SimpleNamespace(jobs=_Jobs(_job(schedule="UNPAUSED")))
    )
    assert "growth_agent_scheduler" not in statuses
