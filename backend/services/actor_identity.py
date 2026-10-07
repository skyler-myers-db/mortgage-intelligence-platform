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

:func:`actor_cache_key_aliases` names the same actor's key under the
previous rotation secret (D-identity-review-a4): ``/api/health`` carries it so
an open tab re-stamps instead of resetting, and the request binding accepts
it, for the length of a rotation's grace window.

Imports stay minimal on purpose (settings, the runtime-secret text rule, the
stdlib and Starlette's connection type): the module never imports
``backend.api`` or ``backend.main``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from starlette.requests import HTTPConnection

from backend.config.runtime_secret_policy import runtime_secret_text
from backend.config.settings import settings

_PROCESS_ACTOR_CACHE_SECRET = secrets.token_urlsafe(32)

# A retired previous secret (tools/databricks/provision_runtime_secrets.py
# DISABLED_PREVIOUS_PREFIX, line 67) is a random value under this prefix: it
# names no real rotation, so it derives no alias. The two literals are pinned
# equal by tests/unit/test_actor_binding.py.
_DISABLED_PREVIOUS_PREFIX = "disabled."


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


def _actor_cache_secret() -> str:
    """The secret actor_cache_key derives with: current, then legacy, then the process one."""

    configured_secret = settings.mip_genie_action_secret_current or settings.mip_genie_action_secret
    secret = configured_secret.get_secret_value().strip() if configured_secret else ""
    return secret or _PROCESS_ACTOR_CACHE_SECRET


def _derive(secret: str, identity: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        identity.strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]
    return f"actor_{digest}"


def actor_cache_key(identity: str) -> str:
    """Return a non-reversible actor/session discriminator for browser caches."""

    return _derive(_actor_cache_secret(), identity)


def actor_cache_key_aliases(identity: str) -> list[str]:
    """The same actor's key under the previous rotation secret, if one is live.

    During a ``MIP_GENIE_ACTION_SECRET_PREVIOUS`` grace window an open tab
    still holds the key derived from the previous secret; the browser's gate
    re-stamps an alias instead of reading it as another actor, and the
    request binding (backend/services/actor_binding.py) accepts it. Empty when
    no previous secret is configured, when it is a placeholder, a retired
    ``disabled.`` value, or the current secret itself.
    """

    previous = runtime_secret_text(settings.mip_genie_action_secret_previous)
    if previous is None or previous.startswith(_DISABLED_PREVIOUS_PREFIX):
        return []
    if previous == _actor_cache_secret():
        return []
    return [_derive(previous, identity)]
