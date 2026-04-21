from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException

from backend.config.settings import settings
from backend.schemas.offer import (
    OfferAlternative,
    OfferRecommendation,
    OfferRecommendRequest,
    OfferType,
)
from backend.services.repositories import (
    BorrowerRepository,
    OfferRepository,
    get_borrower_repository,
    get_offer_repository,
)
from backend.services.scoring import NBO_PRODUCT_LABELS

router = APIRouter(prefix="/api/offers", tags=["offers"])

BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
OfferRepoDep = Annotated[OfferRepository, Depends(get_offer_repository)]

# The eight codes ``fn_next_best_offer`` returns are all valid OfferType
# literals. This cast is safe because NBO_PRODUCT_LABELS is the contract.
_VALID_OFFER_TYPES: set[str] = set(NBO_PRODUCT_LABELS.keys())


def _rationale_for(
    code: str,
    spread: int,
    equity: int,
    permit: bool,
    listed: bool,
    investor: bool,
    customer: bool,
    competitor_lien: bool,
    min_sp: int,
    min_eq: int,
    heloc_min: int,
    cashout_min: int,
    retention_min: int,
) -> str:
    """Deterministic rationale cited for the winning branch.

    Mirrors the branch narrative in fn_next_best_offer.sql so the
    approver sees exactly which inputs crossed which thresholds — the
    evidence chips on the page link to the same signals.
    """
    if code == "purchase":
        return "Listed for sale — purchase mortgage opportunity on the next home; current lien will be paid off at close."
    if code == "refi_plus_heloc":
        return (
            f"Rate spread +{spread} bps (>= {min_sp}) and equity {equity}% "
            f"(>= {heloc_min}% HELOC-grade) — refi + HELOC cross-sell."
        )
    if code == "heloc":
        return (
            f"Permit on file and equity {equity}% clears HELOC threshold "
            f"({heloc_min}%); rate spread {spread} bps is below refi bar "
            f"({min_sp}) — HELOC-only."
        )
    if code == "refi":
        return (
            f"Rate spread +{spread} bps (>= {min_sp}) and equity {equity}% "
            f"(>= {min_eq}% refi-minimum, below {heloc_min}% HELOC bar) — lead with refi."
        )
    if code == "cash_out":
        return (
            f"No refi rate incentive ({spread} bps < {min_sp}); equity "
            f"{equity}% clears cash-out bar ({cashout_min}%) — cash-out refi."
        )
    if code == "investor":
        return "Owner Link shows multi-property/investor behavior and owner-occupant equity branches did not fire — investor desk."
    if code == "retention":
        trigger = "competitor lien on Owner Link" if competitor_lien else f"spread {spread} bps >= {retention_min} (retention bar)"
        return f"Current customer with {trigger} — retention outreach."
    return "No strong refi/HELOC/cash-out/listing/investor/retention signal — keep in nurture until a trigger fires."


def _sources_for(code: str) -> list[str]:
    """Unity Catalog tables consulted for this branch. Drives the
    'Source evidence' chips the Offer Orchestrator renders."""
    base = ["mip_demo.gold.fn_next_best_offer"]
    if code in {"refi_plus_heloc", "refi", "retention"}:
        base.append("mip_demo.gold.fn_rate_spread")
        base.append("mip_demo.gold.fn_in_the_money")
    if code in {"heloc", "cash_out", "refi_plus_heloc"}:
        base.append("mip_demo.gold.fn_rate_spread")
    # fn_lead_score is always cited — the orchestrator shows confidence
    # on every recommendation and that confidence rolls up from the
    # lead_score weighted bundle.
    base.append("mip_demo.gold.fn_lead_score")
    # Dedupe while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for s in base:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _alternatives_for(
    code: str,
    equity: int,
    permit: bool,
    heloc_min: int,
) -> list[OfferAlternative]:
    """Deterministic 1–2 runners-up per branch. Rules are fixed so the
    UI/test can pin them. Rendered as 'considered but not chosen' chips.
    """
    if code == "refi_plus_heloc":
        return [
            OfferAlternative(
                offer_code="refi",
                product_label=NBO_PRODUCT_LABELS["refi"],
                reason_not_chosen=f"Equity {equity}% is above the HELOC threshold ({heloc_min}%); cross-sell wins over refi-alone.",
            ),
            OfferAlternative(
                offer_code="heloc",
                product_label=NBO_PRODUCT_LABELS["heloc"],
                reason_not_chosen=(
                    "Refi rate economics also qualify, so the refi+HELOC cross-sell beats a pure HELOC."
                    if not permit
                    else "Refi rate economics also qualify, so cross-sell captures both products in one outreach."
                ),
            ),
        ]
    if code == "purchase":
        return [
            OfferAlternative(
                offer_code="refi_plus_heloc",
                product_label=NBO_PRODUCT_LABELS["refi_plus_heloc"],
                reason_not_chosen="Active listing — the current lien is about to be paid off at close; refi loses to purchase.",
            ),
        ]
    if code == "heloc":
        return [
            OfferAlternative(
                offer_code="refi",
                product_label=NBO_PRODUCT_LABELS["refi"],
                reason_not_chosen="Rate spread is below the refi minimum; HELOC is the permit-driven lane.",
            ),
            OfferAlternative(
                offer_code="cash_out",
                product_label=NBO_PRODUCT_LABELS["cash_out"],
                reason_not_chosen="Permit signal steers to HELOC (cheaper capital than cash-out).",
            ),
        ]
    if code == "refi":
        return [
            OfferAlternative(
                offer_code="refi_plus_heloc",
                product_label=NBO_PRODUCT_LABELS["refi_plus_heloc"],
                reason_not_chosen=f"Equity {equity}% is below the HELOC threshold ({heloc_min}%); cross-sell would not underwrite.",
            ),
        ]
    if code == "cash_out":
        return [
            OfferAlternative(
                offer_code="heloc",
                product_label=NBO_PRODUCT_LABELS["heloc"],
                reason_not_chosen="No permit trigger on file and equity is below HELOC threshold — cash-out is the fit.",
            ),
        ]
    if code == "investor":
        return [
            OfferAlternative(
                offer_code="nurture",
                product_label=NBO_PRODUCT_LABELS["nurture"],
                reason_not_chosen="Multi-property signal beats nurture even without owner-occupant equity.",
            ),
        ]
    if code == "retention":
        return [
            OfferAlternative(
                offer_code="nurture",
                product_label=NBO_PRODUCT_LABELS["nurture"],
                reason_not_chosen="Customer relationship plus recapture signal crosses the retention bar.",
            ),
        ]
    return []


@router.post("/recommend", response_model=OfferRecommendation)
def recommend_offer(
    payload: OfferRecommendRequest,
    borrower_repo: BorrowerRepoDep,
    offer_repo: OfferRepoDep,
) -> OfferRecommendation:
    borrower = borrower_repo.get(payload.borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")

    inputs = offer_repo.get_offer_inputs(borrower.borrower_id)
    if inputs is None:
        # Defense in depth: every borrower in the population has offer inputs.
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    code = cast(str, inputs["offer_code"])
    if code not in _VALID_OFFER_TYPES:
        # Defense in depth: scoring contract violation would surface here.
        raise HTTPException(status_code=500, detail=f"Invalid offer_code '{code}' from next_best_offer")

    thresholds_applied = {
        "min_spread_bps": settings.mip_min_spread_bps,
        "min_equity_pct": settings.mip_min_equity_pct,
        "heloc_equity_min_pct": settings.mip_heloc_equity_min_pct,
        "cashout_equity_min_pct": settings.mip_cashout_equity_min_pct,
        "retention_min_spread_bps": settings.mip_retention_min_spread_bps,
    }

    return OfferRecommendation(
        borrower_id=borrower.borrower_id,
        offer_code=code,
        offer_type=cast(OfferType, code),
        product_label=NBO_PRODUCT_LABELS[code],
        confidence=borrower.confidence,
        rationale=_rationale_for(
            code,
            spread=cast(int, inputs["rate_spread_bps"]),
            equity=cast(int, inputs["equity_pct"]),
            permit=cast(bool, inputs["has_permit"]),
            listed=cast(bool, inputs["listed_for_sale"]),
            investor=cast(bool, inputs["is_investor"]),
            customer=cast(bool, inputs["is_current_customer"]),
            competitor_lien=cast(bool, inputs["is_competitor_lien"]),
            min_sp=settings.mip_min_spread_bps,
            min_eq=settings.mip_min_equity_pct,
            heloc_min=settings.mip_heloc_equity_min_pct,
            cashout_min=settings.mip_cashout_equity_min_pct,
            retention_min=settings.mip_retention_min_spread_bps,
        ),
        evidence_ids=borrower.evidence_ids,
        sources=_sources_for(code),
        alternatives=_alternatives_for(
            code,
            equity=cast(int, inputs["equity_pct"]),
            permit=cast(bool, inputs["has_permit"]),
            heloc_min=settings.mip_heloc_equity_min_pct,
        ),
        thresholds_applied=thresholds_applied,
    )
