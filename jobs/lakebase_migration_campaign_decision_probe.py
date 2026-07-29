"""Exact campaign-decision proof helpers for the Lakebase migration probe."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from uuid import uuid4

_APPROVAL_INSERT = """
    INSERT INTO mip_app.approvals (
        approval_id, campaign_id, variant_name, channel,
        borrower_id, action, actor_email, request_id,
        decision_intent, decision_payload_hash
    ) VALUES (%s, %s, 'Integrity proof', 'email', %s,
              'approve', %s, %s, %s, %s)
"""


def _campaign_decision_intent(
    *,
    action: str,
    actor: str,
    borrower_id: str,
    campaign_id: object,
    variant_name: str,
    channel: str,
    owner_email: str,
    treatment_fingerprint: str,
) -> tuple[str, str]:
    intent = json.dumps(
        {
            "action": action,
            "actor": actor,
            "borrower_id": borrower_id,
            "campaign_id": str(campaign_id),
            "variant_name": variant_name,
            "channel": channel,
            "offer_code": None,
            "campaign_owner_email": owner_email,
            "campaign_treatment_fingerprint": treatment_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return intent, hashlib.sha256(intent.encode("utf-8")).hexdigest()


def _run_campaign_decision_negative_probes(
    cur: object,
    *,
    campaign_id: object,
    actor: str,
    borrower_id: str,
    expect_rejection: Callable[..., None],
) -> tuple[str, str]:
    """Reject stale lifecycle, treatment, owner, row, and digest evidence."""

    valid_intent, valid_hash = _campaign_decision_intent(
        action="approve",
        actor=actor,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
        variant_name="Integrity proof",
        channel="email",
        owner_email=actor,
        treatment_fingerprint="4" * 64,
    )

    def reject(savepoint: str, intent: str, intent_hash: str) -> None:
        expect_rejection(
            cur,
            savepoint=savepoint,
            statement=_APPROVAL_INSERT,
            params=(
                uuid4(),
                campaign_id,
                borrower_id,
                actor,
                str(uuid4()),
                intent,
                intent_hash,
            ),
            expected_sqlstates=("23514",),
        )

    cur.execute(  # type: ignore[attr-defined]
        "UPDATE mip_app.campaigns SET status = 'archived' WHERE campaign_id = %s",
        (campaign_id,),
    )
    reject("probe_approval_after_campaign_archive", valid_intent, valid_hash)
    cur.execute(  # type: ignore[attr-defined]
        "UPDATE mip_app.campaigns SET status = 'approved' WHERE campaign_id = %s",
        (campaign_id,),
    )

    stale_treatment_intent, stale_treatment_hash = _campaign_decision_intent(
        action="approve",
        actor=actor,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
        variant_name="Integrity proof",
        channel="email",
        owner_email=actor,
        treatment_fingerprint="7" * 64,
    )
    reject(
        "probe_approval_treatment_fingerprint_drift",
        stale_treatment_intent,
        stale_treatment_hash,
    )

    changed_owner = f"changed-{uuid4().hex[:12]}@integrity.invalid"
    cur.execute(  # type: ignore[attr-defined]
        "UPDATE mip_app.campaigns SET owner_email = %s WHERE campaign_id = %s",
        (changed_owner, campaign_id),
    )
    reject("probe_approval_campaign_owner_drift", valid_intent, valid_hash)
    cur.execute(  # type: ignore[attr-defined]
        "UPDATE mip_app.campaigns SET owner_email = %s WHERE campaign_id = %s",
        (actor, campaign_id),
    )

    mismatched_row_intent, mismatched_row_hash = _campaign_decision_intent(
        action="approve",
        actor=actor,
        borrower_id="B-9999999999999",
        campaign_id=campaign_id,
        variant_name="Integrity proof",
        channel="email",
        owner_email=actor,
        treatment_fingerprint="4" * 64,
    )
    reject(
        "probe_approval_row_intent_mismatch",
        mismatched_row_intent,
        mismatched_row_hash,
    )
    reject("probe_approval_intent_hash_mismatch", valid_intent, "9" * 64)
    return valid_intent, valid_hash
