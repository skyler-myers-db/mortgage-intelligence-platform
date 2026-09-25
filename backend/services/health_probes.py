"""Shared dependency health probes for API health and proof surfaces."""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from threading import Lock
from typing import Any, Literal

from backend.config.settings import settings
from backend.services.observability import emit
from backend.services.resilience import StaleWhileRevalidateCache, all_breakers

log = logging.getLogger(__name__)

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


DependencyState = Literal["up", "down", "resuming"]
DEPENDENCY_STATES: frozenset[str] = frozenset({"up", "down", "resuming"})

# Warehouse lifecycle -> dependency state (audit delivery-01 / delivery-v1).
# STOPPED / STOPPING read as "up" on purpose: a serverless warehouse is
# available on demand, and with keep-warm off by default STOPPED is the steady
# idle state; calling it "resuming" would show a perpetual "Waking warehouse"
# on idle tabs and make tools/wait_app_ready.py (which waits for "up") hang
# after every deploy. STARTING is the positive resume signal.
_WAREHOUSE_STATE_MAP: dict[str, DependencyState] = {
    "RUNNING": "up",
    "STARTING": "resuming",
    "STOPPED": "up",
    "STOPPING": "up",
    "DELETING": "down",
    "DELETED": "down",
}
# Inside the health request budget (mip_health_cold_wait_budget_s, 3 s), so a
# slow state read fails over to SELECT 1 instead of timing health out. The
# bound covers the state READ only: building the client resolves host
# metadata with the SDK's own default timeouts, which is why the build runs in
# the lifespan warm path (prime_warehouse_state_client), a probe never waits
# on a build in flight, and a failed build is retried at most once per
# _CLIENT_RETRY_AFTER_S. Until a client exists the probe uses SELECT 1.
_WAREHOUSE_STATE_HTTP_TIMEOUT_S = 2.0
_CLIENT_RETRY_AFTER_S = 60.0
_workspace_client: Any = None
_client_failed_at: float | None = None
_workspace_client_lock = Lock()
# A state read the App cannot make (e.g. no CAN_USE on the warehouse) fails on
# every probe refresh, about every 2 s while any tab polls. The first failure
# logs at WARNING and repeats inside this window at DEBUG; a successful read
# re-arms it, so a new failure after a recovery warns at once.
_READ_FAILURE_WARN_EVERY_S = 300.0
_read_failure_warned_at: float | None = None
_read_failure_lock = Lock()


class WarehouseStateClientUnavailable(RuntimeError):
    """No state-read client yet: a build is in flight or recently failed."""


def _warehouse_state_client() -> Any:
    """Return the SDK client, building it once; never wait on another build."""

    global _workspace_client, _client_failed_at
    client = _workspace_client
    if client is not None:
        return client
    if not _workspace_client_lock.acquire(blocking=False):
        raise WarehouseStateClientUnavailable("build_in_flight")
    try:
        if _workspace_client is None:
            failed_at = _client_failed_at
            if failed_at is not None and time.monotonic() - failed_at < _CLIENT_RETRY_AFTER_S:
                raise WarehouseStateClientUnavailable("build_failed_recently")
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.core import Config

            try:
                _workspace_client = WorkspaceClient(
                    config=Config(
                        http_timeout_seconds=_WAREHOUSE_STATE_HTTP_TIMEOUT_S,
                        retry_timeout_seconds=int(_WAREHOUSE_STATE_HTTP_TIMEOUT_S),
                    )
                )
            except Exception:
                _client_failed_at = time.monotonic()
                raise
            _client_failed_at = None
        return _workspace_client
    finally:
        _workspace_client_lock.release()


def prime_warehouse_state_client() -> None:
    """Build the state-read client at startup; log-and-continue like the warms."""

    if not (settings.databricks_warehouse_id or "").strip():
        return
    try:
        _warehouse_state_client()
    except Exception as exc:  # noqa: BLE001 -- the probe falls back to SELECT 1
        emit(
            log,
            "warehouse_state_client_prime_failed",
            level=logging.WARNING,
            dependency="warehouse",
            exc_type=type(exc).__name__,
        )


def _read_failure_level(*, failed: bool) -> int:
    """WARNING for the first state-read failure per window, DEBUG for repeats."""

    global _read_failure_warned_at
    with _read_failure_lock:
        if not failed:
            _read_failure_warned_at = None
            return logging.DEBUG
        now = time.monotonic()
        last = _read_failure_warned_at
        if last is not None and now - last < _READ_FAILURE_WARN_EVERY_S:
            return logging.DEBUG
        _read_failure_warned_at = now
        return logging.WARNING


def _read_warehouse_state() -> DependencyState | None:
    """Map the warehouse lifecycle state; None means "could not tell"."""

    warehouse_id = (settings.databricks_warehouse_id or "").strip()
    if not warehouse_id:
        return None
    try:
        warehouse = _warehouse_state_client().warehouses.get(id=warehouse_id)
    except Exception as exc:  # noqa: BLE001 -- the SELECT 1 fallback decides
        emit(
            log,
            "warehouse_state_read_failed",
            level=_read_failure_level(failed=True),
            dependency="warehouse",
            reason="error",
            exc_type=type(exc).__name__,
        )
        return None
    raw = getattr(warehouse, "state", None)
    name = str(getattr(raw, "value", raw) or "").upper()
    mapped = _WAREHOUSE_STATE_MAP.get(name)
    if mapped is None:
        emit(
            log,
            "warehouse_state_read_failed",
            level=_read_failure_level(failed=True),
            dependency="warehouse",
            reason="unknown_state" if name else "no_state",
            exc_type=None,
        )
    else:
        _read_failure_level(failed=False)
    return mapped


def probe_warehouse() -> DependencyState:
    """Return the warehouse dependency state: ``up`` | ``down`` | ``resuming``.

    Reads the warehouse lifecycle state (``GET /api/2.0/sql/warehouses/{id}``)
    instead of running SQL, so a routine serverless resume reads as
    ``resuming`` rather than an outage, and the health poll no longer keeps
    the warehouse awake by accident (keep-warm is an explicit policy now; see
    ``backend.services.keep_warm``). When the state cannot be read, falls back
    to today's ``SELECT 1`` probe, which never yields ``resuming``.
    """

    state = _read_warehouse_state()
    if state is not None:
        return state
    return "up" if _probe_warehouse_select_one() else "down"


def _probe_warehouse_select_one() -> bool:
    """Return True when ``SELECT 1`` against the warehouse succeeds.

    The shared health cache owns the caller deadline and single-flight work;
    this function performs only the real dependency call.
    """

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
    try:
        result = client.execute_one("SELECT 1 AS one")
    except Exception as exc:  # noqa: BLE001
        _emit_probe_warning("health_probe_failed", dependency="warehouse", exc=exc)
        return False
    return bool(result)


def probe_lakebase() -> bool:
    """Return True when ``SELECT 1`` against Lakebase succeeds."""

    host = settings.lakebase_host or os.environ.get("PGHOST") or ""
    if not host:
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
    try:
        healthcheck = getattr(client, "healthcheck", None)
        if callable(healthcheck):
            return bool(healthcheck())
        # Test doubles and legacy injected clients retain the narrow fetch seam;
        # the production Lakebase client always exposes bounded ``healthcheck``.
        client.fetchone("SELECT 1 AS one")
    except Exception as exc:  # noqa: BLE001
        _emit_probe_warning("health_probe_failed", dependency="lakebase", exc=exc)
        return False
    return True


def probe_genie() -> bool:
    """Return True when a ping against the Genie space succeeds."""

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
    try:
        return bool(client.ping())
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
    """Return whether the dependency is up, through the shared SWR cache.

    Fail-closed: only ``True`` or the state ``"up"`` count as up. A state
    probe's ``"down"`` or ``"resuming"``, ``None`` or any other value reads as
    not up; ``bool(result)`` would have turned the truthy string ``"down"``
    into up for a state-returning probe such as ``probe_warehouse``.
    """

    result = _probe_cache.get_or_refresh(
        name,
        probe,
        wait_timeout_s=settings.mip_health_cold_wait_budget_s,
    )
    return _dependency_state(result) == "up"


def _dependency_state(result: Any) -> DependencyState:
    """Normalise a probe result: bools (and test doubles) or a state string."""

    if isinstance(result, str) and result in DEPENDENCY_STATES:
        return result  # type: ignore[return-value]
    if result is True:
        return "up"
    return "down"


def probe_snapshot() -> tuple[str, dict[str, str]]:
    """``status`` is ``ok`` iff every dependency is ``up`` or ``resuming``."""

    results = _probe_cache.get_or_refresh_many(
        {
            "warehouse": probe_warehouse,
            "lakebase": probe_lakebase,
            "genie": probe_genie,
        },
        wait_timeout_s=settings.mip_health_cold_wait_budget_s,
    )
    deps: dict[str, str] = {
        name: _dependency_state(results[name]) for name in ("warehouse", "lakebase", "genie")
    }
    status = "ok" if all(state != "down" for state in deps.values()) else "degraded"
    return status, deps
