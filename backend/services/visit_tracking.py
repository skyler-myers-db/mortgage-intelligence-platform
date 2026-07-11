"""Throttled authenticated-visit tracking for ``mip_app.user_visits``.

S3: the personalized home summary (S4) needs "when did this actor last
open the app" to anchor its "since your last login" KPI deltas. This
module records that signal with deliberately low overhead:

* **Identity**: only the existing actor identity model -- the
  ``X-Forwarded-Email`` / ``X-Forwarded-User`` headers the Databricks
  Apps edge injects. Requests without a forwarded identity (platform
  health probes, local curl, untrusted-edge deploys) are skipped
  entirely; we never attribute a visit to the ``default_actor``
  fallback the audit ledger uses.
* **Throttle**: a browsing session must not write a row per request.
  An in-process per-actor window (default 15 minutes) gates the write,
  and the INSERT itself re-checks the window in SQL (``WHERE NOT
  EXISTS`` within the window) so multiple app replicas cannot stack
  rows either.
* **Off the request path**: the middleware claims the throttle slot
  synchronously (a dict lookup) and pushes the actual Lakebase INSERT
  to a background thread. A Lakebase outage therefore costs requests
  nothing -- the write failure is logged, the throttle slot is
  released, and the next request past the window retries.

No PII beyond the actor email, no routes, no IPs, no user agents.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from threading import Lock, Thread
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp

from backend.config.settings import _running_under_pytest, settings
from backend.services.observability import emit

log = logging.getLogger(__name__)

# One row per actor per window. A module constant (not a Settings field)
# because no deployment has needed to tune it yet; tests inject their own
# window through the constructor.
DEFAULT_VISIT_DEDUPE_WINDOW_S: float = 900.0

# Bound the in-process throttle map. Workspace user counts are tiny next to
# this; the sweep exists so a header-fuzzing probe can't grow the dict forever.
_MAX_TRACKED_ACTORS = 10_000

# SQL-side dedupe mirrors the in-process window so multiple app processes
# (or a process restart mid-window) still can't write more than one row per
# actor per window. Named-parameter binding only, per the Lakebase client
# contract.
_INSERT_VISIT_SQL = """
INSERT INTO mip_app.user_visits (actor_email, visited_at)
SELECT %(actor_email)s, now()
WHERE NOT EXISTS (
    SELECT 1
    FROM mip_app.user_visits
    WHERE actor_email = %(actor_email)s
      AND visited_at > now() - make_interval(secs => %(window_s)s)
)
"""


def forwarded_actor(request: StarletteRequest) -> str | None:
    """Return the edge-forwarded identity, or ``None`` when unauthenticated.

    Unlike ``audit_store.resolve_actor`` this NEVER falls back to
    ``settings.default_actor`` -- a visit row attributed to a placeholder
    identity would poison the "since your last login" anchor for every
    unauthenticated probe. When ``trust_forwarded_headers`` is off the
    headers are attacker-writable, so visits are not recorded at all.
    """
    if not settings.trust_forwarded_headers:
        return None
    email = request.headers.get("X-Forwarded-Email")
    if email:
        return email
    user = request.headers.get("X-Forwarded-User")
    if user:
        return user
    return None


class VisitTracker:
    """Per-actor throttled writer for ``mip_app.user_visits``.

    ``client_factory`` defaults to the process Lakebase client; when the
    default is in use, writes are additionally gated on the same
    connection hints ``backend.main._warm_lakebase`` checks, so local
    dev / pytest processes without Lakebase configuration never attempt
    a connection. Tests inject a fake client factory, which bypasses the
    hint gate.
    """

    def __init__(
        self,
        client_factory: Callable[[], Any] | None = None,
        *,
        window_s: float = DEFAULT_VISIT_DEDUPE_WINDOW_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if window_s <= 0:
            raise ValueError("visit dedupe window must be > 0 seconds")
        self._explicit_factory = client_factory
        self._window_s = window_s
        self._now = now
        self._lock = Lock()
        self._last_claim: dict[str, float] = {}

    @property
    def window_s(self) -> float:
        return self._window_s

    def _connection_hints_present(self) -> bool:
        host = settings.lakebase_host or os.environ.get("PGHOST")
        user = settings.lakebase_user or os.environ.get("PGUSER")
        return bool(host and user)

    def maybe_claim(self, actor: str | None) -> bool:
        """Claim the actor's throttle slot; True when a write should happen.

        Synchronous and cheap (dict + lock) -- safe on the request path.
        Unauthenticated callers (``actor`` falsy) never claim. When the
        default Lakebase client would be used but no connection hints are
        present, the claim is refused so no background write is spawned.
        """
        if not actor:
            return False
        # Default-factory path only: pytest processes must never open a
        # background Postgres connection (the conftest Lakebase override is a
        # FastAPI dependency seam, which this service-level call bypasses),
        # and processes without connection hints (local dev without Lakebase)
        # have nothing to write to. Tests exercise the tracker by injecting
        # an explicit factory.
        if self._explicit_factory is None and (
            _running_under_pytest() or not self._connection_hints_present()
        ):
            return False
        now = self._now()
        with self._lock:
            last = self._last_claim.get(actor)
            if last is not None and (now - last) < self._window_s:
                return False
            if len(self._last_claim) >= _MAX_TRACKED_ACTORS:
                # Drop expired claims; if everything is live we still insert
                # (one actor over the cap beats losing the visit signal).
                expired = [
                    key
                    for key, claimed in self._last_claim.items()
                    if (now - claimed) >= self._window_s
                ]
                for key in expired:
                    del self._last_claim[key]
            self._last_claim[actor] = now
            return True

    def _release_claim(self, actor: str) -> None:
        with self._lock:
            self._last_claim.pop(actor, None)

    def record_visit(self, actor: str) -> None:
        """Write the visit row. Never raises -- visit tracking must not
        break a request; failures release the throttle slot so the next
        request past the window retries."""
        try:
            if self._explicit_factory is not None:
                client = self._explicit_factory()
            else:
                from backend.services.lakebase import get_lakebase_client

                client = get_lakebase_client()
            client.execute(
                _INSERT_VISIT_SQL,
                {"actor_email": actor, "window_s": self._window_s},
            )
        except Exception as exc:  # noqa: BLE001 -- fire-and-forget contract
            self._release_claim(actor)
            emit(
                log,
                "visit_record_failed",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            return
        emit(log, "visit_recorded", dependency="lakebase", outcome="ok")


_TRACKER: VisitTracker | None = None
_TRACKER_LOCK = Lock()


def get_visit_tracker() -> VisitTracker:
    """Process-singleton accessor (mirrors ``get_lakebase_client``)."""
    global _TRACKER
    if _TRACKER is None:
        with _TRACKER_LOCK:
            if _TRACKER is None:
                _TRACKER = VisitTracker()
    return _TRACKER


def _reset_tracker_for_tests() -> None:
    global _TRACKER
    with _TRACKER_LOCK:
        _TRACKER = None


class VisitTrackingMiddleware(BaseHTTPMiddleware):
    """Record one ``mip_app.user_visits`` row per actor per window.

    Only ``/api`` traffic counts as a visit (static asset fetches carry no
    intent signal). The throttle claim is the only synchronous work; the
    Lakebase INSERT runs on a short-lived daemon thread (at most one per
    actor per window) so it survives event-loop teardown and never delays
    the response.
    """

    def __init__(self, app: ASGIApp, tracker: VisitTracker | None = None) -> None:
        super().__init__(app)
        self._tracker = tracker

    def _resolve_tracker(self) -> VisitTracker:
        return self._tracker if self._tracker is not None else get_visit_tracker()

    async def dispatch(
        self, request: StarletteRequest, call_next: Any
    ) -> StarletteResponse:
        response = await call_next(request)
        path = request.url.path
        if path == "/api" or path.startswith("/api/"):
            tracker = self._resolve_tracker()
            actor = forwarded_actor(request)
            if actor and tracker.maybe_claim(actor):
                Thread(
                    target=tracker.record_visit,
                    args=(actor,),
                    name="mip-visit-write",
                    daemon=True,
                ).start()
        return response
