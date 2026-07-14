"""Shared validation for deployment-scoped HMAC secrets.

These values protect persisted confirmation tokens, campaign provenance, and
masked Cotality identifiers.  A present-but-weak value is a configuration
error, not a usable fallback.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

MIN_RUNTIME_SECRET_BYTES = 32

DEFAULT_RUNTIME_SECRET_PLACEHOLDERS = frozenset(
    {
        "redacted",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "example",
        "your-secret",
        "your_secret",
        "mip-cotality-id-mask-v1",
    }
)


def runtime_secret_text(
    value: Any,
    *,
    extra_placeholders: Iterable[str] = (),
) -> str | None:
    """Return a non-placeholder secret string, or ``None`` when unset."""

    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    if not isinstance(raw, str):
        return None
    configured = raw.strip()
    normalized = configured.casefold()
    placeholders = DEFAULT_RUNTIME_SECRET_PLACEHOLDERS | {
        str(item).strip().casefold() for item in extra_placeholders
    }
    if (
        not configured
        or normalized in placeholders
        or normalized.startswith("<")
        or normalized.endswith(">")
    ):
        return None
    return configured


def require_strong_runtime_secret(
    value: Any,
    *,
    name: str,
    extra_placeholders: Iterable[str] = (),
) -> str:
    """Return a secret with at least 32 UTF-8 bytes or raise clearly."""

    configured = runtime_secret_text(value, extra_placeholders=extra_placeholders)
    if configured is None:
        raise ValueError(f"{name} is required and must not be a placeholder")
    if len(configured.encode("utf-8")) < MIN_RUNTIME_SECRET_BYTES:
        raise ValueError(
            f"{name} must contain at least {MIN_RUNTIME_SECRET_BYTES} UTF-8 bytes"
        )
    return configured


def require_distinct_rotation_secrets(
    current: str,
    previous: str | None,
    *,
    current_name: str,
    previous_name: str,
) -> None:
    """Reject a rotation pair that provides no independent verification key."""

    if previous is not None and previous == current:
        raise ValueError(f"{previous_name} must differ from {current_name}")


__all__ = [
    "DEFAULT_RUNTIME_SECRET_PLACEHOLDERS",
    "MIN_RUNTIME_SECRET_BYTES",
    "require_distinct_rotation_secrets",
    "require_strong_runtime_secret",
    "runtime_secret_text",
]
