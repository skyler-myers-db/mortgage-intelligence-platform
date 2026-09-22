"""Minimal actor-session capabilities for fail-closed frontend navigation."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.config.settings import settings
from backend.services.rbac import can_access_admin, can_access_approver

router = APIRouter(prefix="/session", tags=["session"])


class SessionResponse(BaseModel):
    can_access_admin: bool
    can_approve: bool
    actor_email: str | None = Field(
        default=None,
        description=(
            "The caller's own identity as forwarded by the trusted Databricks "
            "Apps edge (X-Forwarded-Email, else X-Forwarded-User): the name an "
            "approval's audit row is recorded under. Null when the edge "
            "forwarded none or forwarded headers are not trusted; never a "
            "configured default actor."
        ),
    )


def _forwarded_actor(request: Request) -> str | None:
    """Return the caller's own forwarded identity, or None.

    Same headers, same precedence as ``audit_store.resolve_actor`` (which the
    RBAC gates use), minus its fallbacks: the UI prints this as "Approving as
    ...", so a configured ``default_actor`` or the untrusted-edge marker must
    never be presented as the signed-in person. Reading the headers directly
    also keeps this read off the identity-fallback counter, which is a
    regression signal for audited WRITES. The value is returned to its owner
    only and is never logged.
    """
    if not settings.trust_forwarded_headers:
        return None
    return (
        request.headers.get("X-Forwarded-Email")
        or request.headers.get("X-Forwarded-User")
        or None
    )


@router.get("", response_model=SessionResponse)
def get_session(request: Request) -> SessionResponse:
    return SessionResponse(
        can_access_admin=can_access_admin(request),
        can_approve=can_access_approver(request),
        actor_email=_forwarded_actor(request),
    )
