"""Shared authorization policy for campaign lifecycle mutations."""

from fastapi import Request

from backend.services.rbac import require_admin


def authorize_campaign_quarantine_actor(
    request: Request,
    *,
    requested_status: str,
    treatment_state: object,
    actor: str,
) -> str:
    """Require an admin for every alias that quarantines an active build."""

    if requested_status == "archived" and str(treatment_state or "") == "building":
        return require_admin(request)
    return actor
