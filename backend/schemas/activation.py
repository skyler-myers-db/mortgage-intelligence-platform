"""Governed customer-activation/writeback API contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from backend.schemas.common import (
    validate_public_audit_identifier_or_none,
    validate_public_borrower_id,
    validate_public_opaque_id,
)
from backend.schemas.offer import OfferType

ActivationDestinationType = Literal["salesforce", "crm_cdp", "los_pos", "servicing", "webhook"]
ActivationDestinationStatus = Literal["not_configured", "dry_run", "connected", "disabled"]
ActivationOutboxStatus = Literal["dry_run", "staged", "delivered", "failed", "cancelled"]
ActivationEntityType = Literal["borrower", "campaign", "cohort"]
ActivationChannel = Literal["email", "sms", "direct_mail"]

_DESTINATION_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ALLOWED_ACTIONS = {"stage_lead", "stage_campaign"}
_OFFER_CODES = set(get_args(OfferType))


def _destination_key(value: str) -> str:
    key = str(value).strip().lower()
    if not _DESTINATION_KEY_RE.fullmatch(key):
        raise ValueError("destination_key must be a governed destination slug")
    return key


def _offer_code(value: str | None) -> str | None:
    if value is None:
        return None
    code = str(value).strip().lower()
    if not code:
        return None
    if code not in _OFFER_CODES:
        raise ValueError("offer_code must be a governed next-best-offer code")
    return code


class ActivationDestination(BaseModel):
    model_config = ConfigDict(extra="ignore")

    destination_key: str
    destination_type: ActivationDestinationType
    display_name: str
    status: ActivationDestinationStatus
    allowed_actions: list[str]
    updated_at: datetime | str | None = None

    @field_validator("destination_key")
    @classmethod
    def _destination_key_is_safe(cls, value: str) -> str:
        return _destination_key(value)

    @field_validator("allowed_actions")
    @classmethod
    def _allowed_actions_are_governed(cls, value: list[str]) -> list[str]:
        actions = [str(item).strip() for item in value if str(item).strip()]
        if not actions or any(action not in _ALLOWED_ACTIONS for action in actions):
            raise ValueError("allowed_actions must use the governed activation vocabulary")
        return actions


class ActivationOutboxItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    activation_id: str
    destination_key: str
    destination_type: ActivationDestinationType
    destination_display_name: str
    destination_status: ActivationDestinationStatus
    entity_type: ActivationEntityType
    entity_id: str
    borrower_id: str
    campaign_id: str | None = None
    approval_id: str
    offer_code: str | None = None
    channel: ActivationChannel | None = None
    status: ActivationOutboxStatus
    request_id: str
    created_by: str
    created_at: datetime | str
    updated_at: datetime | str

    @field_validator("activation_id", "campaign_id", "approval_id", "request_id")
    @classmethod
    def _opaque_ids_are_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)

    @field_validator("destination_key")
    @classmethod
    def _destination_key_is_safe(cls, value: str) -> str:
        return _destination_key(value)

    @field_validator("entity_id")
    @classmethod
    def _entity_id_is_safe(cls, value: str) -> str:
        return validate_public_audit_identifier_or_none(value) or value

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_safe(cls, value: str | None) -> str | None:
        return validate_public_borrower_id(value) if value else None

    @field_validator("offer_code")
    @classmethod
    def _offer_code_is_safe(cls, value: str | None) -> str | None:
        return _offer_code(value)


class ActivationSummary(BaseModel):
    destinations: list[ActivationDestination]
    recent_outbox: list[ActivationOutboxItem]


class ActivationStageRequest(BaseModel):
    borrower_id: str
    destination_key: str
    offer_code: str | None = None
    channel: ActivationChannel = "email"
    campaign_id: str | None = None
    approval_id: str
    request_id: str

    @field_validator("borrower_id")
    @classmethod
    def _borrower_id_is_safe(cls, value: str) -> str:
        return validate_public_borrower_id(value)

    @field_validator("destination_key")
    @classmethod
    def _destination_key_is_safe(cls, value: str) -> str:
        return _destination_key(value)

    @field_validator("campaign_id", "approval_id")
    @classmethod
    def _db_uuid_ids_are_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError("id must be a UUID") from exc

    @field_validator("request_id")
    @classmethod
    def _request_id_is_safe(cls, value: str) -> str:
        return validate_public_opaque_id(value)

    @field_validator("offer_code")
    @classmethod
    def _offer_code_is_safe(cls, value: str | None) -> str | None:
        return _offer_code(value)


class ActivationStageResponse(BaseModel):
    staged: bool
    activation: ActivationOutboxItem
    audit_event_id: str | None = None
