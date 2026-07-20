"""Durable verification for server-generated borrower outreach drafts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.schemas.offer import OutreachDraft
from backend.services.lakebase import LakebaseClient

_GENERATED_OUTREACH_DRAFT_LOOKUP = """
SELECT generation_id, audit_event_id, actor_email, borrower_id, campaign_id,
       variant_name, channel, offer_code, generation_mode, response_hash, response_json
FROM mip_app.generated_outreach_drafts
WHERE generation_id = %(generation_id)s
  AND actor_email = %(actor_email)s
  AND borrower_id = %(borrower_id)s
LIMIT 1
"""


class GeneratedOutreachDraftProofError(ValueError):
    """Raised when a requested generated-draft artifact is absent or invalid."""


def outreach_draft_response_hash(response: OutreachDraft) -> str:
    """Return the canonical hash bound into a generated outreach response."""

    payload = response.model_dump(mode="json", exclude={"response_hash"})
    if response.campaign_id is None and response.variant_name is None:
        # Preserve verification of campaign-less proofs emitted before these
        # fields existed. Bound drafts always retain all three fields.
        payload.pop("campaign_id", None)
        payload.pop("variant_name", None)
        payload.pop("campaign_treatment_fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_verified_generated_outreach_draft(
    lakebase: LakebaseClient,
    *,
    generation_id: str,
    response_hash: str,
    actor: str,
    borrower_id: str,
    campaign_binding: tuple[str | None, str | None] | None = None,
) -> OutreachDraft:
    """Load an actor-owned draft and verify every duplicated durable field."""

    row = lakebase.fetchone(
        _GENERATED_OUTREACH_DRAFT_LOOKUP,
        {
            "generation_id": generation_id,
            "actor_email": actor,
            "borrower_id": borrower_id,
        },
    )
    if row is None:
        raise GeneratedOutreachDraftProofError("generated draft proof was not found")
    stored_json: Any = row.get("response_json")
    if isinstance(stored_json, str):
        try:
            stored_json = json.loads(stored_json)
        except json.JSONDecodeError as exc:
            raise GeneratedOutreachDraftProofError("generated draft response is invalid") from exc
    if not isinstance(stored_json, dict):
        raise GeneratedOutreachDraftProofError("generated draft response is invalid")
    try:
        generated = OutreachDraft.model_validate(stored_json)
    except ValueError as exc:
        raise GeneratedOutreachDraftProofError("generated draft response is invalid") from exc

    row_campaign_id = str(row["campaign_id"]) if row.get("campaign_id") is not None else None
    row_variant_name = str(row["variant_name"]) if row.get("variant_name") is not None else None
    exact_match = (
        str(row.get("generation_id") or "") == generated.generation_id == generation_id
        and bool(str(row.get("audit_event_id") or ""))
        and str(row.get("actor_email") or "") == actor
        and str(row.get("borrower_id") or "") == generated.borrower_id == borrower_id
        and row_campaign_id == generated.campaign_id
        and row_variant_name == generated.variant_name
        and str(row.get("channel") or "") == generated.channel
        and str(row.get("offer_code") or "") == generated.offer_code
        and str(row.get("generation_mode") or "") == generated.generation_mode
        and str(row.get("response_hash") or "")
        == generated.response_hash
        == response_hash
        == outreach_draft_response_hash(generated)
    )
    if campaign_binding is not None:
        exact_match = exact_match and campaign_binding == (
            generated.campaign_id,
            generated.variant_name,
        )
    if not exact_match:
        raise GeneratedOutreachDraftProofError("generated draft proof does not match")
    return generated
