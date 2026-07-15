"""Offer recommendation request and response contracts."""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from backend.schemas.common import (
    validate_internal_staff_email,
    validate_public_borrower_id,
    validate_public_campaign_label,
    validate_public_opaque_id,
)

# The eight lowercase codes returned by fn_next_best_offer plus 'recapture'
# (forward-compat alias — no current analog in the decision tree).
OfferType = Literal[
    "refi",
    "heloc",
    "cash_out",
    "purchase",
    "retention",
    "recapture",
    "refi_plus_heloc",
    "investor",
    "nurture",
]

_EVIDENCE_ID_PATTERN_TEXT = r"^ev-[0-9a-f]{3,61}$"
_EVIDENCE_ID_PATTERN = re.compile(_EVIDENCE_ID_PATTERN_TEXT)
_MAX_OFFER_EVIDENCE_IDS = 20
_MAX_SOURCE_REFRESHED_AT_LENGTH = 64
_MAX_SOURCE_REFRESHED_AT_FUTURE_SKEW = timedelta(minutes=5)
EvidenceIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=6,
        max_length=64,
        pattern=_EVIDENCE_ID_PATTERN_TEXT,
    ),
]


def validate_offer_evidence_ids(value: object) -> list[str]:
    """Validate ordered, public evidence references for an offer proof."""

    if not isinstance(value, list | tuple):
        raise ValueError("offer evidence_ids must be an ordered list")
    if not 1 <= len(value) <= _MAX_OFFER_EVIDENCE_IDS:
        raise ValueError(
            f"offer evidence_ids must contain between 1 and {_MAX_OFFER_EVIDENCE_IDS} items"
        )

    evidence_ids: list[str] = []
    for raw_value in value:
        if not isinstance(raw_value, str):
            raise ValueError("offer evidence_ids contains an invalid identifier")
        evidence_id = raw_value.strip()
        if _EVIDENCE_ID_PATTERN.fullmatch(evidence_id) is None:
            raise ValueError("offer evidence_ids contains an invalid identifier")
        evidence_ids.append(evidence_id)
    return evidence_ids


def validate_offer_source_refreshed_at(value: object) -> str:
    """Validate the bounded, timezone-aware source version for an offer proof."""

    if not isinstance(value, str):
        raise ValueError("offer source_refreshed_at must be a timestamp")
    timestamp = value.strip()
    if not timestamp or len(timestamp) > _MAX_SOURCE_REFRESHED_AT_LENGTH:
        raise ValueError("offer source_refreshed_at must be a bounded timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError("offer source_refreshed_at must be a parseable timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("offer source_refreshed_at must include a timezone")
    if parsed.astimezone(UTC) > datetime.now(UTC) + _MAX_SOURCE_REFRESHED_AT_FUTURE_SKEW:
        raise ValueError("offer source_refreshed_at cannot be materially in the future")
    return timestamp


def validate_campaign_uuid(value: str) -> str:
    """Return the canonical UUID used by Lakebase campaign foreign keys."""

    campaign_id = str(value).strip()
    try:
        return str(UUID(campaign_id))
    except (AttributeError, ValueError) as exc:
        raise ValueError("campaign_id must be a valid UUID") from exc


def validate_complete_campaign_binding(
    campaign_id: str | None,
    variant_name: str | None,
) -> None:
    """Prevent campaign ids and variant labels from travelling independently."""

    if (campaign_id is None) != (variant_name is None):
        raise ValueError("campaign_id and variant_name must be supplied together")


class GovernedOfferInputs(BaseModel):
    """Strict atomic input row used to build and audit a recommendation."""

    model_config = ConfigDict(extra="ignore", strict=True)

    clip_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]
    borrower_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    ]
    confidence: int = Field(ge=0, le=100)
    evidence_ids: list[EvidenceIdentifier] = Field(
        min_length=1,
        max_length=_MAX_OFFER_EVIDENCE_IDS,
    )
    source_refreshed_at: str = Field(min_length=1, max_length=64)
    rate_spread_bps: int
    equity_pct: int = Field(ge=0, le=100)
    has_permit: bool
    has_heloc_propensity_trigger: bool
    heloc_propensity_score: int | None = Field(ge=0, le=999)
    has_refi_propensity_trigger: bool
    refi_propensity_score: int | None = Field(ge=0, le=999)
    listed_for_sale: bool
    is_investor: bool
    is_current_customer: bool
    is_competitor_lien: bool
    offer_code: OfferType
    min_spread_bps: int
    min_equity_pct: int = Field(ge=0, le=100)
    heloc_equity_min_pct: int = Field(ge=0, le=100)
    cashout_equity_min_pct: int = Field(ge=0, le=100)
    retention_min_spread_bps: int

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("source_refreshed_at", mode="before")
    @classmethod
    def _source_refreshed_at_is_valid(cls, value: object) -> str:
        return validate_offer_source_refreshed_at(value)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _evidence_ids_are_valid(cls, value: object) -> list[str]:
        return validate_offer_evidence_ids(value)


class OfferAlternative(BaseModel):
    """Runner-up offers the orchestrator considered but did not pick.

    Rendered on the Offer Orchestrator page next to the primary
    recommendation so the approver sees which branches of the tree were
    close but lost — preserves the evidence posture required by Module 0.
    """

    offer_code: str
    product_label: str
    reason_not_chosen: str


class SourceLabel(BaseModel):
    """A Unity Catalog source reference with a business-friendly label.

    Added 2026-04-22 to fix the "evidence chip renders raw function
    names" blocker. ``name`` stays the authoritative UC FQN so the
    evidence drawer can follow the lineage link; ``display_label`` is
    what the chip renders on the compliance-visible surface (Borrower
    360 rationale, Offer Orchestrator sources, Genie trusted-assets).
    """

    name: str
    display_label: str


class OfferRecommendation(BaseModel):
    borrower_id: str
    source_refreshed_at: str = Field(
        min_length=1,
        max_length=64,
        json_schema_extra={"format": "date-time"},
    )
    offer_code: str
    offer_type: OfferType
    product_label: str
    confidence: int = Field(ge=0, le=100)
    rationale: str
    evidence_ids: list[EvidenceIdentifier] = Field(
        min_length=1,
        max_length=_MAX_OFFER_EVIDENCE_IDS,
    )
    # Legacy: raw UC FQN list kept for drawer lineage (the frontend's
    # sourceDescriptor() helper still substring-matches on 'fn_in_the_money'
    # etc. to route to DRAWER_SOURCES). Superseded for chip rendering by
    # `source_labels` below.
    sources: list[str] = []
    # 2026-04-22 addition: parallel list with human-readable chip labels.
    # One entry per `sources` entry; same index. Frontend should prefer
    # source_labels[i].display_label for chip text and fall back to
    # sources[i].split('.').pop() only when source_labels is empty
    # (defensive — older cached responses).
    source_labels: list[SourceLabel] = []
    alternatives: list[OfferAlternative] = []
    thresholds_applied: dict[str, int] = {}

    @field_validator("source_refreshed_at", mode="before")
    @classmethod
    def _source_refreshed_at_is_valid(cls, value: object) -> str:
        return validate_offer_source_refreshed_at(value)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _evidence_ids_are_valid(cls, value: object) -> list[str]:
        return validate_offer_evidence_ids(value)


class OfferRecommendRequest(BaseModel):
    borrower_id: str

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)


OutreachChannel = Literal["email", "sms", "direct_mail"]


class OutreachDraft(BaseModel):
    generation_id: str = Field(min_length=1, max_length=64)
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_refreshed_at: str = Field(min_length=1, max_length=64)
    borrower_id: str
    campaign_id: str | None = Field(default=None, max_length=64)
    variant_name: str | None = Field(default=None, max_length=64)
    offer_code: str
    channel: OutreachChannel
    subject: str | None = None
    body: str
    status: Literal["draft"] = "draft"
    disclosure_version: str
    disclosure_state: str
    marketing_eligible: bool
    generation_mode: Literal["supervisor", "governed_fallback"]
    generator_label: str = Field(min_length=1, max_length=80)
    strategy_summary: str = Field(min_length=1, max_length=500)
    evidence_summary: list[str] = Field(min_length=1, max_length=5)
    evidence_assets: list[str] = Field(min_length=1, max_length=5)

    @field_validator("campaign_id")
    @classmethod
    def _campaign_id_is_uuid(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_campaign_uuid(value)

    @field_validator("variant_name")
    @classmethod
    def _variant_name_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_campaign_label(value)

    @model_validator(mode="after")
    def _campaign_binding_is_complete(self) -> "OutreachDraft":
        validate_complete_campaign_binding(self.campaign_id, self.variant_name)
        return self


class OutreachDraftRequest(BaseModel):
    borrower_id: str
    channel: OutreachChannel = "email"
    campaign_id: str | None = Field(default=None, max_length=64)
    variant_name: str | None = Field(default=None, max_length=64)

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("campaign_id")
    @classmethod
    def _campaign_id_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_campaign_uuid(value)

    @field_validator("variant_name")
    @classmethod
    def _variant_name_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_campaign_label(value)

    @model_validator(mode="after")
    def _campaign_binding_is_complete(self) -> "OutreachDraftRequest":
        validate_complete_campaign_binding(self.campaign_id, self.variant_name)
        return self


class OutreachApproveRequest(BaseModel):
    borrower_id: str
    offer_code: OfferType | None = None
    channel: OutreachChannel = "email"
    campaign_id: str | None = Field(default=None, max_length=64)
    variant_name: str | None = Field(default=None, max_length=64)
    actor: str = "anonymous"
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str | None = Field(default=None, max_length=500)
    bulk_id: str | None = Field(default=None, max_length=64)
    bulk_rationale: str | None = Field(default=None, max_length=500)
    # Governance approval boundary: the endpoint requires the final
    # approver-visible draft body to include the configured tenant
    # disclosure before writing the decision. The schema keeps this
    # nullable so FastAPI can return the endpoint's clearer 422 detail
    # instead of a generic request-body parse failure.
    draft_body: str | None = None
    # The exact approver-visible subject for channels that support one.
    # SMS is intentionally subjectless and is rejected when a caller sends
    # non-empty subject text.
    draft_subject: str | None = Field(default=None, max_length=120)
    draft_generation_id: str | None = Field(default=None, max_length=64)
    draft_response_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    draft_source_refreshed_at: str | None = Field(default=None, max_length=64)
    # R5-01 idempotency key. When present, the router short-circuits a
    # retry that arrived after a successful INSERT whose response was
    # lost: the partial unique index on ``mip_app.approvals.request_id``
    # catches the collision and we return the existing decision row.
    # Clients generate this with ``crypto.randomUUID()`` and reuse the
    # same value across retries of the same user action. Bounded at 64
    # chars to match the DDL column width; None keeps legacy callers
    # working at pre-R5-01 semantics (no duplicate protection).
    request_id: str | None = Field(default=None, max_length=64)
    # Feature C: optional loan-officer assignment + "follow up in N days"
    # reminder captured at approval time. Both are optional and None-able so
    # the bulk approve path and legacy callers keep working. Reminder
    # DELIVERY is out of scope -- the endpoint only persists the assignment
    # and the computed follow_up_at timestamp.
    #
    # NOTE: this is a point-in-time SNAPSHOT of who owned this outreach
    # decision -- it is domain-validated (internal staff email) but does NOT
    # go through the gated routing path in backend/api/sales.py
    # (POST /leads/{id}/assign), which enforces sales_team roster membership +
    # contactability (approved + marketing_eligible + opt_in + not suppressed)
    # and writes the authoritative, single-active mip_app.lead_assignments
    # record. "Assigned" here is a weaker claim than "assigned" there; the two
    # are intentionally not coupled.
    assigned_to_email: str | None = Field(default=None, max_length=120)
    follow_up_in_days: int | None = Field(default=None, ge=1, le=30)

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("assigned_to_email")
    @classmethod
    def _assigned_to_email_is_staff(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_internal_staff_email(value)

    @field_validator("bulk_id", "request_id", "draft_generation_id")
    @classmethod
    def _opaque_id_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_opaque_id(value)

    @field_validator("campaign_id")
    @classmethod
    def _campaign_id_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_campaign_uuid(value)

    @field_validator("variant_name")
    @classmethod
    def _variant_name_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_campaign_label(value)

    @model_validator(mode="after")
    def _draft_proof_is_complete(self) -> "OutreachApproveRequest":
        validate_complete_campaign_binding(self.campaign_id, self.variant_name)
        proof = (
            self.draft_generation_id,
            self.draft_response_hash,
            self.draft_source_refreshed_at,
        )
        if any(value is not None for value in proof) and not all(
            isinstance(value, str) and value.strip() for value in proof
        ):
            raise ValueError(
                "draft_generation_id, draft_response_hash, and "
                "draft_source_refreshed_at must be supplied together"
            )
        return self


class OutreachApproveResponse(BaseModel):
    approved: bool
    approval_id: str
    audit_event_id: str
    # Feature C echo-back: the persisted loan-officer assignment and the
    # computed follow-up timestamp (now + follow_up_in_days). Both are None
    # when the approver did not request an assignment / reminder.
    assigned_to_email: str | None = None
    follow_up_at: datetime | None = None
    draft_generation_id: str | None = None
    draft_edited: bool | None = None


class OutreachRejectRequest(BaseModel):
    """Payload for ``POST /api/outreach/reject``.

    Mirrors ``OutreachApproveRequest`` so the UI can treat reject as the
    structural twin of approve. Rejection still writes into
    ``mip_app.approvals`` (action='reject') + ``mip_app.action_audit``
    (event_type='OUTREACH_REJECT'), giving compliance the "who / when"
    answer for every dropped borrower.
    """

    borrower_id: str
    offer_code: OfferType | None = None
    channel: OutreachChannel = "email"
    campaign_id: str | None = Field(default=None, max_length=64)
    variant_name: str | None = Field(default=None, max_length=64)
    actor: str = "anonymous"
    evidence_ids: list[str] = Field(default_factory=list)
    rationale_code: Literal[
        "out_of_footprint",
        "do_not_call",
        "opt_out",
        "fair_lending_review",
        "low_intent",
        "data_quality",
        "other_with_text",
    ]
    rationale: str | None = Field(default=None, max_length=500)
    # R5-01 idempotency key -- see ``OutreachApproveRequest.request_id``.
    # Reject carries the same retry-safety contract as approve.
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("request_id")
    @classmethod
    def _opaque_id_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_opaque_id(value)

    @field_validator("campaign_id")
    @classmethod
    def _campaign_id_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_campaign_uuid(value)

    @field_validator("variant_name")
    @classmethod
    def _variant_name_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_campaign_label(value)

    @model_validator(mode="after")
    def _other_requires_text(self) -> "OutreachRejectRequest":
        validate_complete_campaign_binding(self.campaign_id, self.variant_name)
        if self.rationale_code == "other_with_text" and not (self.rationale or "").strip():
            raise ValueError("other_with_text requires a rationale")
        return self


class OutreachRejectResponse(BaseModel):
    rejected: bool
    approval_id: str
    audit_event_id: str
