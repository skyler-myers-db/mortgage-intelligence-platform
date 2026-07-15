"""Fail-closed admin and approver authorization.

Deployed automation is authorized by exact server-configured identities matched
against the actor resolved from ``X-Forwarded-Email`` / ``X-Forwarded-User``.
Email allowlists are additive for governed human access. Because the Databricks
Apps identity contract does not document ``X-Forwarded-Groups``, group matching
is compatibility-only and is never authoritative outside local/test execution.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from backend.config.settings import settings
from backend.services.audit_store import resolve_actor
from backend.services.observability import emit

log = logging.getLogger(__name__)


# Hard-coded fallback in addition to the configured group name. Keeping
# it as a constant rather than a second setting means operators can't
# accidentally strip admin access by zeroing the env var; they'd have
# to edit code and get it through review.
_FALLBACK_ADMIN_GROUP: str = "admins"


def _parse_groups(raw: str | None) -> set[str]:
    """Split an optional local/test compatibility group header."""
    if not raw:
        return set()
    return {tok.strip().lower() for tok in raw.split(",") if tok.strip()}


def _parse_admin_emails(raw: str | None) -> set[str]:
    """Parse ``settings.admin_emails`` (comma-separated env var) into a
    set of lower-cased email addresses."""
    if not raw:
        return set()
    return {tok.strip().lower() for tok in raw.split(",") if tok.strip()}


def _admin_access(request: Request) -> tuple[bool, str, set[str]]:
    """Resolve the single admin-access decision used by UI and enforcement."""
    actor = resolve_actor(request)
    compatibility_groups_enabled = (
        settings.trust_forwarded_headers and settings.app_env in {"local", "test"}
    )
    if compatibility_groups_enabled:
        groups = _parse_groups(request.headers.get("X-Forwarded-Groups"))
        allowed_groups = {settings.admin_group_name.lower(), _FALLBACK_ADMIN_GROUP}
        if groups & allowed_groups:
            return True, actor, groups
    else:
        # Trust disabled: pretend the header doesn't exist for log
        # forensics below.
        groups = set()

    admin_identities = _parse_admin_emails(getattr(settings, "admin_emails", None)) | (
        _parse_admin_emails(getattr(settings, "admin_identities", None))
    )
    return bool(actor and actor.lower() in admin_identities), actor, groups


def can_access_admin(request: Request) -> bool:
    """Return the same fail-closed decision enforced by ``require_admin``."""
    allowed, _actor, _groups = _admin_access(request)
    return allowed


def _approver_access(request: Request) -> tuple[bool, str, set[str]]:
    """Resolve the fail-closed approver decision shared by UI and enforcement."""

    is_admin, actor, groups = _admin_access(request)
    if is_admin:
        return True, actor, groups
    if settings.trust_forwarded_headers:
        approver_group = settings.approver_group_name.strip().lower()
        if approver_group and approver_group in groups:
            return True, actor, groups
    approver_identities = _parse_admin_emails(getattr(settings, "approver_emails", None)) | (
        _parse_admin_emails(getattr(settings, "approver_identities", None))
    )
    return bool(actor and actor.lower() in approver_identities), actor, groups


def can_access_approver(request: Request) -> bool:
    """Return the same fail-closed decision enforced by ``require_approver``."""

    allowed, _actor, _groups = _approver_access(request)
    return allowed


def require_admin(request: Request) -> str:
    """Admit exact configured identities/emails; otherwise return the shared 403."""
    allowed, actor, groups = _admin_access(request)
    if allowed:
        return actor

    # Deny: log at INFO for forensic traceability; never log the raw
    # group header value, which can contain PII / group-name attack surface.
    emit(
        log,
        "admin_access_denied",
        outcome="denied",
        actor_present=bool(actor),
        groups_present=len(groups),
        admin_group=settings.admin_group_name,
    )
    raise HTTPException(status_code=403, detail="forbidden")


# Type alias routers pin on so the dependency signature stays uniform
# across ``backend/api/admin.py``. ``AdminDep`` evaluates to ``str``
# (the admitted actor email).
AdminDep = Annotated[str, Depends(require_admin)]


def require_approver(request: Request) -> str:
    """Fail-closed approver gate for human-decision endpoints.

    Admission is limited to exact configured identities/emails or the admin
    rule. Group matching is a local/test-only compatibility path.
    """
    allowed, actor, groups = _approver_access(request)
    if allowed:
        return actor
    emit(
        log,
        "approver_access_denied",
        outcome="denied",
        actor_present=bool(actor),
        groups_present=len(groups),
        approver_group=settings.approver_group_name,
    )
    raise HTTPException(status_code=403, detail="forbidden")


# ``ApproverDep`` evaluates to ``str`` (the admitted decision-maker email).
ApproverDep = Annotated[str, Depends(require_approver)]
