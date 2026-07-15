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

import base64
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agents.gateway_contract import gateway_runtime_binding_hash
from backend.api import health as health_mod
from backend.main import app
from backend.services import health_probes, resilience
from backend.services.forced_degraded import (
    FORCED_DEGRADED_COOKIE_NAME,
    FORCED_DEGRADED_SOURCE,
    _reset_forced_degraded_for_tests,
)

client = TestClient(app)
ADMIN_HEADERS = {
    "X-Forwarded-Email": "ops@example.com",
    "X-Forwarded-Groups": "mip-admin",
}


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    resilience._reset_breakers_for_tests()
    _reset_forced_degraded_for_tests()
    client.cookies.clear()
    # Slice-13 perf cache: /api/health caches each probe result with a
    # stale-while-revalidate policy (2 s soft TTL, 10 s hard TTL). That
    # cache leaks across tests and would make the second-test-onward
    # see stale results (all "up"/all "down") because the v1 cached
    # booleans stay valid under the hard TTL. Drop between tests so
    # every monkeypatched probe is exercised cleanly.
    health_probes._probe_cache.clear()


def test_health_returns_ok_when_both_deps_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    res = client.get("/api/admin/health", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    payload: dict[str, Any] = res.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "live"
    assert payload["dependencies"] == {"warehouse": "up", "lakebase": "up", "genie": "up"}
    assert set(payload["circuit_breakers"].keys()) >= {"warehouse", "lakebase", "genie"}


def test_health_returns_degraded_when_warehouse_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: False)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
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
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    # Register the warehouse breaker and force it open.
    cb = resilience.get_breaker("warehouse", failure_threshold=1, cooldown_s=60)
    cb.record_failure()
    assert cb.state == "open"

    res = client.get("/api/admin/health", headers=ADMIN_HEADERS)
    payload = res.json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"]["warehouse"] == "down"
    assert payload["circuit_breakers"]["warehouse"] == "open"


def test_authenticated_health_degrades_when_genie_breaker_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    cb = resilience.get_breaker("genie", failure_threshold=1, cooldown_s=60)
    cb.record_failure()
    assert cb.state == "open"

    res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"]["genie"] == "down"
    assert payload["circuit_breakers"]["genie"] == "open"


def test_health_reports_genie_down_and_degrades_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice-7: Genie is the third dependency. When its ping fails the
    body flips to degraded even if warehouse + Lakebase are up. Status
    is still HTTP 200 so the LB doesn't pull the container."""
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: False)

    res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"]["genie"] == "down"


def test_admin_force_degraded_overlays_authenticated_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin-only force switch proves the deployed banner without stopping infra."""

    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    forced = client.post(
        "/api/admin/force-degraded",
        headers=ADMIN_HEADERS,
        json={"state": "on", "dependency": "warehouse", "ttl_s": 30},
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["forced"] is True

    res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"]["warehouse"] == "down"
    assert payload["dependencies"]["lakebase"] == "up"
    forced_meta = payload["forced_degraded"]
    assert forced_meta["active"] is True
    assert forced_meta["dependency"] == "warehouse"
    assert forced_meta["source"] == "admin_drill_cookie"
    assert 1 <= forced_meta["expires_in_s"] <= 30

    audit = client.get("/api/audit/events?limit=10", headers=ADMIN_HEADERS)
    assert audit.status_code == 200
    events = audit.json()
    assert any(
        event["event_type"] == "FORCE_DEGRADED"
        and event["payload_json"]["forced_state"] == "on"
        and event["payload_json"]["proof_scope"] == "browser_cookie"
        for event in events
    )

    cleared = client.post(
        "/api/admin/force-degraded",
        headers=ADMIN_HEADERS,
        json={"state": "off"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["forced"] is False

    recovered = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    assert recovered.json()["status"] == "ok"
    assert recovered.json().get("forced_degraded") is None


def test_unsigned_force_degraded_cookie_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-admin cannot forge the admin proof by hand-crafting a cookie."""

    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    raw = json.dumps(
        {
            "v": 1,
            "dependency": "warehouse",
            "expires_at": int(time.time()) + 60,
            "source": FORCED_DEGRADED_SOURCE,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    forged = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    res = client.get(
        "/api/health",
        headers={
            "X-Forwarded-Email": "analyst@example.com",
            "Cookie": f"{FORCED_DEGRADED_COOKIE_NAME}={forged}",
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "ok"
    assert payload["dependencies"]["warehouse"] == "up"
    assert payload.get("forced_degraded") is None


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
        def list(self, **kwargs: object) -> list:
            _ = kwargs
            raise DependencyDownError(
                "warehouse",
                reason="state=FAILED statement_id=01abc secret predicate",
                kind=DependencyDownError.KIND_BREAKER_OPEN,
            )

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
        assert body["reason"] == "breaker_open"
        assert body["correlation_id"]
        assert "warehouse" in body["detail"]
        assert "statement_id" not in body["detail"]
        assert "secret predicate" not in body["detail"]
    finally:
        if previous is None:
            del app.dependency_overrides[get_segment_repository]
        else:
            app.dependency_overrides[get_segment_repository] = previous


# ---------------------------------------------------------------------------
# SWR cache behaviour at the /api/health seam
#
# These tests exist because the v1 TTLCache → v2 StaleWhileRevalidateCache
# swap is a behaviour change, not just an implementation detail: bursts of
# health hits must now share a single probe-per-dependency under the soft
# TTL, and a probe-result swap inside that window must NOT be visible to
# the caller. The suite above stays focused on "what does the JSON look
# like"; these three check "how often does the underlying probe run".
# ---------------------------------------------------------------------------


def test_health_bursts_share_one_probe_per_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five back-to-back /api/health hits must fan out to exactly one
    probe call per dependency. This is the whole reason the SWR cache
    exists -- the v1 plain TTL had the same guarantee inside the 3 s
    window, so we'd regress silently if it broke."""
    counts = {"warehouse": 0, "lakebase": 0, "genie": 0}

    def _probe(name: str) -> bool:
        counts[name] += 1
        return True

    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: _probe("warehouse"))
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: _probe("lakebase"))
    monkeypatch.setattr(health_probes, "probe_genie", lambda: _probe("genie"))

    for _ in range(5):
        res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    # First hit is a sync miss; the other four are cache hits under the
    # 2 s soft TTL. None of those follow-ons may run the probe.
    assert counts == {"warehouse": 1, "lakebase": 1, "genie": 1}


def test_health_returns_cached_value_during_stale_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a probe's soft TTL has elapsed but hard TTL has not, /api/health
    returns the CACHED value (up=True here) even though the freshly-flipped
    probe now returns down. The next-generation value only materialises
    after the background refresh stores it."""
    # First hit seeds the cache with up=True.
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    assert res.json()["status"] == "ok"

    # Manually poke the cache so the warehouse entry is past the soft
    # TTL but not the hard TTL. This simulates "2.5 seconds later"
    # without time.sleep(). We cheat by rewriting the tuple directly
    # under the cache's lock -- the public API doesn't expose time
    # travel.
    cache = health_probes._probe_cache
    with cache._lock:
        value, hard_expiry, _soft_expiry, in_flight = cache._entries["warehouse"]
        # Soft expiry in the past, hard expiry in the future.
        cache._entries["warehouse"] = (value, hard_expiry, 0.0, in_flight)

    # Flip the probe to return down. The IN-THREAD response must still
    # report up (the cached value); the background refresh happens on
    # a worker thread so it may or may not be done when we check -- but
    # the contract is "the request thread sees the cached value", which
    # is up=True.
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: False)
    res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    # Cached value wins for this call.
    assert res.json()["dependencies"]["warehouse"] == "up"


# ---------------------------------------------------------------------------
# R6-09: anonymous callers get a minimal probe-friendly body; authenticated
# callers get UI-safe runtime detail. Full diagnostics are admin-only.
# The LB probe path stays 200 in both.
# ---------------------------------------------------------------------------


def _fail_if_probe_runs() -> bool:
    raise AssertionError("anonymous /api/health must not run dependency probes")


def test_health_anonymous_returns_minimal_liveness_without_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unauthenticated caller (no X-Forwarded-Email) must only see
    ``{status, mode}`` so an attacker probing the public Databricks Apps
    URL doesn't get breaker state, app_env, or identity-fallback counters
    for free. It also must not wake billable dependencies."""
    monkeypatch.setattr(health_probes, "probe_warehouse", _fail_if_probe_runs)
    monkeypatch.setattr(health_probes, "probe_lakebase", _fail_if_probe_runs)
    monkeypatch.setattr(health_probes, "probe_genie", _fail_if_probe_runs)

    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    # Exactly these two keys, nothing more. Anything additional is a
    # reconnaissance regression.
    assert set(body.keys()) == {"status", "mode"}
    assert body["status"] == "ok"
    assert body["mode"] == "live"


def test_health_anonymous_ignores_open_breakers_for_liveness() -> None:
    """The LB contract is process liveness, not dependency readiness."""
    cb = resilience.get_breaker("warehouse", failure_threshold=1, cooldown_s=60)
    cb.record_failure()
    assert cb.state == "open"

    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"status", "mode"}
    assert body["status"] == "ok"


def test_health_authenticated_returns_runtime_status_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-admin workspace user gets the runtime state needed by the UI
    without ops diagnostics such as warehouse IDs or process-local counters."""
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    res = client.get("/api/health", headers={"X-Forwarded-Email": "skyler@entrada.ai"})
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {
        "status",
        "mode",
        "dependencies",
        "circuit_breakers",
        "actor_cache_key",
    }
    assert body["dependencies"] == {"warehouse": "up", "lakebase": "up", "genie": "up"}
    assert set(body["circuit_breakers"].keys()) >= {"warehouse", "lakebase", "genie"}
    assert "warehouse_id" not in body
    assert "app_env" not in body
    assert "log_export" not in body
    assert "fallback_identity_fallbacks_total" not in body
    assert body["actor_cache_key"].startswith("actor_")
    assert "skyler" not in body["actor_cache_key"].lower()
    assert "entrada" not in body["actor_cache_key"].lower()


def test_authenticated_health_surfaces_deployed_git_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    monkeypatch.setattr(health_mod.settings, "mip_git_sha", "abc123def456")
    monkeypatch.setattr(
        health_mod.settings,
        "mip_agent_serving_endpoint",
        "mip-growth-agent-gateway",
    )
    monkeypatch.setattr(health_mod.settings, "mip_agent_supervisor_id", "supervisor-123")
    monkeypatch.setattr(
        health_mod.settings,
        "mip_agent_supervisor_endpoint",
        "mas-supervisor-endpoint",
    )
    monkeypatch.setattr(
        health_mod.settings,
        "mip_agent_gateway_model",
        "mip.audit.mortgage_growth_supervisor_proxy",
    )
    monkeypatch.setattr(health_mod.settings, "mip_agent_gateway_model_version", 7)
    monkeypatch.setattr(
        health_mod.settings,
        "mip_ai_gateway_inference_table",
        "mip.audit.mip_agent_gateway_growth_agent",
    )
    expected_binding = gateway_runtime_binding_hash(
        endpoint="mip-growth-agent-gateway",
        supervisor_id="supervisor-123",
        upstream_endpoint="mas-supervisor-endpoint",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        model_version=7,
        inference_table="mip.audit.mip_agent_gateway_growth_agent",
    )

    anon = client.get("/api/health")
    assert anon.status_code == 200
    assert "git_sha" not in anon.json()
    assert "agent_gateway_binding_sha256" not in anon.json()

    auth = client.get("/api/health", headers={"X-Forwarded-Email": "skyler@entrada.ai"})
    assert auth.status_code == 200
    assert auth.json()["git_sha"] == "abc123def456"
    assert auth.json()["agent_gateway_binding_sha256"] == expected_binding

    admin = client.get("/api/admin/health", headers=ADMIN_HEADERS)
    assert admin.status_code == 200
    assert admin.json()["git_sha"] == "abc123def456"
    assert admin.json()["agent_gateway_binding_sha256"] == expected_binding


def test_health_admin_endpoint_returns_full_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full diagnostic body is available only through admin RBAC."""
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    res = client.get("/api/admin/health", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    body = res.json()
    # Contract: every diagnostic field is present for admin callers.
    for key in [
        "status",
        "mode",
        "app_env",
        "warehouse_id",
        "dependencies",
        "circuit_breakers",
        "actor_cache_key",
        "breaker_state_changes_last_hour",
        "recent_errors_count",
        "counters_persistence",
        "log_export",
        "fallback_identity_fallbacks_total",
    ]:
        assert key in body, f"authenticated body must surface {key}"
    assert body["actor_cache_key"].startswith("actor_")
    assert "ops" not in body["actor_cache_key"].lower()
    assert "example" not in body["actor_cache_key"].lower()


def test_health_admin_endpoint_surfaces_trust_boundary_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    monkeypatch.setattr(health_mod.settings, "trust_forwarded_headers", True)
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_URL", raising=False)

    res = client.get("/api/admin/health", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    warning = res.json()["boundary_warning"]
    assert warning["code"] == "rbac_trust_boundary_unclear"
    assert warning["severity"] == "warning"
    assert "MIP_TRUST_FORWARDED_HEADERS=false" in warning["recommended_action"]


def test_health_admin_endpoint_clears_trust_boundary_warning_for_apps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    monkeypatch.setattr(health_mod.settings, "trust_forwarded_headers", True)
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8080")

    res = client.get("/api/admin/health", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    assert res.json()["boundary_warning"] is None


def test_health_admin_endpoint_rejects_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    monkeypatch.setattr(health_mod.settings, "admin_emails", "")

    res = client.get(
        "/api/admin/health",
        headers={"X-Forwarded-Email": "user@example.com", "X-Forwarded-Groups": ""},
    )
    assert res.status_code == 403


def test_health_actor_cache_key_changes_by_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    first = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"}).json()
    second = client.get("/api/health", headers={"X-Forwarded-Email": "skyler@entrada.ai"}).json()

    assert first["actor_cache_key"].startswith("actor_")
    assert second["actor_cache_key"].startswith("actor_")
    assert first["actor_cache_key"] != second["actor_cache_key"]


def test_health_ignores_forwarded_actor_when_headers_are_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_probes, "probe_warehouse", _fail_if_probe_runs)
    monkeypatch.setattr(health_probes, "probe_lakebase", _fail_if_probe_runs)
    monkeypatch.setattr(health_probes, "probe_genie", _fail_if_probe_runs)
    monkeypatch.setattr(health_mod.settings, "trust_forwarded_headers", False)

    res = client.get("/api/health", headers={"X-Forwarded-Email": "spoofed@example.com"})
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"status", "mode"}
    assert body == {"status": "ok", "mode": "live"}


def test_health_forces_sync_reprobe_after_hard_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the hard TTL, the cache is evicted and the request thread
    MUST run the probe synchronously. This is the "a real outage
    surfaces within ~10 s" guarantee -- the hard TTL is the worst-case
    time before a cached 'up' flips to 'down' on a genuine failure."""
    counts = {"n": 0}

    def probe_up() -> bool:
        counts["n"] += 1
        return True

    monkeypatch.setattr(health_probes, "probe_warehouse", probe_up)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    # Seed the cache.
    client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    assert counts["n"] == 1

    # Force hard-TTL expiry by setting both expiries into the past.
    cache = health_probes._probe_cache
    with cache._lock:
        value, _hard, _soft, in_flight = cache._entries["warehouse"]
        cache._entries["warehouse"] = (value, 0.0, 0.0, in_flight)

    # Flip the probe to return down. Because the cache is past the hard
    # TTL, this request must re-probe synchronously and see the new
    # value.
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: False)
    res = client.get("/api/health", headers={"X-Forwarded-Email": "ops@example.com"})
    assert res.json()["dependencies"]["warehouse"] == "down"
    assert res.json()["status"] == "degraded"
