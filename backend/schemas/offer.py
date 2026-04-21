from typing import Literal

from pydantic import BaseModel, Field

OfferType = Literal["refi", "heloc", "cash_out", "purchase", "retention", "recapture"]


class OfferRecommendation(BaseModel):
    borrower_id: str
    offer_code: str
    offer_type: OfferType
    product_label: str
    confidence: int = Field(ge=0, le=100)
    rationale: str
    evidence_ids: list[str]


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
