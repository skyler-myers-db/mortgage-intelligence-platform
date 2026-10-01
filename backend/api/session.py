"""Minimal actor-session capabilities for fail-closed frontend navigation."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.config.settings import settings
from backend.services.actor_identity import actor_cache_key, forwarded_identity
from backend.services.identity_display import display_name_for
from backend.services.rbac import (
    can_access_admin,
    can_access_approver,
    can_read_audit,
    is_configured_auditor,
)

router = APIRouter(prefix="/session", tags=["session"])

# Role labels the topbar identity menu shows. They name the capability tiers
# the server already decides (``rbac``), in the words the UI uses elsewhere:
# the admin 403 surface requires "Administrator", the approve gate says
# "Requires approver role".
ROLE_ADMINISTRATOR = "Administrator"
ROLE_APPROVER = "Approver"
ROLE_AUDITOR = "Auditor"
ROLE_WORKSPACE_USER = "Workspace user"

class SessionResponse(BaseModel):
    can_access_admin: bool
    can_approve: bool
    can_read_audit: bool = Field(
        default=False,
        description=(
            "Same fail-closed decision the audit-ledger reads enforce: an "
            "administrator or a configured auditor may read the full audit "
            "ledger. Grants no administrator or approver capability."
        ),
    )
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
    actor_display_name: str | None = Field(
        default=None,
        description=(
            "A readable label for the caller's own forwarded identity, derived "
            "from the same header as actor_email and nothing else (no "
            "directory lookup): an email's local part with dot, underscore or "
            "hyphen separated letter words read as capitalised words "
            "(jane.doe@... -> 'Jane Doe'), else the local part verbatim; a "
            "non-email identity verbatim. Null exactly when actor_email is."
        ),
    )
    role_labels: list[str] = Field(
        default_factory=list,
        description=(
            "Display labels for the capability tiers this session holds, most "
            "privileged first: 'Administrator' (can_access_admin), 'Approver' "
            "(can_approve), 'Auditor' (a configured auditor, read-only), else "
            "'Workspace user' for a forwarded identity that holds no tier. "
            "Empty only when the session holds no tier and no identity was "
            "forwarded: a tier admitted by "
            "group membership alone (the local and test group-compat "
            "admission included) carries its label while actor_email is "
            "null. Labels only: the can_* booleans stay the authorization "
            "contract."
        ),
    )
    lender_name: str | None = Field(
        default=None,
        description=(
            "The configured lender's display name (settings.mip_lender_name), "
            "the same value /api/config/options returns. Served here too so "
            "the shell's tenant label rides this zero-dependency call instead "
            "of the warehouse-backed options call (audit delivery-07)."
        ),
    )
    rum_enabled: bool | None = Field(
        default=None,
        description=(
            "Whether the browser may install the opt-in RUM beacon "
            "(settings.mip_rum_enabled), the same value /api/config/options "
            "returns (audit delivery-07)."
        ),
    )
    actor_cache_key: str | None = Field(
        default=None,
        description=(
            "Opaque per-actor browser-cache discriminator: the same keyed hash "
            "of the forwarded identity that the authenticated health body "
            "carries, so the browser can seed its actor-scoped storage gate "
            "before the first health probe. Not reversible to the identity "
            "and never sent to telemetry. Null exactly when actor_email is."
        ),
    )


def role_labels_for(
    *, identity: str | None, admin: bool, approver: bool, auditor: bool = False
) -> list[str]:
    """Capability tiers as display labels, most privileged first."""
    labels: list[str] = []
    if admin:
        labels.append(ROLE_ADMINISTRATOR)
    if approver:
        labels.append(ROLE_APPROVER)
    if auditor:
        labels.append(ROLE_AUDITOR)
    if not labels and identity:
        labels.append(ROLE_WORKSPACE_USER)
    return labels


# Async on purpose (audit delivery-09): the handler only reads forwarded
# headers and settings, with no I/O, so it never needs one of the worker
# threads a cold-cache burst can exhaust. (A comment, not a docstring: a
# docstring would become the OpenAPI operation description.)
@router.get("", response_model=SessionResponse)
async def get_session(request: Request) -> SessionResponse:
    # The shared resolver, minus resolve_actor's fallbacks: the UI prints this
    # as "Approving as ...", so a configured default_actor or the
    # untrusted-edge marker must never be presented as the signed-in person,
    # and this read stays off the identity-fallback counter. Returned to its
    # owner only; never logged.
    identity = forwarded_identity(request)
    admin = can_access_admin(request)
    approver = can_access_approver(request)
    auditor = is_configured_auditor(request)
    return SessionResponse(
        can_access_admin=admin,
        can_approve=approver,
        can_read_audit=can_read_audit(request),
        actor_email=identity,
        actor_display_name=display_name_for(identity),
        role_labels=role_labels_for(
            identity=identity, admin=admin, approver=approver, auditor=auditor
        ),
        lender_name=settings.mip_lender_name,
        rum_enabled=settings.mip_rum_enabled,
        actor_cache_key=actor_cache_key(identity) if identity else None,
    )
