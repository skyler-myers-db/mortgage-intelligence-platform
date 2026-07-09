"""Lead, borrower, segment, and evidence API contracts."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas._validators import normalize_public_lender_ref
from backend.schemas.common import EvidenceEvent
from backend.schemas.why import WhyPanel

SegmentCode = Literal["itm", "listed", "permit", "investor", "equity", "retention"]
# Round-4 hole-finder R4-19: `hold` is a valid 4th state — jobs/sync_lifecycle_state.py
# writes it; Lakebase CHECK constraint accepts it; gold DDL documents it. The Literal
# used to reject it, which would 500 `/api/leads` the moment the lead_population CTAS
# learned to JOIN borrower_lifecycle_state. Include it preemptively.
ApprovalStatus = Literal["pending", "approved", "rejected", "hold"]
ConsentStatus = Literal["opt_in", "opt_out", "unknown"]
OutreachStatus = Literal["none", "queued", "actioned", "sent", "bounced", "replied"]


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
    # Display-safe Cotality property reference. Raw CLIP never leaves the
    # repository boundary by default; values are `clip_ref_*` or synthetic
    # `clip_demo_*` refs unless an internal debug flag explicitly exposes raw
    # identifiers.
    clip: str = ""
    segment_codes: list[SegmentCode]
    equity_estimate: int
    rate_spread_bps: int
    opportunity_score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    recommended_offer_code: str = "nurture"
    recommended_offer: str
    why_now: str
    evidence_ids: list[str]
    approval_status: ApprovalStatus = "pending"
    outreach_status: OutreachStatus = "none"
    approved_at: datetime | None = None
    outreach_at: datetime | None = None
    # Secondary-filter fields (2026-04-23) -- carried from
    # gold.borrower_360 through gold.lead_population so the
    # /segment-intelligence page can run real client-side predicates
    # against occupancy, owner-link (related properties), lien state, and
    # purchase intent and propensity overlays. All default to safe "unknown" values so older
    # cached rows + the Borrower360-driven in-process test fixture keep
    # validating. `listed_for_sale` is live from Cotality MLS. `has_permit`
    # remains FALSE until a true filed-permit source exists; HELOC
    # propensity is exposed separately and must not be described as a permit.
    is_owner_occupied: bool = False
    is_investor: bool = False
    is_current_customer: bool = False
    is_former_customer: bool = False
    is_competitor_lien: bool = False
    related_property_count: int = 1
    current_lien_balance: int = 0
    second_pos_amount: int = 0
    has_permit: bool = False
    listed_for_sale: bool = False
    listing_status_category: str | None = None
    listing_status_description: str | None = None
    listing_date: str | None = None
    listing_status_date: str | None = None
    listing_price: int | None = None
    listing_days_on_market: int | None = None
    listing_service: str | None = None
    heloc_propensity_score: int | None = None
    heloc_propensity_run_date: str | None = None
    has_heloc_propensity_trigger: bool = False
    refi_propensity_score: int | None = None
    refi_propensity_run_date: str | None = None
    has_refi_propensity_trigger: bool = False
    current_lender_ref: str | None = None
    # Marketing-contactability fields. These are sourced from first-party
    # CRM/campaign membership, not Cotality, and intentionally carry only
    # controlled consent/suppression enums plus timestamps. They are required
    # for fail-closed campaign/export/draft flows; no emails, phones, names,
    # street addresses, or raw campaign-member ids are exposed.
    marketing_eligible: bool = True
    consent_status: ConsentStatus = "opt_in"
    suppression_reason: str | None = None
    last_touch_at: datetime | None = None
    eligible_recontact_at: datetime | None = None
    assigned_to_email: str | None = None
    assigned_to_label: str | None = None
    assigned_at: datetime | None = None
    assignment_expires_at: datetime | None = None
    latest_disposition_outcome: str | None = None
    latest_disposition_at: datetime | None = None
    latest_callback_at: datetime | None = None
    aging_days: int | None = None

    @field_validator("display_name")
    @classmethod
    def _display_name_is_public_safe(cls, value: str) -> str:
        stripped = value.strip()
        if re.fullmatch(r"(Owner|Borrower) [A-Za-z0-9_-]{3,16}|Owner anon", stripped):
            return stripped
        raise ValueError("display_name must be synthesized, not a borrower name")

    @field_validator("clip")
    @classmethod
    def _clip_is_public_safe(cls, value: str) -> str:
        if not value:
            return value
        if re.fullmatch(r"(clip_ref_[0-9a-f]{12}|clip_demo_[A-Za-z0-9_-]+)", value):
            return value
        raise ValueError("clip must be a masked display ref")

    @field_validator("current_lender_ref")
    @classmethod
    def _lender_ref_is_public_safe(cls, value: str | None) -> str | None:
        try:
            return normalize_public_lender_ref(value)
        except ValueError as exc:
            raise ValueError("current_lender_ref must be a public-safe lender alias") from exc


class Borrower360(LeadSummary):
    clip_id: str
    owner_link_id: str
    subject_property: str
    avm_value: int
    current_lien_balance: int
    current_lien_balance_low: int = 0
    current_lien_balance_high: int = 0
    current_rate: float
    ltv: int
    related_property_count: int
    situs_cbsa_code: str | None = None
    first_pos_loan_type: str | None = None
    is_absentee: bool = False
    is_corporate_owner: bool = False
    has_first_party_relationship: bool = False
    first_party_relationship_depth: int = 0
    first_party_recent_interactions: int = 0
    first_party_recent_application: bool = False
    first_party_synthetic_demo: bool = False
    trigger_timeline: list[EvidenceEvent]
    evidence_events: list[EvidenceEvent]
    why_panel: WhyPanel

    @field_validator("clip_id")
    @classmethod
    def _clip_id_is_public_safe(cls, value: str) -> str:
        if re.fullmatch(r"(clip_ref_[0-9a-f]{12}|clip_demo_[A-Za-z0-9_-]+)", value):
            return value
        raise ValueError("clip_id must be a masked display ref")

    @field_validator("owner_link_id")
    @classmethod
    def _owner_link_id_is_public_safe(cls, value: str) -> str:
        if re.fullmatch(r"(owner_link_ref_[0-9a-f]{12}|ol_demo_[A-Za-z0-9_-]+)", value):
            return value
        raise ValueError("owner_link_id must be a masked display ref")

    @field_validator("subject_property")
    @classmethod
    def _subject_property_has_no_street(cls, value: str) -> str:
        if re.search(
            r"\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,40}\s+"
            r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|ct|court|"
            r"blvd|boulevard|way|pl|place|pkwy|parkway)\b",
            value,
            flags=re.IGNORECASE,
        ):
            raise ValueError("subject_property must not contain a street address")
        return value
