"""Whether a release of the profile's tier may stand on its signed blue (M2-PR7b).

Every site that judges blue for a release (the core pre-stop record clause,
the core step-0a proof and restore in tools/databricks/core_blue_restore.py,
and the pre-activation proof in tools/databricks/blue_restore_proof.py) calls
``assert_blue_tier`` before it proves anything else. A blue is restored only
by its own tier's proof, so the profile's tier (``MIP_AGENTIC_TIER``) and the
record's must agree:

* **same tier**: nothing to acknowledge. Client #1's cutover run 1 is this
  case: blue was adopted as a core record and the release is core.
* **an agentic blue under a core profile** (a v5 or v6 record, or a v7
  agentic one) always refuses ``blue_tier_mismatch``, and no ack lifts it:
  restoring an agentic blue needs its Gateway resource proof and the agent
  proxy's boundary, so the agentic identities the core profile does not hold.
  Release that install in the agentic tier.
* **a core blue under an agentic profile** refuses ``blue_tier_mismatch``
  unless the operator gives the exact ``tier_transition`` digest ack
  (tools/databricks/operation_ack.py) over this App, this blue's deployment,
  this exact signed record version and the two tiers. With it, blue is still
  verified and restored by the core proof (the agentic profile holds the four
  core identities); the ack never stands in for a proof. The agentic release
  path still refuses a v7 blue before the lease, as before this module: the
  one-time core-to-agentic transition (client #1's run 2) is wired by M4-PR1.

No record, or a retired adopt-live tombstone, has no blue: there is nothing to
judge, and the caller's own absence handling applies.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from tools.databricks.app_rollback_record_contract import V7_RECORD_VERSION
from tools.databricks.app_rollback_record_v7 import tier_of_profile
from tools.databricks.operation_ack import AckRefused, ack_digest, require_ack

PURPOSE = "tier_transition"
CODE = "blue_tier_mismatch"


class BlueTierRefused(RuntimeError):
    """The profile's tier may not stand on this blue; ``code`` is reviewed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def signed_record_sha256(record: Mapping[str, Any]) -> str:
    """The sha256 of one signed record version, canonical JSON (adopt_live.record_digest's)."""

    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _sha256_text(canonical)


def current_blue(record: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The record a release protects: None for an empty slot or a retired tombstone."""

    if record is None or (
        record.get("version") == V7_RECORD_VERSION and record.get("provenance") == "retired"
    ):
        return None
    return record


def record_tier(record: Mapping[str, Any]) -> str:
    """A v7 record's own tier; a v5 or v6 record carries the Gateway contract: agentic."""

    if record.get("version") == V7_RECORD_VERSION:
        return str(record.get("tier"))
    return "agentic"


def tier_transition_plan(
    *, app_name: str, record: Mapping[str, Any], agentic_tier: str
) -> dict[str, str]:
    """The canonical plan a ``tier_transition`` ack covers: this blue, this version, both tiers."""

    return {
        "app_name": app_name,
        "blue_deployment_sha256": _sha256_text(str(record["deployment_id"])),
        "blue_record_sha256": signed_record_sha256(record),
        "from_tier": record_tier(record),
        "to_tier": tier_of_profile(agentic_tier),
    }


def assert_blue_tier(
    record: Mapping[str, Any],
    *,
    app_name: str,
    agentic_tier: str,
    tier_transition_ack: str | None = None,
) -> str:
    """``same`` or ``transition``; raises BlueTierRefused when this profile may not use blue."""

    profile = tier_of_profile(agentic_tier)
    blue = record_tier(record)
    if blue == profile:
        return "same"
    if profile == "core":
        raise BlueTierRefused(
            CODE,
            f"the signed blue is a v{record.get('version')} {blue}-tier record and the profile's "
            "MIP_AGENTIC_TIER is off: an agentic blue is restored only by its own Gateway and "
            "agent-proxy proofs, which need the agentic identities a core profile does not hold, "
            "so no ack lifts this; release this install in the agentic tier",
        )
    plan = tier_transition_plan(app_name=app_name, record=record, agentic_tier=agentic_tier)
    try:
        require_ack(PURPOSE, plan, tier_transition_ack)
    except AckRefused as exc:
        raise BlueTierRefused(
            CODE,
            f"the signed blue is a core-tier record and the profile's MIP_AGENTIC_TIER is on "
            f"({exc.code}): a release over the other tier's blue needs the exact tier_transition "
            f"ack {ack_digest(PURPOSE, plan)} for this blue and this record version",
        ) from exc
    return "transition"
