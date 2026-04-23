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
    offer_code: str
    offer_type: OfferType
    product_label: str
    confidence: int = Field(ge=0, le=100)
    rationale: str
    evidence_ids: list[str]
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
    actor: str = "anonymous"
    evidence_ids: list[str] = []
    # 2026-04-22 governance follow-up: the approver may edit the draft
    # body before approving. We forward the final text into the audit
    # metadata so compliance can reconstruct exactly what was approved,
    # not just the borrower + offer code. Optional for back-compat with
    # callers that haven't been updated yet.
    draft_body: str | None = None
    # R5-01 idempotency key. When present, the router short-circuits a
    # retry that arrived after a successful INSERT whose response was
    # lost: the partial unique index on ``mip_app.approvals.request_id``
    # catches the collision and we return the existing decision row.
    # Clients generate this with ``crypto.randomUUID()`` and reuse the
    # same value across retries of the same user action. Bounded at 64
    # chars to match the DDL column width; None keeps legacy callers
    # working at pre-R5-01 semantics (no duplicate protection).
    request_id: str | None = Field(default=None, max_length=64)


class OutreachApproveResponse(BaseModel):
    approved: bool
    approval_id: str
    audit_event_id: str


class OutreachRejectRequest(BaseModel):
    """Payload for ``POST /api/outreach/reject``.

    Mirrors ``OutreachApproveRequest`` so the UI can treat reject as the
    structural twin of approve. Rejection still writes into
    ``mip_app.approvals`` (action='reject') + ``mip_app.action_audit``
    (event_type='OUTREACH_REJECT'), giving compliance the "who / when"
    answer for every dropped borrower.
    """

    borrower_id: str
    offer_code: str | None = None
    actor: str = "anonymous"
    evidence_ids: list[str] = []
    rationale: str | None = None
    # R5-01 idempotency key -- see ``OutreachApproveRequest.request_id``.
    # Reject carries the same retry-safety contract as approve.
    request_id: str | None = Field(default=None, max_length=64)


class OutreachRejectResponse(BaseModel):
    rejected: bool
    approval_id: str
    audit_event_id: str
