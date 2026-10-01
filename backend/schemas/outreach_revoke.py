"""Revoke an approval (audit flow-v2): the approver's correction path.

Approve and reject were terminal. An approver may now revoke an APPROVE while
its outreach is still none or queued. The revoke is a NEW decision row
(action 'revoke') with its own audit row; the approve row it supersedes is
never updated or deleted, and the borrower reads pending again.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.approval_request import canonical_uuid_text
from backend.schemas.common import (
    PUBLIC_UUID_PATTERN,
    validate_no_human_name_shape,
    validate_public_borrower_id,
    validate_public_opaque_id,
)


class OutreachRevokeRequest(BaseModel):
    """POST /outreach/revoke: one borrower's current approval, with a reason."""

    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    approval_id: str = Field(
        pattern=PUBLIC_UUID_PATTERN,
        description="The approval being revoked: it must still be the borrower's current decision.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=500,
        description="Why the approval is revoked: required, without personal details.",
    )
    request_id: str = Field(
        min_length=1,
        max_length=64,
        description="Client idempotency key; a retry with the same key replays the stored answer.",
    )

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_public_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("approval_id")
    @classmethod
    def _approval_id_is_canonical(cls, value: str) -> str:
        # Compared as text with the ledger's ``approval_id::text`` (the
        # current-decision check, the intent, the audit row): one spelling.
        return canonical_uuid_text(value)

    @field_validator("rationale")
    @classmethod
    def _rationale_is_public_safe(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale must not be blank")
        return validate_no_human_name_shape(value, field_name="rationale")

    @field_validator("request_id")
    @classmethod
    def _request_id_is_opaque(cls, value: str) -> str:
        return validate_public_opaque_id(value)


class OutreachRevokeResponse(BaseModel):
    """The revoke row and what it superseded."""

    revoked: bool
    approval_id: str = Field(description="The revoke decision row.")
    revoked_approval_id: str = Field(description="The approval it superseded.")
    audit_event_id: str
    released_assignment_id: str | None = Field(
        default=None,
        description="The not-yet-worked lead assignment the revoke released, if there was one.",
    )
