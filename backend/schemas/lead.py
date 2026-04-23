from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.common import EvidenceEvent
from backend.schemas.why import WhyPanel

SegmentCode = Literal["itm", "listed", "permit", "investor", "equity", "retention"]
ApprovalStatus = Literal["pending", "approved", "rejected"]


class SegmentSummary(BaseModel):
    code: SegmentCode
    name: str
    count: int
    delta: str
    avg_score: int = Field(ge=0, le=100)
    description: str
    color: str


class LeadSummary(BaseModel):
    borrower_id: str
    display_name: str
    city: str
    state: str
    zip: str
    # Cotality CLIP (10-digit property identifier). Added 2026-04-22 to fix
    # the "two different CLIP formats across routes" blocker -- the
    # segment-row preview + lead table must show the SAME CLIP that
    # Borrower 360 shows. Frontend previously derived a fake CLIP via
    # `clip_${borrower_id.toLowerCase().replace('-', '')}`; that derivation
    # is retired in favour of this real field.
    clip: str = ""
    segment_codes: list[SegmentCode]
    equity_estimate: int
    rate_spread_bps: int
    opportunity_score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    recommended_offer: str
    why_now: str
    evidence_ids: list[str]
    approval_status: ApprovalStatus = "pending"
    # Secondary-filter fields (2026-04-23) -- carried from
    # gold.borrower_360 through gold.lead_population so the
    # /segment-intelligence page can run real client-side predicates
    # against occupancy, owner-link (related properties), lien state, and
    # purchase intent. All default to safe "unknown" values so older
    # cached rows + the Borrower360-driven in-process test fixture keep
    # validating. `has_permit` / `listed_for_sale` are BLOCKED FALSE in
    # gold until Cotality Building Permits + MLS Delta shares land --
    # the UI surfaces a "data-dependency pending" note for that filter.
    is_owner_occupied: bool = False
    is_investor: bool = False
    related_property_count: int = 1
    current_lien_balance: int = 0
    second_pos_amount: int = 0
    has_permit: bool = False
    listed_for_sale: bool = False


class Borrower360(LeadSummary):
    clip_id: str
    owner_link_id: str
    subject_property: str
    avm_value: int
    current_lien_balance: int
    current_rate: float
    ltv: int
    related_property_count: int
    trigger_timeline: list[EvidenceEvent]
    evidence_events: list[EvidenceEvent]
    why_panel: WhyPanel
