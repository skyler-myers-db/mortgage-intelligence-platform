"""Health endpoint with live dependency status + breaker snapshot.

Slice-6/7 contract (returned body):

    {
      "status":            "ok" | "degraded",
      "mode":              "live",
      "app_env":           "<env>",
      "warehouse_id":      "<id>",
      "dependencies": {
        "warehouse":       "up" | "down",
        "lakebase":        "up" | "down",
        "genie":           "up" | "down"
      },
      "circuit_breakers": {
        "warehouse":       "closed" | "open" | "half_open",
        "lakebase":        "closed" | "open" | "half_open",
        "genie":           "closed" | "open" | "half_open"
      }
    }

A degraded response STILL returns HTTP 200 so the Databricks App load
balancer doesn't yank the container. Degraded state is carried in the
body, which the frontend reads to show the banner.

Each dependency probe is a lightweight ping with a 1-second timeout.
Warehouse / Lakebase probes issue ``SELECT 1``; Genie probes hit
``GET /spaces/{id}``. Failures do not raise; they flip the dependency
status to ``down`` and bump the breaker's failure counter. The
frontend's degraded banner auto-retries until ``status == "ok"``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import APIRouter, Request

from backend.config.settings import looks_like_databricks_app_deploy, settings
from backend.schemas.health import AdminHealthResponse, HealthResponse
from backend.services.audit_store import get_fallback_identity_count
from backend.services.forced_degraded import apply_forced_degraded
from backend.services.health_probes import breaker_states, probe_snapshot
from backend.services.observability import (
    get_otel_handler,
    recent_breaker_state_changes,
    recent_error_count,
)
from backend.services.rbac import AdminDep

router = APIRouter()

_PROCESS_ACTOR_CACHE_SECRET = secrets.token_urlsafe(32)


def _actor_cache_key(actor_email: str) -> str:
    """Return a non-reversible actor/session discriminator for browser caches."""

    configured_secret = settings.mip_genie_action_secret_current or settings.mip_genie_action_secret
    secret = configured_secret.get_secret_value().strip() if configured_secret else ""
    if not secret:
        secret = _PROCESS_ACTOR_CACHE_SECRET
    digest = hmac.new(
        secret.encode("utf-8"),
        actor_email.strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]
    return f"actor_{digest}"


def _trusted_health_actor(request: Request) -> str | None:
    """Return forwarded actor identity only when this edge is trusted.

    Health cannot call `resolve_actor()` directly because anonymous load
    balancer probes are expected and must not increment the audit fallback
    counter. The trust-boundary behavior still has to match audit writes:
    when forwarded headers are not trusted, spoofed client headers are
    ignored and the caller receives the minimal health body.
    """

    if not settings.trust_forwarded_headers:
        return None
    return request.headers.get("X-Forwarded-Email") or request.headers.get("X-Forwarded-User")


def _diagnostic_body(status: str, deps: dict[str, str], actor_email: str) -> dict[str, Any]:
    boundary_warning = None
    if settings.trust_forwarded_headers and not looks_like_databricks_app_deploy():
        boundary_warning = {
            "code": "rbac_trust_boundary_unclear",
            "severity": "warning",
            "message": (
                "Forwarded identity headers are trusted, but this process does not "
                "look like a Databricks Apps deployment."
            ),
            "recommended_action": (
                "Set MIP_TRUST_FORWARDED_HEADERS=false outside Databricks Apps "
                "unless an upstream proxy strips forwarded identity headers."
            ),
            "docs_ref": "docs/security/GRANTS.md#10",
        }
    return {
        "status": status,
        "mode": "live",
        "app_env": settings.app_env,
        "warehouse_id": settings.databricks_warehouse_id,
        "dependencies": deps,
        "circuit_breakers": breaker_states(),
        # PII-safe identity discriminator for frontend cache hygiene.
        # The browser never receives the actor email, but can still clear
        # QueryClient data if Databricks Apps swaps the workspace identity
        # within the same browser session.
        "actor_cache_key": _actor_cache_key(actor_email),
        # Slice-13 observability counters. Values reflect the last
        # rolling hour. A non-zero ``breaker_state_changes_last_hour``
        # is the earliest signal that a dependency is flapping; a
        # non-zero ``recent_errors_count`` with ``status=="ok"`` means
        # the breaker caught transient failures without user-facing
        # degradation.
        "breaker_state_changes_last_hour": recent_breaker_state_changes(),
        "recent_errors_count": recent_error_count(),
        # Slice-13 follow-up: the two counters above are process-local
        # and reset on every container restart. That is intentional --
        # the durable path is the OTLP log exporter (set
        # ``MIP_OTEL_ENDPOINT`` to enable; see docs/observability.md).
        # Surfacing the posture in the admin health body means operators can
        # tell at a glance whether to trust the counters for trend
        # analysis (they should not) vs. use them for "right now" signal
        # (they should). ``log_export`` tells them whether the durable
        # path is active.
        "counters_persistence": "process-local",
        "log_export": "otlp" if get_otel_handler() is not None else "stdout-only",
        # Slice-RBAC follow-up: count of requests where the audit
        # ``resolve_actor`` path fell back to ``settings.default_actor``
        # because ``X-Forwarded-Email`` was absent. A non-zero value in a
        # production deploy is a regression signal -- Databricks Apps
        # should always forward the header. The counter is process-local
        # (like the two above); the durable trail is the structured
        # WARNING log emitted at each fallback.
        #
        # R6-08: the legacy ``_total`` suffix matches the Prometheus
        # global-monotonic-counter convention but the body explicitly
        # declares ``counters_persistence: "process-local"``. Multi-replica
        # Databricks Apps deployments show inconsistent scrapes under the
        # old name because each replica's count is independent.
        # Canonical key is now ``fallback_identity_fallbacks_process_total``
        # (``process_`` infix signals per-replica scope). The legacy key is
        # still emitted for one cycle so dashboards / alerts that already
        # scrape it don't silently drop to 0; remove in the next cleanup
        # pass once downstream consumers cut over.
        "fallback_identity_fallbacks_process_total": get_fallback_identity_count(),
        "fallback_identity_fallbacks_total": get_fallback_identity_count(),
        "boundary_warning": boundary_warning,
    }


@router.get("/health", response_model=HealthResponse, response_model_exclude_unset=True)
def health(request: Request) -> dict[str, Any]:
    """Return probe-friendly health with only runtime status.

    R6-09 (governance/security): the endpoint is publicly reachable via
    the Databricks Apps URL, which is how the platform's load balancer
    does liveness/readiness checks. The LB sends anonymous requests, so
    we MUST keep returning a 200 with a minimal body shape
    (``{status, mode}``) for that path. What changed: an external
    attacker probing the same URL used to get circuit-breaker state,
    app_env, warehouse IDs, flap-history counts, and the identity-fallback
    counter. That diagnostic body now lives behind admin RBAC at
    ``/api/admin/health``. Trusted authenticated browsers only receive
    the coarse dependency / breaker states required for the
    degraded-state UI plus a PII-safe actor cache discriminator.

    HTTP status stays 200 in both shapes even when ``status=="degraded"``
    so the LB probe contract (degraded != unhealthy) is preserved.
    """
    status, deps = apply_forced_degraded(*probe_snapshot())

    # Anonymous caller (LB / external probe): minimal body only. Use the
    # same trust boundary as audit actor resolution without calling
    # resolve_actor(), so routine probes never bump the fallback counter
    # and untrusted proxy deployments cannot spoof diagnostic access.
    actor_email = _trusted_health_actor(request)
    authenticated = bool(actor_email)
    if not authenticated:
        return {"status": status, "mode": "live"}

    return {
        "status": status,
        "mode": "live",
        "dependencies": deps,
        # Keep the topbar / degraded-state UI useful for authenticated
        # workspace users without exposing admin-only diagnostics such as
        # warehouse ids, app_env, log exporter posture, or fallback counters.
        "circuit_breakers": breaker_states(),
        "actor_cache_key": _actor_cache_key(actor_email or ""),
    }


@router.get("/admin/health", response_model=AdminHealthResponse)
def admin_health(_actor: AdminDep) -> dict[str, Any]:
    """Return ops diagnostics behind admin RBAC."""
    status, deps = apply_forced_degraded(*probe_snapshot())
    return _diagnostic_body(status, deps, _actor)
