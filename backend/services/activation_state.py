"""Lakebase-backed governed activation/writeback outbox."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.schemas.activation import (
    ActivationDestination,
    ActivationOutboxItem,
    ActivationStageRequest,
)
from backend.schemas.lead import Borrower360
from backend.services.activation_campaign_proof import (
    ACTIVE_CAMPAIGN_APPROVAL_LOCK,
    CAMPAIGN_ACTIVATION_ERROR,
    CAMPAIGN_PROOF_FOR_APPROVAL,
    CampaignActivationProof,
    campaign_activation_proof_from_row,
)
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import AuditMetadataViolation, AuditPIIError
from backend.services.campaign_targeting import campaign_contains_borrower
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.outreach_decision_ordering import (
    BORROWER_DECISION_LOCK,
    LATEST_BORROWER_DECISION,
    is_current_approval,
)
from backend.services.repositories import LeadRepository
from backend.services.scoring import NBO_PRODUCT_LABELS, offer_display_label

_ACTIVATION_OFFER_CODES = set(NBO_PRODUCT_LABELS) | {"recapture"}

_DESTINATION_SELECT = """
SELECT destination_key, destination_type, display_name, status,
       allowed_actions, updated_at
FROM mip_app.activation_destinations
ORDER BY
  CASE status
    WHEN 'connected' THEN 1
    WHEN 'dry_run' THEN 2
    WHEN 'not_configured' THEN 3
    ELSE 4
  END,
  display_name ASC
"""

_DESTINATION_BY_KEY = """
SELECT destination_key, destination_type, display_name, status,
       allowed_actions, updated_at
FROM mip_app.activation_destinations
WHERE destination_key = %(destination_key)s
LIMIT 1
"""

_OUTBOX_SELECT_BASE = """
SELECT o.activation_id, o.destination_key, d.destination_type,
       d.display_name AS destination_display_name, d.status AS destination_status,
       o.entity_type, o.entity_id, o.borrower_id, o.campaign_id, o.approval_id,
       o.offer_code, o.channel, o.status, o.request_id, o.created_by,
       o.created_at, o.updated_at, o.delivery_metadata
FROM mip_app.activation_outbox AS o
JOIN mip_app.activation_destinations AS d
  ON d.destination_key = o.destination_key
{where_clause}
ORDER BY o.created_at DESC
LIMIT %(limit)s
"""

_OUTBOX_BY_REQUEST = """
SELECT o.activation_id, o.destination_key, d.destination_type,
       d.display_name AS destination_display_name, d.status AS destination_status,
       o.entity_type, o.entity_id, o.borrower_id, o.campaign_id, o.approval_id,
       o.offer_code, o.channel, o.status, o.request_id, o.created_by,
       o.created_at, o.updated_at, o.delivery_metadata
FROM mip_app.activation_outbox AS o
JOIN mip_app.activation_destinations AS d
  ON d.destination_key = o.destination_key
WHERE o.request_id = %(request_id)s
LIMIT 1
"""

_OUTBOX_BY_BUSINESS_KEY = """
SELECT o.activation_id, o.destination_key, d.destination_type,
       d.display_name AS destination_display_name, d.status AS destination_status,
       o.entity_type, o.entity_id, o.borrower_id, o.campaign_id, o.approval_id,
       o.offer_code, o.channel, o.status, o.request_id, o.created_by,
       o.created_at, o.updated_at, o.delivery_metadata
FROM mip_app.activation_outbox AS o
JOIN mip_app.activation_destinations AS d
  ON d.destination_key = o.destination_key
WHERE o.destination_key = %(destination_key)s
  AND o.approval_id = %(approval_id)s::uuid
  AND o.borrower_id = %(borrower_id)s
  AND o.channel = %(channel)s
  AND o.status IN ('dry_run','staged','failed','delivered')
ORDER BY o.created_at DESC
LIMIT 1
"""

_OUTBOX_UPDATE_DELIVERY = """
UPDATE mip_app.activation_outbox
SET status = %(status)s,
    delivery_metadata = %(delivery_metadata)s::jsonb,
    updated_at = now()
WHERE activation_id = %(activation_id)s
"""

_OUTBOX_BY_ACTIVATION_ID = _OUTBOX_SELECT_BASE.format(
    where_clause="WHERE o.activation_id = %(activation_id)s"
)

_OUTBOX_BY_ACTIVATION_ID_FOR_UPDATE = """
SELECT o.activation_id, o.destination_key, d.destination_type,
       d.display_name AS destination_display_name, d.status AS destination_status,
       o.entity_type, o.entity_id, o.borrower_id, o.campaign_id, o.approval_id,
       o.offer_code, o.channel, o.status, o.request_id, o.created_by,
       o.created_at, o.updated_at, o.delivery_metadata
FROM mip_app.activation_outbox AS o
JOIN mip_app.activation_destinations AS d
  ON d.destination_key = o.destination_key
WHERE o.activation_id = %(activation_id)s
LIMIT 1
FOR UPDATE OF o
"""

_APPROVAL_BY_ID = """
SELECT approval_id, borrower_id, action, actor_email, offer_code, campaign_id,
       variant_name, channel, decision_intent, decision_payload_hash, decided_at
FROM mip_app.approvals
WHERE approval_id = %(approval_id)s::uuid
LIMIT 1
"""

_NON_CAMPAIGN_APPROVAL_LOCK = """
SELECT approval_id::text, borrower_id, action, actor_email, offer_code,
       campaign_id::text, variant_name, channel, decision_intent,
       decision_payload_hash
FROM mip_app.approvals AS a
WHERE a.approval_id = %(approval_id)s::uuid
  AND a.borrower_id = %(borrower_id)s
  AND a.action = 'approve'
  AND a.campaign_id IS NULL
FOR SHARE OF a
"""

_OUTBOX_INSERT = """
INSERT INTO mip_app.activation_outbox (
    activation_id, destination_key, entity_type, entity_id, borrower_id,
    campaign_id, approval_id, offer_code, channel, status, request_id,
    created_by, payload_json, delivery_metadata
) VALUES (
    %(activation_id)s, %(destination_key)s, %(entity_type)s, %(entity_id)s, %(borrower_id)s,
    %(campaign_id)s, %(approval_id)s, %(offer_code)s, %(channel)s, %(status)s, %(request_id)s,
    %(created_by)s, %(payload_json)s::jsonb, %(delivery_metadata)s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING activation_id
"""


@dataclass(frozen=True)
class ActivationWriteResult:
    activation: ActivationOutboxItem
    audit_event_id: str | None


@dataclass
class ActivationDeliveryGuard:
    """One checked-out transaction that serializes and persists a delivery."""

    activation: ActivationOutboxItem
    should_deliver: bool
    _conn: Any
    block_reason: str | None = None

    def update_delivery_state(
        self,
        *,
        activation_id: str,
        status: str,
        delivery_metadata: dict[str, Any],
    ) -> ActivationOutboxItem | None:
        if activation_id != self.activation.activation_id:
            raise PermissionError("delivery guard is bound to a different activation")
        if status not in {"delivered", "failed", "cancelled"}:
            raise PermissionError("delivery guard received an invalid activation status")
        self._conn.execute(
            _OUTBOX_UPDATE_DELIVERY,
            {
                "activation_id": activation_id,
                "status": status,
                "delivery_metadata": json.dumps(delivery_metadata, sort_keys=True),
            },
        )
        row = self._conn.execute(
            _OUTBOX_BY_ACTIVATION_ID_FOR_UPDATE,
            {"activation_id": activation_id},
        ).fetchone()
        if row is None:
            return None
        self.activation = _outbox_from_row(dict(row))
        self.should_deliver = False
        return self.activation


def get_activation_state_store() -> ActivationStateStore:
    return ActivationStateStore()


def _destination_from_row(row: dict[str, Any]) -> ActivationDestination:
    return ActivationDestination(
        destination_key=str(row["destination_key"]),
        destination_type=row["destination_type"],
        display_name=str(row["display_name"]),
        status=row["status"],
        allowed_actions=list(row.get("allowed_actions") or []),
        updated_at=row.get("updated_at"),
    )


def _outbox_from_row(row: dict[str, Any]) -> ActivationOutboxItem:
    return ActivationOutboxItem(
        activation_id=str(row["activation_id"]),
        destination_key=str(row["destination_key"]),
        destination_type=row["destination_type"],
        destination_display_name=str(row["destination_display_name"]),
        destination_status=row["destination_status"],
        entity_type=row["entity_type"],
        entity_id=str(row["entity_id"]),
        borrower_id=str(row["borrower_id"]) if row.get("borrower_id") else None,
        campaign_id=str(row["campaign_id"]) if row.get("campaign_id") else None,
        approval_id=str(row["approval_id"]) if row.get("approval_id") else None,
        offer_code=str(row["offer_code"]) if row.get("offer_code") else None,
        channel=row.get("channel"),
        status=row["status"],
        request_id=str(row["request_id"]) if row.get("request_id") else None,
        created_by=str(row["created_by"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        delivery_metadata=_coerce_delivery_metadata(row.get("delivery_metadata")),
    )


def _coerce_delivery_metadata(value: Any) -> dict[str, Any] | None:
    """psycopg returns jsonb as a dict; tests may pass a JSON string."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _same_activation_identity(
    expected: ActivationOutboxItem,
    current: ActivationOutboxItem,
) -> bool:
    """Reject a row whose governed identity changed between discovery and lock."""

    fields = (
        "activation_id",
        "destination_key",
        "destination_type",
        "entity_type",
        "entity_id",
        "borrower_id",
        "campaign_id",
        "approval_id",
        "offer_code",
        "channel",
        "request_id",
        "created_by",
    )
    return all(getattr(expected, field) == getattr(current, field) for field in fields)


def _non_campaign_approval_is_valid(
    row: dict[str, Any] | None,
    *,
    activation: ActivationOutboxItem,
) -> bool:
    """Validate the immutable approval intent for a campaign-less delivery."""

    if row is None:
        return False
    if (
        str(row.get("approval_id") or "") != activation.approval_id
        or str(row.get("borrower_id") or "") != activation.borrower_id
        or row.get("action") != "approve"
        or row.get("campaign_id") is not None
        or row.get("variant_name") is not None
        or row.get("channel") != activation.channel
        or str(row.get("offer_code") or "") != str(activation.offer_code or "")
    ):
        return False
    intent_text = str(row.get("decision_intent") or "")
    digest = hashlib.sha256(intent_text.encode("utf-8")).hexdigest()
    if not intent_text or str(row.get("decision_payload_hash") or "") != digest:
        return False
    try:
        intent = json.loads(intent_text)
    except json.JSONDecodeError:
        return False
    if (
        not isinstance(intent, dict)
        or json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        != intent_text
    ):
        return False
    expected = {
        "action": "approve",
        "actor": str(row.get("actor_email") or ""),
        "borrower_id": activation.borrower_id,
        "campaign_id": None,
        "variant_name": None,
        "channel": activation.channel,
        "offer_code": activation.offer_code,
    }
    return all(intent.get(key) == value for key, value in expected.items())


def _approved_offer_code(
    *,
    borrower: Borrower360,
    payload: ActivationStageRequest,
    approved_decision: dict[str, Any],
) -> str:
    approved_offer = approved_decision.get("offer_code")
    if approved_offer:
        offer_code = str(approved_offer).strip().lower()
        if offer_code not in _ACTIVATION_OFFER_CODES:
            raise PermissionError("approved decision has unsupported offer_code")
        return offer_code
    offer_code = borrower.recommended_offer_code or "nurture"
    if offer_code not in _ACTIVATION_OFFER_CODES:
        offer_code = "nurture"
    return offer_code


def _offer_label(offer_code: str, borrower: Borrower360) -> str:
    if offer_code == borrower.recommended_offer_code:
        return borrower.recommended_offer
    if offer_code == "recapture":
        return "Recapture"
    return offer_display_label(offer_code, borrower.recommended_offer)


def _approved_campaign_id(approved_decision: dict[str, Any]) -> str | None:
    value = approved_decision.get("campaign_id")
    return str(value) if value else None


def _assert_approved_decision_matches(
    payload: ActivationStageRequest,
    approved_decision: dict[str, Any],
) -> None:
    if str(approved_decision.get("approval_id") or "") != payload.approval_id:
        raise PermissionError("approval_id is not the approved decision being staged")
    if str(approved_decision.get("borrower_id") or "") != payload.borrower_id:
        raise PermissionError("approved decision belongs to a different borrower")
    if approved_decision.get("action") != "approve":
        raise PermissionError("approved decision is not an approve action")
    if str(approved_decision.get("channel") or "") != payload.channel:
        raise PermissionError("activation channel must match the approved decision")


def _borrower_payload(
    borrower: Borrower360,
    payload: ActivationStageRequest,
    *,
    offer_code: str,
    campaign_proof: CampaignActivationProof | None,
) -> dict[str, Any]:
    activation_payload: dict[str, Any] = {
        "schema_version": 1,
        "borrower_id": borrower.borrower_id,
        "property_ref": borrower.clip,
        "state": borrower.state,
        "zip": borrower.zip,
        "segment_codes": list(borrower.segment_codes),
        "offer_code": offer_code,
        "recommended_offer": _offer_label(offer_code, borrower),
        "channel": payload.channel,
        "opportunity_score": borrower.opportunity_score,
        "confidence": borrower.confidence,
        "marketing_eligible": borrower.marketing_eligible,
        "consent_status": borrower.consent_status,
        "current_lender_ref": borrower.current_lender_ref,
        "relationship_flags": {
            "is_current_customer": borrower.is_current_customer,
            "is_former_customer": borrower.is_former_customer,
            "is_competitor_lien": borrower.is_competitor_lien,
        },
        "source": "mip.activation_outbox",
    }
    if campaign_proof is not None:
        activation_payload["campaign_treatment"] = {
            "campaign_id": campaign_proof.campaign_id,
            "materialization_id": campaign_proof.materialization_id,
            "delta_version": campaign_proof.delta_version,
            "treatment_fingerprint": campaign_proof.treatment_fingerprint,
        }
    return activation_payload


class ActivationStateStore:
    """Durable customer-activation state.

    The store stages payloads for customer destinations and audits the staging
    event. It intentionally does not deliver to external systems. Delivery
    adapters must be configured separately and consume `activation_outbox`.
    """

    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_lakebase_client()

    def list_destinations(self) -> list[ActivationDestination]:
        return [_destination_from_row(row) for row in self._client.fetchall(_DESTINATION_SELECT, limit=50)]

    def get_destination(self, destination_key: str) -> ActivationDestination | None:
        row = self._client.fetchone(_DESTINATION_BY_KEY, {"destination_key": destination_key})
        return _destination_from_row(row) if row else None

    def list_outbox(
        self,
        *,
        borrower_id: str | None = None,
        destination_key: str | None = None,
        limit: int = 25,
    ) -> list[ActivationOutboxItem]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        where: list[str] = []
        if borrower_id:
            where.append("o.borrower_id = %(borrower_id)s")
            params["borrower_id"] = borrower_id
        if destination_key:
            where.append("o.destination_key = %(destination_key)s")
            params["destination_key"] = destination_key
        where_clause = "WHERE " + " AND ".join(where) if where else ""
        rows = self._client.fetchall(
            _OUTBOX_SELECT_BASE.format(where_clause=where_clause),
            params,
            limit=params["limit"],
        )
        return [_outbox_from_row(row) for row in rows]

    def outbox_for_request(self, request_id: str | None) -> ActivationOutboxItem | None:
        if not request_id:
            return None
        row = self._client.fetchone(_OUTBOX_BY_REQUEST, {"request_id": request_id})
        return _outbox_from_row(row) if row else None

    def outbox_for_business_key(
        self,
        *,
        destination_key: str,
        approval_id: str,
        borrower_id: str,
        channel: str,
    ) -> ActivationOutboxItem | None:
        row = self._client.fetchone(
            _OUTBOX_BY_BUSINESS_KEY,
            {
                "destination_key": destination_key,
                "approval_id": approval_id,
                "borrower_id": borrower_id,
                "channel": channel,
            },
        )
        return _outbox_from_row(row) if row else None

    def get_outbox_by_activation_id(self, activation_id: str) -> ActivationOutboxItem | None:
        row = self._client.fetchone(
            _OUTBOX_BY_ACTIVATION_ID, {"activation_id": activation_id, "limit": 1}
        )
        return _outbox_from_row(row) if row else None

    def approved_decision_for(self, *, approval_id: str, borrower_id: str) -> dict[str, Any] | None:
        row = self._client.fetchone(_APPROVAL_BY_ID, {"approval_id": approval_id})
        if not row:
            return None
        if str(row.get("borrower_id")) != borrower_id or row.get("action") != "approve":
            return None
        return dict(row)

    def campaign_activation_proof_for_approval(
        self,
        *,
        approval_id: str,
        borrower_id: str,
        campaign_id: str,
    ) -> CampaignActivationProof | None:
        row = self._client.fetchone(
            CAMPAIGN_PROOF_FOR_APPROVAL,
            {
                "approval_id": approval_id,
                "borrower_id": borrower_id,
                "campaign_id": campaign_id,
            },
        )
        if row is None:
            return None
        return campaign_activation_proof_from_row(
            row,
            approval_id=approval_id,
            borrower_id=borrower_id,
            campaign_id=campaign_id,
        )

    @contextmanager
    def delivery_guard(
        self,
        *,
        activation_id: str,
        lead_repo: LeadRepository,
    ) -> Iterator[ActivationDeliveryGuard]:
        """Authorize one external delivery while holding its governing locks.

        A preliminary read provides the campaign identity needed to preserve the
        campaign-before-outbox lock order used by staging and lifecycle updates.
        The row is then reloaded under ``FOR UPDATE`` and must retain that exact
        identity. Campaign-bound rows additionally require a current active
        campaign, immutable approval proof, and exact version-pinned Delta
        treatment membership before the external side effect can begin.
        """

        discovered = self.get_outbox_by_activation_id(activation_id)
        if discovered is None:
            raise PermissionError("activation disappeared before delivery")
        with self._client.transaction() as conn, conn.cursor() as cur:
            cur.execute(
                BORROWER_DECISION_LOCK,
                {"borrower_id": discovered.borrower_id},
            )
            cur.execute(
                LATEST_BORROWER_DECISION,
                {
                    "borrower_id": discovered.borrower_id,
                    "approval_id": discovered.approval_id,
                },
            )
            latest_decision = cur.fetchone()
            campaign_proof: CampaignActivationProof | None = None
            non_campaign_approval: dict[str, Any] | None = None
            if discovered.campaign_id:
                cur.execute(
                    ACTIVE_CAMPAIGN_APPROVAL_LOCK,
                    {
                        "approval_id": discovered.approval_id,
                        "borrower_id": discovered.borrower_id,
                        "campaign_id": discovered.campaign_id,
                    },
                )
                campaign_row = cur.fetchone()
                if campaign_row is not None:
                    try:
                        campaign_proof = campaign_activation_proof_from_row(
                            dict(campaign_row),
                            approval_id=discovered.approval_id,
                            borrower_id=discovered.borrower_id,
                            campaign_id=discovered.campaign_id,
                        )
                    except PermissionError:
                        campaign_proof = None
            else:
                cur.execute(
                    _NON_CAMPAIGN_APPROVAL_LOCK,
                    {
                        "approval_id": discovered.approval_id,
                        "borrower_id": discovered.borrower_id,
                    },
                )
                approval_row = cur.fetchone()
                non_campaign_approval = dict(approval_row) if approval_row is not None else None
            cur.execute(
                _OUTBOX_BY_ACTIVATION_ID_FOR_UPDATE,
                {"activation_id": activation_id},
            )
            locked_row = cur.fetchone()
            if locked_row is None:
                raise PermissionError("activation disappeared before delivery")
            current = _outbox_from_row(dict(locked_row))
            if not _same_activation_identity(discovered, current):
                raise PermissionError("activation identity changed before delivery")

            block_reason: str | None = None
            candidate = (
                current.destination_type == "salesforce"
                and current.destination_status == "connected"
                and current.status in {"staged", "failed"}
            )
            if candidate and not is_current_approval(
                latest_decision,
                approval_id=current.approval_id or "",
            ):
                block_reason = "approval_not_current"
            elif candidate and current.campaign_id:
                if campaign_proof is None:
                    block_reason = "campaign_not_active_or_proof_invalid"
                elif current.channel != campaign_proof.channel:
                    block_reason = "campaign_approval_channel_mismatch"
                elif current.offer_code != campaign_proof.offer_code:
                    block_reason = "campaign_approval_offer_mismatch"
                else:
                    try:
                        is_treatment_member = campaign_contains_borrower(
                            lead_repo,
                            borrower_id=current.borrower_id,
                            campaign_id=campaign_proof.campaign_id,
                            materialization_id=campaign_proof.materialization_id,
                            delta_version=campaign_proof.delta_version,
                            treatment_fingerprint=campaign_proof.treatment_fingerprint,
                            suppression_policy=campaign_proof.suppression_policy,
                        )
                    except (TypeError, ValueError) as exc:
                        raise PermissionError(CAMPAIGN_ACTIVATION_ERROR) from exc
                    if not is_treatment_member:
                        block_reason = "campaign_borrower_not_treatment_member"
            elif candidate and not _non_campaign_approval_is_valid(
                non_campaign_approval,
                activation=current,
            ):
                block_reason = "approval_proof_invalid"

            delivery = ActivationDeliveryGuard(
                activation=current,
                should_deliver=candidate and block_reason is None,
                _conn=conn,
                block_reason=block_reason,
            )
            if block_reason is not None:
                metadata = dict(current.delivery_metadata or {})
                metadata.update(
                    {
                        "delivered": False,
                        "cancelled_reason": block_reason,
                    }
                )
                delivery.update_delivery_state(
                    activation_id=current.activation_id,
                    status="cancelled",
                    delivery_metadata=metadata,
                )
            yield delivery

    def _validate_existing(
        self,
        existing: ActivationOutboxItem,
        *,
        destination: ActivationDestination,
        payload: ActivationStageRequest,
        approved_offer_code: str,
        approved_campaign_id: str | None,
    ) -> ActivationOutboxItem:
        expected_entity_id = approved_campaign_id or payload.borrower_id
        if (
            existing.borrower_id != payload.borrower_id
            or existing.destination_key != destination.destination_key
            or existing.approval_id != payload.approval_id
            or existing.channel != payload.channel
            or existing.offer_code != approved_offer_code
            or existing.campaign_id != approved_campaign_id
            or existing.entity_id != expected_entity_id
        ):
            raise PermissionError("idempotency key already belongs to a different activation")
        return existing

    def stage_borrower(
        self,
        *,
        borrower: Borrower360,
        destination: ActivationDestination,
        payload: ActivationStageRequest,
        approved_decision: dict[str, Any],
        campaign_proof: CampaignActivationProof | None = None,
        actor: str,
    ) -> ActivationWriteResult:
        _assert_approved_decision_matches(payload, approved_decision)
        approved_offer_code = _approved_offer_code(
            borrower=borrower,
            payload=payload,
            approved_decision=approved_decision,
        )
        approved_campaign_id = _approved_campaign_id(approved_decision)
        if payload.offer_code and payload.offer_code != approved_offer_code:
            raise PermissionError("activation offer_code must match the approved decision")
        if payload.campaign_id and payload.campaign_id != approved_campaign_id:
            raise PermissionError("activation campaign_id must match the approved decision")
        if approved_campaign_id is None:
            if campaign_proof is not None:
                raise PermissionError("campaign proof does not match the approved decision")
        elif campaign_proof is None or campaign_proof.campaign_id != approved_campaign_id:
            raise PermissionError(CAMPAIGN_ACTIVATION_ERROR)
        elif payload.channel != campaign_proof.channel:
            raise PermissionError("activation channel must match campaign approval proof")

        existing = self.outbox_for_request(payload.request_id)
        if existing is not None:
            return ActivationWriteResult(
                activation=self._validate_existing(
                    existing,
                    destination=destination,
                    payload=payload,
                    approved_offer_code=approved_offer_code,
                    approved_campaign_id=approved_campaign_id,
                ),
                audit_event_id=None,
            )
        existing_by_business_key = self.outbox_for_business_key(
            destination_key=destination.destination_key,
            approval_id=payload.approval_id,
            borrower_id=payload.borrower_id,
            channel=payload.channel,
        )
        if existing_by_business_key is not None:
            return ActivationWriteResult(
                activation=self._validate_existing(
                    existing_by_business_key,
                    destination=destination,
                    payload=payload,
                    approved_offer_code=approved_offer_code,
                    approved_campaign_id=approved_campaign_id,
                ),
                audit_event_id=None,
            )

        activation_id = str(uuid4())
        status = "staged" if destination.status == "connected" else "dry_run"
        entity_id = approved_campaign_id or payload.borrower_id
        payload_json = _borrower_payload(
            borrower,
            payload,
            offer_code=approved_offer_code,
            campaign_proof=campaign_proof,
        )
        delivery_metadata = {
            "delivery_mode": "outbox_only",
            "destination_status": destination.status,
            "connector_configured": destination.status == "connected",
        }
        insert_params = {
            "activation_id": activation_id,
            "destination_key": destination.destination_key,
            "entity_type": "borrower",
            "entity_id": entity_id,
            "borrower_id": payload.borrower_id,
            "campaign_id": approved_campaign_id,
            "approval_id": payload.approval_id,
            "offer_code": approved_offer_code,
            "channel": payload.channel,
            "status": status,
            "request_id": payload.request_id,
            "created_by": actor,
            "payload_json": json.dumps(payload_json, sort_keys=True),
            "delivery_metadata": json.dumps(delivery_metadata, sort_keys=True),
        }
        try:
            with self._client.transaction() as conn, conn.cursor() as cur:
                cur.execute(
                    BORROWER_DECISION_LOCK,
                    {"borrower_id": payload.borrower_id},
                )
                cur.execute(
                    LATEST_BORROWER_DECISION,
                    {
                        "borrower_id": payload.borrower_id,
                        "approval_id": payload.approval_id,
                    },
                )
                if not is_current_approval(
                    cur.fetchone(),
                    approval_id=payload.approval_id,
                ):
                    raise PermissionError(
                        "activation approval is no longer the borrower's current decision"
                    )
                if approved_campaign_id is not None:
                    cur.execute(
                        ACTIVE_CAMPAIGN_APPROVAL_LOCK,
                        {
                            "approval_id": payload.approval_id,
                            "borrower_id": payload.borrower_id,
                            "campaign_id": approved_campaign_id,
                        },
                    )
                    campaign_lock = cur.fetchone()
                    if campaign_lock is None:
                        raise PermissionError(CAMPAIGN_ACTIVATION_ERROR)
                    locked_proof = campaign_activation_proof_from_row(
                        dict(campaign_lock),
                        approval_id=payload.approval_id,
                        borrower_id=payload.borrower_id,
                        campaign_id=approved_campaign_id,
                    )
                    if locked_proof != campaign_proof:
                        raise PermissionError(
                            "campaign treatment proof changed before activation staging"
                        )
                cur.execute(_OUTBOX_INSERT, insert_params)
                inserted = cur.fetchone()
                if inserted is None:
                    existing_after_insert = conn.execute(
                        _OUTBOX_BY_REQUEST, {"request_id": payload.request_id}
                    ).fetchone()
                    if existing_after_insert is not None:
                        activation = self._validate_existing(
                            _outbox_from_row(dict(existing_after_insert)),
                            destination=destination,
                            payload=payload,
                            approved_offer_code=approved_offer_code,
                            approved_campaign_id=approved_campaign_id,
                        )
                        return ActivationWriteResult(activation=activation, audit_event_id=None)
                    existing_after_business_conflict = conn.execute(
                        _OUTBOX_BY_BUSINESS_KEY,
                        {
                            "destination_key": destination.destination_key,
                            "approval_id": payload.approval_id,
                            "borrower_id": payload.borrower_id,
                            "channel": payload.channel,
                        },
                    ).fetchone()
                    if existing_after_business_conflict is None:
                        raise RuntimeError("activation outbox insert returned no row")
                    activation = self._validate_existing(
                        _outbox_from_row(dict(existing_after_business_conflict)),
                        destination=destination,
                        payload=payload,
                        approved_offer_code=approved_offer_code,
                        approved_campaign_id=approved_campaign_id,
                    )
                    return ActivationWriteResult(activation=activation, audit_event_id=None)
                activation = _outbox_from_row(
                    dict(
                        conn.execute(
                            _OUTBOX_SELECT_BASE.format(where_clause="WHERE o.activation_id = %(activation_id)s"),
                            {"activation_id": activation_id, "limit": 1},
                        ).fetchone()
                        or {}
                    )
                )
                event = write_audit_event_in_transaction(
                    conn,
                    actor=actor,
                    action="activation.stage",
                    entity_type="activation",
                    entity_id=activation_id,
                    payload_json={
                        "activation_id": activation_id,
                        "destination_key": destination.destination_key,
                        "destination_type": destination.destination_type,
                        "activation_status": status,
                        "borrower_id": payload.borrower_id,
                        "campaign_id": approved_campaign_id,
                        "campaign_treatment_fingerprint": (
                            campaign_proof.treatment_fingerprint
                            if campaign_proof is not None
                            else None
                        ),
                        "approval_id": payload.approval_id,
                        "offer_code": approved_offer_code,
                        "channel": payload.channel,
                        "request_id": payload.request_id,
                    },
                    event_type="ACTIVATION_STAGE",
                    subject_clip=borrower.clip_id,
                    request_id=payload.request_id,
                )
        except (AuditMetadataViolation, AuditPIIError, PermissionError):
            raise
        except Exception as exc:  # noqa: BLE001 -- normalize raw transaction/client errors
            raise LakebaseError("Lakebase activation staging failed") from exc
        return ActivationWriteResult(activation=activation, audit_event_id=event.event_id)
