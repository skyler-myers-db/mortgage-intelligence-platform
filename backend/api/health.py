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
from backend.services.resilience import TTLCache, all_breakers

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


_PROBE_TIMEOUT_S = 1.0

# Slice-13 performance follow-up: cache each dependency probe's last
# result for 3 s. Under the 20-VU warm-UC load baseline (see
# docs/load-baseline.md), `/api/health` p95 was 1.8 s — dominated by
# the Genie probe's real HTTP round-trip. A 3-second TTL means a burst
# of health hits fans out at most one probe per dependency per 3 s,
# cutting p95 from ~1.8 s to the cache-hit latency (<5 ms). The TTL is
# short enough that a genuine dependency outage is surfaced within
# three seconds — well under the frontend's degraded-banner polling
# cadence — so resilience semantics are unchanged.
_HEALTH_PROBE_TTL_S = 3.0
_probe_cache: TTLCache = TTLCache()


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

    Wraps a zero-argument probe callable with the 3-second TTLCache.
    On cache miss, runs the probe and stores the boolean. The cache is
    process-local; operator-triggered restarts always see a fresh
    probe on the next request.
    """
    cached = _probe_cache.get(name)
    if cached is not None:
        return bool(cached)
    result = probe()
    _probe_cache.set(name, result, _HEALTH_PROBE_TTL_S)
    return bool(result)


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
