"""Leads list API.

Slice 5 adds audit emission on the ranked-list view: one
``VIEW_LEADS`` row per render, carrying the segment filter (if any)
and the list of borrower_ids the user saw. Governance §4 wants this
so we can reconstruct "which list did the approver see when they
decided to approve". No PII lands in the audit row -- borrower ids are
already the synthetic ``B-#####`` form.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

from backend.schemas.lead import LeadSummary
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.observability import emit
from backend.services.repositories import LeadRepository, get_lead_repository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["leads"])

# Kept in sync with DatabricksLeadRepository.{DEFAULT_LIMIT, MAX_LIMIT}.
# Exposed as module-level constants so the router's Query() annotations
# and the unit tests can both read one source of truth.
DEFAULT_LEAD_LIMIT: int = 500
MAX_LEAD_LIMIT: int = 5000

RepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]


def _safe_audit_write(store: AuditStore, **kwargs: object) -> None:
    """Background-task audit writer -- swallow + log every failure.

    R5-18: broaden from ``LakebaseError`` to ``Exception`` because this
    runs in BackgroundTasks and an unhandled exception is silently
    swallowed by FastAPI's runner. Emit only the exception class name
    (never ``str(exc)``, which can leak payload content) so operators
    see the pattern in structured logs without widening the PII
    surface.
    """
    try:
        store.write(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 -- background path must not raise
        emit(
            log,
            "audit.dropped",
            dependency="lakebase",
            exc_type=type(exc).__name__,
            outcome="error",
        )


def _parse_csv_filter(
    raw: str | None,
    *,
    width: int,
    label: str,
    numeric: bool = False,
) -> list[str] | None:
    if raw is None:
        return None
    values = [part.strip().upper() for part in raw.split(",") if part.strip()]
    if not values:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        valid_chars = value.isdigit() if numeric else value.isalpha()
        if len(value) != width or not valid_chars:
            raise HTTPException(
                status_code=422,
                detail=f"{label} must be comma-separated {width}-character values",
            )
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _parse_borrower_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for value in raw.split(","):
        borrower_id = value.strip()
        if not borrower_id:
            continue
        if not borrower_id.startswith("B-") or len(borrower_id) > 64:
            raise HTTPException(
                status_code=422,
                detail="borrower_ids must be comma-separated synthetic B-* ids",
            )
        if borrower_id not in out:
            out.append(borrower_id)
    return out or None


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
    segment: str | None = None,
    segment_codes: Annotated[
        str | None,
        Query(
            alias="segment_codes",
            description=(
                "Optional comma-separated SegmentCode list for multi-card "
                "filters. Prefer this over legacy `segment` when more than "
                "one segment card is active."
            ),
        ),
    ] = None,
    segment_mode: Annotated[
        str,
        Query(
            pattern="^(any|all)$",
            description=(
                "any = segment arrays overlap; all = borrower contains every "
                "selected segment code."
            ),
        ),
    ] = "any",
    portfolio_id: str | None = None,
    state: Annotated[
        str | None,
        Query(
            min_length=2,
            max_length=2,
            description=(
                "Optional 2-char USPS state code. When present, the repo "
                "queries borrower_360 directly (no score floor) so the "
                "returned rows match the per-state map count."
            ),
        ),
    ] = None,
    zip_code: Annotated[
        str | None,
        Query(
            alias="zip",
            min_length=5,
            max_length=5,
            description=(
                "Optional 5-char ZIP. Same borrower_360 query path as state. "
                "Use with state for the most narrow filter."
            ),
        ),
    ] = None,
    states: Annotated[
        str | None,
        Query(
            alias="states",
            max_length=128,
            description=(
                "Optional comma-separated USPS states for Genie-generated "
                "cohort actions."
            ),
        ),
    ] = None,
    zips: Annotated[
        str | None,
        Query(
            alias="zips",
            max_length=512,
            description=(
                "Optional comma-separated 5-digit ZIP list for Genie-generated "
                "cohort actions."
            ),
        ),
    ] = None,
    borrower_ids: Annotated[
        str | None,
        Query(
            alias="borrower_ids",
            max_length=768,
            description=(
                "Optional comma-separated synthetic borrower IDs for Genie "
                "borrower-list cohort actions."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_LEAD_LIMIT,
            description=(
                "Maximum leads to return. Defaults to 500; max 5000. When the "
                "resultset hits this cap the response sets `X-Truncated-At` "
                "so the UI can render 'Showing N — refine filters'."
            ),
        ),
    ] = DEFAULT_LEAD_LIMIT,
) -> list[LeadSummary]:
    # 2026-05-04 FIX β: plumb optional state/zip filters through to the
    # repo. The repo's geo-filtered path bypasses lead_population (which
    # has score >= 50 baked in) and queries borrower_360, so the returned
    # rows match the addressable counts the map tooltips report. Without
    # this, the previous behaviour returned the national top-N from
    # lead_population and the FE filtered client-side, producing 0 rows
    # for ZIPs whose borrowers didn't make the national top 500.
    parsed_segments: list[str] | None = None
    if segment_codes:
        parsed_segments = [s.strip() for s in segment_codes.split(",") if s.strip()]
        if not parsed_segments or len(parsed_segments) >= 6:
            parsed_segments = None
    parsed_states = _parse_csv_filter(states, width=2, label="states")
    parsed_zips = _parse_csv_filter(zips, width=5, label="zips", numeric=True)
    parsed_borrower_ids = _parse_borrower_ids(borrower_ids)

    leads = repo.list(
        segment=segment,
        portfolio_id=portfolio_id,
        limit=limit,
        state=state,
        zip_code=zip_code,
        state_codes=parsed_states,
        zip_codes=parsed_zips,
        borrower_ids=parsed_borrower_ids,
        segment_codes=parsed_segments,
        segment_mode=segment_mode,
    )
    # When the result set hit the requested cap, advertise the truncation
    # explicitly so the frontend can tell "exactly N" vs "N and there's
    # more you didn't see". We can't distinguish at this layer between
    # "exactly N rows exist" and "more than N exist"; the header is a
    # conservative signal ("capped at") and the UI phrases it that way.
    if len(leads) >= limit:
        response.headers["X-Truncated-At"] = str(limit)
    audit_payload: dict[str, object] = {
        "rendered_borrower_ids": [lead.borrower_id for lead in leads],
        "portfolio_id": portfolio_id,
        "segment": segment,
        "limit": limit,
    }
    if parsed_segments:
        audit_payload["segment_codes"] = parsed_segments
        audit_payload["segment_mode"] = segment_mode
    if state:
        audit_payload["state"] = state.upper()
    if zip_code:
        audit_payload["zip"] = zip_code
    if parsed_states:
        audit_payload["states"] = parsed_states
    if parsed_zips:
        audit_payload["zips"] = parsed_zips
    if parsed_borrower_ids:
        audit_payload["borrower_ids"] = parsed_borrower_ids

    background.add_task(
        _safe_audit_write,
        audit,
        actor=resolve_actor(request),
        action="view_leads_ranked",
        entity_type="lead_queue",
        entity_id=segment or (",".join(parsed_segments) if parsed_segments else "_all"),
        payload_json=audit_payload,
        event_type="VIEW_LEADS",
        subject_segment=segment or (",".join(parsed_segments) if parsed_segments else None),
    )
    return leads
