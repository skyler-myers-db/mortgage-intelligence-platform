"""Shared schema validators with no service-layer dependencies."""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence

_DEFAULT_PUBLIC_LENDER_NAME = "Summit Mortgage"
_PUBLIC_COMPETITOR_REF_RE = re.compile(r"^Competitor ([A-Z]|Other)$")
_PublicLenderNameProvider = Callable[[], str]
_StateFootprintProvider = Callable[[], tuple[Sequence[tuple[str, str]], bool]]
_public_lender_name_provider: _PublicLenderNameProvider | None = None
_state_footprint_provider: _StateFootprintProvider | None = None

_PROTECTED_CLASS_MARKETING_RE = re.compile(
    r"\b(?:age|aged|asian|black|color|disab(?:ility|led)|elderly|ethnic(?:ity)?|"
    r"familial status|families? with children|family status|female|gender|handicap(?:ped)?|"
    r"hispanic|latino|male|marital status|military status|national origin|native american|"
    r"pacific islander|pregnan(?:cy|t)|public assistance|race|racial|religion|religious|"
    r"senior citizens?|sex|sexual orientation|source of income|veteran|white|woman|women)\b",
    re.IGNORECASE,
)

_CONTEXTUAL_HUMAN_NAME_RE = re.compile(
    r"\b(?:call|contact|email|message|text|ask|target|prioritize|dear|hello|hi)\s+"
    r"(?!(?:to|the|a|an|this|that|your|our)\b)[A-Za-z]{2,30}\s+[A-Za-z]{2,30}\b|"
    r"\b[A-Za-z]{2,30}\s+[A-Za-z]{2,30}\s+(?:qualifies?|is the top borrower)\b",
    re.IGNORECASE,
)


def set_public_lender_name_provider(provider: _PublicLenderNameProvider | None) -> None:
    """Register the configured tenant lender without importing runtime settings."""

    global _public_lender_name_provider
    _public_lender_name_provider = provider


def _configured_public_lender_name() -> str:
    if _public_lender_name_provider is None:
        return _DEFAULT_PUBLIC_LENDER_NAME
    try:
        configured = _public_lender_name_provider().strip()
    except Exception:
        return _DEFAULT_PUBLIC_LENDER_NAME
    return configured or _DEFAULT_PUBLIC_LENDER_NAME


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


def assert_no_protected_class_marketing_text(value: str, *, field_name: str) -> str:
    """Reject protected-class language from targeting or outreach copy.

    This is intentionally narrower than a general prose validator. It is used
    only at campaign/outreach decision boundaries, where protected-class
    language must fail closed instead of being silently scrubbed or persisted.
    """

    if _PROTECTED_CLASS_MARKETING_RE.search(value):
        raise ValueError(f"{field_name} cannot contain protected-class targeting language")
    return value


def contains_contextual_human_name(value: str) -> bool:
    """Detect name-shaped text in contexts where a person is being addressed.

    General lowercase two-word prose is intentionally not classified as a
    name. The governed free-text boundaries use this alongside mechanical PII
    checks and title-case detection to catch case-normalized names such as
    ``call john smith`` without treating ordinary sentences as identities.
    """

    return bool(_CONTEXTUAL_HUMAN_NAME_RE.search(str(value)))
