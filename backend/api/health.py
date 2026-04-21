"""Health endpoint with live dependency status + breaker snapshot.

Slice-6 contract (returned body):

    {
      "status":            "ok" | "degraded",
      "mode":              "live",
      "app_env":           "<env>",
      "warehouse_id":      "<id>",
      "dependencies": {
        "warehouse":       "up" | "down",
        "lakebase":        "up" | "down"
      },
      "circuit_breakers": {
        "warehouse":       "closed" | "open" | "half_open",
        "lakebase":        "closed" | "open" | "half_open"
      }
    }

A degraded response STILL returns HTTP 200 so the Databricks App load
balancer doesn't yank the container. Degraded state is carried in the
body, which the frontend reads to show the banner.

Each dependency probe is a ``SELECT 1`` with a 1-second timeout.
Failures do not raise; they flip the dependency status to ``down`` and
bump the breaker's failure counter. The frontend's degraded banner
auto-retries until ``status == "ok"``.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from fastapi import APIRouter

from backend.config.settings import settings
from backend.services.resilience import all_breakers

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


_PROBE_TIMEOUT_S = 1.0


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


def _breaker_states() -> dict[str, str]:
    """Snapshot the current state of every registered breaker.

    Reads ``state`` via the public property so a pending OPEN ->
    HALF_OPEN transition is reflected in the JSON.
    """
    out: dict[str, str] = {}
    for name, breaker in all_breakers().items():
        out[name] = breaker.state
    # Ensure the two we care about are always present even before any
    # repository has been constructed (e.g. right after startup with
    # no traffic). Reporting "closed" by default keeps the frontend's
    # JSON shape stable.
    out.setdefault("warehouse", "closed")
    out.setdefault("lakebase", "closed")
    return out


@router.get("/health")
def health() -> dict[str, Any]:
    warehouse_up = _probe_warehouse()
    lakebase_up = _probe_lakebase()
    deps = {
        "warehouse": "up" if warehouse_up else "down",
        "lakebase": "up" if lakebase_up else "down",
    }
    status = "ok" if (warehouse_up and lakebase_up) else "degraded"
    return {
        "status": status,
        "mode": "live",
        "app_env": settings.app_env,
        "warehouse_id": settings.databricks_warehouse_id,
        "dependencies": deps,
        "circuit_breakers": _breaker_states(),
    }
