import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import _backpressure_controller, app
from backend.services import health_probes
from backend.services.backpressure import BackpressureController, DependencySlot


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


@pytest.mark.parametrize("path", ["/api/portfolio/preview", "/api/v1/portfolio/preview"])
def test_read_only_portfolio_preview_post_is_a_warehouse_read(path: str) -> None:
    """delivery-09: the preview POST only reads the warehouse, so it must not
    draw one of the 16 Lakebase mutation slots."""
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("warehouse-read", "warehouse")
    assert budget.requests_per_minute == settings.mip_rate_limit_expensive_per_minute


@pytest.mark.parametrize(
    "path",
    [
        # Writes a RECOMMEND_OFFER audit row (offers.py).
        "/api/v1/offers/recommend",
        # Reads Lakebase through sales_state.campaign_performance_funnel.
        "/api/v1/portfolio/campaign-recommendation",
        # A real portfolio write stays a mutation.
        "/api/v1/portfolio",
    ],
)
def test_other_posts_stay_lakebase_mutations(path: str) -> None:
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("mutation", "lakebase")


def test_only_post_is_allow_listed_for_the_preview_path() -> None:
    budget = BackpressureController().classify("DELETE", "/api/v1/portfolio/preview")

    assert budget is not None
    assert budget.scope == "mutation"


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


# --- 2026-09-21 audit genie-01: the completion job's poll and slot ---------


@pytest.mark.parametrize("path", ["/api/genie/message/status", "/api/v1/genie/message/status"])
def test_the_completion_job_poll_is_a_lakebase_read_outside_the_genie_budget(path: str) -> None:
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("genie-job", "lakebase")
    assert budget.requests_per_minute == settings.mip_rate_limit_default_per_minute


def test_forty_completion_job_polls_a_minute_never_429() -> None:
    now = {"t": 0.0}
    controller = BackpressureController(now=lambda: now["t"])
    budget = controller.classify("POST", "/api/v1/genie/message/status")
    assert budget is not None

    for poll in range(40):
        now["t"] = poll * 1.5
        assert controller.check_rate("lo@example.com", budget) is None


@pytest.mark.parametrize("path", ["/api/genie/export-receipt", "/api/v1/genie/export-receipt"])
def test_the_genie_export_receipt_is_a_lakebase_mutation(path: str) -> None:
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("mutation", "lakebase")
    assert budget.requests_per_minute == settings.mip_rate_limit_mutation_per_minute


@pytest.mark.parametrize("path", ["/api/genie/message/cancel", "/api/v1/genie/message/cancel"])
def test_the_genie_cancel_is_a_lakebase_mutation_outside_the_genie_budget(path: str) -> None:
    # Audit genie-03: a Stop is one Lakebase transaction, never a Genie call,
    # and must land even when the Genie budget is spent.
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("mutation", "lakebase")
    assert budget.requests_per_minute == settings.mip_rate_limit_mutation_per_minute


@pytest.mark.parametrize(
    "path",
    ["/api/v1/genie/message/complete", "/api/v1/genie/message/submit", "/api/v1/genie/actions"],
)
def test_genie_answer_path_calls_keep_the_genie_budget_and_slot(path: str) -> None:
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("genie", "genie")


def test_an_adopted_slot_is_released_once_by_its_adopter_only() -> None:
    controller = BackpressureController(genie_concurrency=1)
    acquired, semaphore = controller.acquire_dependency("genie")
    assert acquired is True and semaphore is not None
    slot = DependencySlot(semaphore, "genie")

    assert slot.adopt() is slot
    assert slot.adopted is True
    assert controller.acquire_dependency("genie")[0] is False
    assert slot.release() is True
    assert slot.release() is False  # a second release never over-frees
    first, token = controller.acquire_dependency("genie")
    second, _ = controller.acquire_dependency("genie")
    assert (first, second) == (True, False)
    token.release()


# --- 2026-09-21 audit shell-03: conversation History and its deep link -----


@pytest.mark.parametrize(
    "path",
    [
        "/api/genie/sessions",
        "/api/v1/genie/sessions",
        "/api/genie/sessions/0123456789abcdef0123456789abcdef",
        "/api/v1/genie/sessions/01234567-89ab-cdef-0123-456789abcdef",
    ],
)
def test_genie_session_reads_are_lakebase_reads_outside_the_genie_budget(path: str) -> None:
    # The /ask-genie/:conversationId deep link replays the caller's own turns
    # from Lakebase: a reload of a shared link must not spend the bucket that
    # gates asks, nor hold a Genie slot.
    budget = BackpressureController().classify("GET", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("lakebase-read", "lakebase")
    assert budget.requests_per_minute == settings.mip_rate_limit_default_per_minute


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/genie/message/complete"),
        ("POST", "/api/v1/genie/message/submit"),
        ("POST", "/api/v1/genie/actions"),
        ("GET", "/api/v1/genie/start"),
        ("GET", "/api/v1/genie/sessionsx"),
        ("POST", "/api/v1/genie/sessions/0123456789abcdef0123456789abcdef"),
    ],
)
def test_only_session_gets_leave_the_genie_budget(method: str, path: str) -> None:
    budget = BackpressureController().classify(method, path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("genie", "genie")


@pytest.mark.parametrize(
    "path",
    ["/api/admin/sse-probe", "/api/v1/admin/sse-probe", "/api/v1/admin/sse-probe/0123456789abcdef"],
)
def test_the_admin_sse_probe_holds_no_dependency_slot(path: str) -> None:
    # delivery-04: the probe streams for up to five minutes; it must never sit
    # in the /api/admin warehouse branch and hold a warehouse slot meanwhile.
    budget = BackpressureController().classify("GET", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("admin-diagnostic", None)
    assert BackpressureController().classify("GET", "/api/admin/rules").dependency == "warehouse"


@pytest.mark.parametrize("path", ["/api/assets/x/freshness", "/api/v1/assets/x/freshness"])
def test_asset_freshness_is_a_warehouse_read(path: str) -> None:
    # critic-03: every authenticated user's freshness read is one
    # source-readiness SELECT on the warehouse; the admin metadata read keeps
    # its /api/admin branch.
    budget = BackpressureController().classify("GET", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("warehouse-read", "warehouse")
    admin = BackpressureController().classify("GET", "/api/v1/admin/assets/x/metadata")
    assert admin is not None and (admin.scope, admin.dependency) == ("warehouse-read", "warehouse")


@pytest.mark.parametrize("path", ["/api/kpi-proof", "/api/v1/kpi-proof"])
def test_kpi_proof_takes_the_default_budget_and_no_slot(path: str) -> None:
    # flow-06: the route emits fixed SQL text and reads nothing.
    budget = BackpressureController().classify("GET", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("default", None)


@pytest.mark.parametrize(
    "path",
    ["/api/borrowers/B-0123456789ABC/decisions", "/api/v1/borrowers/B-X/decisions"],
)
def test_the_borrower_decision_history_is_a_lakebase_read_never_a_warehouse_read(path: str) -> None:
    # Audit flow-04 phase 2: the history reads the Lakebase ledger only; under
    # the '/api/borrowers' prefix it would spend the expensive warehouse budget
    # and hold a warehouse slot.
    controller = BackpressureController()
    budget = controller.classify("GET", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("lakebase-read", "lakebase")
    assert budget.requests_per_minute == settings.mip_rate_limit_default_per_minute
    # Non-vacuity: the dossier read beside it keeps the warehouse budget.
    dossier = controller.classify("GET", "/api/v1/borrowers/B-X")
    assert dossier is not None and dossier.dependency == "warehouse"


# --- 2026-09-21 audit delivery-09: the Genie surface's open ---------------------


@pytest.mark.parametrize("path", ["/api/genie/start", "/api/v1/genie/start"])
def test_genie_start_is_a_lakebase_read_outside_the_genie_budget(path: str) -> None:
    # genie_start reads the caller's latest Lakebase session row, the static
    # trusted assets and the sample-question file: no Genie call, no audit
    # row. Inside the Genie budget every surface open would hold a Genie slot.
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("lakebase-read", "lakebase")
    assert budget.requests_per_minute == settings.mip_rate_limit_default_per_minute


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/genie/message",
        "/api/v1/genie/message/complete",
        "/api/v1/genie/actions",
        "/api/v1/genie/startx",
    ],
)
def test_the_genie_start_branch_leaves_answer_path_posts_in_the_genie_budget(path: str) -> None:
    budget = BackpressureController().classify("POST", path)

    assert budget is not None
    assert (budget.scope, budget.dependency) == ("genie", "genie")
