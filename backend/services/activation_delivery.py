"""Salesforce delivery adapter for the activation outbox (Feature B).

The activation outbox stages approved borrower outreach. This module is
the delivery path for the ``salesforce`` destination type: it maps a
staged outbox row to a Salesforce ``Task`` (or the configured sObject) and
creates it via the real REST client.

Honesty contract (commercial product -- do not weaken):

- We ONLY mark a row ``delivered`` after a real External-ID upsert returns or
  resolves a Salesforce record id. Any other outcome is recorded truthfully:
  * unconfigured (no client) -> status UNCHANGED, metadata
    ``{delivered: false, reason: "salesforce_not_configured"}``.
  * REST/breaker failure -> status ``failed``, metadata
    ``{delivered: false, error: ...}``.
- The Salesforce payload carries NO PII. The borrower id is already masked
  (``B-...``); we send only the masked id, offer code, channel, and the
  request_id (for idempotency / dedupe on the Salesforce side). No name,
  address, email, phone, or property identifier leaves MIP here.
"""
from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from backend.schemas.activation import ActivationOutboxItem
from backend.services.observability import emit
from backend.services.resilience import DependencyDownError
from backend.services.salesforce_client import (
    SalesforceClientError,
    get_salesforce_client,
)

if TYPE_CHECKING:
    from backend.services.repositories import LeadRepository
    from backend.services.salesforce_client import ResilientSalesforceClient

log = logging.getLogger(__name__)

REASON_NOT_CONFIGURED = "salesforce_not_configured"


class GovernedDeliveryGuard(Protocol):
    activation: ActivationOutboxItem
    should_deliver: bool

    def update_delivery_state(
        self,
        *,
        activation_id: str,
        status: str,
        delivery_metadata: dict[str, Any],
    ) -> ActivationOutboxItem | None: ...


class GovernedDeliveryStore(Protocol):
    def delivery_guard(
        self,
        *,
        activation_id: str,
        lead_repo: LeadRepository,
    ) -> AbstractContextManager[GovernedDeliveryGuard]: ...


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of a delivery attempt.

    ``attempted`` is False on the unconfigured no-op path; ``delivered`` is
    True only when a real Salesforce record id came back. ``activation`` is
    the refreshed outbox row when the status changed, else the input row.
    """

    delivered: bool
    attempted: bool
    status: str
    delivery_metadata: dict[str, Any]
    activation: ActivationOutboxItem
    salesforce_id: str | None = None


def _task_fields(row: ActivationOutboxItem, sobject: str) -> dict[str, Any]:
    """Map an outbox row to Salesforce sObject fields -- NO PII.

    Default sObject is ``Task``. We use only governed, non-PII values: the
    masked borrower id, offer code, channel, and request_id. The request_id
    is embedded in the Description so a Salesforce-side flow can dedupe.
    """
    offer = row.offer_code or "nurture"
    channel = row.channel or "email"
    subject = f"MIP outreach: {offer} via {channel}"
    description = (
        f"Mortgage Intelligence Platform activation.\n"
        f"Masked borrower: {row.borrower_id}\n"
        f"Offer: {offer}\n"
        f"Channel: {channel}\n"
        f"Request id (idempotency): {row.request_id}\n"
        f"Activation id: {row.activation_id}"
    )
    fields: dict[str, Any] = {"Subject": subject, "Description": description}
    # Status is a standard Task picklist; "Not Started" is a safe default
    # that does not assume the customer's custom picklist values.
    if sobject == "Task":
        fields["Status"] = "Not Started"
    return fields


def deliver_to_salesforce(
    activation_id: str,
    *,
    store: GovernedDeliveryStore,
    lead_repo: LeadRepository,
    sobject: str | None = None,
    client: ResilientSalesforceClient | None = None,
) -> DeliveryResult:
    """Attempt one governed Salesforce delivery from the locked outbox row.

    The public adapter accepts only an activation id. The state store must
    reload and lock the real row, prove its approval/campaign boundary, and
    recheck exact Delta treatment membership before ``should_deliver`` can be
    true. The guard is also the only object allowed to persist an outcome.
    """
    with store.delivery_guard(
        activation_id=activation_id,
        lead_repo=lead_repo,
    ) as delivery:
        row = delivery.activation
        if not delivery.should_deliver:
            return DeliveryResult(
                delivered=row.status == "delivered",
                attempted=False,
                status=row.status,
                delivery_metadata=dict(row.delivery_metadata or {}),
                activation=row,
                salesforce_id=(
                    str((row.delivery_metadata or {}).get("salesforce_id") or "") or None
                ),
            )
        return _deliver_locked_to_salesforce(
            row,
            state_writer=delivery,
            sobject=sobject,
            client=client,
        )


def _deliver_locked_to_salesforce(
    row: ActivationOutboxItem,
    *,
    state_writer: GovernedDeliveryGuard,
    sobject: str | None,
    client: ResilientSalesforceClient | None,
) -> DeliveryResult:
    """Perform the remote side effect while the governed delivery guard is held."""
    from backend.config.settings import settings

    resolved_sobject = sobject or settings.salesforce_sobject
    external_id_field = str(settings.salesforce_external_id_field or "").strip()

    sf_client = client if client is not None else get_salesforce_client()

    if sf_client is None or not external_id_field:
        # Honest degraded path: never claim a delivery happened.
        metadata = {"delivered": False, "reason": REASON_NOT_CONFIGURED}
        emit(
            log,
            "salesforce_delivery_skipped",
            dependency="salesforce",
            activation_id=row.activation_id,
            reason=REASON_NOT_CONFIGURED,
        )
        return DeliveryResult(
            delivered=False,
            attempted=False,
            status=row.status,
            delivery_metadata=metadata,
            activation=row,
        )

    try:
        created = sf_client.upsert_record(
            resolved_sobject,
            external_id_field,
            row.activation_id,
            _task_fields(row, resolved_sobject),
        )
    except (SalesforceClientError, DependencyDownError) as exc:
        error_detail = _error_detail(exc)
        metadata = {
            "delivered": False,
            "error": error_detail,
            "sobject": resolved_sobject,
        }
        emit(
            log,
            "salesforce_delivery_failed",
            level=logging.WARNING,
            dependency="salesforce",
            activation_id=row.activation_id,
            exc_type=type(exc).__name__,
        )
        refreshed = state_writer.update_delivery_state(
            activation_id=row.activation_id,
            status="failed",
            delivery_metadata=metadata,
        )
        return DeliveryResult(
            delivered=False,
            attempted=True,
            status="failed",
            delivery_metadata=metadata,
            activation=refreshed or row,
        )

    salesforce_id = str(created.get("id"))
    metadata = {
        "delivered": True,
        "salesforce_id": salesforce_id,
        "sobject": resolved_sobject,
    }
    emit(
        log,
        "salesforce_delivery_ok",
        dependency="salesforce",
        activation_id=row.activation_id,
        salesforce_id=salesforce_id,
        sobject=resolved_sobject,
    )
    refreshed = state_writer.update_delivery_state(
        activation_id=row.activation_id,
        status="delivered",
        delivery_metadata=metadata,
    )
    return DeliveryResult(
        delivered=True,
        attempted=True,
        status="delivered",
        delivery_metadata=metadata,
        activation=refreshed or row,
        salesforce_id=salesforce_id,
    )


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, SalesforceClientError):
        parts = [type(exc).__name__]
        if exc.status_code is not None:
            parts.append(f"http_{exc.status_code}")
        if exc.sf_error_code:
            parts.append(exc.sf_error_code)
        return " ".join(parts)
    if isinstance(exc, DependencyDownError):
        return f"DependencyDownError:{exc.kind}"
    return type(exc).__name__


__all__ = ["DeliveryResult", "REASON_NOT_CONFIGURED", "deliver_to_salesforce"]
