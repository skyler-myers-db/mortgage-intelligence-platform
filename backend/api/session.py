"""Minimal actor-session capabilities for fail-closed frontend navigation."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.services.rbac import can_access_admin, can_access_approver

router = APIRouter(prefix="/session", tags=["session"])


class SessionResponse(BaseModel):
    can_access_admin: bool
    can_approve: bool


@router.get("", response_model=SessionResponse)
def get_session(request: Request) -> SessionResponse:
    return SessionResponse(
        can_access_admin=can_access_admin(request),
        can_approve=can_access_approver(request),
    )
