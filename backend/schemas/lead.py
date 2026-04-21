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
    segment_codes: list[SegmentCode]
    equity_estimate: int
    rate_spread_bps: int
    opportunity_score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    recommended_offer: str
    why_now: str
    evidence_ids: list[str]
    approval_status: ApprovalStatus = "pending"


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
