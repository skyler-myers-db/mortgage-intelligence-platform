"""The one forwarded-identity resolver and the browser actor-cache key.

Six readers used to read the Databricks Apps edge headers each on their own
(health, session, audit ``resolve_actor``, visit tracking, the authenticated
RBAC gate and the backpressure bucket), with the same truthy
``X-Forwarded-Email``-then-``X-Forwarded-User`` rule copied six times
(D-identity-review-a1). They now all call :func:`forwarded_identity`, so the
rule cannot drift between the audit ledger and the browser's actor boundary.
Each caller keeps its own fallback (``default_actor``, the untrusted-edge
marker, a 401, ``'anonymous'``); only the header read is shared.

:func:`actor_cache_key` is the opaque per-actor discriminator the browser
keys its actor-scoped storage on (``/api/health`` and ``/api/session``). It is
byte-identical to the derivation health.py used before the move, including
the secret order, so a deploy of this module is an actor change to no open
tab. There is exactly one per-process fallback secret, so health and session
can never derive different keys when no secret is configured.

Imports stay minimal on purpose (settings, the stdlib and Starlette's
connection type): the module never imports ``backend.api`` or
``backend.main``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from starlette.requests import HTTPConnection

from backend.config.settings import settings

_PROCESS_ACTOR_CACHE_SECRET = secrets.token_urlsafe(32)


def forwarded_identity(conn: HTTPConnection) -> str | None:
    """Return the caller's edge-forwarded identity, RAW, or None.

    None unless forwarded headers are trusted (otherwise they are
    attacker-writable). Then ``X-Forwarded-Email`` when non-empty, else
    ``X-Forwarded-User`` when non-empty, else None. No ``strip()`` and no
    ``lower()``: callers that need a normalised form (the cache key, the
    backpressure bucket) normalise it themselves. Never logged.
    """
    if not settings.trust_forwarded_headers:
        return None
    return conn.headers.get("X-Forwarded-Email") or conn.headers.get("X-Forwarded-User") or None


def actor_cache_key(identity: str) -> str:
    """Return a non-reversible actor/session discriminator for browser caches."""

    configured_secret = settings.mip_genie_action_secret_current or settings.mip_genie_action_secret
    secret = configured_secret.get_secret_value().strip() if configured_secret else ""
    if not secret:
        secret = _PROCESS_ACTOR_CACHE_SECRET
    digest = hmac.new(
        secret.encode("utf-8"),
        identity.strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]
    return f"actor_{digest}"
