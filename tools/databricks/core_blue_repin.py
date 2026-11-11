"""Re-pin a v7 core record onto the deployment ``apps start`` created (M0-PR8 over M2-PR7b).

Spike S7 recorded how a start behaves (tools/databricks/app_start_deployment.py):
it creates a NEW deployment from the CURRENT bytes of the App's default
source, with the newest deployment's env-name set. So a start of a core blue
needs what a v5/v6 blue's start gets from ``app_deployment_repin``: prove the
start's deployment is blue and sign the record onto it. ``repin_core_start``
does that with the v7 core record's own proofs:

1. the record is the signed core blue (the tier gate, ``load_core_blue``) and
   names the expected blue deployment (or already names the start deployment
   as a re-pin of it: an idempotent rerun);
2. ``prove_start_deployment``: the start is SUCCEEDED, from blue's own
   source folder (the record's ``source_code_path``), its ``/src/`` snapshot
   hashes to the record's signed ``source_tree_sha256`` (a caller's digest,
   the window journal's, must be that one: the re-signed record keeps it, so
   it never names a snapshot it does not certify), and its env-name set is
   the record's ``env_names``;
3. the start is what the App runs and would rerun (``last_deployment_is``:
   nothing pending, the active and newest deployment of any state, SUCCEEDED,
   the default source its own);
4. the App's live bindings are the record's own, or exactly the contract a
   WR1 window applied (never one that rebinds the Genie space the record
   names): blue restarted on those, so the re-pinned record names them. The
   core resource proof and the service-principal identity hold against them
   (never the Gateway resource proof);
5. authenticated health pinned to the start deployment, before any write;
6. under the held lease, the record's next version (same provenance and ack,
   ``predecessor_deployment_id`` blue), read back exactly; health again.

A v7 agentic record is refused by the tier gate (M4-PR1). Every refusal is a
``CoreBlueRefused`` or a ``StartProofError`` with a reviewed code; no message
names an id, a path or a value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tools.databricks.app_rollback_record_contract import _text
from tools.databricks.app_rollback_record_v7 import V7RecordError, _genie_space_of
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract_digest,
    validated_app_resource_contract,
)
from tools.databricks.app_start_deployment import (
    last_deployment_is,
    prove_start_deployment,
    tree_digest,
)
from tools.databricks.core_blue import (
    CoreBlueRefused,
    Health,
    assert_identity,
    live_core_bindings,
    load_core_blue,
    require_core_blue,
    resign,
    verify_core_blue,
)

PREDECESSOR_FIELD = "predecessor_deployment_id"


def _assert_start_is_current(workspace: Any, *, app_name: str, deployment_id: str) -> None:
    if not last_deployment_is(workspace, app_name=app_name, deployment_id=deployment_id):
        raise CoreBlueRefused(
            "repin_unproven",
            "the start deployment is not the App's active, newest deployment with nothing pending",
        )


def _bindings(
    workspace: Any,
    record: Mapping[str, Any],
    *,
    app_name: str,
    applied: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    """The live core bindings, when they are the record's own or the window's applied set."""

    live = live_core_bindings(workspace, app_name=app_name)
    if live == validated_app_resource_contract(record["app_resources"]):
        return live
    if applied is None or live != validated_app_resource_contract(applied):
        raise CoreBlueRefused(
            "resource_drift",
            "the App's live bindings are neither the signed core record's nor the window's applied set",
        )
    try:
        same_space = _genie_space_of(live) == record["genie_space_id"]
    except V7RecordError as exc:
        raise CoreBlueRefused("resource_drift", str(exc)) from exc
    if not same_space:
        # Blue's restore payload and its health name the record's space: never re-bound here.
        raise CoreBlueRefused("resource_drift", "the window's bindings rebind the governed Genie space")
    return live


def repin_core_start(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    deployment_id: str,
    expected_rollback_deployment_id: str,
    health: Health,
    lease: Callable[[], None],
    agentic_tier: str = "off",
    tier_transition_ack: str | None = None,
    expected_tree_sha256: str | None = None,
    applied_app_resources: list[dict[str, object]] | None = None,
    digest: Callable[[Any, str], str] = tree_digest,
) -> dict[str, Any]:
    """Prove the start deployment is the core blue, then sign the record onto it; return it."""

    start_id = deployment_id.strip()
    expected_blue = expected_rollback_deployment_id.strip()
    if not start_id or not expected_blue:
        raise CoreBlueRefused("usage", "re-pin needs the start deployment id and the expected blue id")
    record = require_core_blue(
        load_core_blue(
            workspace,
            app_name=app_name,
            scope=scope,
            lakebase_instance=lakebase_instance,
            agentic_tier=agentic_tier,
            tier_transition_ack=tier_transition_ack,
        )
    )
    if record["deployment_id"] == start_id and record.get(PREDECESSOR_FIELD) == expected_blue:
        # A rerun after a crash between the save and the window close.
        assert_identity(workspace, record, app_name=app_name)
        _assert_start_is_current(workspace, app_name=app_name, deployment_id=start_id)
        health(record, start_id)
        return record
    if record["deployment_id"] != expected_blue:
        raise CoreBlueRefused("record_changed", "the signed core record no longer names the window's blue")
    if start_id == expected_blue:
        raise CoreBlueRefused("repin_unproven", "the start deployment is blue's own; nothing to re-pin")
    assert_identity(workspace, record, app_name=app_name)
    restore_source = _text(
        getattr(workspace.apps.get_deployment(app_name, expected_blue), "source_code_path", None)
    )
    if restore_source != record["source_code_path"]:
        raise CoreBlueRefused("repin_unproven", "blue's deployment is not from the record's source folder")
    signed_tree = str(record["source_tree_sha256"])
    if expected_tree_sha256 and expected_tree_sha256 != signed_tree:
        raise CoreBlueRefused(
            "repin_unproven", "the expected tree digest is not the signed core record's source tree"
        )
    proof = prove_start_deployment(
        workspace,
        app_name=app_name,
        deployment=workspace.apps.get_deployment(app_name, start_id),
        restore_source=restore_source,
        expected_tree_sha256=signed_tree,
        expected_env_names=frozenset(record["env_names"]),
        digest=digest,
    )
    _assert_start_is_current(workspace, app_name=app_name, deployment_id=proof.deployment_id)
    bindings = _bindings(workspace, record, app_name=app_name, applied=applied_app_resources)
    changes = {
        "deployment_id": proof.deployment_id,
        PREDECESSOR_FIELD: expected_blue,
        "app_resources": bindings,
        "app_resources_sha256": app_resource_contract_digest(bindings),
    }
    verify_core_blue(workspace, {**record, **changes}, app_name=app_name)
    health(record, proof.deployment_id)
    _assert_start_is_current(workspace, app_name=app_name, deployment_id=proof.deployment_id)
    repinned = resign(
        workspace,
        app_name=app_name,
        scope=scope,
        lakebase_instance=lakebase_instance,
        record=record,
        lease=lease,
        **changes,
    )
    health(repinned, proof.deployment_id)
    return repinned
