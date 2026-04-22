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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from fastapi import APIRouter

from backend.config.settings import settings
from backend.services.observability import (
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
    """Return True when a 1s ``SELECT 1`` against Lakebase succeeds."""
    if not settings.lakebase_host or not settings.lakebase_user:
        # Not configured -- treat as up so we don't show a scary
        # banner in a dev environment where Lakebase isn't expected
        # to be available. Governance §4 still requires real Lakebase
        # for production; that's enforced on deploy, not here.
        return True
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
def health() -> dict[str, Any]:
    warehouse_up = _cached_probe("warehouse", _probe_warehouse)
    lakebase_up = _cached_probe("lakebase", _probe_lakebase)
    genie_up = _cached_probe("genie", _probe_genie)
    deps = {
        "warehouse": "up" if warehouse_up else "down",
        "lakebase": "up" if lakebase_up else "down",
        "genie": "up" if genie_up else "down",
    }
    status = "ok" if (warehouse_up and lakebase_up and genie_up) else "degraded"
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
    }
