"""Short-lived, per-browser degraded-banner proof cookie.

This does not mock borrower data, bypass a dependency, or mutate global app
health. An admin-gated endpoint can issue a bounded cookie so that one
authenticated browser session sees an explicitly labelled forced-degraded
health payload. The scope is intentionally browser-local: it is reliable across
app replicas and cannot make other users see a false outage.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from backend.config.runtime_secret_policy import (
    require_distinct_rotation_secrets,
    require_strong_runtime_secret,
    runtime_secret_text,
)
from backend.config.settings import settings

_ALLOWED_DEPENDENCIES = {"warehouse", "lakebase", "genie", "all"}
FORCED_DEGRADED_COOKIE_NAME = "mip_force_degraded"
FORCED_DEGRADED_COOKIE_PATH = "/api"
FORCED_DEGRADED_SOURCE = "admin_drill_cookie"
_DEFAULT_TTL_S = 60
_MAX_TTL_S = 300
_PROCESS_FORCED_DEGRADED_SECRET = secrets.token_urlsafe(32).encode("utf-8")
_LOCAL_TEST_APP_ENVS = frozenset({"local", "test"})


@dataclass(frozen=True)
class ForcedDegradedSnapshot:
    active: bool
    dependency: str = "warehouse"
    expires_in_s: int = 0
    source: str = FORCED_DEGRADED_SOURCE


def _normalize_dependency(dependency: str) -> str:
    normalized = dependency.strip().lower() or "warehouse"
    if normalized not in _ALLOWED_DEPENDENCIES:
        raise ValueError(f"Unsupported degraded dependency: {dependency!r}")
    return normalized


def _clamp_ttl(ttl_s: int) -> int:
    return max(1, min(int(ttl_s), _MAX_TTL_S))


def _b64_encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> dict[str, Any]:
    padded = value + ("=" * (-len(value) % 4))
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("forced-degraded cookie payload must be an object")
    return payload


def _configured_secret_bytes(
    configured: Any,
    *,
    name: str,
    require_strong: bool,
) -> bytes | None:
    value = runtime_secret_text(configured)
    if value is None:
        return None
    if require_strong:
        value = require_strong_runtime_secret(value, name=name)
    return value.encode("utf-8")


def _current_cookie_key() -> tuple[str, bytes]:
    app_env = (settings.app_env or "").strip().lower()
    require_strong = app_env not in _LOCAL_TEST_APP_ENVS
    configured = settings.mip_genie_action_secret_current or settings.mip_genie_action_secret
    try:
        secret = _configured_secret_bytes(
            configured,
            name="MIP_GENIE_ACTION_SECRET_CURRENT",
            require_strong=require_strong,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    key_id = (settings.mip_genie_action_secret_kid or "").strip() or "v1"
    if secret is not None:
        return key_id, secret
    if require_strong:
        raise RuntimeError(
            "MIP_GENIE_ACTION_SECRET_CURRENT is required outside local/test app environments"
        )
    return "process", _PROCESS_FORCED_DEGRADED_SECRET


def _previous_cookie_key() -> tuple[str, bytes] | None:
    app_env = (settings.app_env or "").strip().lower()
    try:
        secret = _configured_secret_bytes(
            settings.mip_genie_action_secret_previous,
            name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
            require_strong=app_env not in _LOCAL_TEST_APP_ENVS,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if secret is None:
        return None
    current = _current_cookie_key()[1].decode("utf-8")
    try:
        require_distinct_rotation_secrets(
            current,
            secret.decode("utf-8"),
            current_name="MIP_GENIE_ACTION_SECRET_CURRENT",
            previous_name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    key_id = (settings.mip_genie_action_secret_previous_kid or "").strip() or "previous"
    return key_id, secret


def _cookie_keys(*, kid_hint: str | None = None) -> list[tuple[str, bytes]]:
    keys = [_current_cookie_key()]
    previous = _previous_cookie_key()
    if previous is not None:
        keys.append(previous)
    if kid_hint:
        matching = [item for item in keys if item[0] == kid_hint]
        nonmatching = [item for item in keys if item[0] != kid_hint]
        return matching + nonmatching
    return keys


def _sign_claims(claims: dict[str, Any]) -> str:
    body = _b64_encode(claims)
    _kid, secret = _current_cookie_key()
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode('ascii').rstrip('=')}"


def _decode_signed_claims(value: str) -> dict[str, Any]:
    body, supplied_sig = value.split(".", 1)
    claims = _b64_decode(body)
    supplied = base64.urlsafe_b64decode(
        (supplied_sig + ("=" * (-len(supplied_sig) % 4))).encode("ascii")
    )
    kid_hint = str(claims.get("kid") or "") or None
    for _kid, secret in _cookie_keys(kid_hint=kid_hint):
        expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
        if hmac.compare_digest(supplied, expected):
            return claims
    raise ValueError("forced-degraded cookie signature mismatch")


def build_forced_degraded_cookie(
    dependency: str = "warehouse",
    ttl_s: int = _DEFAULT_TTL_S,
) -> tuple[str, ForcedDegradedSnapshot]:
    """Return a browser-local proof cookie value and its public snapshot."""

    normalized = _normalize_dependency(dependency)
    ttl = _clamp_ttl(ttl_s)
    expires_at = int(time.time()) + ttl
    kid, _secret = _current_cookie_key()
    value = _sign_claims(
        {
            "v": 1,
            "dependency": normalized,
            "expires_at": expires_at,
            "kid": kid,
            "source": FORCED_DEGRADED_SOURCE,
        }
    )
    return value, ForcedDegradedSnapshot(
        active=True,
        dependency=normalized,
        expires_in_s=ttl,
    )


def forced_degraded_snapshot_from_cookie(
    cookie_value: str | None,
) -> ForcedDegradedSnapshot:
    """Parse a browser-local proof cookie into a health payload snapshot."""

    if not cookie_value:
        return ForcedDegradedSnapshot(active=False)
    try:
        payload = _decode_signed_claims(cookie_value)
        version = int(payload.get("v") or 0)
        dependency = _normalize_dependency(str(payload.get("dependency") or "warehouse"))
        source = str(payload.get("source") or "")
        expires_at = int(payload.get("expires_at") or 0)
    except Exception:
        return ForcedDegradedSnapshot(active=False)
    if version != 1:
        return ForcedDegradedSnapshot(active=False)
    if source != FORCED_DEGRADED_SOURCE:
        return ForcedDegradedSnapshot(active=False)
    expires_in_s = expires_at - int(time.time())
    if expires_in_s <= 0:
        return ForcedDegradedSnapshot(active=False)
    return ForcedDegradedSnapshot(
        active=True,
        dependency=dependency,
        expires_in_s=min(expires_in_s, _MAX_TTL_S),
    )


def apply_forced_degraded(
    status: str,
    deps: dict[str, str],
    snapshot: ForcedDegradedSnapshot,
) -> tuple[str, dict[str, str]]:
    """Overlay a labelled forced-degraded state onto a live health result."""

    if not snapshot.active:
        return status, deps
    next_deps = dict(deps)
    if snapshot.dependency == "all":
        for dep in ("warehouse", "lakebase", "genie"):
            next_deps[dep] = "down"
    else:
        next_deps[snapshot.dependency] = "down"
    return "degraded", next_deps


def forced_degraded_payload(snapshot: ForcedDegradedSnapshot) -> dict[str, object] | None:
    """Return a public, explicit label for health payloads."""

    if not snapshot.active:
        return None
    return {
        "active": True,
        "dependency": snapshot.dependency,
        "source": snapshot.source,
        "expires_in_s": snapshot.expires_in_s,
    }


def _reset_forced_degraded_for_tests() -> None:
    """Compatibility no-op: forced degraded state now lives in cookies."""
