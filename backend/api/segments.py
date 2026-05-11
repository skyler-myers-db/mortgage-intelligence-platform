from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.schemas.lead import SegmentSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.repositories import SegmentRepository, get_segment_repository

router = APIRouter(prefix="/api", tags=["segments"])

RepoDep = Annotated[SegmentRepository, Depends(get_segment_repository)]


def _parse_segment_codes(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    allowed = {"itm", "listed", "permit", "investor", "equity", "retention"}
    out: list[str] = []
    for part in raw.split(","):
        code = part.strip().lower()
        if not code:
            continue
        if code not in allowed:
            raise HTTPException(status_code=422, detail="segment_codes contains an unknown segment")
        if code not in out:
            out.append(code)
    return out or None


def _portfolio_criteria_from_query(
    *,
    geography: str | None,
    occupancy: str | None,
    lien_status: str | None,
    lender_relationship: str | None,
    product: str | None,
    target_lender_ref: str | None,
    min_equity_pct_label: str | None,
    min_equity_pct: float | None,
    owner_link: str | None,
    purchase_intent: str | None,
) -> PortfolioCriteria | None:
    fields: dict[str, object] = {}
    for key, value in (
        ("geography", geography),
        ("occupancy", occupancy),
        ("lien_status", lien_status),
        ("lender_relationship", lender_relationship),
        ("product", product),
        ("target_lender_ref", target_lender_ref),
        ("min_equity_pct_label", min_equity_pct_label),
        ("owner_link", owner_link),
        ("purchase_intent", purchase_intent),
    ):
        if value:
            fields[key] = value
    if min_equity_pct is not None:
        fields["min_equity_pct"] = min_equity_pct
    if not fields:
        return None
    return PortfolioCriteria(**fields)


@router.get("/segments", response_model=list[SegmentSummary])
def list_segments(
    repo: RepoDep,
    portfolio_id: str | None = None,
    segment_codes: Annotated[str | None, Query(alias="segment_codes")] = None,
    segment_mode: Annotated[str, Query(alias="segment_mode")] = "any",
    geography: str | None = None,
    occupancy: str | None = None,
    lien_status: str | None = None,
    lender_relationship: str | None = None,
    product: str | None = None,
    target_lender_ref: str | None = None,
    min_equity_pct_label: str | None = None,
    min_equity_pct: float | None = None,
    owner_link: str | None = None,
    purchase_intent: str | None = None,
) -> list[SegmentSummary]:
    if segment_mode not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="segment_mode must be any or all")
    try:
        portfolio_criteria = _portfolio_criteria_from_query(
            geography=geography,
            occupancy=occupancy,
            lien_status=lien_status,
            lender_relationship=lender_relationship,
            product=product,
            target_lender_ref=target_lender_ref,
            min_equity_pct_label=min_equity_pct_label,
            min_equity_pct=min_equity_pct,
            owner_link=owner_link,
            purchase_intent=purchase_intent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return repo.list(
        portfolio_id=portfolio_id,
        segment_codes=_parse_segment_codes(segment_codes),
        segment_mode=segment_mode,
        portfolio_criteria=portfolio_criteria,
    )
