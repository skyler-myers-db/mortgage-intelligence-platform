"""Genie context-scope route (audit 2026-09-21 genie-04, phase 2).

``POST /api/v1/genie/context-scope`` renders a closed :class:`GenieContext`
(codes only) into a reviewed, guard-clean question for the Genie composer, or
returns the closed reason none is offered. It is render-only: the browser
calls it on an explicit click, prefills the composer with the answer and
never submits it, so the scope reaches Genie only inside the question the
user sends, hashed and audited like any other prompt of record.

Behavior is pinned by ``tests/unit/test_genie_context_model.py``. It shares
the ``/genie`` prefix with the other Genie routers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.genie_context import GenieContext, GenieContextScopeResponse
from backend.services.genie_context_scope import render_genie_context_scope
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.rbac import AuthenticatedActorDep

router = APIRouter(prefix="/genie", tags=["genie"])


@router.post(
    "/context-scope",
    response_model=GenieContextScopeResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def genie_context_scope(
    payload: GenieContext,
    _: Annotated[None, Depends(require_json_content_type)],
    _actor: AuthenticatedActorDep,
) -> GenieContextScopeResponse:
    """Render a Lead Queue filter selection into a reviewed Genie question.

    AUDIT EXEMPT: renders a reviewed prompt from closed codes; reads no
    borrower data and writes nothing. Answers 200 with a prompt or a withheld
    reason, 422 only for a body outside the closed model.
    """

    return render_genie_context_scope(payload)
