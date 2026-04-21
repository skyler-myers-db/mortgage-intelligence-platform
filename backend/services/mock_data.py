from backend.config.settings import settings
from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, SegmentSummary
from backend.schemas.portfolio import PortfolioPreview
from backend.schemas.why import WhyPanel
from backend.services.scoring import (
    NBO_PRODUCT_LABELS,
    in_the_money,
    lead_score,
    next_best_offer,
    rate_spread_bps,
)

# components per tests/fixtures/lead_score_golden.json (case_03/04/05)
_B48291_COMPONENTS = {"economic_incentive": 98, "intent_trigger": 95, "fit": 90, "relationship": 85, "evidence": 92}
_B48294_COMPONENTS = {"economic_incentive": 85, "intent_trigger": 92, "fit": 85, "relationship": 80, "evidence": 85}
_B48295_COMPONENTS = {"economic_incentive": 70, "intent_trigger": 95, "fit": 80, "relationship": 85, "evidence": 80}

_WHY_SOURCES = ["mip_demo.gold.fn_rate_spread", "mip_demo.gold.fn_in_the_money"]
_MKT = settings.mip_market_rate
_MIN_SP = settings.mip_min_spread_bps
_MIN_EQ = settings.mip_min_equity_pct
_HELOC_MIN = settings.mip_heloc_equity_min_pct
_CASHOUT_MIN = settings.mip_cashout_equity_min_pct
_RETENTION_MIN = settings.mip_retention_min_spread_bps

# current_rate on the Borrower schema is a percent (5.75); the rate_spread
# contract takes fractional rates (0.0575) — divide by 100 at the boundary.
_B48291_SPREAD = rate_spread_bps(5.75 / 100, _MKT)
_B48294_SPREAD = rate_spread_bps(6.75 / 100, _MKT)
_B48295_SPREAD = rate_spread_bps(6.50 / 100, _MKT)
_B48291_EQUITY = round(100 * (1 - 340000 / 625000))
_B48294_EQUITY = round(100 * (1 - 342000 / 560000))
_B48295_EQUITY = round(100 * (1 - 320000 / 725000))

# Boolean signals feeding fn_next_best_offer — values pinned to
# tests/fixtures/next_best_offer_golden.json case_11 / case_12 / case_13
# so derivation through next_best_offer() reproduces the golden labels.
_B48291_HAS_PERMIT = False
_B48291_LISTED = False
_B48291_INVESTOR = False
_B48291_CUSTOMER = False
_B48291_COMPETITOR_LIEN = False

_B48294_HAS_PERMIT = True
_B48294_LISTED = False
_B48294_INVESTOR = False
_B48294_CUSTOMER = False
_B48294_COMPETITOR_LIEN = False

_B48295_HAS_PERMIT = False
_B48295_LISTED = True
_B48295_INVESTOR = False
_B48295_CUSTOMER = False
_B48295_COMPETITOR_LIEN = False


def _nbo(spread: int, equity: int, permit: bool, listed: bool, inv: bool, cust: bool, comp: bool) -> str:
    return next_best_offer(
        spread, equity, permit, listed, inv, cust, comp,
        _MIN_SP, _MIN_EQ, _HELOC_MIN, _CASHOUT_MIN, _RETENTION_MIN,
    )


# Derived offer codes (lowercase) — these are the contract the Offer
# Orchestrator renders via NBO_PRODUCT_LABELS.
B48291_OFFER_CODE = _nbo(_B48291_SPREAD, _B48291_EQUITY, _B48291_HAS_PERMIT, _B48291_LISTED, _B48291_INVESTOR, _B48291_CUSTOMER, _B48291_COMPETITOR_LIEN)
B48294_OFFER_CODE = _nbo(_B48294_SPREAD, _B48294_EQUITY, _B48294_HAS_PERMIT, _B48294_LISTED, _B48294_INVESTOR, _B48294_CUSTOMER, _B48294_COMPETITOR_LIEN)
B48295_OFFER_CODE = _nbo(_B48295_SPREAD, _B48295_EQUITY, _B48295_HAS_PERMIT, _B48295_LISTED, _B48295_INVESTOR, _B48295_CUSTOMER, _B48295_COMPETITOR_LIEN)

# Map from borrower id to the full signal/code bundle the offers router
# consumes when building OfferRecommendation. Keeping it next to the
# borrower definitions keeps the "inputs that made the branch fire"
# traceable for rationale rendering.
BORROWER_OFFER_INPUTS: dict[str, dict] = {
    "B-48291": {
        "rate_spread_bps": _B48291_SPREAD, "equity_pct": _B48291_EQUITY,
        "has_permit": _B48291_HAS_PERMIT, "listed_for_sale": _B48291_LISTED,
        "is_investor": _B48291_INVESTOR, "is_current_customer": _B48291_CUSTOMER,
        "is_competitor_lien": _B48291_COMPETITOR_LIEN,
        "offer_code": B48291_OFFER_CODE,
    },
    "B-48294": {
        "rate_spread_bps": _B48294_SPREAD, "equity_pct": _B48294_EQUITY,
        "has_permit": _B48294_HAS_PERMIT, "listed_for_sale": _B48294_LISTED,
        "is_investor": _B48294_INVESTOR, "is_current_customer": _B48294_CUSTOMER,
        "is_competitor_lien": _B48294_COMPETITOR_LIEN,
        "offer_code": B48294_OFFER_CODE,
    },
    "B-48295": {
        "rate_spread_bps": _B48295_SPREAD, "equity_pct": _B48295_EQUITY,
        "has_permit": _B48295_HAS_PERMIT, "listed_for_sale": _B48295_LISTED,
        "is_investor": _B48295_INVESTOR, "is_current_customer": _B48295_CUSTOMER,
        "is_competitor_lien": _B48295_COMPETITOR_LIEN,
        "offer_code": B48295_OFFER_CODE,
    },
}

_B48291_WHY = WhyPanel(
    rate_spread_bps=_B48291_SPREAD, market_rate=_MKT, equity_pct=_B48291_EQUITY,
    in_the_money=in_the_money(_B48291_SPREAD, _B48291_EQUITY, _MIN_SP, _MIN_EQ),
    in_the_money_reason=f"+{_B48291_SPREAD} bps spread (>= {_MIN_SP}) AND {_B48291_EQUITY}% equity (>= {_MIN_EQ}%)",
    min_spread_bps=_MIN_SP, min_equity_pct=_MIN_EQ, sources=_WHY_SOURCES,
)
_B48294_WHY = WhyPanel(
    rate_spread_bps=_B48294_SPREAD, market_rate=_MKT, equity_pct=_B48294_EQUITY,
    in_the_money=in_the_money(_B48294_SPREAD, _B48294_EQUITY, _MIN_SP, _MIN_EQ),
    in_the_money_reason=f"+{_B48294_SPREAD} bps spread (>= {_MIN_SP}) AND {_B48294_EQUITY}% equity (>= {_MIN_EQ}%)",
    min_spread_bps=_MIN_SP, min_equity_pct=_MIN_EQ, sources=_WHY_SOURCES,
)
_B48295_WHY = WhyPanel(
    rate_spread_bps=_B48295_SPREAD, market_rate=_MKT, equity_pct=_B48295_EQUITY,
    in_the_money=in_the_money(_B48295_SPREAD, _B48295_EQUITY, _MIN_SP, _MIN_EQ),
    in_the_money_reason=f"+{_B48295_SPREAD} bps spread (>= {_MIN_SP}) AND {_B48295_EQUITY}% equity (>= {_MIN_EQ}%)",
    min_spread_bps=_MIN_SP, min_equity_pct=_MIN_EQ, sources=_WHY_SOURCES,
)

EVIDENCE = [
    EvidenceEvent(evidence_id="ev-001", source_product="Voluntary Lien", source_table="cotality.liens.voluntary_lien", signal_type="rate_spread", signal_value="+88 bps", display_text="Current lien rate is 87.5 bps above par.", confidence=0.92, timestamp="2026-04-20T06:12:00Z"),
    EvidenceEvent(evidence_id="ev-002", source_product="AVM", source_table="cotality.avm.current", signal_type="equity", signal_value="$285K", display_text="Estimated equity is above HELOC threshold.", confidence=0.88, timestamp="2026-04-20T06:12:00Z"),
    EvidenceEvent(evidence_id="ev-003", source_product="Mortgage Market Analytics", source_table="cotality.mma.refi_activity", signal_type="market_trend", signal_value="+28% QoQ", display_text="Local refi activity is up 28% quarter over quarter.", confidence=0.84, timestamp="2026-04-20T06:12:00Z"),
]

SEGMENTS = [
    SegmentSummary(code="itm", name="In the Money", count=12840, delta="+18%", avg_score=82, description="Lien rate ≥ 75 bps above par and equity ≥ 15%.", color="#5CE1E6"),
    SegmentSummary(code="listed", name="Listed for Sale", count=2614, delta="+9%", avg_score=74, description="Active listing, likely purchase mortgage opportunity.", color="#F59E0B"),
    SegmentSummary(code="permit", name="Permit Activity", count=4108, delta="+11%", avg_score=71, description="Recent high-value permits indicate HELOC/cash-out demand.", color="#A78BFA"),
    SegmentSummary(code="investor", name="Investor / Multi-Property", count=1892, delta="+6%", avg_score=79, description="Owner Link shows 2+ properties or repeat behavior.", color="#F472B6"),
    SegmentSummary(code="equity", name="Home Equity Candidate", count=6320, delta="+14%", avg_score=76, description="Strong equity and prior cash-out/HELOC propensity.", color="#66C5FF"),
    SegmentSummary(code="retention", name="Retention Risk", count=3471, delta="+4%", avg_score=88, description="Current customer showing refi/listing/competitor signals.", color="#34D399"),
]

BORROWERS = [
    Borrower360(
        borrower_id="B-48291", display_name="James & Maria Rodriguez", city="Atlanta", state="GA", zip="30309",
        segment_codes=["itm", "equity"], equity_estimate=285000, rate_spread_bps=_B48291_SPREAD, opportunity_score=lead_score(**_B48291_COMPONENTS), confidence=88,
        recommended_offer=NBO_PRODUCT_LABELS[B48291_OFFER_CODE], why_now="Lien matures in 4 months, strong equity, and local refi activity is rising.", evidence_ids=["ev-001", "ev-002", "ev-003"], approval_status="pending",
        clip_id="clip_demo_48291", owner_link_id="ol_demo_48291", subject_property="Synthetic property · Atlanta, GA 30309", avm_value=625000, current_lien_balance=340000, current_rate=5.75, ltv=54, related_property_count=1,
        trigger_timeline=EVIDENCE, evidence_events=EVIDENCE, why_panel=_B48291_WHY,
    ),
    Borrower360(
        borrower_id="B-48294", display_name="David Park", city="Atlanta", state="GA", zip="30305",
        segment_codes=["permit", "equity"], equity_estimate=218000, rate_spread_bps=_B48294_SPREAD, opportunity_score=lead_score(**_B48294_COMPONENTS), confidence=82,
        recommended_offer=NBO_PRODUCT_LABELS[B48294_OFFER_CODE], why_now=f"Rate spread is +{_B48294_SPREAD} bps above par and equity clears the HELOC threshold — refi + HELOC cross-sell.", evidence_ids=["ev-002"], approval_status="pending",
        clip_id="clip_demo_48294", owner_link_id="ol_demo_48294", subject_property="Synthetic property · Atlanta, GA 30305", avm_value=560000, current_lien_balance=342000, current_rate=6.75, ltv=61, related_property_count=1,
        trigger_timeline=EVIDENCE[1:], evidence_events=EVIDENCE[1:], why_panel=_B48294_WHY,
    ),
    Borrower360(
        borrower_id="B-48295", display_name="Lisa Thompson", city="Atlanta", state="GA", zip="30324",
        segment_codes=["listed", "retention"], equity_estimate=405000, rate_spread_bps=_B48295_SPREAD, opportunity_score=lead_score(**_B48295_COMPONENTS), confidence=79,
        recommended_offer=NBO_PRODUCT_LABELS[B48295_OFFER_CODE], why_now="Listed-for-sale trigger suggests a purchase mortgage opportunity.", evidence_ids=["ev-003"], approval_status="pending",
        clip_id="clip_demo_48295", owner_link_id="ol_demo_48295", subject_property="Synthetic property · Atlanta, GA 30324", avm_value=725000, current_lien_balance=320000, current_rate=6.50, ltv=44, related_property_count=1,
        trigger_timeline=EVIDENCE[2:], evidence_events=EVIDENCE[2:], why_panel=_B48295_WHY,
    ),
]

PORTFOLIO = PortfolioPreview(marketable_population=89553, high_intent_leads=12840, avg_score=81, projected_contact_to_app=9.7, cost_per_contact=2.18)
