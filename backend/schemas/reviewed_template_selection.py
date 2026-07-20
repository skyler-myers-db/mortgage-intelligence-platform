"""Strict Agent Responses contracts for selecting server-owned copy templates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CampaignTemplateSelection(BaseModel):
    """Bounded campaign choice; extra model-authored copy fails closed."""

    model_config = ConfigDict(extra="forbid")

    template_id: Literal["benefit_guidance_v1"]
    strategy_id: Literal["controlled_message_test_v1"]


class OutreachTemplateSelection(BaseModel):
    """Bounded outreach choice; extra model-authored copy fails closed."""

    model_config = ConfigDict(extra="forbid")

    template_id: Literal["relationship_review_v1"]
    strategy_id: Literal["relationship_review_strategy_v1"]
