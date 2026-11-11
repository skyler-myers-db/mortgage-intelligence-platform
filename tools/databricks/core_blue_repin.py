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

``stranded_start`` reads what an earlier tier-a start left when it failed
before its re-pin (a FAILED install, a refused proof, a lost runner), so step
0a and the pre-stop probe re-pin it or start blue's folder again instead of
stranding a payload-less blue behind it.

A v7 agentic record is refused by the tier gate (M4-PR1). Every refusal is a
``CoreBlueRefused`` or a ``StartProofError`` with a reviewed code; no message
names an id, a path or a value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tools.databricks.app_deployment_state import deployment_state, newest_deployment
from tools.databricks.app_rollback_record_contract import _text
from tools.databricks.app_rollback_record_v7 import V7RecordError, _genie_space_of
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract_digest,
    validated_app_resource_contract,
)
from tools.databricks.app_start_deployment import (
    StartProofError,
    env_names,
    is_stopped,
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
# What ``stranded_start`` reads: an earlier tier-a start to re-pin in place, or to start again.
STRANDED_REPIN = "repin"
STRANDED_RESTART = "restart"


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


def _env_vars(deployment: object) -> tuple[tuple[str, ...], ...]:
    """A deployment's env vars as the API shows them: name, value and value_from."""

    rows = []
    for item in getattr(deployment, "env_vars", None) or []:
        rows.append(tuple(_text(getattr(item, key, None)) for key in ("name", "value", "value_from")))
    return tuple(sorted(rows))


def stranded_start(workspace: Any, record: Mapping[str, Any], *, app_name: str) -> str:
    """An earlier tier-a start of blue's own folder the record was never re-pinned onto.

    A tier-a start that failed (a FAILED install, a refused proof, a lost
    runner) is left the App's newest deployment of any state: from the
    record's ``source_code_path``, with blue's env-name set (a start copies the
    newest deployment's), the default source still that folder, so a new
    start snapshots that folder with blue's env set again (spike S7). Its env
    vars must be blue's as the API shows them (a start copies them; values
    Databricks redacts are redacted alike), so a deployment of blue's folder
    with other env values is never taken for one.
    ``STRANDED_REPIN`` when the running App serves it SUCCEEDED (re-pinned in
    place once its snapshot is proven to be the signed tree);
    ``STRANDED_RESTART`` when the App is stopped or serves it unsucceeded
    (stopped, and blue's folder started again once it still hashes to the
    signed tree); ``""`` for anything else, nothing pending, always. The
    pre-stop probe reads the same function.
    """

    stopped = is_stopped(workspace, app_name=app_name)
    app = workspace.apps.get(app_name)
    folder = str(record["source_code_path"])
    if getattr(app, "pending_deployment", None) is not None or (
        _text(getattr(app, "default_source_code_path", None)) != folder
    ):
        return ""
    try:
        newest_id = _text(
            getattr(newest_deployment(workspace.apps.list_deployments(app_name)), "deployment_id", None)
        )
        if not newest_id or newest_id == record["deployment_id"]:
            return ""
        start = workspace.apps.get_deployment(app_name, newest_id)
        blue = workspace.apps.get_deployment(app_name, record["deployment_id"])
        if (
            _text(getattr(start, "source_code_path", None)) != folder
            or env_names(start) != frozenset(record["env_names"])
            or _env_vars(start) != _env_vars(blue)
        ):
            return ""
    except (RuntimeError, StartProofError):
        return ""
    active = _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))
    if stopped:
        return "" if active else STRANDED_RESTART
    if active != newest_id:
        return ""
    return STRANDED_REPIN if deployment_state(start) == "SUCCEEDED" else STRANDED_RESTART
