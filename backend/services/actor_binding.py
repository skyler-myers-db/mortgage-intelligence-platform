"""Per-request actor binding (D-identity-review-a4, audit genie-02 / bundle-04).

The browser's actor gate (frontend/src/lib/actorScope.ts) resets a document
whose signed-in user changed, but only after a health probe proves the
change. Between the cookie swap and that probe a request from the old
document would run under the NEW actor's identity: an approval or a read
recorded for someone who never saw the page. So every app request from an
opened document carries the document owner's opaque cache key in
``X-MIP-Actor-Key`` (frontend/src/lib/apiActorBinding.ts), and this
middleware refuses one that no longer names the forwarded identity, before
routing, a dependency, a body read, the visit ledger or a backpressure slot.

The header can only refuse, never grant: a request without it passes through
unchanged (boot primes, the raw footprint / banner / session probes, server
clients, CI, the live smoke, load tests), and authorization stays with the
routers. Health, session and telemetry are exempt: they are how a tab learns
who it is now, and the RUM beacon carries no actor.

Pure ASGI on purpose (the ServerTiming shape): a refusal never enters the
inner app, and ``receive`` / ``send`` pass through untouched otherwise.
Registered just inside ServerTiming, so a 409 still carries
X-Correlation-ID, the security headers and Server-Timing.

The refusal logs ONE ``actor_binding_mismatch`` WARNING with the method and
the correlation id only: never the path (masked borrower ids, conversation
ids), the header value, the identity or a key.
"""

from __future__ import annotations

import hmac
import json
import logging

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.services.actor_identity import (
    actor_cache_key,
    actor_cache_key_aliases,
    forwarded_identity,
)
from backend.services.observability import emit

log = logging.getLogger("mip.actor_binding")

ACTOR_KEY_HEADER = b"x-mip-actor-key"
REFUSAL_REASON = "actor_changed"
REFUSAL_DETAIL = "The signed-in user changed in this browser. Nothing was recorded."
_REFUSAL_BODY = json.dumps({"detail": REFUSAL_DETAIL, "reason": REFUSAL_REASON}).encode("utf-8")

# A closed literal set, compared after stripping ONE trailing '/'.
EXEMPT_PATHS = frozenset({"/api/health", "/api/v1/health", "/api/session", "/api/v1/session"})
EXEMPT_PREFIXES = ("/api/telemetry/", "/api/v1/telemetry/")


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def is_exempt(path: str) -> bool:
    """Health, session and telemetry: how a tab learns who it is now."""

    trimmed = path[:-1] if path.endswith("/") else path
    return trimmed in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def _header_values(scope: Scope) -> list[bytes]:
    return [value for name, value in scope.get("headers", []) if name.lower() == ACTOR_KEY_HEADER]


def _matches(presented: bytes, identity: str) -> bool:
    try:
        candidate = presented.decode("ascii").strip().encode("ascii")
    except UnicodeDecodeError:
        return False  # A non-ASCII value names no key.
    accepted = [actor_cache_key(identity), *actor_cache_key_aliases(identity)]
    # Every candidate is compared, so the time spent does not say which matched.
    results = [hmac.compare_digest(candidate, key.encode("ascii")) for key in accepted]
    return any(results)


def bound_request_refused(scope: Scope) -> bool:
    """True when a bound request no longer names the forwarded identity."""

    values = _header_values(scope)
    if not values:
        return False
    if len(values) > 1:
        return True
    identity = forwarded_identity(HTTPConnection(scope))
    if identity is None:
        return True
    return not _matches(values[0], identity)


class ActorBindingMiddleware:
    """Refuse an ``/api`` request whose X-MIP-Actor-Key names another actor."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if not _is_api_path(path) or is_exempt(path) or not bound_request_refused(scope):
            await self.app(scope, receive, send)
            return
        emit(
            log,
            "actor_binding_mismatch",
            level=logging.WARNING,
            method=str(scope.get("method", "")),
        )
        await send(
            {
                "type": "http.response.start",
                "status": 409,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_REFUSAL_BODY)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _REFUSAL_BODY})


__all__ = [
    "ACTOR_KEY_HEADER",
    "EXEMPT_PATHS",
    "EXEMPT_PREFIXES",
    "REFUSAL_DETAIL",
    "REFUSAL_REASON",
    "ActorBindingMiddleware",
    "bound_request_refused",
    "is_exempt",
]
