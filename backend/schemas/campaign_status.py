"""Campaign lifecycle status contracts."""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from backend.schemas.portfolio_campaign import assert_public_campaign_text

CampaignStatus = Literal[
    "draft",
    "pending_review",
    "approved",
    "live",
    "active",
    "rejected",
    "archived",
]

_CAMPAIGN_STATUSES = frozenset(
    {"draft", "pending_review", "approved", "live", "active", "rejected", "archived"}
)
_APPROVER_GATED_STATUSES = frozenset({"approved", "live", "active"})
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"pending_review", "archived"}),
    "pending_review": frozenset({"draft", "approved", "rejected", "archived"}),
    "approved": frozenset({"live", "rejected", "archived"}),
    "live": frozenset({"active", "archived"}),
    "active": frozenset({"archived"}),
    "rejected": frozenset({"draft", "archived"}),
    "archived": frozenset(),
}
_TRANSITION_SECRET = secrets.token_bytes(32)
_TRANSITION_PROOF_TTL = timedelta(minutes=5)
_TRANSITION_PROOF_FUTURE_SKEW = timedelta(seconds=30)


@dataclass(frozen=True)
class CampaignTransitionEvidence:
    campaign_id: str
    from_status: str
    to_status: str
    approver_email: str
    approval_id: str
    authorized_at: str
    signature: str


def _transition_claims(evidence: CampaignTransitionEvidence) -> bytes:
    return json.dumps(
        {
            "approval_id": evidence.approval_id,
            "approver_email": evidence.approver_email,
            "authorized_at": evidence.authorized_at,
            "campaign_id": evidence.campaign_id,
            "from_status": evidence.from_status,
            "to_status": evidence.to_status,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sign_transition_evidence(evidence: CampaignTransitionEvidence) -> str:
    return hmac.new(_TRANSITION_SECRET, _transition_claims(evidence), hashlib.sha256).hexdigest()


class CampaignStatusPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CampaignStatus
    expected_status: CampaignStatus | None = None
    rationale: str | None = Field(default=None, max_length=500)
    _transition_evidence: CampaignTransitionEvidence | None = PrivateAttr(default=None)

    @field_validator("rationale")
    @classmethod
    def _validate_rationale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = assert_public_campaign_text(
            value,
            field_name="campaign status rationale",
            max_length=500,
        )
        if not cleaned:
            return None
        # assert_public_campaign_text above already applies the shared
        # human-name gate with the governed geography/product exemptions; a
        # second raw title-case scan here refused city and offer phrases
        # ("Home Equity volume dropped") the shared detector accepts.
        return cleaned

    @model_validator(mode="after")
    def _require_governed_rationale(self) -> "CampaignStatusPatchRequest":
        if self.status in _APPROVER_GATED_STATUSES and not self.rationale:
            raise ValueError(
                "approved, live, and active campaign transitions require approval rationale"
            )
        return self


def authorize_campaign_status_transition(
    payload: CampaignStatusPatchRequest,
    *,
    campaign_id: str,
    current_status: str,
    approver_email: str,
    now: datetime | None = None,
) -> CampaignStatusPatchRequest:
    """Attach request-internal approval proof that JSON clients cannot supply."""

    if payload.status not in _APPROVER_GATED_STATUSES:
        return payload
    approver = approver_email.strip().lower()
    if not approver:
        raise ValueError("campaign transition requires a governed approver identity")
    issued_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    unsigned = CampaignTransitionEvidence(
        campaign_id=campaign_id,
        from_status=current_status,
        to_status=payload.status,
        approver_email=approver,
        approval_id=str(uuid4()),
        authorized_at=issued_at,
        signature="",
    )
    payload._transition_evidence = CampaignTransitionEvidence(
        **{
            **unsigned.__dict__,
            "signature": _sign_transition_evidence(unsigned),
        }
    )
    return payload


def validate_campaign_status_transition(
    payload: CampaignStatusPatchRequest,
    *,
    campaign_id: str,
    current_status: str,
    actor: str,
    now: datetime | None = None,
) -> CampaignTransitionEvidence | None:
    """Validate lifecycle legality and return verified approval evidence."""

    if current_status not in _CAMPAIGN_STATUSES:
        raise ValueError("campaign has an invalid current lifecycle status")
    if payload.status not in _LEGAL_TRANSITIONS[current_status]:
        raise ValueError(
            f"campaign status transition from {current_status} to {payload.status} is not allowed"
        )
    if payload.status not in _APPROVER_GATED_STATUSES:
        return None
    evidence = payload._transition_evidence
    if evidence is None:
        raise ValueError("campaign transition requires governed approver authorization")
    expected = _sign_transition_evidence(
        CampaignTransitionEvidence(
            **{
                **evidence.__dict__,
                "signature": "",
            }
        )
    )
    if not hmac.compare_digest(evidence.signature, expected):
        raise ValueError("campaign transition approval evidence is invalid")
    if (
        evidence.campaign_id != campaign_id
        or evidence.from_status != current_status
        or evidence.to_status != payload.status
        or evidence.approver_email != actor.strip().lower()
    ):
        raise ValueError("campaign transition approval evidence does not match the request")
    try:
        authorized_at = datetime.fromisoformat(evidence.authorized_at)
    except ValueError as exc:
        raise ValueError("campaign transition approval evidence has an invalid timestamp") from exc
    if authorized_at.tzinfo is None:
        raise ValueError("campaign transition approval evidence has an invalid timestamp")
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    authorized_at = authorized_at.astimezone(UTC)
    if authorized_at > checked_at + _TRANSITION_PROOF_FUTURE_SKEW:
        raise ValueError("campaign transition approval evidence is future-dated")
    if checked_at - authorized_at > _TRANSITION_PROOF_TTL:
        raise ValueError("campaign transition approval evidence is stale")
    return evidence
