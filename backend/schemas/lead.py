"""Lead, borrower, segment, and evidence API contracts."""

import re
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator

from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.common import EvidenceEvent
from backend.schemas.loan_officer import AssignmentLifecycleStatus
from backend.schemas.why import WhyPanel

SegmentCode = Literal[
    "itm",
    "listed",
    "permit",
    "investor",
    "equity",
    "retention",
    # S1.3 overlay segments. Membership predicates live in
    # sql/transformations/gold_borrower_360.sql (with_segments) and the
    # registry rows in gold_segment_population.sql. permit_activity is the
    # TRUE filed-permit segment (gated until the Cotality source lands);
    # the legacy `permit` code remains the customer-facing HELOC Intent.
    "second_lien_itm",
    "heloc_draw_to_payback",
    "home_equity_history",
    "refi_propensity",
    "itm_on_related_property",
    "payoff_loss_leads",
    "permit_activity",
]
# Canonical runtime vocabulary derived from the Literal so API allowlists,
# audit validation, and repositories share exactly one registry.
SEGMENT_CODE_VALUES: tuple[str, ...] = get_args(SegmentCode)
# The subset a GENIE ANSWER may hand to a governed action. The Lead Queue can
# filter on all of SEGMENT_CODE_VALUES, but a cohort a Genie answer opens also
# becomes a Lakebase cohort row, draft-campaign criteria and an approval
# record, and only these six are reviewed for that. Three readers enforced it
# with three private copies -- the Genie cohort writer's regex, the campaign
# JSON projection's frozenset, and the answer's own segment reader -- so a
# code the reader emitted could be rejected two layers later, which is exactly
# how the S1.3 overlay codes came to 400 every governed action on an answer
# that filtered on one (adversarial review 2026-08-11). One definition now.
GENIE_REPLAY_SEGMENT_CODES: frozenset[str] = frozenset(
    {"itm", "listed", "permit", "investor", "equity", "retention"}
)
# S1.3 three-state source gating for segments whose driving source can be
# disconnected or unlicensed: connected / not_connected / not_licensed.
# Derived from gold.source_readiness; the full entitlement matrix ships in
# S5.1 and will extend this same field.
SegmentSourceStatus = Literal["connected", "not_connected", "not_licensed"]
# S1.1 multi-owner: entity classification of an owner slot. Classify +
# caveat + suppress only (ROADMAP-TEMPORARY pending Cotality entity
# resolution). `unresolved` owners are excluded from contact-eligible
# populations upstream in gold.borrower_360.
OwnerEntityType = Literal["individual", "trust", "llc", "unresolved"]
# Round-4 hole-finder R4-19: `hold` is a valid 4th state — jobs/sync_lifecycle_state.py
# writes it; Lakebase CHECK constraint accepts it; gold DDL documents it. The Literal
# used to reject it, which would 500 `/api/leads` the moment the lead_population CTAS
# learned to JOIN borrower_lifecycle_state. Include it preemptively.
ApprovalStatus = Literal["pending", "approved", "rejected", "hold"]
ConsentStatus = Literal["opt_in", "opt_out", "unknown"]
OutreachStatus = Literal["none", "queued", "actioned", "sent", "bounced", "replied"]


class DimensionFacetCount(BaseModel):
    """One (value, count) facet cell inside a SegmentCard mix (S1.6)."""

    value: str
    count: int = Field(ge=0)


class SegmentSummary(BaseModel):
    code: SegmentCode
    name: str
    count: int
    # Contact-eligible subset of `count` -- the borrowers this card's Lead
    # Queue link will actually show, since the queue applies the
    # eligibility predicate and the headline count does not. Live
    # 2026-08-11: itm 3,217 of 74,335. Optional so older clients and
    # cached pre-change frames are unaffected; None means "not reported",
    # never "zero contactable".
    contactable: int | None = Field(default=None, ge=0)
    delta: str
    avg_score: int = Field(ge=0, le=100)
    description: str
    color: str
    # S1.3: three-state gate resolved from gold.source_readiness for the
    # segment's driving source. "connected" for core-spine segments and
    # whenever the readiness read is unavailable (gating is presentational;
    # counts are always real -- a gated segment simply has 0 members).
    source_status: SegmentSourceStatus = "connected"
    # Human source label backing the gate (e.g. "MLS Listings"); NULL for
    # core-spine segments that are not separately entitleable.
    source_name: str | None = None
    # S1.6 facet mixes from gold.segment_population (state='_ALL' row or the
    # dynamically-filtered rollup). Sorted by count desc then value; 'unknown'
    # aggregates borrowers with a NULL dimension value. Default [] keeps older
    # cached rows and test fixtures validating.
    loan_product_mix: list[DimensionFacetCount] = Field(default_factory=list)
    origination_channel_mix: list[DimensionFacetCount] = Field(default_factory=list)


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
    # S1.1 multi-owner caveat fields (from silver.property_owners via
    # gold.borrower_360). Defaults are single-resolved-owner so older cached
    # rows and fixtures keep validating; has_unresolved_owner=True rows are
    # never marketing_eligible upstream.
    owner_count: int = 1
    has_unresolved_owner: bool = False
    primary_owner_entity_type: OwnerEntityType | None = None
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
    # S1.6 dimensions. loan_product_type is the fn_loan_product_type bucket
    # (conventional/jumbo/fha/va/other); origination_channel is the funded
    # first-party LOS application channel. None = unknown, rendered as such.
    loan_product_type: str | None = None
    origination_channel: str | None = None
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
    # S1.4: explicit do-not-contact flag plus consent-provenance slug.
    # eligibility_source is 'synthetic_seed' for the governed demo feed and
    # becomes a CRM/CDP connector id once customer ingestion lands (S4.1).
    dnc: bool = False
    eligibility_source: str = "synthetic_seed"
    assigned_to_email: str | None = None
    assigned_to_label: str | None = None
    assigned_at: datetime | None = None
    assignment_expires_at: datetime | None = None
    # S2 assignment lifecycle stage for the active assignment; None when
    # the lead is unassigned (or the assignment is outside the actor scope).
    assignment_status: AssignmentLifecycleStatus | None = None
    # S6: the active assignment id so the row can host the lifecycle-advance
    # control (PATCH status / record outcome) without a second lookup.
    assignment_id: str | None = None
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
    source_refreshed_at: str | None = None
    clip_id: str
    owner_link_id: str
    subject_property: str
    avm_value: int
    current_lien_balance: int
    current_lien_balance_low: int = 0
    current_lien_balance_high: int = 0
    current_rate: float
    # None when ``ltv_basis_is_unreliable`` — the AVM is below the $10k
    # plausibility floor or the lien exceeds 5x it (blanket/portfolio
    # attribution), and Cotality modeled CLTV is missing or itself absurd.
    # Publishing gold's 0 in that case would read as "free and clear"; the
    # UI renders an explicit unknown instead. ``avm_value`` and
    # ``current_lien_balance`` are still populated, so the dossier keeps the
    # raw facts and withholds only the derived ratio.
    ltv: int | None = None
    ltv_basis_is_unreliable: bool = False
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
