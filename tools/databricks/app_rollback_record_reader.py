"""Read the current signed App rollback record, any version from v5 to v7 (M2-PR7).

v7 shares v6's current-record slot (tools/databricks/app_rollback_record_v7.py),
so one key always holds the one current record and a v6 capture replaces an
adopted v7 in place, never shadowed by it.

* ``signed_record`` returns the verified current record of any version, a v7
  retired tombstone included, or None when the slot is empty
  (``app_rollback_record_contract.load_signed_record``).
* ``current_record`` is the v5/v6 reader the agentic release path calls
  (``app_rollback_record_contract._load_record``): an empty slot and a v7
  retired tombstone both read as absent, with the remedy that fits each (a
  tombstone's is a re-adoption: the one-time unsigned rebase refuses over any
  record, ``app_rollback_bootstrap_gate``), and any other v7 record refuses by
  name (``V7_REFUSAL``) unless the caller is tier-aware (``accept_v7``). The
  tombstone's re-adoption is the core tier's only (adopt-live refuses the
  agentic tier), so its remedy names that tier in either tier.

Every helper is read through the contract module, so its monkeypatches apply.
"""

from __future__ import annotations

import json
from typing import Any

from tools.databricks import app_rollback_record_contract as contract

ABSENT = "no server-owned last-good App rollback contract exists"
_BOOTSTRAP = f"{ABSENT}; run the explicit bootstrap command before upgrading this existing App"
# Tier-accurate in either tier (this reader is not told the run's tier): an
# unadopt runs in both, but adopt-live signs the core tier only.
_TOMBSTONE = (
    f"{ABSENT} (the slot holds a retired adopt-live tombstone): re-adopt in the core tier, "
    "with ./scripts/deploy.sh --phase adopt from a profile whose MIP_AGENTIC_TIER is off, "
    "since adopt-live refuses the agentic tier (tier_agentic, until the estate inventory of "
    "M4-PR1) and the one-time unsigned rebase refuses over any signed record, a tombstone "
    "included"
)


def signed_record(
    workspace: Any, *, app_name: str, scope: str, expected_lakebase_instance: str
) -> dict[str, Any] | None:
    """The verified current record of any version (a v7 tombstone included), or None."""

    contract.assert_owned_app_rollback_scope(workspace, app_name=app_name, scope=scope)
    raw = contract._secret_value(workspace, scope=scope, key=contract._record_key(app_name))
    if raw is None:
        raw = contract._secret_value(
            workspace, scope=scope, key=contract._legacy_record_key(app_name)
        )
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("App rollback contract is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("App rollback contract is not an object")
    contract._verify_record_attestation(decoded)
    return contract._validated_record(
        decoded, app_name=app_name, expected_lakebase_instance=expected_lakebase_instance
    )


def current_record(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    expected_lakebase_instance: str,
    accept_v7: bool = False,
) -> dict[str, Any]:
    """The v5/v6 reader: absent, a tombstone or an unaccepted v7 record raises RuntimeError."""

    record = signed_record(
        workspace,
        app_name=app_name,
        scope=scope,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    if record is None:
        raise RuntimeError(_BOOTSTRAP)
    if record["version"] == contract.V7_RECORD_VERSION and record["provenance"] == "retired":
        raise RuntimeError(_TOMBSTONE)  # a tombstone holds no rollback target
    if record["version"] == contract.V7_RECORD_VERSION and not accept_v7:
        raise RuntimeError(
            f"the signed App rollback record is a v7 {record['tier']}-tier record; "
            f"{contract.V7_REFUSAL} (the tier-aware release path reads v7)"
        )
    return record
