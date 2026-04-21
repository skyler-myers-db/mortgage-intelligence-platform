"""Deterministic synthetic borrower population -- TEST FIXTURE ONLY.

Moved in Slice 4 from ``backend/services/mock_data.py`` to
``tests/fixtures/mock_population.py`` to make the "this is not the
runtime path" posture clear at the import site. Production routers do
NOT import this module -- they read live Unity Catalog rows through
the ``Databricks*Repository`` classes in
``backend.services.repositories.databricks_repo``. Tests inject the
fixtures below via FastAPI ``dependency_overrides`` (see
``tests/conftest.py``).

ALL names, addresses, CLIP/Owner-Link ids, and rates in this module are
synthetic. No real PII exists here -- this is the booth-demo path, not a
fallback. The three canonical demo borrowers (B-48291, B-48294, B-48295)
are pinned by golden fixtures in ``tests/fixtures/`` (lead_score,
rate_spread, in_the_money, next_best_offer); their inputs MUST NOT change
without updating those fixtures. New borrowers are built from a compact
spec table and scored through the same primitives that UC functions
mirror, so every ``opportunity_score``, ``rate_spread_bps``,
``equity_pct``, and ``recommended_offer`` is derived — never hardcoded.

The population is designed so every branch of ``fn_next_best_offer``
fires on at least two borrowers, geography spans the 6-state Delta
Share footprint (IL/CA/FL/TX/WA/CO per docs/data-sources-gap-analysis
§1) with Chicago as the anchor metro per data-contract §10, and
``opportunity_score`` spreads from ~45 to ~96 — the ranked-borrower
table and segment counts look like a real book of business at DAIS.
``SEGMENTS`` aggregate counts (e.g. 12,840 ITM) remain population-level
estimates of Summit Mortgage's marketable book — the 25 rows here are
the ranked sample the orchestrator surfaces first.
"""
from __future__ import annotations

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

_MKT = settings.mip_market_rate
_MIN_SP = settings.mip_min_spread_bps
_MIN_EQ = settings.mip_min_equity_pct
_HELOC_MIN = settings.mip_heloc_equity_min_pct
_CASHOUT_MIN = settings.mip_cashout_equity_min_pct
_RETENTION_MIN = settings.mip_retention_min_spread_bps

_WHY_SOURCES = ["mip.gold.fn_rate_spread", "mip.gold.fn_in_the_money"]


# ---------------------------------------------------------------------------
# Evidence catalog — one reusable row per Cotality signal type so different
# borrowers cite different evidence. Timestamps are fixed for determinism.
# ---------------------------------------------------------------------------
EVIDENCE = [
    EvidenceEvent(evidence_id="ev-001", source_product="Voluntary Lien", source_table="cotality.liens.voluntary_lien", signal_type="rate_spread", signal_value="+88 bps", display_text="Current lien rate is 87.5 bps above par.", confidence=0.92, timestamp="2026-04-20T06:12:00Z"),
    EvidenceEvent(evidence_id="ev-002", source_product="AVM", source_table="cotality.avm.current", signal_type="equity", signal_value="$285K", display_text="Estimated equity is above HELOC threshold.", confidence=0.88, timestamp="2026-04-20T06:12:00Z"),
    EvidenceEvent(evidence_id="ev-003", source_product="Mortgage Market Analytics", source_table="cotality.mma.refi_activity", signal_type="market_trend", signal_value="+28% QoQ", display_text="Local refi activity is up 28% quarter over quarter.", confidence=0.84, timestamp="2026-04-20T06:12:00Z"),
    EvidenceEvent(evidence_id="ev-004", source_product="Permits", source_table="cotality.permits.recent", signal_type="permit", signal_value="$72K kitchen", display_text="High-value remodel permit filed in the last 90 days.", confidence=0.90, timestamp="2026-04-18T14:03:00Z"),
    EvidenceEvent(evidence_id="ev-005", source_product="AVM Refresh", source_table="cotality.avm.refresh", signal_type="equity_delta", signal_value="+14% YoY", display_text="AVM refresh lifted estimated value materially over 12 months.", confidence=0.86, timestamp="2026-04-15T09:41:00Z"),
    EvidenceEvent(evidence_id="ev-006", source_product="Owner Link", source_table="cotality.ownerlink.related_properties", signal_type="multi_property", signal_value="3 properties", display_text="Owner Link identifies three related properties under the same entity.", confidence=0.81, timestamp="2026-04-12T18:22:00Z"),
    EvidenceEvent(evidence_id="ev-007", source_product="Voluntary Lien", source_table="cotality.liens.voluntary_lien", signal_type="competitor_lien", signal_value="competitor refi", display_text="Competitor lien recorded on Owner Link relationship within 60 days.", confidence=0.89, timestamp="2026-04-10T07:55:00Z"),
    EvidenceEvent(evidence_id="ev-008", source_product="Listings", source_table="cotality.listings.active", signal_type="listing", signal_value="Active MLS", display_text="Subject property is actively listed for sale.", confidence=0.94, timestamp="2026-04-08T13:10:00Z"),
]

_EVIDENCE_BY_ID = {e.evidence_id: e for e in EVIDENCE}


def _pick_evidence(ids: list[str]) -> list[EvidenceEvent]:
    return [_EVIDENCE_BY_ID[i] for i in ids if i in _EVIDENCE_BY_ID]


# ---------------------------------------------------------------------------
# Segment aggregates. Counts are Summit Mortgage book-level estimates, NOT
# the 25-row sample — the ranked table on /leads is a triaged slice of
# these populations. Keep aligned with frontend/src/mocks/demoData.ts.
# ---------------------------------------------------------------------------
SEGMENTS = [
    SegmentSummary(code="itm", name="In the Money", count=12840, delta="+18%", avg_score=82, description="Lien rate >= 75 bps above par and equity >= 15%.", color="#5CE1E6"),
    SegmentSummary(code="listed", name="Listed for Sale", count=2614, delta="+9%", avg_score=74, description="Active listing, likely purchase mortgage opportunity.", color="#F59E0B"),
    SegmentSummary(code="permit", name="Permit Activity", count=4108, delta="+11%", avg_score=71, description="Recent high-value permits indicate HELOC/cash-out demand.", color="#A78BFA"),
    SegmentSummary(code="investor", name="Investor / Multi-Property", count=1892, delta="+6%", avg_score=79, description="Owner Link shows 2+ properties or repeat behavior.", color="#F472B6"),
    SegmentSummary(code="equity", name="Home Equity Candidate", count=6320, delta="+14%", avg_score=76, description="Strong equity and prior cash-out/HELOC propensity.", color="#66C5FF"),
    SegmentSummary(code="retention", name="Retention Risk", count=3471, delta="+4%", avg_score=88, description="Current customer showing refi/listing/competitor signals.", color="#34D399"),
]


# ---------------------------------------------------------------------------
# Borrower spec table — one row per borrower. Helper below derives
# everything else (spread, equity, NBO code, lead_score, WhyPanel). Order
# of columns matches the helper signature; keep it compact so the
# population reads like a table.
# ---------------------------------------------------------------------------
# Column keys:
#   bid            : synthetic borrower id (B-#####)
#   name           : synthetic display name (individual or couple)
#   city/state/zip : geography
#   segs           : SegmentCode list (UI chips)
#   current_rate   : current lien rate (percent, e.g. 6.25)
#   avm            : AVM value (int USD)
#   lien           : current lien balance (int USD)
#   permit/listed/investor/customer/comp_lien : fn_next_best_offer booleans
#   components     : 5-tuple of lead_score inputs (eco, intent, fit, rel, ev)
#   why_now        : one-sentence orchestrator hook
#   evidence_ids   : which rows from EVIDENCE back this borrower
#   related_props  : Owner Link count (>=2 for investor branch)

BorrowerSpec = tuple

_BORROWER_SPECS: list[dict] = [
    # --- Pinned canonical trio (DO NOT modify numeric inputs; golden fixtures pin them) ---
    # Geography re-anchored from Atlanta/GA to Chicago/IL in Slice 8 so the synthetic
    # trio lives inside the real Delta Share footprint (IL/CA/FL/TX/WA/CO). Per
    # docs/data-contract-module0.md §10, Chicago is the recommended demo metro
    # (1.86M properties in-share, highest avg rate at 4.75%, broadest cohort mix).
    # Cook County ZIPs: 60611 (Streeterville / Gold Coast), 60647 (Logan Square),
    # 60613 (Lakeview). IDs, names, rates, AVM, lien, components, evidence,
    # segment codes — all unchanged; the golden fixtures key on inputs, not city.
    {"bid": "B-48291", "name": "James & Maria Rodriguez", "city": "Chicago", "state": "IL", "zip": "60611",
     "segs": ["itm", "equity"], "current_rate": 5.75, "avm": 625000, "lien": 340000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 98, "intent_trigger": 95, "fit": 90, "relationship": 85, "evidence": 92},
     "why_now": "Lien matures in 4 months, strong equity, and local refi activity is rising.",
     "evidence_ids": ["ev-001", "ev-002", "ev-003"], "related_props": 1},
    {"bid": "B-48294", "name": "David Park", "city": "Chicago", "state": "IL", "zip": "60647",
     "segs": ["permit", "equity"], "current_rate": 6.75, "avm": 560000, "lien": 342000,
     "permit": True, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 85, "intent_trigger": 92, "fit": 85, "relationship": 80, "evidence": 85},
     "why_now": "Rate spread clears par and equity clears the HELOC threshold — refi + HELOC cross-sell.",
     "evidence_ids": ["ev-002", "ev-004"], "related_props": 1},
    {"bid": "B-48295", "name": "Lisa Thompson", "city": "Chicago", "state": "IL", "zip": "60613",
     "segs": ["listed", "retention"], "current_rate": 6.50, "avm": 725000, "lien": 320000,
     "permit": False, "listed": True, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 70, "intent_trigger": 95, "fit": 80, "relationship": 85, "evidence": 80},
     "why_now": "Listed-for-sale trigger suggests a purchase mortgage opportunity on the next home.",
     "evidence_ids": ["ev-003", "ev-008"], "related_props": 1},

    # --- refi_plus_heloc (spread >= 75 AND equity >= 35): +3 = 5 total ---
    {"bid": "B-51872", "name": "Thomas Chen", "city": "Austin", "state": "TX", "zip": "78704",
     "segs": ["itm", "equity"], "current_rate": 6.25, "avm": 780000, "lien": 395000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 92, "intent_trigger": 80, "fit": 88, "relationship": 70, "evidence": 88},
     "why_now": "138 bps spread and 49% equity make refi + HELOC a clean cross-sell.",
     "evidence_ids": ["ev-001", "ev-002", "ev-005"], "related_props": 1},
    {"bid": "B-54103", "name": "Maria & Carlos Rivera", "city": "San Francisco", "state": "CA", "zip": "94110",
     "segs": ["itm", "equity"], "current_rate": 6.00, "avm": 1450000, "lien": 720000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 95, "intent_trigger": 75, "fit": 90, "relationship": 70, "evidence": 88},
     "why_now": "Bay Area AVM refresh lifted equity to 50%; rate spread easily clears par.",
     "evidence_ids": ["ev-002", "ev-003", "ev-005"], "related_props": 1},
    {"bid": "B-56219", "name": "Priya Natarajan", "city": "Seattle", "state": "WA", "zip": "98103",
     "segs": ["itm", "equity"], "current_rate": 6.25, "avm": 920000, "lien": 460000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 90, "intent_trigger": 78, "fit": 85, "relationship": 65, "evidence": 85},
     "why_now": "138 bps above par with 50% equity — top-of-list for refi + HELOC.",
     "evidence_ids": ["ev-001", "ev-002"], "related_props": 1},

    # --- heloc (permit AND equity>=35 AND spread<75): 3 total ---
    {"bid": "B-52418", "name": "Daniel O'Connor", "city": "Denver", "state": "CO", "zip": "80206",
     "segs": ["permit", "equity"], "current_rate": 5.25, "avm": 810000, "lien": 420000,
     "permit": True, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 60, "intent_trigger": 85, "fit": 78, "relationship": 60, "evidence": 80},
     "why_now": "$72K remodel permit plus 48% equity — permit-driven HELOC opportunity.",
     "evidence_ids": ["ev-002", "ev-004"], "related_props": 1},
    {"bid": "B-57041", "name": "Jennifer Walsh", "city": "Seattle", "state": "WA", "zip": "98115",
     "segs": ["permit", "equity"], "current_rate": 5.00, "avm": 675000, "lien": 325000,
     "permit": True, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 55, "intent_trigger": 82, "fit": 76, "relationship": 60, "evidence": 78},
     "why_now": "Kitchen/bath permit filed with 52% equity — HELOC is the right lane.",
     "evidence_ids": ["ev-004", "ev-005"], "related_props": 1},
    {"bid": "B-53987", "name": "Michael & Jessica Hoffmann", "city": "Chicago", "state": "IL", "zip": "60614",
     "segs": ["permit", "equity"], "current_rate": 5.25, "avm": 595000, "lien": 275000,
     "permit": True, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 58, "intent_trigger": 80, "fit": 75, "relationship": 62, "evidence": 78},
     "why_now": "Recent permit with 54% equity points to HELOC demand.",
     "evidence_ids": ["ev-002", "ev-004"], "related_props": 1},

    # --- refi (spread>=75 AND 15<=equity<35): 3 total ---
    {"bid": "B-55328", "name": "Kevin Nakamura", "city": "Austin", "state": "TX", "zip": "78745",
     "segs": ["itm"], "current_rate": 6.50, "avm": 510000, "lien": 395000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 80, "intent_trigger": 70, "fit": 70, "relationship": 55, "evidence": 72},
     "why_now": "162 bps spread with 23% equity — straight refi (below HELOC cushion).",
     "evidence_ids": ["ev-001", "ev-003"], "related_props": 1},
    {"bid": "B-58664", "name": "Angela Brooks", "city": "Orlando", "state": "FL", "zip": "32801",
     "segs": ["itm"], "current_rate": 6.25, "avm": 385000, "lien": 305000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 75, "intent_trigger": 68, "fit": 70, "relationship": 55, "evidence": 70},
     "why_now": "138 bps above par with 21% equity — refi pencils, HELOC does not.",
     "evidence_ids": ["ev-001", "ev-003"], "related_props": 1},
    {"bid": "B-59112", "name": "Robert Fitzgerald", "city": "Chicago", "state": "IL", "zip": "60657",
     "segs": ["itm"], "current_rate": 5.75, "avm": 430000, "lien": 330000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 72, "intent_trigger": 66, "fit": 68, "relationship": 55, "evidence": 70},
     "why_now": "88 bps spread with 23% equity — refi-only lane.",
     "evidence_ids": ["ev-001"], "related_props": 1},

    # --- cash_out (spread<75, no permit, equity>=25): 2 total ---
    {"bid": "B-61447", "name": "Sandra Whitfield", "city": "Denver", "state": "CO", "zip": "80220",
     "segs": ["equity"], "current_rate": 5.25, "avm": 540000, "lien": 385000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 50, "intent_trigger": 55, "fit": 65, "relationship": 50, "evidence": 62},
     "why_now": "No refi incentive, but 29% equity crosses the cash-out bar.",
     "evidence_ids": ["ev-002", "ev-005"], "related_props": 1},
    {"bid": "B-62019", "name": "Gregory Alvarez", "city": "Orlando", "state": "FL", "zip": "32819",
     "segs": ["equity"], "current_rate": 5.00, "avm": 495000, "lien": 360000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 48, "intent_trigger": 52, "fit": 62, "relationship": 48, "evidence": 60},
     "why_now": "Spread below par but 27% equity supports a cash-out conversation.",
     "evidence_ids": ["ev-002"], "related_props": 1},

    # --- purchase (listed=True): +2 = 3 total ---
    {"bid": "B-60284", "name": "Alicia Greenberg", "city": "Austin", "state": "TX", "zip": "78702",
     "segs": ["listed"], "current_rate": 5.50, "avm": 640000, "lien": 285000,
     "permit": False, "listed": True, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 65, "intent_trigger": 90, "fit": 78, "relationship": 55, "evidence": 78},
     "why_now": "Active listing — purchase mortgage opportunity on the next home.",
     "evidence_ids": ["ev-003", "ev-008"], "related_props": 1},
    {"bid": "B-60517", "name": "Wei Zhang", "city": "San Francisco", "state": "CA", "zip": "94122",
     "segs": ["listed", "retention"], "current_rate": 5.25, "avm": 1380000, "lien": 690000,
     "permit": False, "listed": True, "investor": False, "customer": True, "comp_lien": False,
     "components": {"economic_incentive": 55, "intent_trigger": 92, "fit": 82, "relationship": 85, "evidence": 78},
     "why_now": "Existing customer with an active MLS listing — keep the purchase mortgage in-house.",
     "evidence_ids": ["ev-008"], "related_props": 1},

    # --- investor (multi-property, owner-occupant branches fail): 2 total ---
    {"bid": "B-63258", "name": "Marcus Delgado", "city": "Miami", "state": "FL", "zip": "33130",
     "segs": ["investor"], "current_rate": 5.00, "avm": 325000, "lien": 265000,
     "permit": False, "listed": False, "investor": True, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 40, "intent_trigger": 60, "fit": 70, "relationship": 50, "evidence": 65},
     "why_now": "Owner Link ties three related properties — investor desk conversation.",
     "evidence_ids": ["ev-006"], "related_props": 3},
    {"bid": "B-64873", "name": "Harold Petrov", "city": "Seattle", "state": "WA", "zip": "98144",
     "segs": ["investor"], "current_rate": 5.25, "avm": 410000, "lien": 335000,
     "permit": False, "listed": False, "investor": True, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 42, "intent_trigger": 58, "fit": 72, "relationship": 52, "evidence": 62},
     "why_now": "Multi-property Owner Link with no owner-occupant economics — route to investor team.",
     "evidence_ids": ["ev-006"], "related_props": 2},

    # --- retention (customer + spread>=50 OR competitor_lien, no refi/heloc/cash_out): 3 total ---
    {"bid": "B-65102", "name": "Patricia Lindqvist", "city": "Denver", "state": "CO", "zip": "80246",
     "segs": ["retention"], "current_rate": 5.50, "avm": 445000, "lien": 360000,
     "permit": False, "listed": False, "investor": False, "customer": True, "comp_lien": False,
     "components": {"economic_incentive": 55, "intent_trigger": 62, "fit": 70, "relationship": 95, "evidence": 70},
     "why_now": "Existing Summit customer with 62 bps of drift — retention call before she shops.",
     "evidence_ids": ["ev-003"], "related_props": 1},
    {"bid": "B-66541", "name": "Steven & Joanne Hayashi", "city": "Chicago", "state": "IL", "zip": "60610",
     "segs": ["retention"], "current_rate": 5.00, "avm": 385000, "lien": 310000,
     "permit": False, "listed": False, "investor": False, "customer": True, "comp_lien": True,
     "components": {"economic_incentive": 35, "intent_trigger": 78, "fit": 68, "relationship": 92, "evidence": 75},
     "why_now": "Competitor lien recorded on Owner Link — retention outreach before recapture.",
     "evidence_ids": ["ev-007"], "related_props": 1},
    {"bid": "B-67219", "name": "Tasha Williams", "city": "Los Angeles", "state": "CA", "zip": "90042",
     "segs": ["retention"], "current_rate": 5.50, "avm": 365000, "lien": 295000,
     "permit": False, "listed": False, "investor": False, "customer": True, "comp_lien": False,
     "components": {"economic_incentive": 52, "intent_trigger": 60, "fit": 68, "relationship": 90, "evidence": 68},
     "why_now": "Current customer 62 bps above par — proactive retention before a competitor calls.",
     "evidence_ids": ["ev-003"], "related_props": 1},

    # --- nurture (no trigger): 2 total ---
    {"bid": "B-68412", "name": "Edwin Caldwell", "city": "Orlando", "state": "FL", "zip": "32806",
     "segs": [], "current_rate": 5.00, "avm": 315000, "lien": 255000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 30, "intent_trigger": 40, "fit": 58, "relationship": 40, "evidence": 55},
     "why_now": "No active trigger — keep in nurture until a signal fires.",
     "evidence_ids": [], "related_props": 1},
    {"bid": "B-69088", "name": "Rachel Okonkwo", "city": "San Francisco", "state": "CA", "zip": "94117",
     "segs": [], "current_rate": 5.00, "avm": 820000, "lien": 640000,
     "permit": False, "listed": False, "investor": False, "customer": False, "comp_lien": False,
     "components": {"economic_incentive": 32, "intent_trigger": 38, "fit": 60, "relationship": 42, "evidence": 55},
     "why_now": "Spread below par and equity below cash-out floor — nurture, no outreach yet.",
     "evidence_ids": [], "related_props": 1},
]


def _build_borrower(spec: dict) -> tuple[Borrower360, dict]:
    """Derive a Borrower360 from a compact spec by running every number
    through the canonical scoring primitives. Also returns the offer-input
    bundle the offers router consumes. Nothing here is hardcoded —
    opportunity_score, rate_spread_bps, equity_pct, recommended_offer,
    and the WhyPanel are all function outputs.
    """
    current_rate_pct: float = spec["current_rate"]
    avm: int = spec["avm"]
    lien: int = spec["lien"]

    spread = rate_spread_bps(current_rate_pct / 100, _MKT)
    equity = round(100 * (1 - lien / avm))
    ltv = round(100 * lien / avm)

    code = next_best_offer(
        spread, equity,
        spec["permit"], spec["listed"], spec["investor"],
        spec["customer"], spec["comp_lien"],
        _MIN_SP, _MIN_EQ, _HELOC_MIN, _CASHOUT_MIN, _RETENTION_MIN,
    )
    score = lead_score(**spec["components"])
    itm = in_the_money(spread, equity, _MIN_SP, _MIN_EQ)
    why = WhyPanel(
        rate_spread_bps=spread, market_rate=_MKT, equity_pct=equity,
        in_the_money=itm,
        in_the_money_reason=(
            f"+{spread} bps spread (>= {_MIN_SP}) AND {equity}% equity (>= {_MIN_EQ}%)"
            if itm
            else f"{spread} bps spread or {equity}% equity does not clear ({_MIN_SP} / {_MIN_EQ}%)"
        ),
        min_spread_bps=_MIN_SP, min_equity_pct=_MIN_EQ, sources=_WHY_SOURCES,
    )

    # Confidence = average of the five lead_score components, capped
    # [0,100]. Keeps confidence visibly correlated with score without
    # duplicating the weighted bundle.
    comps = spec["components"]
    confidence = round(sum(comps.values()) / len(comps))

    evidence_events = _pick_evidence(spec["evidence_ids"])
    equity_estimate = max(0, avm - lien)

    borrower = Borrower360(
        borrower_id=spec["bid"],
        display_name=spec["name"],
        city=spec["city"], state=spec["state"], zip=spec["zip"],
        segment_codes=spec["segs"],
        equity_estimate=equity_estimate,
        rate_spread_bps=spread,
        opportunity_score=score,
        confidence=confidence,
        recommended_offer=NBO_PRODUCT_LABELS[code],
        why_now=spec["why_now"],
        evidence_ids=spec["evidence_ids"],
        approval_status="pending",
        clip_id=f"clip_demo_{spec['bid'].replace('B-', '')}",
        owner_link_id=f"ol_demo_{spec['bid'].replace('B-', '')}",
        subject_property=f"Synthetic property · {spec['city']}, {spec['state']} {spec['zip']}",
        avm_value=avm,
        current_lien_balance=lien,
        current_rate=current_rate_pct,
        ltv=ltv,
        related_property_count=spec["related_props"],
        trigger_timeline=evidence_events or EVIDENCE[:1],
        evidence_events=evidence_events,
        why_panel=why,
    )

    offer_inputs = {
        "rate_spread_bps": spread,
        "equity_pct": equity,
        "has_permit": spec["permit"],
        "listed_for_sale": spec["listed"],
        "is_investor": spec["investor"],
        "is_current_customer": spec["customer"],
        "is_competitor_lien": spec["comp_lien"],
        "offer_code": code,
    }
    return borrower, offer_inputs


_BUILT = [_build_borrower(s) for s in _BORROWER_SPECS]
BORROWERS: list[Borrower360] = [b for b, _ in _BUILT]
BORROWER_OFFER_INPUTS: dict[str, dict] = {b.borrower_id: inputs for b, inputs in _BUILT}

# Back-compat: the three pinned offer codes are exported by name so any
# downstream reference (tests, the offers router) keeps working.
B48291_OFFER_CODE = BORROWER_OFFER_INPUTS["B-48291"]["offer_code"]
B48294_OFFER_CODE = BORROWER_OFFER_INPUTS["B-48294"]["offer_code"]
B48295_OFFER_CODE = BORROWER_OFFER_INPUTS["B-48295"]["offer_code"]


PORTFOLIO = PortfolioPreview(marketable_population=89553, high_intent_leads=12840, avg_score=81, projected_contact_to_app=9.7, cost_per_contact=2.18)
