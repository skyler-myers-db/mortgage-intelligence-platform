"""Process-local rate limiting and dependency backpressure.

The Databricks App runs behind workspace authentication, so this layer is
not an internet-edge security control. It is an application protection
rail: one authenticated actor should not be able to saturate the SQL
warehouse, Genie, or Lakebase write path with accidental refresh loops or
over-eager automations.

Design constraints:

* No external service dependency. The app must still boot in a fresh
  Databricks App container without Redis or a sidecar.
* Fail fast with HTTP 429 and `Retry-After`, not a slow queue that makes
  the UI look broken.
* Keep the dependency-down `503 retryable=true` contract distinct from
  caller overload. A warm warehouse is a 503; an actor over budget is a
  429.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from threading import Lock, Semaphore
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from backend.config.settings import _running_under_pytest, settings
from backend.services.observability import emit, get_correlation_id

log = logging.getLogger("mip.backpressure")


@dataclass(frozen=True)
class RouteBudget:
    scope: str
    requests_per_minute: int
    dependency: str | None = None


@dataclass
class _Bucket:
    tokens: float
    last_seen: float


class BackpressureController:
    """Token-bucket rate limiter + non-blocking dependency semaphores."""

    _BORROWER_ID_RE = re.compile(r"/api/borrowers/[^/]+$")
    _ASSIGNMENT_RE = re.compile(r"/api/leads/[^/]+/(?:assign|disposition)$")

    def __init__(
        self,
        *,
        now: Any = time.monotonic,
        warehouse_concurrency: int | None = None,
        lakebase_concurrency: int | None = None,
        genie_concurrency: int | None = None,
    ) -> None:
        self._now = now
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = Lock()
        self._hits_since_prune = 0
        self._semaphores = {
            "warehouse": Semaphore(
                warehouse_concurrency
                if warehouse_concurrency is not None
                else settings.mip_warehouse_concurrency_limit
            ),
            "lakebase": Semaphore(
                lakebase_concurrency
                if lakebase_concurrency is not None
                else settings.mip_lakebase_concurrency_limit
            ),
            "genie": Semaphore(
                genie_concurrency
                if genie_concurrency is not None
                else settings.mip_genie_concurrency_limit
            ),
        }

    def classify(self, method: str, path: str) -> RouteBudget | None:
        """Return a budget for the request, or None for unthrottled paths."""
        if not path.startswith("/api/"):
            return None
        if path in {"/api/health", "/api/admin/health"}:
            return RouteBudget("health", settings.mip_rate_limit_default_per_minute, None)
        if path.startswith("/api/telemetry"):
            return RouteBudget("telemetry", settings.mip_rate_limit_telemetry_per_minute, None)
        if path.startswith("/api/genie"):
            return RouteBudget("genie", settings.mip_rate_limit_genie_per_minute, "genie")
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            dependency = "lakebase"
            if path.startswith("/api/genie"):
                dependency = "genie"
            return RouteBudget("mutation", settings.mip_rate_limit_mutation_per_minute, dependency)
        if path.startswith("/api/audit") or path.startswith("/api/workspace"):
            return RouteBudget("lakebase-read", settings.mip_rate_limit_default_per_minute, "lakebase")
        if self._BORROWER_ID_RE.match(path):
            return RouteBudget("borrower-dossier", settings.mip_rate_limit_expensive_per_minute, "warehouse")
        if self._ASSIGNMENT_RE.match(path):
            return RouteBudget("mutation", settings.mip_rate_limit_mutation_per_minute, "lakebase")
        if path.startswith((
            "/api/leads",
            "/api/segments",
            "/api/portfolio",
            "/api/geo",
            "/api/config",
            "/api/admin",
            "/api/data-estate",
            "/api/offers",
            "/api/sales",
            "/api/borrowers",
        )):
            return RouteBudget("warehouse-read", settings.mip_rate_limit_expensive_per_minute, "warehouse")
        return RouteBudget("default", settings.mip_rate_limit_default_per_minute, None)

    def check_rate(self, actor: str, budget: RouteBudget) -> int | None:
        """Return retry-after seconds when over budget, otherwise None."""
        limit = max(1, int(budget.requests_per_minute))
        now = float(self._now())
        key = (actor, budget.scope)
        refill_per_second = limit / 60.0
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(tokens=float(limit - 1), last_seen=now)
                self._prune_locked(now)
                return None
            elapsed = max(0.0, now - bucket.last_seen)
            bucket.tokens = min(float(limit), bucket.tokens + elapsed * refill_per_second)
            bucket.last_seen = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                self._prune_locked(now)
                return None
            retry_after = max(1, math.ceil((1.0 - bucket.tokens) / refill_per_second))
            self._prune_locked(now)
            return retry_after

    def acquire_dependency(self, dependency: str | None) -> tuple[bool, Any]:
        if dependency is None:
            return True, None
        semaphore = self._semaphores.get(dependency)
        if semaphore is None:
            return True, None
        acquired = semaphore.acquire(blocking=False)
        return acquired, semaphore if acquired else None

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._hits_since_prune = 0

    def _prune_locked(self, now: float) -> None:
        self._hits_since_prune += 1
        if self._hits_since_prune < 128:
            return
        self._hits_since_prune = 0
        idle_cutoff = now - 600.0
        for key, bucket in list(self._buckets.items()):
            if bucket.last_seen < idle_cutoff:
                self._buckets.pop(key, None)


def actor_key_for_request(request: Request) -> str:
    if not settings.trust_forwarded_headers:
        return "untrusted-edge"
    email = request.headers.get("X-Forwarded-Email") or request.headers.get("X-Forwarded-User")
    if email and len(email) <= 254:
        return email.strip().lower()
    return "anonymous"


class BackpressureMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that returns 429 for caller overload."""

    def __init__(self, app: ASGIApp, controller: BackpressureController | None = None) -> None:
        super().__init__(app)
        self._controller = controller or BackpressureController()

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # type: ignore[override]
        if not settings.mip_backpressure_enabled:
            return await call_next(request)
        # Unit-test suites create many TestClient instances against the
        # same process. Keep normal tests isolated while allowing explicit
        # middleware tests to opt in with this header.
        if _running_under_pytest() and request.headers.get("X-Enable-Backpressure-Test") != "1":
            return await call_next(request)

        budget = self._controller.classify(request.method, request.url.path)
        if budget is None:
            return await call_next(request)

        actor = actor_key_for_request(request)
        retry_after = self._controller.check_rate(actor, budget)
        if retry_after is not None:
            return self._too_many_requests(
                budget,
                reason="rate_limited",
                retry_after=retry_after,
            )

        acquired, token = self._controller.acquire_dependency(budget.dependency)
        if not acquired:
            return self._too_many_requests(
                budget,
                reason="dependency_saturated",
                retry_after=2,
            )
        try:
            return await call_next(request)
        finally:
            if token is not None:
                token.release()

    @staticmethod
    def _too_many_requests(
        budget: RouteBudget,
        *,
        reason: str,
        retry_after: int,
    ) -> JSONResponse:
        emit(
            log,
            "backpressure_rejected",
            level=logging.WARNING,
            scope=budget.scope,
            dependency=budget.dependency,
            reason=reason,
            retry_after_s=retry_after,
            correlation_id=get_correlation_id(),
        )
        body = {
            "detail": "Request budget exceeded; retry after the indicated delay.",
            "retryable": True,
            "dependency": budget.dependency or "app",
            "reason": reason,
            "scope": budget.scope,
            "retry_after_seconds": retry_after,
            "correlation_id": get_correlation_id(),
        }
        return JSONResponse(
            status_code=429,
            content=body,
            headers={
                "Retry-After": str(retry_after),
                "X-Correlation-ID": get_correlation_id(),
            },
        )


__all__ = [
    "BackpressureController",
    "BackpressureMiddleware",
    "RouteBudget",
    "actor_key_for_request",
]
