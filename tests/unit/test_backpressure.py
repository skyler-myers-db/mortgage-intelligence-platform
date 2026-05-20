from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import _backpressure_controller, app
from backend.services import health_probes
from backend.services.backpressure import BackpressureController


def test_backpressure_controller_rate_limits_by_actor_and_scope() -> None:
    now = {"t": 0.0}
    controller = BackpressureController(now=lambda: now["t"])
    budget = controller.classify("GET", "/api/borrowers/B-102FL7THC6Q3L")
    versioned_budget = controller.classify("GET", "/api/v1/borrowers/B-102FL7THC6Q3L")
    assert budget is not None
    assert versioned_budget == budget
    object.__setattr__(budget, "requests_per_minute", 2)

    assert controller.check_rate("sam@summit.example", budget) is None
    assert controller.check_rate("sam@summit.example", budget) is None
    assert controller.check_rate("sam@summit.example", budget) == 30
    assert controller.check_rate("maya@summit.example", budget) is None

    now["t"] = 30.0
    assert controller.check_rate("sam@summit.example", budget) is None


def test_backpressure_controller_dependency_semaphore_fails_fast() -> None:
    controller = BackpressureController(warehouse_concurrency=1)

    acquired, token = controller.acquire_dependency("warehouse")
    assert acquired is True
    assert token is not None

    second, second_token = controller.acquire_dependency("warehouse")
    assert second is False
    assert second_token is None

    token.release()
    third, third_token = controller.acquire_dependency("warehouse")
    assert third is True
    assert third_token is not None
    third_token.release()


def test_backpressure_classifies_rum_as_lightweight_telemetry() -> None:
    controller = BackpressureController()
    budget = controller.classify("POST", "/api/telemetry/rum")

    assert budget is not None
    assert budget.scope == "telemetry"
    assert budget.dependency is None


def test_backpressure_classifies_lakebase_writes_as_mutations() -> None:
    controller = BackpressureController()

    audit_budget = controller.classify("POST", "/api/audit/event")
    assert audit_budget is not None
    assert audit_budget.scope == "mutation"
    assert audit_budget.dependency == "lakebase"

    workspace_budget = controller.classify("DELETE", "/api/workspace/leads/B-123")
    assert workspace_budget is not None
    assert workspace_budget.scope == "mutation"
    assert workspace_budget.dependency == "lakebase"

    audit_read_budget = controller.classify("GET", "/api/audit/events")
    assert audit_read_budget is not None
    assert audit_read_budget.scope == "lakebase-read"
    assert audit_read_budget.dependency == "lakebase"


def test_backpressure_classifies_analytics_as_warehouse_read() -> None:
    controller = BackpressureController()
    budget = controller.classify("GET", "/api/v1/analytics/executive")

    assert budget is not None
    assert budget.scope == "warehouse-read"
    assert budget.dependency == "warehouse"


def test_backpressure_classifies_admin_health_as_health() -> None:
    controller = BackpressureController()
    budget = controller.classify("GET", "/api/admin/health")

    assert budget is not None
    assert budget.scope == "health"
    assert budget.dependency is None


def test_backpressure_middleware_returns_429_with_retry_after(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mip_rate_limit_default_per_minute", 1)
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    health_probes._probe_cache.clear()
    _backpressure_controller.clear()

    client = TestClient(app, raise_server_exceptions=False)
    headers = {
        "X-Enable-Backpressure-Test": "1",
        "X-Forwarded-Email": "sam.manager@summit.example",
        "X-Correlation-ID": "bp-review-123",
    }

    first = client.get("/api/health", headers=headers)
    assert first.status_code == 200

    second = client.get("/api/health", headers=headers)
    assert second.status_code == 429
    retry_after = int(second.headers["Retry-After"])
    assert 1 <= retry_after <= 60
    body = second.json()
    assert body["retryable"] is True
    assert body["reason"] == "rate_limited"
    assert body["scope"] == "health"
    assert body["retry_after_seconds"] == retry_after
    assert body["correlation_id"] == "bp-review-123"
    assert second.headers["X-Correlation-ID"] == "bp-review-123"
    assert second.headers["x-content-type-options"] == "nosniff"
    assert second.headers["x-frame-options"] == "DENY"


def test_backpressure_middleware_is_opt_in_under_pytest(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mip_rate_limit_default_per_minute", 1)
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    health_probes._probe_cache.clear()
    _backpressure_controller.clear()

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-Forwarded-Email": "sam.manager@summit.example"}

    assert client.get("/api/health", headers=headers).status_code == 200
    assert client.get("/api/health", headers=headers).status_code == 200
