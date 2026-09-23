"""Decision receipt read model over the audit ledger.

``build_decision_receipt`` projects one persisted ``AuditEvent`` (the row
an approve / reject / hold wrote) onto the closed ``DecisionReceipt``
allowlist. It reads the stored row only: nothing here echoes a request
body, and a metadata key that is not named below cannot reach the wire.
"""

from __future__ import annotations

import re
from typing import Any

from backend.schemas.audit import AuditEvent
from backend.schemas.audit_receipt import DecisionOutcome, DecisionReceipt
from backend.schemas.common import validate_public_borrower_id
from backend.services.databricks_sql_helpers import qualify
from backend.services.scoring import NBO_PRODUCT_LABELS, offer_display_label

# Lakebase ids are UUIDs; the in-process test ledger issues ``evt-<hex>``.
# Either way an audit id is an opaque token from this closed alphabet, so a
# path parameter outside it is answered "not found" before any lookup.
AUDIT_EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")

_DECISION_BY_EVENT_TYPE: dict[str, DecisionOutcome] = {
    "APPROVE": "approved",
    "OUTREACH_APPROVE": "approved",
    "OUTREACH_REJECT": "rejected",
    "REJECT": "rejected",
    "OUTREACH_HOLD": "held",
    "HOLD": "held",
}
_DECISION_BY_ACTION: dict[str, DecisionOutcome] = {
    "outreach.approve": "approved",
    "outreach.reject": "rejected",
    "outreach.hold": "held",
}


def is_valid_audit_event_id(value: str) -> bool:
    return AUDIT_EVENT_ID_PATTERN.fullmatch(value) is not None


def decision_outcome_for(event: AuditEvent) -> DecisionOutcome | None:
    """Map a ledger row to its decision, or ``None`` for non-decision rows."""

    event_type = (event.event_type or "").strip().upper()
    if event_type in _DECISION_BY_EVENT_TYPE:
        return _DECISION_BY_EVENT_TYPE[event_type]
    return _DECISION_BY_ACTION.get((event.action or "").strip().lower())


def decision_evidence_assets(
    offer_code: str | None,
    *,
    has_heloc_propensity_trigger: bool = False,
) -> list[str]:
    """Unity Catalog objects the offer branch consulted.

    Mirrors the source registry the Offer Orchestrator cites for the same
    branch (``_sources_for`` in the offers router); a unit test pins the two
    lists equal so the receipt never tells a different lineage story than
    the recommendation the approver saw.

    This is the receipt's asset list for every decision row written today:
    the outreach approve / reject writes store ``offer_code`` (approve also
    ``decision_inputs``) but no ``evidence_assets`` key, so the list is
    derived from those stored values, not read from a stored list. The UI
    labels it as the recorded offer branch's assets accordingly.
    """

    code = (offer_code or "").strip().lower()
    if not code:
        return []
    base = [qualify("gold", "fn_next_best_offer")]
    if code == "purchase":
        base.append(qualify("silver", "listing_activity"))
    if code in {"refi_plus_heloc", "refi", "retention"}:
        base.append(qualify("gold", "fn_rate_spread"))
        base.append(qualify("gold", "fn_in_the_money"))
    if code in {"heloc", "cash_out", "refi_plus_heloc"}:
        base.append(qualify("gold", "fn_rate_spread"))
    if code in {"heloc", "refi_plus_heloc"} and has_heloc_propensity_trigger:
        base.append(qualify("silver", "heloc_propensity"))
    base.append(qualify("gold", "fn_lead_score"))
    return list(dict.fromkeys(base))


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _stored_asset_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _heloc_trigger(decision_inputs: Any) -> bool:
    if not isinstance(decision_inputs, dict):
        return False
    return decision_inputs.get("has_heloc_propensity_trigger") is True


def _public_borrower_id(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return validate_public_borrower_id(text)
    except ValueError:
        return None


def build_decision_receipt(event: AuditEvent) -> DecisionReceipt | None:
    """Project a decision row onto the receipt allowlist; ``None`` otherwise."""

    decision = decision_outcome_for(event)
    if decision is None:
        return None
    metadata: dict[str, Any] = dict(event.payload_json or {})
    offer_code = _text(metadata.get("offer_code"))
    offer_label = (
        offer_display_label(offer_code, NBO_PRODUCT_LABELS.get(offer_code))
        if offer_code is not None
        else None
    )
    # A stored asset list wins if a writer ever records one; no decision
    # write does today, so this derives from the stored offer branch.
    evidence_assets = _stored_asset_paths(metadata.get("evidence_assets")) or (
        decision_evidence_assets(
            offer_code,
            has_heloc_propensity_trigger=_heloc_trigger(metadata.get("decision_inputs")),
        )
    )
    approval_id = (
        _text(event.entity_id)
        if (event.entity_type or "").strip().lower() == "approval"
        else _text(metadata.get("approval_id"))
    )
    return DecisionReceipt(
        audit_event_id=event.event_id,
        event_type=(event.event_type or event.action or "").strip().upper(),
        decision=decision,
        approval_id=approval_id,
        borrower_id=_public_borrower_id(metadata.get("borrower_id")),
        offer_code=offer_code,
        offer_label=offer_label,
        campaign_id=_text(metadata.get("campaign_id")),
        variant_name=_text(metadata.get("variant_name")),
        channel=_text(metadata.get("channel")),
        rationale_code=_text(metadata.get("rationale_code")),
        copy_generation_id=_text(metadata.get("draft_generation_id")),
        copy_hash=_text(metadata.get("draft_response_hash")),
        approver=event.actor,
        request_id=_text(event.request_id),
        correlation_id=_text(event.correlation_id),
        created_at=event.created_at,
        evidence_ids=[str(item) for item in (event.evidence_ids or []) if str(item).strip()],
        evidence_assets=evidence_assets,
    )
