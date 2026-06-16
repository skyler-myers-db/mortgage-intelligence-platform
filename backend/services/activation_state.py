"""Lakebase-backed governed activation/writeback outbox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.schemas.activation import (
    ActivationDestination,
    ActivationOutboxItem,
    ActivationStageRequest,
)
from backend.schemas.lead import Borrower360
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_store import AuditMetadataViolation, AuditPIIError
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
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
  AND o.status IN ('dry_run','staged','delivered')
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

_APPROVAL_BY_ID = """
SELECT approval_id, borrower_id, action, actor_email, offer_code, campaign_id, decided_at
FROM mip_app.approvals
WHERE approval_id = %(approval_id)s::uuid
LIMIT 1
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


def _borrower_payload(
    borrower: Borrower360,
    payload: ActivationStageRequest,
    *,
    offer_code: str,
) -> dict[str, Any]:
    return {
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

    def update_delivery_state(
        self,
        *,
        activation_id: str,
        status: str,
        delivery_metadata: dict[str, Any],
    ) -> ActivationOutboxItem | None:
        """Persist a delivery outcome on an existing outbox row.

        Used by the Salesforce delivery adapter AFTER a real REST call.
        ``status`` is one of the ActivationOutboxStatus values ('delivered'
        / 'failed'); ``delivery_metadata`` is the honest JSON record of what
        actually happened. Returns the refreshed row or None if it vanished.
        """
        self._client.execute(
            _OUTBOX_UPDATE_DELIVERY,
            {
                "activation_id": activation_id,
                "status": status,
                "delivery_metadata": json.dumps(delivery_metadata, sort_keys=True),
            },
        )
        return self.get_outbox_by_activation_id(activation_id)

    def approved_decision_for(self, *, approval_id: str, borrower_id: str) -> dict[str, Any] | None:
        row = self._client.fetchone(_APPROVAL_BY_ID, {"approval_id": approval_id})
        if not row:
            return None
        if str(row.get("borrower_id")) != borrower_id or row.get("action") != "approve":
            return None
        return dict(row)

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
        payload_json = _borrower_payload(borrower, payload, offer_code=approved_offer_code)
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
