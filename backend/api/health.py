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

Authenticated dependency probes are lightweight pings sharing one
configurable request deadline. Warehouse / Lakebase probes issue ``SELECT
1``; Lakebase also enforces bounded connect, TCP transport, and server-side
statement deadlines, while Genie hits ``GET /spaces/{id}``. Failures do not raise;
they flip the dependency status to ``down`` and bump the breaker's failure
counter.
Anonymous load-balancer/liveness requests intentionally do not run those
probes, so external monitoring cannot keep billable dependencies warm.
The frontend's degraded banner auto-retries until ``status == "ok"``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import APIRouter, Request

from backend.agents.gateway_contract import gateway_runtime_binding_hash
from backend.config.settings import looks_like_databricks_app_deploy, settings
from backend.schemas.health import AdminHealthResponse, HealthResponse
from backend.services.asset_metadata_utils import workspace_origin
from backend.services.audit_store import get_fallback_identity_count
from backend.services.campaign_treatment_runtime import (
    CAMPAIGN_TREATMENT_RUNTIME_ENABLED,
    campaign_treatment_runtime_state,
)
from backend.services.forced_degraded import (
    FORCED_DEGRADED_COOKIE_NAME,
    apply_forced_degraded,
    forced_degraded_payload,
    forced_degraded_snapshot_from_cookie,
)
from backend.services.health_probes import breaker_states, probe_snapshot
from backend.services.observability import (
    get_otel_handler,
    recent_breaker_state_changes,
    recent_error_count,
)
from backend.services.rbac import AdminDep

router = APIRouter()

_PROCESS_ACTOR_CACHE_SECRET = secrets.token_urlsafe(32)


def _agent_gateway_binding_sha256() -> str | None:
    endpoint = (settings.mip_agent_serving_endpoint or "").strip()
    supervisor_id = (settings.mip_agent_supervisor_id or "").strip()
    upstream = (settings.mip_agent_supervisor_endpoint or "").strip()
    runtime_application_id = (settings.mip_agent_runtime_client_id or "").strip()
    workspace_host = (settings.databricks_host or "").strip()
    model_name = settings.mip_agent_gateway_model.strip()
    model_version = settings.mip_agent_gateway_model_version
    inference_table = (settings.mip_ai_gateway_inference_table or "").strip()
    proxy_application_id = (settings.mip_agent_proxy_client_id or "").strip()
    proxy_credential_id = (settings.mip_agent_proxy_credential_id or "").strip()
    proxy_secret_reference = (settings.mip_agent_proxy_secret_reference or "").strip()
    if (
        not endpoint
        or not supervisor_id
        or not upstream
        or not runtime_application_id
        or not workspace_host
        or not model_name
        or model_version is None
        or not inference_table
        or not proxy_application_id
        or not proxy_credential_id
        or not proxy_secret_reference
    ):
        return None
    return gateway_runtime_binding_hash(
        endpoint=endpoint,
        supervisor_id=supervisor_id,
        upstream_endpoint=upstream,
        runtime_application_id=runtime_application_id,
        workspace_host=workspace_host,
        model_name=model_name,
        model_version=model_version,
        inference_table=inference_table,
        proxy_caller_application_id=proxy_application_id,
        proxy_caller_credential_id=proxy_credential_id,
        proxy_caller_secret_reference=proxy_secret_reference,
    )


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


def _apply_browser_forced_degraded(
    request: Request,
    status: str,
    deps: dict[str, str],
) -> tuple[str, dict[str, str], dict[str, object] | None]:
    snapshot = forced_degraded_snapshot_from_cookie(
        request.cookies.get(FORCED_DEGRADED_COOKIE_NAME)
    )
    next_status, next_deps = apply_forced_degraded(status, deps, snapshot)
    return next_status, next_deps, forced_degraded_payload(snapshot)


def _apply_breaker_degraded(
    status: str,
    deps: dict[str, str],
    breakers: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Make health fail visibly when a dependency breaker is not closed."""

    states = breakers or breaker_states()
    next_status = status
    next_deps = dict(deps)
    for dependency, state in states.items():
        if state == "closed":
            continue
        if dependency in next_deps:
            next_deps[dependency] = "down"
        next_status = "degraded"
    return next_status, next_deps


def _apply_treatment_runtime_degraded(status: str) -> tuple[str, str]:
    """Fold the deploy-promotion marker into overall health status.

    2026-07-30 gate restructure: a baseline (un-promoted) deploy no longer
    crashes the process; it serves reads with treatment writes failing
    closed. Health is where that state becomes visible — ``status`` flips
    to ``degraded`` so the existing frontend banner and operator probes
    catch a bare UI/CLI deploy immediately, and the returned state string
    names the reason for authenticated/admin bodies.
    """

    state = campaign_treatment_runtime_state()
    if state != CAMPAIGN_TREATMENT_RUNTIME_ENABLED:
        return "degraded", state
    return status, state


def _diagnostic_body(
    status: str,
    deps: dict[str, str],
    actor_email: str,
    forced_degraded: dict[str, object] | None = None,
) -> dict[str, Any]:
    status, treatment_runtime = _apply_treatment_runtime_degraded(status)
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
    body = {
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
        # Validated workspace origin so admin surfaces can deep-link UC
        # assets exactly like the authenticated browser body does.
        **(
            {"workspace_host": origin}
            if (origin := workspace_origin(settings.databricks_host))
            else {}
        ),
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
        # Deploy-promotion marker state. "disabled_baseline_deploy" means the
        # running process came from a bare deploy that bypassed the
        # scripts/deploy.sh promoted snapshot — treatment writes 503 until a
        # governed roll-forward.
        "campaign_treatment_runtime": treatment_runtime,
        "boundary_warning": boundary_warning,
    }
    if settings.mip_git_sha:
        body["git_sha"] = settings.mip_git_sha
    if settings.mip_app_deployment_lease_id:
        body["deployment_lease_id"] = settings.mip_app_deployment_lease_id
    gateway_binding = _agent_gateway_binding_sha256()
    if gateway_binding:
        body["agent_gateway_binding_sha256"] = gateway_binding
    if forced_degraded is not None:
        body["forced_degraded"] = forced_degraded
    return body


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
    # Anonymous caller (LB / external probe): minimal body only. Use the
    # same trust boundary as audit actor resolution without calling
    # resolve_actor(), so routine probes never bump the fallback counter
    # and untrusted proxy deployments cannot spoof diagnostic access.
    #
    # Cost boundary: anonymous liveness must not issue dependency probes.
    # Otherwise a Databricks App load balancer, uptime monitor, or public
    # scanner can keep the SQL warehouse/Lakebase/Genie path hot forever.
    # Authenticated browsers and /api/admin/health still get full dependency
    # truth for the degraded-state UI.
    actor_email = _trusted_health_actor(request)
    authenticated = bool(actor_email)
    if not authenticated:
        # Keys stay exactly {status, mode} (R6-09 reconnaissance contract),
        # but the VALUE reflects a baseline (un-promoted) deploy so even a
        # curl of the public URL shows the degraded state instead of the
        # pre-2026-07-30 behavior of the whole process being dead.
        anonymous_status, _ = _apply_treatment_runtime_degraded("ok")
        return {"status": anonymous_status, "mode": "live"}

    status, deps = probe_snapshot()
    breakers = breaker_states()
    status, deps = _apply_breaker_degraded(status, deps, breakers)

    status, deps, forced_degraded = _apply_browser_forced_degraded(request, status, deps)
    status, treatment_runtime = _apply_treatment_runtime_degraded(status)
    body = {
        "status": status,
        "mode": "live",
        # Named reason for the degraded flip above; the banner/topbar can
        # distinguish "a dependency is down" from "this deploy was never
        # promoted" without admin diagnostics.
        "campaign_treatment_runtime": treatment_runtime,
        "dependencies": deps,
        # Keep the topbar / degraded-state UI useful for authenticated
        # workspace users without exposing admin-only diagnostics such as
        # warehouse ids, app_env, log exporter posture, or fallback counters.
        "circuit_breakers": breakers,
        "actor_cache_key": _actor_cache_key(actor_email or ""),
    }
    # Workspace origin (scheme+host only, validated) so the UI can deep-link
    # cited Unity Catalog assets to the Catalog Explorer. Authenticated body
    # only — the anonymous liveness contract stays {status, mode}.
    host_origin = workspace_origin(settings.databricks_host)
    if host_origin:
        body["workspace_host"] = host_origin
    if settings.mip_git_sha:
        body["git_sha"] = settings.mip_git_sha
    if settings.mip_app_deployment_lease_id:
        body["deployment_lease_id"] = settings.mip_app_deployment_lease_id
    gateway_binding = _agent_gateway_binding_sha256()
    if gateway_binding:
        body["agent_gateway_binding_sha256"] = gateway_binding
    if forced_degraded is not None:
        body["forced_degraded"] = forced_degraded
    return body


@router.get("/admin/health", response_model=AdminHealthResponse)
def admin_health(request: Request, _actor: AdminDep) -> dict[str, Any]:
    """Return ops diagnostics behind admin RBAC."""
    status, deps = probe_snapshot()
    status, deps = _apply_breaker_degraded(status, deps)
    status, deps, forced_degraded = _apply_browser_forced_degraded(request, status, deps)
    return _diagnostic_body(status, deps, _actor, forced_degraded)
