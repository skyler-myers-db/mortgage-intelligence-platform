"""Decision receipt wire schema.

The receipt is the approver's read-back of the Lakebase audit row their
decision wrote. Every field is read from the persisted row (never echoed
from the request that created it) and the field set is a closed allowlist:
identifiers, closed-vocabulary codes, hashes and timestamps only. The
outreach copy itself, free-text rationale and any staff email other than
the approver's own never cross this boundary.
"""

from typing import Literal

from pydantic import BaseModel, Field

DecisionOutcome = Literal["approved", "rejected", "held"]


class DecisionReceipt(BaseModel):
    """Public-safe projection of one decision row in ``mip_app.action_audit``."""

    audit_event_id: str
    event_type: str
    decision: DecisionOutcome
    approval_id: str | None = None
    borrower_id: str | None = None
    offer_code: str | None = None
    offer_label: str | None = None
    campaign_id: str | None = None
    variant_name: str | None = None
    channel: str | None = None
    rationale_code: str | None = None
    copy_generation_id: str | None = None
    copy_hash: str | None = None
    approver: str
    request_id: str | None = None
    correlation_id: str | None = None
    created_at: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_assets: list[str] = Field(default_factory=list)
