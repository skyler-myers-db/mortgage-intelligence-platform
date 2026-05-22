"""Short-lived operator switch for deployed degraded-banner proof.

This does not mock borrower data or bypass a dependency. It only lets an
admin force the health endpoint to report a degraded dependency for a bounded
TTL so the deployed UI can prove the banner path without stopping shared
infrastructure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

_ALLOWED_DEPENDENCIES = {"warehouse", "lakebase", "genie", "all"}
_DEFAULT_TTL_S = 60
_MAX_TTL_S = 300


@dataclass(frozen=True)
class ForcedDegradedSnapshot:
    active: bool
    dependency: str = "warehouse"
    expires_in_s: int = 0


_lock = Lock()
_dependency = "warehouse"
_expires_at = 0.0


def set_forced_degraded(
    *,
    active: bool,
    dependency: str = "warehouse",
    ttl_s: int = _DEFAULT_TTL_S,
) -> ForcedDegradedSnapshot:
    """Enable or disable the forced-degraded health overlay."""

    global _dependency, _expires_at
    now = time.monotonic()
    if not active:
        with _lock:
            _dependency = "warehouse"
            _expires_at = 0.0
        return ForcedDegradedSnapshot(active=False)

    normalized = dependency.strip().lower() or "warehouse"
    if normalized not in _ALLOWED_DEPENDENCIES:
        raise ValueError(f"Unsupported degraded dependency: {dependency!r}")
    ttl = max(1, min(int(ttl_s), _MAX_TTL_S))
    with _lock:
        _dependency = normalized
        _expires_at = now + ttl
    return ForcedDegradedSnapshot(active=True, dependency=normalized, expires_in_s=ttl)


def forced_degraded_snapshot() -> ForcedDegradedSnapshot:
    """Return the active forced-degraded overlay, expiring stale state."""

    global _dependency, _expires_at
    now = time.monotonic()
    with _lock:
        if _expires_at <= now:
            _dependency = "warehouse"
            _expires_at = 0.0
            return ForcedDegradedSnapshot(active=False)
        return ForcedDegradedSnapshot(
            active=True,
            dependency=_dependency,
            expires_in_s=max(1, int(_expires_at - now)),
        )


def apply_forced_degraded(status: str, deps: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Overlay the forced degraded state onto a live health probe result."""

    snapshot = forced_degraded_snapshot()
    if not snapshot.active:
        return status, deps
    next_deps = dict(deps)
    if snapshot.dependency == "all":
        for dep in ("warehouse", "lakebase", "genie"):
            next_deps[dep] = "down"
    else:
        next_deps[snapshot.dependency] = "down"
    return "degraded", next_deps


def _reset_forced_degraded_for_tests() -> None:
    set_forced_degraded(active=False)
