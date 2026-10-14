"""Follow-ups due: the actor's own open callbacks, follow-ups and new assignments.

D-shell-deviations-a (audit 2026-09-21 shell-09, flow-08, wow-power-3 phase 1).
The rail's dot reads ``FollowupSummary`` (Lakebase only, audit-free); the tray
reads ``FollowupList``, whose every row was checked against gold with the Lead
Queue's own eligibility pair and fails closed.

A row carries a masked borrower id, times and closed codes only: never a raw
suppression reason, consent status or do-not-contact flag, a name, a contact
field, disposition text, an offer code or any email.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas.common import validate_public_borrower_id

FollowupKind = Literal["callback", "approval_follow_up", "requested_follow_up", "new_assignment"]

#: The contact-eligibility reason codes plus ``not_in_coverage`` (an id the
#: current gold population no longer carries).
ContactBlockReason = Literal[
    "consent_not_opt_in",
    "frequency_cap",
    "suppressed",
    "eligibility_not_proven",
    "contactability_not_configured",
    "not_in_coverage",
]

FollowupOwnerState = Literal["assigned", "unassigned"]


class FollowupSummary(BaseModel):
    """The rail item's poll: whether the actor has a tray, and its next due time."""

    eligible: bool = Field(description="True when the caller is an active sales-team member or an approver.")
    next_due_at: datetime | None = Field(
        description=(
            "Earliest due time over the caller's open callbacks and follow-ups due no more "
            "than 14 days ago; null when nothing is due."
        )
    )


class FollowupItem(BaseModel):
    """One open item of the caller's own due work."""

    kind: FollowupKind
    borrower_id: str
    due_at: datetime | None = Field(description="Null for a new assignment, which has no due time.")
    since_at: datetime = Field(description="When the item was scheduled, decided or assigned.")
    attempt_number: int | None = Field(description="The scheduling call's attempt number; callbacks only.")
    owner_label: str | None = Field(
        description="The current assignee's display label; requested follow-ups with an assignee only."
    )
    owner_state: FollowupOwnerState | None = Field(description="Requested follow-ups only.")
    contactable: bool = Field(
        description="True only when the gold eligibility decision and the set-level predicate both allow contact."
    )
    contact_block_reason: ContactBlockReason | None = Field(
        description="Why the row is withheld; null exactly when contactable."
    )
    earliest_recontact_at: datetime | None = Field(
        description="When the contact frequency cap lifts; frequency_cap rows only."
    )
    woke_on_signal: bool = Field(
        description="True when a snooze of this item ended early on a new recorded signal."
    )

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id(cls, value: str) -> str:
        return validate_public_borrower_id(value)


class FollowupList(BaseModel):
    """The tray's read: due and newly assigned items, at most 50 per kind."""

    items: list[FollowupItem]
    generated_at: datetime
    truncated: bool = Field(description="True when a kind had more than 50 open items.")
