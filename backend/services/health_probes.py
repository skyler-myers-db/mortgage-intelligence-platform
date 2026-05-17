"""Shared dependency health probes for API health and proof surfaces."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from backend.config.settings import settings
from backend.services.observability import emit
from backend.services.resilience import StaleWhileRevalidateCache, all_breakers

log = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 1.0

# Stale-while-revalidate cache around each dependency probe.
#
# Inside the soft TTL, callers get a cache-hit response. Between soft and hard
# TTLs, callers still get the cached value immediately while a background
# refresh runs. Only a hard-expired probe blocks the request thread.
_HEALTH_PROBE_SOFT_TTL_S = 2.0
_HEALTH_PROBE_HARD_TTL_S = 10.0
_probe_cache: StaleWhileRevalidateCache = StaleWhileRevalidateCache(
    soft_ttl_s=_HEALTH_PROBE_SOFT_TTL_S,
    hard_ttl_s=_HEALTH_PROBE_HARD_TTL_S,
)


def _emit_probe_warning(event: str, *, dependency: str, exc: BaseException | None = None) -> None:
    emit(
        log,
        event,
        level=logging.WARNING,
        dependency=dependency,
        outcome="error",
        exc_type=type(exc).__name__ if exc is not None else None,
        exc_msg=str(exc)[:500] if exc is not None else None,
    )


def probe_warehouse() -> bool:
    """Return True when a 1s ``SELECT 1`` against the warehouse succeeds."""

    try:
        from backend.services.databricks_sql import get_sql_client
    except Exception as exc:  # pragma: no cover -- defensive
        _emit_probe_warning("health_probe_client_import_failed", dependency="warehouse", exc=exc)
        return False
    try:
        client = get_sql_client()
    except Exception as exc:  # noqa: BLE001
        _emit_probe_warning("health_probe_client_construction_failed", dependency="warehouse", exc=exc)
        return False
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: client.execute_one("SELECT 1 AS one"))
        try:
            result = fut.result(timeout=_PROBE_TIMEOUT_S)
        except FuturesTimeoutError:
            emit(
                log,
                "health_probe_timeout",
                level=logging.WARNING,
                dependency="warehouse",
                timeout_s=_PROBE_TIMEOUT_S,
                outcome="error",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            _emit_probe_warning("health_probe_failed", dependency="warehouse", exc=exc)
            return False
    return bool(result)


def probe_lakebase() -> bool:
    """Return True when a 1s ``SELECT 1`` against Lakebase succeeds."""

    host = settings.lakebase_host or os.environ.get("PGHOST") or ""
    user = settings.lakebase_user or os.environ.get("PGUSER") or ""
    if not host or not user:
        return settings.app_env == "local"
    try:
        from backend.services.lakebase import get_lakebase_client
    except Exception as exc:  # pragma: no cover -- defensive
        _emit_probe_warning("health_probe_client_import_failed", dependency="lakebase", exc=exc)
        return False
    try:
        client = get_lakebase_client()
    except Exception as exc:  # noqa: BLE001
        _emit_probe_warning("health_probe_client_construction_failed", dependency="lakebase", exc=exc)
        return False
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: client.fetchone("SELECT 1 AS one"))
        try:
            fut.result(timeout=_PROBE_TIMEOUT_S)
        except FuturesTimeoutError:
            emit(
                log,
                "health_probe_timeout",
                level=logging.WARNING,
                dependency="lakebase",
                timeout_s=_PROBE_TIMEOUT_S,
                outcome="error",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            _emit_probe_warning("health_probe_failed", dependency="lakebase", exc=exc)
            return False
    return True


def probe_genie() -> bool:
    """Return True when a 1s ping against the Genie space succeeds."""

    try:
        from backend.config.settings import settings as _settings
        from backend.services.genie_client import _load_space_id_from_file
    except Exception as exc:  # pragma: no cover -- defensive
        _emit_probe_warning("health_probe_client_import_failed", dependency="genie", exc=exc)
        return False
    if not _settings.genie_space_id and not _load_space_id_from_file():
        return True
    try:
        from backend.services.genie_client import get_genie_client
    except Exception as exc:  # pragma: no cover -- defensive
        _emit_probe_warning("health_probe_client_import_failed", dependency="genie", exc=exc)
        return False
    try:
        client = get_genie_client()
    except Exception as exc:  # noqa: BLE001
        _emit_probe_warning("health_probe_client_construction_failed", dependency="genie", exc=exc)
        return False
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(client.ping)
        try:
            return bool(fut.result(timeout=_PROBE_TIMEOUT_S))
        except FuturesTimeoutError:
            emit(
                log,
                "health_probe_timeout",
                level=logging.WARNING,
                dependency="genie",
                timeout_s=_PROBE_TIMEOUT_S,
                outcome="error",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            _emit_probe_warning("health_probe_failed", dependency="genie", exc=exc)
            return False


def breaker_states() -> dict[str, str]:
    """Snapshot the current state of every registered breaker."""

    out: dict[str, str] = {}
    for name, breaker in all_breakers().items():
        out[name] = breaker.state
    out.setdefault("warehouse", "closed")
    out.setdefault("lakebase", "closed")
    out.setdefault("genie", "closed")
    return out


def cached_probe(name: str, probe: Callable[[], Any]) -> bool:
    """Return the probe result through the shared SWR cache."""

    return bool(_probe_cache.get_or_refresh(name, probe))


def probe_snapshot() -> tuple[str, dict[str, str]]:
    warehouse_up = cached_probe("warehouse", probe_warehouse)
    lakebase_up = cached_probe("lakebase", probe_lakebase)
    genie_up = cached_probe("genie", probe_genie)
    status = "ok" if (warehouse_up and lakebase_up and genie_up) else "degraded"
    deps = {
        "warehouse": "up" if warehouse_up else "down",
        "lakebase": "up" if lakebase_up else "down",
        "genie": "up" if genie_up else "down",
    }
    return status, deps
