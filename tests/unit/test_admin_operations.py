"""Admin Operations endpoints and Databricks job controls."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.databricks_jobs import (
    DatabricksJobOperations,
    JobAlreadyRunningError,
    JobLaunch,
    JobOperationError,
    ManagedJobRun,
    ManagedJobStatus,
    get_job_operations,
)
from backend.services.lakebase import LakebaseError

client = TestClient(app)


class _FakeOps:
    def __init__(self) -> None:
        self.run_calls: list[str] = []

    def list_statuses(self) -> list[ManagedJobStatus]:
        return [
            ManagedJobStatus(
                key="gold_refresh",
                label="Rebuild scoring snapshot",
                job_name="mip_refresh_scores",
                job_id=123,
                configured=True,
                description="Rebuild gold.",
                run_order=3,
                latest_run=ManagedJobRun(
                    run_id=456,
                    life_cycle_state="TERMINATED",
                    result_state="SUCCESS",
                    state_message="done",
                    started_at="2026-05-31T13:00:00+00:00",
                    ended_at="2026-05-31T13:10:00+00:00",
                    run_page_url="https://example.com/runs/456",
                ),
            )
        ]

    def run_now(self, key: str) -> JobLaunch:
        self.run_calls.append(key)
        return JobLaunch(
            key=key,  # type: ignore[arg-type]
            label="Rebuild scoring snapshot",
            job_name="mip_refresh_scores",
            job_id=123,
            run_id=789,
            run_page_url="https://example.com/runs/789",
        )


def _override_jobs(fake: Any) -> None:
    app.dependency_overrides[get_job_operations] = lambda: fake


def _clear_jobs_override() -> None:
    app.dependency_overrides.pop(get_job_operations, None)


def test_get_operations_returns_allowlisted_job_status() -> None:
    fake = _FakeOps()
    _override_jobs(fake)
    try:
        response = client.get("/api/admin/operations")
    finally:
        _clear_jobs_override()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jobs"][0]["key"] == "gold_refresh"
    assert body["jobs"][0]["configured"] is True
    assert isinstance(body["jobs"][0]["cooldown_remaining_s"], int)
    assert body["jobs"][0]["latest_run"]["result_state"] == "SUCCESS"


def test_get_operations_surfaces_cooldown_state() -> None:
    class _SilverOps(_FakeOps):
        def list_statuses(self) -> list[ManagedJobStatus]:
            return [
                ManagedJobStatus(
                    key="silver_refresh",
                    label="Refresh source features",
                    job_name="mip_refresh_silver",
                    job_id=124,
                    configured=True,
                    description="Refresh silver.",
                    run_order=2,
                    latest_run=None,
                )
            ]

    fake = _SilverOps()
    audit = SimpleNamespace(
        list=lambda **_: [
            SimpleNamespace(created_at=datetime.now(UTC).isoformat())
        ]
    )
    _override_jobs(fake)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        response = client.get("/api/admin/operations")
    finally:
        _clear_jobs_override()
        app.dependency_overrides.pop(get_audit_store, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jobs"][0]["key"] == "silver_refresh"
    assert body["jobs"][0]["cooldown_remaining_s"] > 0


def test_operations_require_admin() -> None:
    fake = _FakeOps()
    _override_jobs(fake)
    try:
        response = client.get(
            "/api/admin/operations",
            headers={"X-Forwarded-Groups": ""},
        )
    finally:
        _clear_jobs_override()

    assert response.status_code == 403


def test_get_operations_audit_factory_failure_returns_sanitized_503() -> None:
    fake = _FakeOps()

    def _broken_audit() -> Any:
        raise LakebaseError("connection string leaked here")

    _override_jobs(fake)
    app.dependency_overrides[get_audit_store] = _broken_audit
    try:
        response = client.get("/api/admin/operations")
    finally:
        _clear_jobs_override()
        app.dependency_overrides.pop(get_audit_store, None)

    assert response.status_code == 503
    assert response.json()["detail"] == "lakebase is temporarily unavailable"
    assert "connection string" not in response.text


def test_get_operations_checks_admin_before_lakebase_resolution() -> None:
    fake = _FakeOps()

    def _broken_audit() -> Any:
        raise AssertionError("audit dependency should not resolve before RBAC")

    _override_jobs(fake)
    app.dependency_overrides[get_audit_store] = _broken_audit
    try:
        response = client.get(
            "/api/admin/operations",
            headers={"X-Forwarded-Groups": ""},
        )
    finally:
        _clear_jobs_override()
        app.dependency_overrides.pop(get_audit_store, None)

    assert response.status_code == 403


def test_run_operation_requires_explicit_confirmation() -> None:
    fake = _FakeOps()
    _override_jobs(fake)
    try:
        response = client.post(
            "/api/admin/operations/run",
            json={"job_key": "gold_refresh"},
        )
    finally:
        _clear_jobs_override()

    assert response.status_code == 400
    assert fake.run_calls == []


def test_run_operation_audits_then_triggers_job() -> None:
    fake = _FakeOps()
    _override_jobs(fake)
    try:
        response = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "gold_refresh",
                "confirm": True,
                "reason": "operator_refresh",
                "request_id": "11111111-1111-4111-8111-111111111111",
            },
        )
        audit_rows = client.get(
            "/api/audit/events?limit=5&action=admin.operation.run"
        ).json()
    finally:
        _clear_jobs_override()

    assert response.status_code == 202, response.text
    assert fake.run_calls == ["gold_refresh"]
    body = response.json()
    assert body["accepted"] is True
    assert body["run_id"] == 789
    assert body["audit_event_id"]
    assert audit_rows[0]["entity_id"] == "gold_refresh"
    assert audit_rows[0]["payload_json"]["job_key"] == "gold_refresh"
    assert audit_rows[0]["payload_json"]["job_id"] == 123
    assert audit_rows[0]["payload_json"]["run_id"] == 789


def test_run_operation_conflict_when_job_already_active() -> None:
    class _AlreadyRunning(_FakeOps):
        def run_now(self, key: str) -> JobLaunch:
            raise JobAlreadyRunningError(key, run_id=444)

    fake = _AlreadyRunning()
    _override_jobs(fake)
    try:
        response = client.post(
            "/api/admin/operations/run",
            json={"job_key": "lifecycle_sync", "confirm": True},
        )
    finally:
        _clear_jobs_override()

    assert response.status_code == 409
    assert response.json()["detail"]["run_id"] == 444


def test_run_operation_cooldown_prevents_repeat_expensive_trigger() -> None:
    fake = _FakeOps()
    _override_jobs(fake)
    try:
        first = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "silver_refresh",
                "confirm": True,
                "request_id": "22222222-2222-4222-8222-222222222222",
            },
        )
        second = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "silver_refresh",
                "confirm": True,
                "request_id": "33333333-3333-4333-8333-333333333333",
            },
        )
    finally:
        _clear_jobs_override()

    assert first.status_code == 202, first.text
    assert second.status_code == 429, second.text
    assert second.headers["Retry-After"]
    assert fake.run_calls == ["silver_refresh"]


def test_failed_operation_launch_does_not_create_cooldown_lockout() -> None:
    class _FailsOnce(_FakeOps):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def run_now(self, key: str) -> JobLaunch:
            if not self.failed:
                self.failed = True
                raise JobOperationError("jobs API down")
            return super().run_now(key)

    fake = _FailsOnce()
    _override_jobs(fake)
    try:
        first = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "fred_rates",
                "confirm": True,
                "request_id": "44444444-4444-4444-8444-444444444444",
            },
        )
        second = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "fred_rates",
                "confirm": True,
                "request_id": "55555555-5555-4555-8555-555555555555",
            },
        )
    finally:
        _clear_jobs_override()

    assert first.status_code == 503, first.text
    assert second.status_code == 202, second.text
    assert fake.run_calls == ["fred_rates"]


def test_run_operation_does_not_trigger_when_audit_is_down() -> None:
    class _BrokenAudit:
        def list(self, **_: Any) -> Any:
            raise LakebaseError("lakebase down")

        def write(self, **_: Any) -> Any:
            raise LakebaseError("lakebase down")

    fake = _FakeOps()
    _override_jobs(fake)
    app.dependency_overrides[get_audit_store] = lambda: _BrokenAudit()
    try:
        response = client.post(
            "/api/admin/operations/run",
            json={"job_key": "fred_rates", "confirm": True},
        )
    finally:
        _clear_jobs_override()
        app.dependency_overrides.pop(get_audit_store, None)

    assert response.status_code == 503
    assert response.json()["detail"] == "lakebase is temporarily unavailable"
    assert fake.run_calls == []


def test_databricks_job_operations_uses_env_job_id_and_blocks_active_run(monkeypatch) -> None:
    monkeypatch.setenv("MIP_GOLD_REFRESH_JOB_ID", "123")
    run = SimpleNamespace(
        run_id=456,
        start_time=1_780_000_000_000,
        end_time=None,
        run_page_url="https://example.com/runs/456",
        state=SimpleNamespace(
            life_cycle_state="RUNNING",
            result_state=None,
            state_message="in progress",
        ),
    )
    workspace = SimpleNamespace(
        jobs=SimpleNamespace(
            list_runs=lambda **kwargs: [run],
            run_now=lambda **kwargs: SimpleNamespace(run_id=999),
        )
    )
    ops = DatabricksJobOperations(workspace)

    status = ops.status_for("gold_refresh")
    assert status.job_id == 123
    assert status.latest_run is not None
    assert status.latest_run.active is True

    try:
        ops.run_now("gold_refresh")
    except JobAlreadyRunningError as exc:
        assert exc.run_id == 456
    else:  # pragma: no cover
        raise AssertionError("expected active run to block a duplicate refresh")


def test_databricks_job_operations_deployed_app_does_not_fall_back_to_job_listing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MIP_GOLD_REFRESH_JOB_ID", raising=False)
    monkeypatch.setattr(settings, "app_env", "sandbox")

    def _list_should_not_run(**_: Any) -> list[Any]:
        raise AssertionError("deployed app must not require workspace-wide jobs.list")

    workspace = SimpleNamespace(
        jobs=SimpleNamespace(
            list=_list_should_not_run,
            list_runs=lambda **_: [],
        )
    )
    ops = DatabricksJobOperations(workspace)

    status = ops.status_for("gold_refresh")
    assert status.job_id is None
    assert status.configured is False
