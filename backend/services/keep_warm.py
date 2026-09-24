"""One warehouse keep-warm policy (audit ``delivery-v1``, critic fix 21).

The health poll's ``SELECT 1`` used to keep the serverless warehouse awake by
accident: any visible tab queried it every 8 s, so it never auto-stopped.
Health now reads the warehouse state instead (``health_probes``), which
removes that accident; keeping the warehouse warm is an explicit choice
between cost and cold starts, decided here and nowhere else.

``MIP_WAREHOUSE_KEEP_WARM`` (``effective_policy`` is the single reader):

* ``off`` (default): nothing runs; an idle warehouse auto-stops after
  ``auto_stop_mins`` (10) and the next visitor waits for a 2-6 s serverless
  resume, which the UI shows as a calm "Waking warehouse" pill.
* ``activity``: an AUTHENTICATED ``GET /api/health`` whose tab saw user input
  within ``MIP_WAREHOUSE_KEEP_WARM_ACTIVITY_WINDOW_MIN`` minutes (the
  ``idle_s`` hint) submits at most one ``SELECT 1 AS keep_warm`` per
  ``KEEP_WARM_PING_INTERVAL_S``, process-wide, on a single-worker executor.
  Health never waits for it. The ping assumes any statement resets the
  warehouse idle timer (standard behaviour, not live-verified).
* ``scheduled``: the lead-page refresh-ahead loop at
  ``MIP_LEADS_WARM_INTERVAL_S``, which must be > 0 (``scheduled`` with 0
  resolves to ``off`` and logs an ERROR).

A positive ``MIP_LEADS_WARM_INTERVAL_S`` under ``off`` or ``activity`` is
ignored with one startup WARNING; it never starts a second keep-warm.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import time
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from threading import Lock
from typing import Literal

from backend.config.settings import settings
from backend.services.observability import emit

log = logging.getLogger(__name__)

KeepWarmPolicy = Literal["off", "activity", "scheduled"]

# Well inside the bundle's auto_stop_mins (10): a ping every 4 minutes keeps
# an active session's warehouse running (pinned by tests/unit/test_keep_warm.py).
KEEP_WARM_PING_INTERVAL_S = 240
_MAX_IDLE_HINT_S = 86_400
KEEP_WARM_STATEMENT = "SELECT 1 AS keep_warm"

_lock = Lock()
_last_ping_at: float | None = None
_ping_in_flight = False
_executor: Executor | None = None
_now: Callable[[], float] = time.monotonic


def effective_policy() -> KeepWarmPolicy:
    """The one resolver every keep-warm decision reads."""

    policy = settings.mip_warehouse_keep_warm
    if policy == "scheduled" and settings.mip_leads_warm_interval_s <= 0:
        return "off"
    return policy


def log_startup_policy() -> KeepWarmPolicy:
    """Announce the resolved policy once at startup and return it."""

    configured = settings.mip_warehouse_keep_warm
    interval_s = settings.mip_leads_warm_interval_s
    policy = effective_policy()
    if configured == "scheduled" and interval_s <= 0:
        emit(
            log,
            "warehouse_keep_warm_scheduled_without_interval",
            level=logging.ERROR,
            dependency="warehouse",
            outcome="off",
            interval_s=interval_s,
        )
    elif configured != "scheduled" and interval_s > 0:
        emit(
            log,
            "warehouse_keep_warm_interval_ignored",
            level=logging.WARNING,
            dependency="warehouse",
            policy=configured,
            interval_s=interval_s,
        )
    emit(
        log,
        "warehouse_keep_warm_policy",
        dependency="warehouse",
        policy=policy,
        configured=configured,
        interval_s=interval_s if policy == "scheduled" else None,
        activity_window_min=(
            settings.mip_warehouse_keep_warm_activity_window_min if policy == "activity" else None
        ),
    )
    return policy


def parse_idle_hint(raw: str | None) -> int | None:
    """Read ``?idle_s=`` leniently: an int in 0..86400, anything else ignored."""

    if raw is None:
        return None
    text = raw.strip()
    if not text or len(text) > 5 or not text.isascii() or not text.isdigit():
        return None
    value = int(text)
    return value if value <= _MAX_IDLE_HINT_S else None


def note_activity(idle_s: int | None) -> bool:
    """Record an authenticated tab's activity hint; True when a ping was sent.

    Called only from the authenticated branch of ``GET /api/health``. Never
    raises and never blocks: the ping is fire-and-forget.
    """

    global _last_ping_at, _ping_in_flight
    if idle_s is None or effective_policy() != "activity":
        return False
    if idle_s > settings.mip_warehouse_keep_warm_activity_window_min * 60:
        return False
    with _lock:
        now = _now()
        if _ping_in_flight:
            return False
        if _last_ping_at is not None and now - _last_ping_at < KEEP_WARM_PING_INTERVAL_S:
            return False
        _ping_in_flight = True
        _last_ping_at = now
    try:
        _get_executor().submit(_ping)
    except RuntimeError:
        with _lock:
            _ping_in_flight = False
        return False
    return True


def _ping() -> None:
    global _ping_in_flight
    try:
        from backend.services.databricks_sql import get_sql_client

        get_sql_client().execute_one(KEEP_WARM_STATEMENT)
    except Exception as exc:  # noqa: BLE001 -- a keep-warm ping never fails a request
        emit(
            log,
            "warehouse_keep_warm_ping_failed",
            level=logging.WARNING,
            dependency="warehouse",
            exc_type=type(exc).__name__,
        )
    finally:
        with _lock:
            _ping_in_flight = False


def _get_executor() -> Executor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mip-keep-warm")
        return _executor


def _shutdown_executor() -> None:
    global _executor
    with _lock:
        pool, _executor = _executor, None
    if pool is not None:
        with contextlib.suppress(Exception):
            pool.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_executor)


def _reset_for_tests(
    *,
    now: Callable[[], float] = time.monotonic,
    executor: Executor | None = None,
) -> None:
    global _last_ping_at, _ping_in_flight, _now, _executor
    _shutdown_executor()
    with _lock:
        _last_ping_at = None
        _ping_in_flight = False
        _now = now
        _executor = executor


__all__ = [
    "KEEP_WARM_PING_INTERVAL_S",
    "KeepWarmPolicy",
    "effective_policy",
    "log_startup_policy",
    "note_activity",
    "parse_idle_hint",
]
