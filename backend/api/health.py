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

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from fastapi import APIRouter, Request

from backend.config.settings import settings
from backend.services.audit_store import get_fallback_identity_count
from backend.services.observability import (
    get_otel_handler,
    recent_breaker_state_changes,
    recent_error_count,
)
from backend.services.resilience import StaleWhileRevalidateCache, all_breakers

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


_PROBE_TIMEOUT_S = 1.0

# Slice-13 performance follow-up (v2): stale-while-revalidate cache
# around each dependency probe.
#
# v1 used a plain 3 s TTLCache and cut p95 from 1.8 s to 1.1 s at 20
# VUs, but the 3 s TTL window expired mid-burst and the next requester
# still ate a real probe (dominated by the ~1 s Genie HTTP round-trip).
#
# v2 keeps a 10 s HARD TTL with a 2 s SOFT TTL. Inside the soft window,
# callers get a cache-hit latency response (<5 ms). Between the soft and
# hard TTLs, callers still get the cached value immediately AND a
# background refresh is kicked on a shared ThreadPoolExecutor so the
# next burst doesn't fall off the hard TTL cliff. Only when the hard
# TTL has blown (cache has been cold for >= 10 s) does any request
# thread block on a real probe.
#
# Outage semantics: a genuine dependency failure is surfaced within the
# hard-TTL window (<=10 s). The frontend's degraded banner polls every
# 5-10 s, so operators still see flipped state within one poll cycle.
# Background refreshes emit a structured log
# (event=health_probe_background_refresh) so operators can correlate
# freshness of the cached value with the underlying dependency state.
_HEALTH_PROBE_SOFT_TTL_S = 2.0
_HEALTH_PROBE_HARD_TTL_S = 10.0
_probe_cache: StaleWhileRevalidateCache = StaleWhileRevalidateCache(
    soft_ttl_s=_HEALTH_PROBE_SOFT_TTL_S,
    hard_ttl_s=_HEALTH_PROBE_HARD_TTL_S,
)


def _probe_warehouse() -> bool:
    """Return True when a 1s ``SELECT 1`` against the warehouse succeeds."""
    try:
        from backend.services.databricks_sql import get_sql_client
    except Exception:  # pragma: no cover -- defensive
        return False
    try:
        client = get_sql_client()
    except Exception as exc:  # noqa: BLE001
        log.warning("health: warehouse client construction failed: %s", exc)
        return False
    # Use a thread-pool timeout on the probe. The stdlib urllib-based
    # client uses a fixed socket timeout of ``wait_timeout + 5`` -- too
    # long for a health probe. Wrapping in a 1s future gives us a tight
    # ceiling regardless of the underlying client.
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: client.execute_one("SELECT 1 AS one"))
        try:
            result = fut.result(timeout=_PROBE_TIMEOUT_S)
        except FuturesTimeoutError:
            log.warning("health: warehouse probe timed out after %.1fs", _PROBE_TIMEOUT_S)
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("health: warehouse probe raised: %s", exc)
            return False
    return bool(result)


def _probe_lakebase() -> bool:
    """Return True when a 1s ``SELECT 1`` against Lakebase succeeds.

    Round-4 R4-10: previously returned True on "not configured" so local
    dev wouldn't show a banner. That's wrong in any deployed context —
    missing Lakebase creds = missing audit trail = governance §4 break,
    and returning "up" made the break invisible. We now only short-circuit
    to True when ``app_env == 'local'``; every other env surfaces False
    + `configured=False` so the degraded banner + on-call know.

    2026-05-04: the configured-check now mirrors the env-var fallback
    chain in ``backend/services/lakebase.py:_compose_dsn`` (which reads
    ``settings.lakebase_host or os.environ.get("PGHOST")``, etc.).
    Databricks Apps's ``database`` resource binding injects ``PGHOST``,
    ``PGUSER``, and ``PGPASSWORD`` (per the platform's standard
    psycopg-compatible env contract), but it does NOT alias them to
    ``LAKEBASE_*``. The old guard checked ``settings.lakebase_user``
    directly, which is None whenever Lakebase creds arrive via PGUSER —
    so the probe short-circuited to "down" in production even though
    the actual lakebase client connected fine. Mirror the client's
    fallback here so the probe and the client agree.
    """
    host = settings.lakebase_host or os.environ.get("PGHOST") or ""
    user = settings.lakebase_user or os.environ.get("PGUSER") or ""
    if not host or not user:
        return settings.app_env == "local"
    try:
        from backend.services.lakebase import get_lakebase_client
    except Exception:  # pragma: no cover -- defensive
        return False
    try:
        client = get_lakebase_client()
    except Exception as exc:  # noqa: BLE001
        log.warning("health: lakebase client construction failed: %s", exc)
        return False
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: client.fetchone("SELECT 1 AS one"))
        try:
            fut.result(timeout=_PROBE_TIMEOUT_S)
        except FuturesTimeoutError:
            log.warning("health: lakebase probe timed out after %.1fs", _PROBE_TIMEOUT_S)
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("health: lakebase probe raised: %s", exc)
            return False
    return True


def _probe_genie() -> bool:
    """Return True when a 1s ping against the Genie space succeeds.

    Uses ``GenieClient.ping`` (GET /spaces/{id}) which is cheap and
    doesn't burn a conversation slot. Missing space id -> report up
    (dev environments where Genie isn't configured shouldn't show a
    scary banner; production deploys set the space id via bundle vars
    and the client construction succeeds).
    """
    try:
        from backend.config.settings import settings as _settings
        from backend.services.genie_client import _load_space_id_from_file
    except Exception:  # pragma: no cover -- defensive
        return False
    if not _settings.genie_space_id and not _load_space_id_from_file():
        return True
    try:
        from backend.services.genie_client import get_genie_client
    except Exception:  # pragma: no cover -- defensive
        return False
    try:
        client = get_genie_client()
    except Exception as exc:  # noqa: BLE001
        log.warning("health: genie client construction failed: %s", exc)
        return False
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(client.ping)
        try:
            return bool(fut.result(timeout=_PROBE_TIMEOUT_S))
        except FuturesTimeoutError:
            log.warning("health: genie probe timed out after %.1fs", _PROBE_TIMEOUT_S)
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("health: genie probe raised: %s", exc)
            return False


def _breaker_states() -> dict[str, str]:
    """Snapshot the current state of every registered breaker.

    Reads ``state`` via the public property so a pending OPEN ->
    HALF_OPEN transition is reflected in the JSON.
    """
    out: dict[str, str] = {}
    for name, breaker in all_breakers().items():
        out[name] = breaker.state
    # Ensure the three we care about are always present even before any
    # repository has been constructed (e.g. right after startup with
    # no traffic). Reporting "closed" by default keeps the frontend's
    # JSON shape stable.
    out.setdefault("warehouse", "closed")
    out.setdefault("lakebase", "closed")
    out.setdefault("genie", "closed")
    return out


def _cached_probe(name: str, probe: Any) -> bool:
    """Return the probe's result from cache, re-probing only when stale.

    Signature preserved from the v1 plain-TTL implementation so the
    three call sites below don't change. Under the hood this is now a
    stale-while-revalidate cache: the caller's request thread only
    blocks on a real probe when the hard TTL has blown; between soft
    and hard TTL the caller gets the cached value instantly and a
    background refresh runs on a shared executor.
    """
    return bool(_probe_cache.get_or_refresh(name, probe))


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Return a probe-friendly body to anonymous callers and the full
    diagnostic body to authenticated callers.

    R6-09 (governance/security): the endpoint is publicly reachable via
    the Databricks Apps URL, which is how the platform's load balancer
    does liveness/readiness checks. The LB sends anonymous requests, so
    we MUST keep returning a 200 with a minimal body shape
    (``{status, mode}``) for that path. What changed: an external
    attacker probing the same URL used to get circuit-breaker state,
    app_env, flap-history counts, and the identity-fallback counter --
    which is free reconnaissance. That data is now gated behind
    ``X-Forwarded-Email``: any authenticated workspace user sees it,
    anonymous callers do not. We deliberately do NOT require admin
    here; the diagnostic body is ops-grade, not admin-grade, and every
    authenticated user can already see the degraded banner's reasoning.

    HTTP status stays 200 in both shapes even when ``status=="degraded"``
    so the LB probe contract (degraded != unhealthy) is preserved.
    """
    warehouse_up = _cached_probe("warehouse", _probe_warehouse)
    lakebase_up = _cached_probe("lakebase", _probe_lakebase)
    genie_up = _cached_probe("genie", _probe_genie)
    status = "ok" if (warehouse_up and lakebase_up and genie_up) else "degraded"

    # Anonymous caller (LB / external probe): minimal body only. We
    # check the forwarded-email header rather than running resolve_actor
    # so this endpoint never bumps the identity-fallback counter for a
    # routine probe, and never accidentally surfaces the counter itself.
    authenticated = bool(request.headers.get("X-Forwarded-Email"))
    if not authenticated:
        return {"status": status, "mode": "live"}

    # Authenticated caller: full diagnostic body. Keep the dependencies
    # + circuit_breakers blocks so the frontend's degraded banner and
    # the ops-facing `/admin/health` page can still reason about
    # dependency state; those fetches happen in a logged-in workspace
    # browser session that Databricks Apps decorates with
    # X-Forwarded-Email.
    deps = {
        "warehouse": "up" if warehouse_up else "down",
        "lakebase": "up" if lakebase_up else "down",
        "genie": "up" if genie_up else "down",
    }
    return {
        "status": status,
        "mode": "live",
        "app_env": settings.app_env,
        "warehouse_id": settings.databricks_warehouse_id,
        "dependencies": deps,
        "circuit_breakers": _breaker_states(),
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
        # Surfacing the posture in the health body means operators can
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
    }
