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

    def run_now(
        self,
        key: str,
        *,
        idempotency_token: str,
        replay: bool = False,
    ) -> JobLaunch:
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
            json={
                "job_key": "gold_refresh",
                "request_id": "10111111-1111-4111-8111-111111111111",
            },
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


def test_run_operation_replays_same_request_without_second_launch(monkeypatch) -> None:
    from backend.api import admin

    monkeypatch.setitem(admin._JOB_COOLDOWN_SECONDS, "gold_refresh", 0)
    fake = _FakeOps()
    _override_jobs(fake)
    payload = {
        "job_key": "gold_refresh",
        "confirm": True,
        "reason": "operator_refresh",
        "request_id": "17111111-1111-4111-8111-111111111111",
    }
    try:
        first = client.post("/api/admin/operations/run", json=payload)
        replay = client.post("/api/admin/operations/run", json=payload)
    finally:
        _clear_jobs_override()

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert replay.json()["run_id"] == first.json()["run_id"] == 789
    assert replay.json()["audit_event_id"] == first.json()["audit_event_id"]
    assert fake.run_calls == ["gold_refresh"]


def test_run_operation_rejects_request_id_payload_mismatch(monkeypatch) -> None:
    from backend.api import admin

    monkeypatch.setitem(admin._JOB_COOLDOWN_SECONDS, "gold_refresh", 0)
    fake = _FakeOps()
    _override_jobs(fake)
    request_id = "18111111-1111-4111-8111-111111111111"
    try:
        first = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "gold_refresh",
                "confirm": True,
                "reason": "operator_refresh",
                "request_id": request_id,
            },
        )
        mismatch = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "gold_refresh",
                "confirm": True,
                "reason": "support_triage",
                "request_id": request_id,
            },
        )
    finally:
        _clear_jobs_override()

    assert first.status_code == 202, first.text
    assert mismatch.status_code == 409, mismatch.text
    assert fake.run_calls == ["gold_refresh"]


def test_run_operation_retry_closes_post_launch_audit_failure(monkeypatch) -> None:
    from backend.api import admin

    monkeypatch.setitem(admin._JOB_COOLDOWN_SECONDS, "gold_refresh", 0)

    class _RetryOps(_FakeOps):
        def __init__(self) -> None:
            super().__init__()
            self.details: list[tuple[str, str, bool]] = []

        def run_now(
            self,
            key: str,
            *,
            idempotency_token: str,
            replay: bool = False,
        ) -> JobLaunch:
            self.details.append((key, idempotency_token, replay))
            return super().run_now(
                key,
                idempotency_token=idempotency_token,
                replay=replay,
            )

    class _AuditFailsFinalOnce:
        def __init__(self) -> None:
            self.events: list[Any] = []
            self.failed = False

        def list(self, **filters: Any) -> list[Any]:
            rows = list(reversed(self.events))
            for key in ("actor", "entity_id", "event_type", "action"):
                value = filters.get(key)
                if value is None:
                    continue
                attr = "actor" if key == "actor" else key
                rows = [row for row in rows if getattr(row, attr, None) == value]
            return rows[: int(filters.get("limit", 50))]

        def write(self, **values: Any) -> Any:
            if values.get("event_type") == "ADMIN_OPERATION_RUN" and not self.failed:
                self.failed = True
                raise LakebaseError("simulated post-launch audit interruption")
            event = SimpleNamespace(
                event_id=f"audit-{len(self.events) + 1}",
                actor=values["actor"],
                action=values["action"],
                entity_id=values["entity_id"],
                event_type=values["event_type"],
                request_id=values.get("request_id"),
                payload_json=values.get("payload_json") or {},
                created_at=datetime.now(UTC).isoformat(),
            )
            self.events.append(event)
            return event

    fake = _RetryOps()
    audit = _AuditFailsFinalOnce()
    _override_jobs(fake)
    app.dependency_overrides[get_audit_store] = lambda: audit
    request_id = "19111111-1111-4111-8111-111111111111"
    payload = {
        "job_key": "gold_refresh",
        "confirm": True,
        "reason": "operator_refresh",
        "request_id": request_id,
    }
    try:
        interrupted = client.post("/api/admin/operations/run", json=payload)
        replay = client.post("/api/admin/operations/run", json=payload)
    finally:
        _clear_jobs_override()
        app.dependency_overrides.pop(get_audit_store, None)

    assert interrupted.status_code == 503, interrupted.text
    assert replay.status_code == 202, replay.text
    assert replay.json()["run_id"] == 789
    assert fake.details == [
        ("gold_refresh", request_id, False),
        ("gold_refresh", request_id, True),
    ]
    assert [event.event_type for event in audit.events].count("ADMIN_OPERATION_RUN") == 1


def test_run_operation_conflict_when_job_already_active() -> None:
    class _AlreadyRunning(_FakeOps):
        def run_now(
            self,
            key: str,
            *,
            idempotency_token: str,
            replay: bool = False,
        ) -> JobLaunch:
            raise JobAlreadyRunningError(key, run_id=444)

    fake = _AlreadyRunning()
    _override_jobs(fake)
    try:
        response = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "fred_rates",
                "confirm": True,
                "request_id": "12111111-1111-4111-8111-111111111111",
            },
        )
    finally:
        _clear_jobs_override()

    assert response.status_code == 409
    assert response.json()["detail"]["run_id"] == 444


def test_lifecycle_operation_uses_warehouse_sync(monkeypatch) -> None:
    from backend.services import lifecycle_sync
    from backend.services.lifecycle_sync import LifecycleSyncResult

    fake = _FakeOps()
    _override_jobs(fake)
    calls: list[str] = []

    def _fake_sync() -> LifecycleSyncResult:
        calls.append("sync")
        return LifecycleSyncResult(
            lakebase_rows=3,
            mirrored_rows=100,
            funnel_snapshot_rows=9,
        )

    monkeypatch.setattr(lifecycle_sync, "sync_lifecycle_state_via_warehouse", _fake_sync)
    try:
        response = client.post(
            "/api/admin/operations/run",
            json={
                "job_key": "lifecycle_sync",
                "confirm": True,
                "request_id": "44444444-4444-4444-8444-444444444444",
            },
        )
    finally:
        _clear_jobs_override()

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job_name"] == "warehouse_lifecycle_sync"
    assert body["run_id"] is None
    assert fake.run_calls == []
    assert calls == ["sync"]


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

        def run_now(
            self,
            key: str,
            *,
            idempotency_token: str,
            replay: bool = False,
        ) -> JobLaunch:
            if not self.failed:
                self.failed = True
                raise JobOperationError("jobs API down")
            return super().run_now(
                key,
                idempotency_token=idempotency_token,
                replay=replay,
            )

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
            json={
                "job_key": "fred_rates",
                "confirm": True,
                "request_id": "13111111-1111-4111-8111-111111111111",
            },
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
        ops.run_now(
            "gold_refresh",
            idempotency_token="14111111-1111-4111-8111-111111111111",
        )
    except JobAlreadyRunningError as exc:
        assert exc.run_id == 456
    else:  # pragma: no cover
        raise AssertionError("expected active run to block a duplicate refresh")


def test_databricks_job_operations_returns_recent_run_history(monkeypatch) -> None:
    monkeypatch.setenv("MIP_FRED_RATES_JOB_ID", "123")
    def _run(run_id: int, *, result_state: str = "SUCCESS") -> Any:
        return SimpleNamespace(
            run_id=run_id,
            start_time=1_780_000_000_000 - (456 - run_id) * 60_000,
            end_time=1_780_000_060_000 - (456 - run_id) * 60_000,
            run_page_url=f"https://example.com/runs/{run_id}",
            state=SimpleNamespace(
                life_cycle_state="TERMINATED",
                result_state=result_state,
                state_message="done" if result_state == "SUCCESS" else "failed",
            ),
        )

    runs = [
        _run(456),
        _run(455, result_state="FAILED"),
        _run(454),
        _run(453),
        _run(452),
        _run(451),
        _run(450),
    ]
    calls: list[dict[str, Any]] = []
    workspace = SimpleNamespace(
        jobs=SimpleNamespace(
            list_runs=lambda **kwargs: calls.append(kwargs) or runs,
            run_now=lambda **kwargs: SimpleNamespace(run_id=999),
        )
    )
    ops = DatabricksJobOperations(workspace)

    status = ops.status_for("fred_rates")

    assert calls == [{"job_id": 123, "limit": 5}]
    assert status.latest_run is not None
    assert status.latest_run.run_id == 456
    assert [run.run_id for run in status.recent_runs] == [456, 455, 454, 453, 452]
    assert status.recent_runs[0].result_state == "SUCCESS"
    assert status.recent_runs[1].result_state == "FAILED"


def test_databricks_job_operations_bounds_active_run_fallback(monkeypatch) -> None:
    monkeypatch.setenv("MIP_FRED_RATES_JOB_ID", "123")
    calls: list[dict[str, Any]] = []

    def _list_runs(**kwargs: Any) -> list[Any]:
        calls.append(kwargs)
        if kwargs.get("active_only"):
            raise TypeError("old SDK does not support active_only")
        return [
            SimpleNamespace(
                run_id=run_id,
                start_time=1_780_000_000_000 - run_id,
                end_time=None,
                run_page_url=f"https://example.com/runs/{run_id}",
                state=SimpleNamespace(
                    life_cycle_state="RUNNING" if run_id == 9 else "TERMINATED",
                    result_state=None,
                    state_message="done",
                ),
            )
            for run_id in range(12)
        ]

    workspace = SimpleNamespace(
        jobs=SimpleNamespace(
            list_runs=_list_runs,
            run_now=lambda **kwargs: SimpleNamespace(run_id=999),
        )
    )
    ops = DatabricksJobOperations(workspace)

    try:
        ops.run_now(
            "fred_rates",
            idempotency_token="15111111-1111-4111-8111-111111111111",
        )
    except JobAlreadyRunningError as exc:
        assert exc.run_id == 9
    else:  # pragma: no cover
        raise AssertionError("expected active run to block a duplicate refresh")

    assert calls == [
        {"job_id": 123, "active_only": True, "limit": 1},
        {"job_id": 123, "limit": 10},
    ]


def test_databricks_job_operations_handles_run_now_waiter_shape(monkeypatch) -> None:
    class _RunNowWaiter:
        def __init__(self, run_id: int) -> None:
            self._bind = {"run_id": run_id}

        def __getattr__(self, key: str) -> Any:
            return self._bind[key]

        def bind(self) -> dict[str, int]:
            return dict(self._bind)

    monkeypatch.setenv("MIP_FRED_RATES_JOB_ID", "321")
    monkeypatch.setattr(settings, "databricks_host", "")
    workspace = SimpleNamespace(
        _api=SimpleNamespace(
            _cfg=SimpleNamespace(
                host="https://dbc-example.cloud.databricks.com",
                workspace_id="12345",
            )
        ),
        jobs=SimpleNamespace(
            list_runs=lambda **_: [],
            run_now=lambda **_: _RunNowWaiter(999),
        ),
    )
    ops = DatabricksJobOperations(workspace)

    launch = ops.run_now(
        "fred_rates",
        idempotency_token="16111111-1111-4111-8111-111111111111",
    )

    assert launch.run_id == 999
    assert launch.run_page_url == "https://dbc-example.cloud.databricks.com/?o=12345#job/321/run/999"


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
