"""Role-based access control for the admin surface.

Module-0 governance requirement: the /api/admin/* endpoints read the
active rules vocabulary, the data-source readiness grid, and the
workspace settings summary. None of those are appropriate for rank-and-
file users. The posture is fail-closed -- we only admit callers whose
Databricks-forwarded group membership includes the configured admin
group (default ``mip-admin``).

Databricks Apps forwards workspace identity via two headers at the app
edge:

* ``X-Forwarded-Email``  - the authenticated user email (used by
  ``backend.services.audit_store.resolve_actor`` for audit attribution).
* ``X-Forwarded-Groups`` - comma-separated workspace group names the
  user is a member of.

This module provides a tiny FastAPI dependency, ``require_admin``, that
reads ``X-Forwarded-Groups``, tokenises the comma-separated list, and
403s with ``{"detail": "forbidden"}`` when the configured admin group
(or the hard-coded fallback ``"admins"``) is not present. On success it
returns the actor email (via ``resolve_actor``) so routers can pass it
straight into the audit ledger without plumbing the ``Request`` twice.

No bypass flag. Local dev/test flows set ``X-Forwarded-Groups:
mip-admin`` on every request -- documented in ``docs/runbook.md``. An
``app_env == "local"`` auto-admit was considered and rejected: flags
like that rot into production the first time someone forgets to strip
them, and the failure mode (silently opening admin to unauthenticated
callers) is exactly the posture governance does not want.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from backend.config.settings import settings
from backend.services.audit_store import resolve_actor

log = logging.getLogger(__name__)


# Hard-coded fallback in addition to the configured group name. Keeping
# it as a constant rather than a second setting means operators can't
# accidentally strip admin access by zeroing the env var; they'd have
# to edit code and get it through review.
_FALLBACK_ADMIN_GROUP: str = "admins"


def _parse_groups(raw: str | None) -> set[str]:
    """Split ``X-Forwarded-Groups`` into a set of group names.

    The header is a comma-separated list per the Databricks Apps edge
    contract. We lower-case the comparison set because workspace group
    naming is case-insensitive in practice and mixed case would be a
    silent-deny footgun.
    """
    if not raw:
        return set()
    return {tok.strip().lower() for tok in raw.split(",") if tok.strip()}


def require_admin(request: Request) -> str:
    """FastAPI dependency: admit admins, reject everyone else.

    On success, returns the actor email (from ``resolve_actor``) so
    routers can thread it into audit writes without re-reading the
    ``Request`` headers. On failure, raises ``HTTPException(403,
    detail="forbidden")`` -- the exact body string is load-bearing for
    the frontend's admin 403 banner copy.
    """
    groups = _parse_groups(request.headers.get("X-Forwarded-Groups"))
    allowed = {settings.admin_group_name.lower(), _FALLBACK_ADMIN_GROUP}
    if not (groups & allowed):
        # Log the deny at INFO so ops can see the identity that got
        # rejected without flooding the log with WARN noise every time
        # the admin page loads for a non-admin. We deliberately do NOT
        # log the raw header value -- the actor email is the stable
        # forensic anchor.
        actor = resolve_actor(request)
        log.info(
            "rbac.require_admin: deny actor=%s groups_present=%d admin_group=%s",
            actor,
            len(groups),
            settings.admin_group_name,
        )
        raise HTTPException(status_code=403, detail="forbidden")
    return resolve_actor(request)


# Type alias routers pin on so the dependency signature stays uniform
# across ``backend/api/admin.py``. ``AdminDep`` evaluates to ``str``
# (the admitted actor email).
AdminDep = Annotated[str, Depends(require_admin)]
