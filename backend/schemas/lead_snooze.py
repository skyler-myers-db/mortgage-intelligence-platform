"""Snooze one item of the caller's own Follow-ups tray (wow-power-3 phase 2).

A snooze hides the item for 1, 3 or 7 days, or until a new event-dated signal
(a listing, sale, refinance or payoff) is recorded at the next workflow sync.
It never hides a suppression: an item withheld for any reason other than the
contact frequency cap is refused, and a frequency-capped one wakes no earlier
than the cap lifts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import PUBLIC_UUID_PATTERN, validate_public_borrower_id

SnoozeDays = Literal[1, 3, 7]


class LeadSnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snooze_days: SnoozeDays
    request_id: str = Field(max_length=64, description="One UUID per snooze intent; a retry resends it.")

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        text = value.strip()
        if not PUBLIC_UUID_PATTERN.fullmatch(text):
            raise ValueError("request_id must be a UUID")
        return text


class LeadSnoozeResponse(BaseModel):
    borrower_id: str
    wake_at: datetime = Field(description="When the item returns to the tray at the latest.")
    wake_clamped: bool = Field(
        description="True when the wake time was moved to the contact frequency cap's end."
    )
    audit_event_id: str

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id(cls, value: str) -> str:
        return validate_public_borrower_id(value)
