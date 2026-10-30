"""Validation for the tenant lender name exposed on public product surfaces."""

from __future__ import annotations

import re

_PUBLIC_LENDER_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9&'. -]{1,79}")
_REVIEWED_ORGANIZATION_SUFFIX_RE = re.compile(
    r"(?:bank|capital|credit union|finance|financial|funding|home loans|"
    r"lender|lending|mortgage)$",
    re.IGNORECASE,
)
_PUBLIC_LENDER_NMLS_ID_RE = re.compile(r"[1-9]\d{3,11}")
_PUBLIC_TENANT_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SUMMIT_LENDER_NAME = "Summit Mortgage"
_SUMMIT_DEMO_NMLS_ID = "123456"
_SUMMIT_TENANT_ID = "summit"
# The reviewed identities are this source-controlled registry (the Summit
# Mortgage demo pair) plus at most one digest-pinned customer record. A
# customer identity is never a bare runtime string: it is the canonical JSON
# record in MIP_LENDER_IDENTITY_JSON, accepted only when its sha256 equals
# the reviewed MIP_LENDER_IDENTITY_SHA256 (lender_identity_record.py). Every
# caller resolves through reviewed_lender_identity_from_env() there; this
# function alone still accepts only the registry.
_REVIEWED_PUBLIC_LENDER_IDENTITIES: dict[str, frozenset[str]] = {
    _SUMMIT_LENDER_NAME: frozenset({_SUMMIT_DEMO_NMLS_ID}),
}
_SMS_DISCLOSURE_SUFFIX = (
    " NMLS #{nmls_id}. Equal Housing Lender. Reply STOP to opt out. "
    "Msg and data rates may apply."
)
_SMS_MINIMUM_REVIEWED_COPY = "Reply YES to review. "
_SMS_MAX_LENGTH = 160


def validate_public_lender_name(value: object) -> str:
    """Return a normalized, organization-shaped public lender name.

    The configured name is a reviewed tenant identity, not general marketing
    prose. Requiring a bounded legal-name shape prevents runtime configuration
    from becoming an arbitrary-text escape hatch at borrower-copy boundaries.
    """

    normalized = " ".join(str(value or "").split())
    words = normalized.split()
    if (
        not 2 <= len(words) <= 8
        or _PUBLIC_LENDER_NAME_RE.fullmatch(normalized) is None
        or _REVIEWED_ORGANIZATION_SUFFIX_RE.search(normalized) is None
    ):
        raise ValueError(
            "mip_lender_name must be a 2-8 word public lender organization name "
            "ending in a reviewed lender suffix"
        )
    return normalized


def validate_public_lender_nmls_id(value: object) -> str:
    """Return the configured lender's exact public NMLS identifier."""

    normalized = str(value or "").strip()
    if _PUBLIC_LENDER_NMLS_ID_RE.fullmatch(normalized) is None:
        raise ValueError("mip_lender_nmls_id must be a 4-12 digit nonzero NMLS identifier")
    return normalized


def resolve_public_lender_nmls_id(value: object, *, lender_name: object) -> str:
    """Resolve the exact NMLS id without leaking the demo default to customers."""

    lender = validate_public_lender_name(lender_name)
    configured = str(value or "").strip()
    if not configured:
        if lender == _SUMMIT_LENDER_NAME:
            return _SUMMIT_DEMO_NMLS_ID
        raise ValueError(
            "mip_lender_nmls_id is required when mip_lender_name is not Summit Mortgage"
        )
    return validate_public_lender_nmls_id(configured)


def validate_public_lender_identity(
    lender_name: object,
    lender_nmls_id: object,
) -> tuple[str, str]:
    """Validate the legal identity and the reviewed SMS delivery budget.

    The shortest reviewed SMS borrower copy still needs room beside the
    mandatory disclosure. Rejecting an identity that cannot fit prevents a
    migration from succeeding while every SMS draft fails at runtime.
    """

    lender = validate_public_lender_name(lender_name)
    nmls_id = resolve_public_lender_nmls_id(lender_nmls_id, lender_name=lender)
    if nmls_id not in _REVIEWED_PUBLIC_LENDER_IDENTITIES.get(lender, frozenset()):
        raise ValueError(
            "mip_lender_name and mip_lender_nmls_id must match an independently "
            "reviewed source-controlled lender identity"
        )
    require_sms_disclosure_budget(lender, nmls_id)
    return lender, nmls_id


def require_sms_disclosure_budget(lender: str, nmls_id: str) -> None:
    """Refuse an identity whose mandatory SMS disclosure leaves no reviewed copy room."""

    disclosure = lender + _SMS_DISCLOSURE_SUFFIX.format(nmls_id=nmls_id)
    if len(_SMS_MINIMUM_REVIEWED_COPY + disclosure) > _SMS_MAX_LENGTH:
        raise ValueError(
            "mip_lender_name and mip_lender_nmls_id exceed the reviewed 160-character "
            "SMS disclosure budget"
        )


def effective_public_tenant_id(value: object, *, lender_name: object) -> str:
    """Resolve the validated Lakebase disclosure namespace for one deployment."""

    normalized_lender = validate_public_lender_name(lender_name)
    configured = str(value or "").strip()
    if configured:
        if _PUBLIC_TENANT_ID_RE.fullmatch(configured) is None:
            raise ValueError(
                "mip_tenant_id must be a lowercase identifier of at most 64 characters"
            )
        if configured == _SUMMIT_TENANT_ID and normalized_lender != _SUMMIT_LENDER_NAME:
            raise ValueError(
                "mip_tenant_id summit is reserved for the Summit Mortgage demo identity"
            )
        return configured
    if normalized_lender == _SUMMIT_LENDER_NAME:
        return _SUMMIT_TENANT_ID
    slug = re.sub(r"[^a-z0-9]+", "_", normalized_lender.lower()).strip("_")
    if _PUBLIC_TENANT_ID_RE.fullmatch(slug) is None:
        raise ValueError("mip_lender_name cannot produce a valid disclosure tenant identifier")
    return slug
