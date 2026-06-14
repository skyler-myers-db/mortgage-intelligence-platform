"""Offer recommendation endpoint with audit-backed recommendation proof."""

from __future__ import annotations

import logging
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.schemas.offer import (
    OfferAlternative,
    OfferRecommendation,
    OfferRecommendRequest,
    OfferType,
    SourceLabel,
)
from backend.services.audit_decision_inputs import decision_inputs_from_offer_inputs
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.lakebase import LakebaseError
from backend.services.observability import emit
from backend.services.repositories import (
    BorrowerRepository,
    OfferRepository,
    get_borrower_repository,
    get_offer_repository,
)
from backend.services.scoring import NBO_PRODUCT_LABELS, source_display_label

log = logging.getLogger(__name__)

router = APIRouter(prefix="/offers", tags=["offers"])

BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
OfferRepoDep = Annotated[OfferRepository, Depends(get_offer_repository)]
AuditStoreDep = Annotated[AuditStore, Depends(get_audit_store)]


def _safe_audit_write(store: AuditStore, **kwargs: object) -> None:
    try:
        store.write(**kwargs)  # type: ignore[arg-type]
    except LakebaseError as exc:
        emit(
            log,
            "audit_write_dropped",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="error",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )

# The eight codes ``fn_next_best_offer`` returns are all valid OfferType
# literals. This cast is safe because NBO_PRODUCT_LABELS is the contract.
_VALID_OFFER_TYPES: set[str] = set(NBO_PRODUCT_LABELS.keys())


def _rationale_for(
    code: str,
    spread: int,
    equity: int,
    heloc_intent: bool,
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
    """Plain-English rationale for the winning offer branch.

    Updated 2026-04-22 (fix/copilot-batch-post-merge) to drop rule-engine
    syntax (``+X bps (>= Y)``, ``cross-sell``, ``pencils``) in favour of
    language a VP of Lending or Marketing Leader would actually send. The
    numbers still anchor each string (a Sales Manager wants concrete
    detail) but with ``bps`` translated to "percentage points above
    market" when it reads cleaner.

    Branch wording mirrors the `why_now` templates in
    ``sql/transformations/gold_borrower_360.sql`` -- the two surfaces
    must agree when Borrower 360 and the Offer Orchestrator render the
    same borrower.
    """
    # Translate bps -> readable "percentage points above market" for
    # the 100+ bps cohort; below that, fall back to a qualitative phrase.
    def _spread_phrase() -> str:
        if spread >= 200:
            return "well above current market rates"
        if spread >= 100:
            return "meaningfully above current market rates"
        if spread >= 50:
            return "above current market rates"
        return "roughly at current market rates"

    def _equity_phrase() -> str:
        if equity >= 50:
            return "very strong home equity"
        if equity >= 35:
            return "strong home equity"
        if equity >= 15:
            return "meaningful home equity"
        return "limited home equity"

    if code == "purchase":
        return (
            "Home is actively listed for sale -- present a purchase mortgage on "
            "the next home before the current lien pays off at close."
        )
    if code == "refi_plus_heloc":
        return (
            f"Rate is {_spread_phrase()} and the home has {_equity_phrase()} -- "
            "a strong candidate for a refinance with a HELOC alongside it."
        )
    if code == "heloc":
        intent_phrase = "Cotality HELOC propensity" if heloc_intent else "HELOC intent"
        return (
            f"{intent_phrase} paired with {_equity_phrase()} supports a "
            "HELOC conversation; the rate isn't compelling enough for a full refinance."
        )
    if code == "refi":
        return (
            f"Rate is {_spread_phrase()} with {_equity_phrase()} -- a straight "
            "refinance fits (equity sits below the HELOC cushion)."
        )
    if code == "cash_out":
        return (
            "Rate isn't high enough to drive a plain refinance, but "
            f"{_equity_phrase()} supports a cash-out conversation."
        )
    if code == "investor":
        return (
            "Owner Link ties multiple properties to this owner -- owner-occupant "
            "economics do not apply; route to the investor desk."
        )
    if code == "retention":
        if competitor_lien:
            return (
                "Current customer with a competitor lien recorded on the Owner "
                "Link -- reach out before the recapture opportunity closes."
            )
        return (
            "Current customer whose rate has drifted above our retention bar -- "
            "reach out before they shop a competitor."
        )
    return (
        "No active trigger on this borrower right now -- keep in nurture until "
        "a signal fires."
    )


def _sources_for(code: str) -> list[str]:
    """Unity Catalog tables consulted for this branch. Drives the
    'Source evidence' chips the Offer Orchestrator renders."""
    from backend.services.databricks_sql_helpers import qualify

    base = [qualify("gold", "fn_next_best_offer")]
    if code == "purchase":
        base.append(qualify("silver", "listing_activity"))
    if code in {"refi_plus_heloc", "refi", "retention"}:
        base.append(qualify("gold", "fn_rate_spread"))
        base.append(qualify("gold", "fn_in_the_money"))
    if code in {"heloc", "cash_out", "refi_plus_heloc"}:
        base.append(qualify("gold", "fn_rate_spread"))
    if code == "heloc":
        base.append(qualify("silver", "heloc_propensity"))
    # fn_lead_score is always cited — the orchestrator shows confidence
    # on every recommendation and that confidence rolls up from the
    # lead_score weighted bundle.
    base.append(qualify("gold", "fn_lead_score"))
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
    heloc_intent: bool,
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
                    if not heloc_intent
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
                reason_not_chosen="Rate spread is below the refi minimum; HELOC is the HELOC-intent lane.",
            ),
            OfferAlternative(
                offer_code="cash_out",
                product_label=NBO_PRODUCT_LABELS["cash_out"],
                reason_not_chosen="HELOC intent steers to HELOC (cheaper capital than cash-out).",
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
                reason_not_chosen="No HELOC-intent trigger is active and equity is below the HELOC threshold; cash-out is the fit.",
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
    request: Request,
    background: BackgroundTasks,
    borrower_repo: BorrowerRepoDep,
    offer_repo: OfferRepoDep,
    audit: AuditStoreDep,
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
        "min_spread_bps": cast(int, inputs["min_spread_bps"]),
        "min_equity_pct": cast(int, inputs["min_equity_pct"]),
        "heloc_equity_min_pct": cast(int, inputs["heloc_equity_min_pct"]),
        "cashout_equity_min_pct": cast(int, inputs["cashout_equity_min_pct"]),
        "retention_min_spread_bps": cast(int, inputs["retention_min_spread_bps"]),
    }
    decision_inputs = decision_inputs_from_offer_inputs(inputs)

    background.add_task(
        _safe_audit_write,
        audit,
        actor=resolve_actor(request),
        action="recommend_offer",
        entity_type="borrower",
        entity_id=borrower.borrower_id,
        payload_json={
            "offer_code": code,
            "confidence": borrower.confidence,
            "thresholds_applied": thresholds_applied,
            "decision_inputs": decision_inputs,
        },
        evidence_ids=list(borrower.evidence_ids),
        event_type="RECOMMEND_OFFER",
        subject_clip=borrower.clip_id,
    )

    sources = _sources_for(code)
    source_labels = [
        SourceLabel(name=s, display_label=source_display_label(s))
        for s in sources
    ]

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
            heloc_intent=(
                cast(bool, inputs["has_permit"])
                or cast(bool, inputs.get("has_heloc_propensity_trigger", False))
            ),
            listed=cast(bool, inputs["listed_for_sale"]),
            investor=cast(bool, inputs["is_investor"]),
            customer=cast(bool, inputs["is_current_customer"]),
            competitor_lien=cast(bool, inputs["is_competitor_lien"]),
            min_sp=thresholds_applied["min_spread_bps"],
            min_eq=thresholds_applied["min_equity_pct"],
            heloc_min=thresholds_applied["heloc_equity_min_pct"],
            cashout_min=thresholds_applied["cashout_equity_min_pct"],
            retention_min=thresholds_applied["retention_min_spread_bps"],
        ),
        evidence_ids=borrower.evidence_ids,
        sources=sources,
        source_labels=source_labels,
        alternatives=_alternatives_for(
            code,
            equity=cast(int, inputs["equity_pct"]),
            heloc_intent=(
                cast(bool, inputs["has_permit"])
                or cast(bool, inputs.get("has_heloc_propensity_trigger", False))
            ),
            heloc_min=thresholds_applied["heloc_equity_min_pct"],
        ),
        thresholds_applied=thresholds_applied,
    )
