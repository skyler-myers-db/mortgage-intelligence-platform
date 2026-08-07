"""Tenant lender vocabulary and reviewed geography, resolved via providers.

Runtime settings and services register providers here so schema validators can
resolve the configured lender name and state footprint with no schema-layer
import of the runtime service or config layers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from backend.schemas.lender_identity import validate_public_lender_name

_DEFAULT_PUBLIC_LENDER_NAME = "Summit Mortgage"
_PUBLIC_COMPETITOR_REF_RE = re.compile(r"^Competitor ([A-Z]|Other)$")
_PublicLenderNameProvider = Callable[[], str]
_StateFootprintProvider = Callable[[], tuple[Sequence[tuple[str, str]], bool]]
_public_lender_name_provider: _PublicLenderNameProvider | None = None
_state_footprint_provider: _StateFootprintProvider | None = None


def set_public_lender_name_provider(provider: _PublicLenderNameProvider | None) -> None:
    """Register the configured tenant lender without importing runtime settings."""

    global _public_lender_name_provider
    _public_lender_name_provider = provider


def _configured_public_lender_name() -> str:
    if _public_lender_name_provider is None:
        return validate_public_lender_name(_DEFAULT_PUBLIC_LENDER_NAME)
    try:
        configured = _public_lender_name_provider()
    except Exception:
        configured = _DEFAULT_PUBLIC_LENDER_NAME
    return validate_public_lender_name(configured or _DEFAULT_PUBLIC_LENDER_NAME)


def configured_public_lender_name() -> str:
    """Return the public display name for the configured tenant lender."""

    return _configured_public_lender_name()


def set_state_footprint_provider(provider: _StateFootprintProvider | None) -> None:
    """Register the runtime geography resolver without importing services."""

    global _state_footprint_provider
    _state_footprint_provider = provider


def _state_footprint_snapshot() -> tuple[Sequence[tuple[str, str]], bool]:
    if _state_footprint_provider is None:
        return (), True
    return _state_footprint_provider()


def reviewed_geography_labels() -> set[str]:
    """Return lowercased Portfolio Builder geography labels."""

    states, using_fallback = _state_footprint_snapshot()
    if using_fallback:
        return {"all"}
    labels = {"all", *(name.lower() for _code, name in states)}
    labels.add(f"all {len(states)} states")
    return labels


def reviewed_state_codes() -> set[str]:
    """Return currently reviewed two-letter state codes, or empty on fallback."""

    states, using_fallback = _state_footprint_snapshot()
    if using_fallback:
        return set()
    return {code for code, _name in states}


def is_public_lender_ref(value: str | None, *, allow_all: bool = False) -> bool:
    """Return TRUE when ``value`` is from the public-safe lender vocabulary."""

    if value is None:
        return False
    stripped = value.strip()
    if allow_all and stripped == "All":
        return True
    if stripped == _configured_public_lender_name():
        return True
    return bool(_PUBLIC_COMPETITOR_REF_RE.fullmatch(stripped))


def normalize_public_lender_ref(
    value: str | None,
    *,
    allow_all: bool = False,
) -> str | None:
    """Validate a caller-provided lender filter without generalizing raw input."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if is_public_lender_ref(stripped, allow_all=allow_all):
        return stripped
    raise ValueError("target_lender_ref must be a public-safe lender alias")
