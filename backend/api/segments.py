"""Borrower segment summary endpoint for Module 0 segment intelligence."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.schemas.lead import SEGMENT_CODE_VALUES, SegmentSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.schemas.segment_combinations import SegmentCombinationResponse
from backend.services.observability import get_correlation_id
from backend.services.repositories import (
    SegmentCombinationContractError,
    SegmentCombinationRepository,
    SegmentRepository,
    get_segment_combination_repository,
    get_segment_repository,
)

router = APIRouter(tags=["segments"])

RepoDep = Annotated[SegmentRepository, Depends(get_segment_repository)]
CombinationRepoDep = Annotated[SegmentCombinationRepository, Depends(get_segment_combination_repository)]


def _parse_segment_codes(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    allowed = set(SEGMENT_CODE_VALUES)
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


def _parse_segment_mode(raw: str) -> Literal["any", "all"]:
    mode = raw.strip().lower()
    if mode not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="segment_mode must be any or all")
    return mode


def _portfolio_criteria_from_query(
    *,
    geography: str | None,
    occupancy: str | None,
    lien_status: str | None,
    lender_relationship: str | None,
    product: str | None,
    loan_product: str | None,
    origination_channel: str | None,
    target_lender_ref: str | None,
    min_equity_pct_label: str | None,
    min_equity_pct: float | None,
    owner_link: str | None,
    purchase_intent: str | None,
    marketing_eligibility: str | None,
    consent_status: str | None,
    recency: str | None,
) -> PortfolioCriteria | None:
    fields: dict[str, object] = {}
    for key, value in (
        ("geography", geography),
        ("occupancy", occupancy),
        ("lien_status", lien_status),
        ("lender_relationship", lender_relationship),
        ("product", product),
        ("loan_product", loan_product),
        ("origination_channel", origination_channel),
        ("target_lender_ref", target_lender_ref),
        ("min_equity_pct_label", min_equity_pct_label),
        ("owner_link", owner_link),
        ("purchase_intent", purchase_intent),
        ("marketing_eligibility", marketing_eligibility),
        ("consent_status", consent_status),
        ("recency", recency),
    ):
        if value:
            fields[key] = value
    if min_equity_pct is not None:
        fields["min_equity_pct"] = min_equity_pct
    if not fields:
        return None
    # Segment Intelligence treats omitted contactability as "Any"; the
    # PortfolioCriteria default stays eligible-only for governed outreach and
    # lead workflows.
    if "marketing_eligibility" not in fields:
        fields["marketing_eligibility"] = "Any"
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
    loan_product: str | None = None,
    origination_channel: str | None = None,
    target_lender_ref: str | None = None,
    min_equity_pct_label: str | None = None,
    min_equity_pct: float | None = None,
    owner_link: str | None = None,
    purchase_intent: str | None = None,
    marketing_eligibility: str | None = None,
    consent_status: str | None = None,
    recency: str | None = None,
) -> list[SegmentSummary]:
    """Return one summary per registered segment.

    ``count`` is the addressable population; the optional ``contactable``
    is its marketing-eligible subset — what this card's Lead Queue link
    will actually show, since the queue applies the contact-eligibility
    predicate and the headline count does not. Optional on the wire so an
    older client is unaffected.
    """
    segment_mode = _parse_segment_mode(segment_mode)
    try:
        portfolio_criteria = _portfolio_criteria_from_query(
            geography=geography,
            occupancy=occupancy,
            lien_status=lien_status,
            lender_relationship=lender_relationship,
            product=product,
            loan_product=loan_product,
            origination_channel=origination_channel,
            target_lender_ref=target_lender_ref,
            min_equity_pct_label=min_equity_pct_label,
            min_equity_pct=min_equity_pct,
            owner_link=owner_link,
            purchase_intent=purchase_intent,
            marketing_eligibility=marketing_eligibility,
            consent_status=consent_status,
            recency=recency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return repo.list(
        portfolio_id=portfolio_id,
        segment_codes=_parse_segment_codes(segment_codes),
        segment_mode=segment_mode,
        portfolio_criteria=portfolio_criteria,
    )


@router.get("/segments/combinations", response_model=SegmentCombinationResponse)
def segment_combinations(repo: CombinationRepoDep) -> SegmentCombinationResponse | JSONResponse:
    """Where several Cotality signals fire on the same borrower (audit wow-stage-5).

    One row per non-empty exact set of the six core segment codes, whole book
    and never narrowed by a filter, with the addressable count from
    ``mip.gold.segment_combination_rollup`` and a live contactable subset.
    ``built`` is False until the gold refresh job has built the table. A cold
    warehouse surfaces as the resilience layer's 503 ``warming_up``; there is
    no fallback, and the route writes no audit row. A built table whose every
    row fails the projection contract answers a non-retryable 503 with reason
    ``contract_failure`` and no dependency, unless a last good projection is
    still cached.
    """
    try:
        return repo.combinations()
    except SegmentCombinationContractError:
        # No ``dependency``: the warehouse answered; the data broke its contract.
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The signal stack failed its data contract.",
                "retryable": False,
                "reason": "contract_failure",
                "correlation_id": get_correlation_id(),
            },
        )
