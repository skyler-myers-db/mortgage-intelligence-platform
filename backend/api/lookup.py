"""Governed property loan lookup API.

``POST /api/v1/lookup/property-loan`` resolves a caller-supplied street address
+ ZIP to a masked CLIP and its loan facts, deep-linking to the governed
borrower dossier when the property maps to a scored borrower.

Boundary (stated for honesty): this is a SHARE-SCOPED, EXACT-after-
canonicalization lookup against the current refreshed Cotality share. It is NOT
Cotality's CLIP mastering / resolution service and performs no fuzzy matching.
The response NEVER echoes the submitted ``address_line``; every surfaced
identifier is masked or already-public.

RBAC: the same authenticated-operator surface as ``/api/v1/borrowers`` -- any
authenticated workspace user, not admin-gated.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.lookup import PropertyLoanLookupRequest, PropertyLoanLookupResponse
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.databricks_sql import get_sql_client
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.observability import emit
from backend.services.property_lookup import lookup_property_loan

log = logging.getLogger(__name__)

router = APIRouter(prefix="/lookup", tags=["lookup"])


def get_lookup_sql_client() -> object:
    """Router-local SQL-client provider so tests can override the warehouse.

    Returns the process-wide ``ResilientSqlClient`` (breaker + retry). Wrapped
    as a FastAPI dependency -- rather than calling ``get_sql_client`` inline --
    so unit tests inject a fake client via ``app.dependency_overrides`` without
    touching a live warehouse.
    """
    return get_sql_client()


SqlClientDep = Annotated[object, Depends(get_lookup_sql_client)]
AuditStoreDep = Annotated[AuditStore, Depends(get_audit_store)]


@router.post(
    "/property-loan",
    response_model=PropertyLoanLookupResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def property_loan_lookup(
    payload: PropertyLoanLookupRequest,
    request: Request,
    sql_client: SqlClientDep,
    audit: AuditStoreDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> PropertyLoanLookupResponse:
    # In-route ZIP digit validation with a FIXED message so a bad value is
    # never reflected back through the error body (defence in depth on top of
    # the app-wide 422 input stripping).
    if not payload.zip5.isdigit():
        raise HTTPException(status_code=422, detail="zip5 must be 5 digits")

    actor = resolve_actor(request)
    try:
        return lookup_property_loan(
            sql_client,  # type: ignore[arg-type]  # duck-typed execute_one surface
            audit,
            actor=actor,
            address_line=payload.address_line,
            zip5=payload.zip5,
            city=payload.city,
            state=payload.state,
        )
    except Exception as exc:
        # The REQUIRED audit write raises when Lakebase is down; the governance
        # record must land or the lookup fails visibly (never a silent
        # unaudited lookup). Translate to a 503 the degraded-state UI reads.
        # DependencyDownError (warehouse breaker open / retries exhausted) is
        # handled by the app-wide handler and is re-raised untouched here.
        from backend.services.resilience import DependencyDownError

        if isinstance(exc, DependencyDownError):
            raise
        emit(
            log,
            "property_lookup_failed",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="error",
            exc_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
