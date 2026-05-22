"""Short-lived, per-browser degraded-banner proof cookie.

This does not mock borrower data, bypass a dependency, or mutate global app
health. An admin-gated endpoint can issue a bounded cookie so that one
authenticated browser session sees an explicitly labelled forced-degraded
health payload. The scope is intentionally browser-local: it is reliable across
app replicas and cannot make other users see a false outage.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

_ALLOWED_DEPENDENCIES = {"warehouse", "lakebase", "genie", "all"}
FORCED_DEGRADED_COOKIE_NAME = "mip_force_degraded"
FORCED_DEGRADED_COOKIE_PATH = "/api"
FORCED_DEGRADED_SOURCE = "admin_drill_cookie"
_DEFAULT_TTL_S = 60
_MAX_TTL_S = 300


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


def build_forced_degraded_cookie(
    dependency: str = "warehouse",
    ttl_s: int = _DEFAULT_TTL_S,
) -> tuple[str, ForcedDegradedSnapshot]:
    """Return a browser-local proof cookie value and its public snapshot."""

    normalized = _normalize_dependency(dependency)
    ttl = _clamp_ttl(ttl_s)
    expires_at = int(time.time()) + ttl
    value = _b64_encode(
        {
            "dependency": normalized,
            "expires_at": expires_at,
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
        payload = _b64_decode(cookie_value)
        dependency = _normalize_dependency(str(payload.get("dependency") or "warehouse"))
        source = str(payload.get("source") or "")
        expires_at = int(payload.get("expires_at") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
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
