"""Browser security-headers middleware.

Extracted verbatim from ``backend/main.py`` (2026-09-21) so the entrypoint
stays under the 900-line file-size gate. ``backend.main`` re-imports
``SecurityHeadersMiddleware`` so existing ``from backend.main import ...``
call sites keep working.
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from backend.version import API_VERSION


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
        if request.url.path.startswith("/assets/"):
            response.headers.setdefault(
                "Cache-Control",
                "public, max-age=31536000, immutable",
            )
        return response
