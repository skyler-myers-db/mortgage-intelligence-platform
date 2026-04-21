from typing import Literal

from pydantic import BaseModel, Field

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


class OfferAlternative(BaseModel):
    """Runner-up offers the orchestrator considered but did not pick.

    Rendered on the Offer Orchestrator page next to the primary
    recommendation so the approver sees which branches of the tree were
    close but lost — preserves the evidence posture required by Module 0.
    """

    offer_code: str
    product_label: str
    reason_not_chosen: str


class OfferRecommendation(BaseModel):
    borrower_id: str
    offer_code: str
    offer_type: OfferType
    product_label: str
    confidence: int = Field(ge=0, le=100)
    rationale: str
    evidence_ids: list[str]
    sources: list[str] = []
    alternatives: list[OfferAlternative] = []
    thresholds_applied: dict[str, int] = {}


class OfferRecommendRequest(BaseModel):
    borrower_id: str


class OutreachDraft(BaseModel):
    borrower_id: str
    offer_code: str
    channel: Literal["email", "sms"]
    subject: str | None = None
    body: str
    status: Literal["draft"] = "draft"


class OutreachDraftRequest(BaseModel):
    borrower_id: str
    channel: Literal["email", "sms"] = "email"


class OutreachApproveRequest(BaseModel):
    borrower_id: str
    offer_code: str | None = None
    actor: str = "demo-user"
    evidence_ids: list[str] = []


class OutreachApproveResponse(BaseModel):
    approved: bool
    approval_id: str
    audit_event_id: str
