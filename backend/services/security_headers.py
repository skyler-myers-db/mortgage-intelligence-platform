"""Browser security-headers and cache-policy middleware.

Extracted from ``backend/main.py`` (2026-09-21) so the entrypoint stays
under the 900-line file-size gate. ``backend.main`` re-imports
``SecurityHeadersMiddleware`` so existing ``from backend.main import ...``
call sites keep working.

Cache policy owned here (2026-09-21 UI/UX audit, bundle-01 + delivery-10):

* ``/assets/*`` served OK (200/206/304) -> one-year ``immutable``.
* ``/assets/*`` any other status        -> ``no-store`` (forced).
* ``/api/*`` without its own directive  -> ``private, no-store``.
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from backend.version import API_VERSION

_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_API_CACHE_CONTROL = "private, no-store"
# Only a served body (200), a served range (206) or a revalidation hit (304)
# of a content-hashed asset is safe to pin for a year.
_CACHEABLE_ASSET_STATUSES = frozenset({200, 206, 304})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach browser security headers to every app response.

    The Databricks Apps edge owns authentication and may add its own
    platform headers after this middleware runs. These headers cover the
    application-controlled browser posture: no content sniffing, no
    framing, conservative referrer behavior, no ambient device APIs, and
    a CSP tuned for the static Vite SPA served from this same origin.
    """

    _CSP = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'"
    )

    async def dispatch(
        self, request: StarletteRequest, call_next: Any
    ) -> StarletteResponse:
        response = await call_next(request)
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), camera=(), microphone=()",
        )
        response.headers.setdefault("Content-Security-Policy", self._CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("X-API-Version", API_VERSION)
            # delivery-10: borrower, lead and workspace JSON must never sit in
            # a browser or intermediary cache on a shared machine. setdefault
            # so a route that owns its caching story keeps its own directive.
            response.headers.setdefault("Cache-Control", _API_CACHE_CONTROL)
        if request.url.path.startswith("/assets/"):
            if response.status_code in _CACHEABLE_ASSET_STATUSES:
                response.headers.setdefault("Cache-Control", _ASSET_CACHE_CONTROL)
            else:
                # bundle-01: a hashed asset that is missing right now (tab
                # left open across a deploy) may exist again after a
                # rollback. Stamping the 404 immutable pinned the failure in
                # the browser cache for a year, so error responses are
                # never cacheable -- assigned, not setdefault.
                response.headers["Cache-Control"] = "no-store"
        return response
