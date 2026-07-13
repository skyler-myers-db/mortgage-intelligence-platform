"""Campaign lifecycle status contracts."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class CampaignStatusPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CampaignStatus
    rationale: str | None = Field(default=None, max_length=500)

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
        if re.search(r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b", cleaned):
            raise ValueError("campaign status rationale cannot contain human-name-shaped values")
        return cleaned
