"""The core tier's step-0a proof and signed-blue restore over a v7 core record (M2-PR7b).

``ensure_core_blue`` is step 0a for a core release (v6's ``ensure_current``):
treatment is quiesced, the record is loaded through the tier gate
(core_blue.load_core_blue), the App's identity is checked, and then:

* the active deployment is not the record's (a run killed after a green
  activation and before its signed capture): blue is restored first (tier b,
  below), with treatment left quiesced;
* otherwise a stopped App whose next start would not rerun blue refuses
  first (``tier_a_refused``), before any binding repair: its newest
  deployment of any state must be blue and its default source blue's folder
  (spike S7: a STOPPED App's GET has no active deployment, and a FAILED
  deployment becomes active and moves the default source). The pre-stop probe
  predicts that refusal before the lease. Then the record's own bindings are
  restored when a killed run left a candidate's (the restore-owned repair,
  the one ``apps.update`` site), and identity and the core resource proof are
  proven against the record. A stopped blue is then started and re-pinned
  (tier a, as v5/v6's step 0a does): the start's new deployment is proven to
  be blue and the record re-signed onto it (tools/databricks/core_blue_repin.py).
  Health is proven against the record's deployment.

``restore_core_blue`` is tier b, the only rollback once green was activated:
blue's restore payload (``blue_restore_proof.tier_b_payload``: the immutable
copy re-hashed, the payload digest re-proven, an ``unverified`` frontend only
with the ``restore_unverified_frontend`` ack) is deployed, the new deployment
is proven to be blue (SUCCEEDED, active, the newest, from the immutable copy,
its ``/src/`` snapshot hashing to the record's full-tree digest, with blue's
env-name set, healthy on blue's commit), and the record is re-pinned to it as
its next version (same provenance and ack, ``predecessor_deployment_id`` the
deployment it replaced). A record with no restore payload (an adopted blue a
release never proved before activation) refuses. A stopped App is started
first, as v6's restore does: Databricks deploys only to a running App, and the
start's own deployment is replaced at once by blue's; the payload is proven
before the start and again after its wait, immediately before the deploy.
Treatment is converged to runtime only after every proof.

A green deploy that never began (a failure between the activation quiesce and
the deploy) leaves blue serving: the record's deployment still the active and
newest one, nothing pending, the App running and its health proven. Tier b then
redeploys nothing (no deploy over the original deployment, no re-pin) and only
converges treatment back. Any doubt in that check is tier b's, as before.

Both quiesce treatment again on any failure. Neither reads a Gateway resource
proof or needs an agentic identity.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from databricks.sdk.service.apps import AppDeployment
from tools.databricks.app_deployment_state import (
    active_deployment_id,
    deployment_state,
    latest_succeeded,
)
from tools.databricks.app_rollback_record_contract import _text
from tools.databricks.app_rollback_resource_contract import restore_signed_app_resource_contract
from tools.databricks.app_start_deployment import (
    StartProofError,
    current_deployment_id,
    ensure_started,
    env_names,
    is_stopped,
    last_deployment_is,
    tree_digest,
    wait_for_compute_active,
)
from tools.databricks.blue_restore_proof import tier_b_payload
from tools.databricks.core_blue import (
    CoreBlueRefused,
    Health,
    assert_identity,
    assert_serving,
    live_core_bindings,
    load_core_blue,
    require_core_blue,
    resign,
    verify_core_blue,
)
from tools.databricks.core_blue_repin import repin_core_start

DEPLOY_TIMEOUT = timedelta(minutes=20)
# The step-0a refusal for a stopped core App whose start would not rerun blue
# (spike S7); the pre-stop probe predicts it.
TIER_A_REFUSED = (
    "the core App is stopped and its next start would not rerun blue (its newest deployment "
    "or its default source is not blue's), so step 0a never starts it; restore blue from the "
    "immutable copy (app_core_rollback restore) or the owner break-glass, then re-run"
)


def redeploy_core_blue(
    workspace: Any, *, app_name: str, payload: Mapping[str, object], timeout: timedelta
) -> str:
    """Tier b's one deploy: blue's restore payload; returns the new deployment's id."""

    restored = workspace.apps.deploy_and_wait(
        app_name, AppDeployment.from_dict(dict(payload)), timeout=timeout
    )
    if deployment_state(restored) != "SUCCEEDED":
        raise CoreBlueRefused("restore_failed", "blue's restore deployment did not succeed")
    return _text(getattr(restored, "deployment_id", None))


def _prove_restored(
    workspace: Any,
    record: Mapping[str, Any],
    *,
    app_name: str,
    restored_id: str,
    tree: Callable[[Any, str], str],
) -> None:
    latest = latest_succeeded(workspace, app_name=app_name)
    if (
        not restored_id
        or active_deployment_id(workspace, app_name=app_name) != restored_id
        or _text(getattr(latest, "deployment_id", None)) != restored_id
        or _text(getattr(latest, "source_code_path", None)) != record["restore_source_path"]
    ):
        raise CoreBlueRefused(
            "restore_unproven",
            "the restored deployment is not the active, newest one from blue's immutable copy",
        )
    if sorted(env_names(latest)) != list(record["env_names"]):
        raise CoreBlueRefused("restore_unproven", "the restored deployment is not blue's env set")
    # What the App runs: the deployment's own snapshot, byte for byte blue's
    # tree (the copy is writable by its owner after the re-hash).
    snapshot = _text(getattr(getattr(latest, "deployment_artifacts", None), "source_code_path", None))
    if "/src/" not in snapshot or tree(workspace, snapshot) != record["source_tree_sha256"]:
        raise CoreBlueRefused(
            "restore_unproven",
            "the restored deployment's snapshot is not blue's full tree: the copy changed after its "
            "re-hash, so the deployment is not blue",
        )


def _still_serving(
    workspace: Any, record: Mapping[str, Any], *, app_name: str, health: Health
) -> bool:
    """True when blue never stopped serving the record's deployment; any doubt is False."""

    try:
        if is_stopped(workspace, app_name=app_name):
            return False
        active = active_deployment_id(workspace, app_name=app_name)  # a pending deployment raises
        latest = _text(getattr(latest_succeeded(workspace, app_name=app_name), "deployment_id", None))
        if active != record["deployment_id"] or latest != active:
            return False
        health(record, active)
    except Exception:  # noqa: BLE001 - any doubt falls through to tier b, never a refusal here
        return False
    return True


def restore_core_blue(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    agentic_tier: str,
    health: Health,
    lease: Callable[[], None],
    treatment: Callable[[str], None],
    tier_transition_ack: str | None = None,
    unverified_frontend_ack: str | None = None,
    expected_rollback_deployment_id: str | None = None,
    restore_treatment: bool = True,
    tree: Callable[[Any, str], str] = tree_digest,
    timeout: timedelta = DEPLOY_TIMEOUT,
    run_git_sha: str = "",
) -> dict[str, Any]:
    """Tier b over a v7 core record; returns the re-pinned (or still-serving) record."""

    treatment("quiesce")
    try:
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
        if (
            expected_rollback_deployment_id is not None
            and record["deployment_id"] != expected_rollback_deployment_id
        ):
            raise CoreBlueRefused(
                "record_changed", "the signed core record changed after step 0a bound it"
            )
        assert_identity(workspace, record, app_name=app_name)
        live_core_bindings(workspace, app_name=app_name)  # a serving binding refuses, unrepaired
        lease()
        restore_signed_app_resource_contract(
            workspace, app_name=app_name, resources=record["app_resources"]
        )
        verify_core_blue(workspace, record, app_name=app_name)
        if _still_serving(workspace, record, app_name=app_name, health=health):
            # Green's deploy never began: blue is restored by redeploying nothing.
            verify_core_blue(workspace, record, app_name=app_name)
            assert_serving(workspace, app_name=app_name, deployment_id=record["deployment_id"])
            lease()
            if restore_treatment:
                treatment("runtime")
            return record

        # load_core_blue's gate judged blue against the profile's tier (a core
        # blue under an agentic one only with its exact tier_transition ack)
        # and returned a core record: tier b restores it as the core tier.
        def proven_payload() -> dict[str, object]:
            return tier_b_payload(
                workspace,
                app_name=app_name,
                record=record,
                agentic_tier="off",
                unverified_frontend_ack=unverified_frontend_ack,
                tree=tree,
                run_git_sha=run_git_sha,
            )

        proven_payload()  # refuses before any start: no payload, a changed copy, an unacked frontend
        try:
            ensure_started(workspace, app_name=app_name)
        except StartProofError:
            # Tier b needs only compute ACTIVE: its deploy replaces the start.
            wait_for_compute_active(workspace, app_name=app_name)
        # The start can wait minutes: re-proven immediately before the deploy.
        payload = proven_payload()
        lease()
        restored_id = redeploy_core_blue(
            workspace, app_name=app_name, payload=payload, timeout=timeout
        )
        _prove_restored(workspace, record, app_name=app_name, restored_id=restored_id, tree=tree)
        health(record, restored_id)
        verify_core_blue(workspace, record, app_name=app_name)
        repinned = resign(
            workspace,
            app_name=app_name,
            scope=scope,
            lakebase_instance=lakebase_instance,
            record=record,
            lease=lease,
            deployment_id=restored_id,
            source_code_path=record["restore_source_path"],
            predecessor_deployment_id=record["deployment_id"],
        )
        if restore_treatment:
            treatment("runtime")
        return repinned
    except BaseException:
        treatment("quiesce")
        raise


def ensure_core_blue(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    agentic_tier: str,
    health: Health,
    lease: Callable[[], None],
    treatment: Callable[[str], None],
    tier_transition_ack: str | None = None,
    unverified_frontend_ack: str | None = None,
    tree: Callable[[Any, str], str] = tree_digest,
    run_git_sha: str = "",
) -> dict[str, Any]:
    """Step 0a over a v7 core record: prove blue, or restore it; treatment stays quiesced."""

    treatment("quiesce")
    try:
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
        assert_identity(workspace, record, app_name=app_name)
        # A stopped App has no active deployment: its newest one is what a start reruns (S7).
        active = current_deployment_id(workspace, app_name=app_name)
        latest = _text(getattr(latest_succeeded(workspace, app_name=app_name), "deployment_id", None))
        if active != record["deployment_id"] or latest != active:
            return restore_core_blue(
                workspace,
                app_name=app_name,
                scope=scope,
                lakebase_instance=lakebase_instance,
                agentic_tier=agentic_tier,
                health=health,
                lease=lease,
                treatment=treatment,
                tier_transition_ack=tier_transition_ack,
                unverified_frontend_ack=unverified_frontend_ack,
                expected_rollback_deployment_id=record["deployment_id"],
                restore_treatment=False,
                tree=tree,
                run_git_sha=run_git_sha,
            )
        # Before any binding repair: a stopped App a start would not restore is left as it is.
        stopped = is_stopped(workspace, app_name=app_name)
        if stopped and not last_deployment_is(workspace, app_name=app_name, deployment_id=active):
            raise CoreBlueRefused("tier_a_refused", TIER_A_REFUSED)
        live_core_bindings(workspace, app_name=app_name)  # a serving binding refuses, unrepaired
        lease()
        restore_signed_app_resource_contract(
            workspace, app_name=app_name, resources=record["app_resources"]
        )
        verify_core_blue(workspace, record, app_name=app_name)
        if stopped:
            # Tier a (spike S7): the start makes a new deployment; prove it is blue, re-pin.
            active = ensure_started(workspace, app_name=app_name)
            record = repin_core_start(
                workspace,
                app_name=app_name,
                scope=scope,
                lakebase_instance=lakebase_instance,
                deployment_id=active,
                expected_rollback_deployment_id=record["deployment_id"],
                health=health,
                lease=lease,
                agentic_tier=agentic_tier,
                tier_transition_ack=tier_transition_ack,
                digest=tree,
            )
        health(record, active)
        verify_core_blue(workspace, record, app_name=app_name)
        if active_deployment_id(workspace, app_name=app_name) != active:
            raise CoreBlueRefused(
                "deployment_changed", "the active deployment changed during the step-0a proof"
            )
        if record.get("attestation_verify_key") != os.environ.get(
            "MIP_AI_GATEWAY_PROOF_VERIFY_KEY", ""
        ).strip():
            # A rotated proof key: the same record, signed again with the current key.
            return resign(
                workspace,
                app_name=app_name,
                scope=scope,
                lakebase_instance=lakebase_instance,
                record=record,
                lease=lease,
            )
        return record
    except BaseException:
        treatment("quiesce")
        raise
