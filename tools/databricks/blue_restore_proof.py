"""Blue's tier-aware restore proofs: inspect, tier a, tier b and the pre-activation proof (M2-PR7).

Restore tiers (plan §4.7):

* **tier a** (start-only) is valid only while blue is still the App's last
  deployment, i.e. inside a pre-activation window. ``assert_tier_a`` refuses
  once the active deployment is no longer blue: a start would then snapshot
  green's source.
* **tier b** redeploys blue's restore payload from the immutable,
  digest-verified copy. ``tier_b_payload`` re-hashes the copy (a tampered or
  added file refuses), requires the payload a release proved before activation,
  and refuses an ``unverified`` frontend without the
  ``restore_unverified_frontend`` ack. The deploy call itself is the core
  release path's (tools/databricks/core_blue_restore.py, M2-PR7b); this
  module never stops, starts, deploys or updates an App.

**The pre-activation proof (attack O2).** Before a green activation over a
signed blue, ``prepare_activation`` requires blue's tier-b restore payload:

* a v5 or v6 record's own payload is that payload (its immutable ``/src/``
  snapshot, which the signed-blue restore already redeploys), so nothing
  changes for them;
* a v7 record must carry ``restore_payload_sha256``, re-proven now (the copy
  re-hashed, the digest recomputed); or the caller hands the restore payload
  built for blue (env-name set equal to blue's, source the immutable copy),
  which is checked the same way and recorded in a new signed version under
  the lease;
* otherwise activation refuses (exit 2) unless the ``no_rollback_for`` ack
  covers exactly this blue and this green: sha256(blue's deployment id), the
  release's source commit and its release id. The refusal prints that digest,
  so an ack exported in a long-lived shell never carries to another release.
* blue's ``frontend/dist`` unverified (M2-PR7b): its tier-b restore needs the
  ``restore_unverified_frontend`` ack, so a payload counts as restorable only
  when that exact ack is given now (``MIP_ACK_RESTORE_UNVERIFIED_FRONTEND``,
  over the payload's own digest); without it, activation refuses
  (``unverified_frontend_unacked``, printing both digests) unless the
  ``no_rollback_for`` ack is given, and nothing is recorded. The ack covers
  this blue, this payload and the commit of the run that gives it: the
  activation gate and the same run's tier-b restore ask for one digest, and a
  later run (another commit) needs its own, which the refusal prints.

No record, or a retired tombstone, has no blue to protect. Blue's tier must
be the profile's (``MIP_AGENTIC_TIER``; tools/databricks/blue_tier_gate.py): a
v5 or v6 record is agentic, so it never protects a core release, nor a core
blue an agentic one, unless that one is given the exact ``tier_transition``
ack (``blue_tier_mismatch``).

``inspect_v7`` is the ``app_deployment_rollback inspect`` reader for a v7
record: the signature and exact schema, the App's service-principal identity,
then the record's own tier proof (core: exactly the record's core bindings,
never the Gateway resource proof; agentic: the v6 Gateway resource proof).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tools.databricks import app_release_contract as release_contract
from tools.databricks.app_deployment_state import active_deployment_id
from tools.databricks.app_rollback_record_contract import (
    V7_RECORD_VERSION,
    _payload_digest,
    _text,
    load_signed_record,
    save_versioned_record,
)
from tools.databricks.app_rollback_record_v7 import (
    V7RecordError,
    assert_core_resource_proof,
    unsigned,
    validated_v7_record,
)
from tools.databricks.app_rollback_resource_contract import app_resource_contract
from tools.databricks.app_start_deployment import last_deployment_is, tree_digest
from tools.databricks.blue_tier_gate import BlueTierRefused, assert_blue_tier
from tools.databricks.operation_ack import AckRefused, ack_digest, require_ack

_PREFIX = "[mip-blue-restore]"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RestoreProofRefused(RuntimeError):
    """A tier, payload or activation proof refuses; ``code`` is reviewed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _current(record: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The record a restore protects: None for no record or a retired tombstone."""

    if record is None or (
        record.get("version") == V7_RECORD_VERSION and record.get("provenance") == "retired"
    ):
        return None
    return record


def _rehash(workspace: Any, record: Mapping[str, Any], tree: Callable[[Any, str], str]) -> None:
    if tree(workspace, str(record["restore_source_path"])) != record["source_tree_sha256"]:
        raise RestoreProofRefused(
            "copy_tampered",
            "the immutable copy's full-tree digest is not the record's: a file was changed, added "
            "or removed after the adoption, so blue is not restorable from it",
        )


def assert_tier_a(workspace: Any, *, app_name: str, record: Mapping[str, Any]) -> None:
    """Tier a (start-only) only while blue is the App's last deployment."""

    if not last_deployment_is(
        workspace, app_name=app_name, deployment_id=str(record["deployment_id"])
    ):
        raise RestoreProofRefused(
            "tier_a_refused",
            "blue is no longer the App's last deployment (green was activated), so a start would "
            "snapshot another source; only tier b (the immutable copy) restores blue",
        )


def no_rollback_plan(
    *, app_name: str, record: Mapping[str, Any], green_git_sha: str, green_release_id: str
) -> dict[str, str]:
    """The canonical plan a ``no_rollback_for`` ack covers: this blue, this green release."""

    try:
        commit = release_contract.check_commit(green_git_sha)
        release_id = release_contract.check_release_id(green_release_id, commit=commit)
    except release_contract.ReleaseContractError as exc:
        raise RestoreProofRefused(
            "green_release_invalid", f"the green release is invalid: {exc}"
        ) from exc
    return {
        "app_name": app_name,
        "no_rollback_for": sha256_text(str(record["deployment_id"])),
        "green_git_sha": commit,
        "green_release_id": release_id,
    }


def unverified_frontend_plan(
    *, app_name: str, record: Mapping[str, Any], run_git_sha: str
) -> dict[str, str]:
    """The canonical plan a ``restore_unverified_frontend`` ack covers: this blue and payload, this run."""

    try:
        commit = release_contract.check_commit(run_git_sha)
    except release_contract.ReleaseContractError as exc:
        raise RestoreProofRefused("run_commit_invalid", f"the run's commit is invalid: {exc}") from exc
    return {
        "app_name": app_name,
        "restore_to": sha256_text(str(record["deployment_id"])),
        "restore_payload_sha256": str(record["restore_payload_sha256"]),
        "run_git_sha": commit,
    }


def tier_b_payload(
    workspace: Any,
    *,
    app_name: str,
    record: Mapping[str, Any],
    unverified_frontend_ack: str | None = None,
    tree: Callable[[Any, str], str] = tree_digest,
    run_git_sha: str = "",
) -> dict[str, object]:
    """Blue's tier-b restore payload, re-proven now; the caller deploys it (M2-PR7b).

    ``run_git_sha`` is the restoring run's commit, which an unverified
    frontend's ack covers; a verified frontend never reads it.
    """

    if record.get("version") != V7_RECORD_VERSION:
        raise RestoreProofRefused(
            "not_v7", "tier b of a v5 or v6 record is the signed-blue restore's"
        )
    if record.get("restore_payload") is None:
        raise RestoreProofRefused(
            "no_restore_payload", "the record carries no restore payload proven before activation"
        )
    _rehash(workspace, record, tree)
    payload = record["restore_payload"]
    if _payload_digest(payload) != record["restore_payload_sha256"]:
        raise RestoreProofRefused("payload_digest", "the restore payload digest does not match")
    if record["frontend_provenance"] != "verified":
        plan = unverified_frontend_plan(app_name=app_name, record=record, run_git_sha=run_git_sha)
        try:
            require_ack("restore_unverified_frontend", plan, unverified_frontend_ack)
        except AckRefused as exc:
            raise RestoreProofRefused(
                exc.code,
                f"blue's frontend/dist is unverified: {exc}. To restore it, export "
                f"MIP_ACK_RESTORE_UNVERIFIED_FRONTEND={ack_digest('restore_unverified_frontend', plan)} "
                "for this run",
            ) from exc
    return dict(payload)


def prepare_activation(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    lease: Callable[[], None],
    agentic_tier: str,
    green_git_sha: str,
    green_release_id: str,
    restore_payload: Mapping[str, object] | None = None,
    no_rollback_ack: str | None = None,
    tree: Callable[[Any, str], str] = tree_digest,
    tier_transition_ack: str | None = None,
    unverified_frontend_ack: str | None = None,
) -> str:
    """The pre-activation rollback proof; returns a note or raises RestoreProofRefused."""

    record = _current(
        load_signed_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
    )
    if record is None:
        return "no signed blue record: there is no blue to protect"
    try:
        assert_blue_tier(
            record,
            app_name=app_name,
            agentic_tier=agentic_tier,
            tier_transition_ack=tier_transition_ack,
        )
    except BlueTierRefused as exc:
        raise RestoreProofRefused(exc.code, str(exc)) from exc
    if record["version"] != V7_RECORD_VERSION:
        return "the signed v5/v6 record's own payload is blue's tier-b restore payload"
    if active_deployment_id(workspace, app_name=app_name) != record["deployment_id"]:
        raise RestoreProofRefused(
            "blue_not_active",
            "blue is not the active deployment: the proof must precede activation",
        )
    candidate: dict[str, Any] | None = None
    digest = record["restore_payload_sha256"]
    if digest is None and restore_payload is not None:
        _rehash(workspace, record, tree)
        candidate = _candidate(record, restore_payload, app_name, lakebase_instance)
        digest = candidate["restore_payload_sha256"]
    elif digest is not None:
        _rehash(workspace, record, tree)
    # An unverified frontend is restorable only with its own ack (M2-PR7b), so a
    # green is activated over it only when that ack is already given.
    frontend_ack = (
        None
        if digest is None
        else _unacked_frontend(app_name, record, str(digest), unverified_frontend_ack, green_git_sha)
    )
    if digest is not None and frontend_ack is None:
        if candidate is None:
            return "blue's tier-b restore payload was proven before activation and re-proven now"
        lease()
        save_versioned_record(workspace, scope=scope, record=unsigned(candidate))
        persisted = load_signed_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
        if persisted is None or persisted["restore_payload_sha256"] != digest:
            raise RestoreProofRefused(
                "payload_unrecorded", "the restore payload record did not read back exactly"
            )
        return "blue's tier-b restore payload was built, proven and recorded before activation"
    plan = no_rollback_plan(
        app_name=app_name,
        record=record,
        green_git_sha=green_git_sha,
        green_release_id=green_release_id,
    )
    try:
        require_ack("no_rollback_for", plan, no_rollback_ack)
    except AckRefused as exc:
        no_rollback = f"export MIP_ACK_NO_ROLLBACK_FOR={ack_digest('no_rollback_for', plan)}"
        if frontend_ack is not None:
            raise RestoreProofRefused(
                "unverified_frontend_unacked",
                "green activation refuses: blue's frontend/dist is unverified, so its tier-b "
                "restore needs the restore_unverified_frontend ack, which this run was not given "
                f"({exc.code}). Export MIP_ACK_RESTORE_UNVERIFIED_FRONTEND={frontend_ack} for this "
                f"run, or, to activate with no rollback for blue, {no_rollback}",
            ) from exc
        raise RestoreProofRefused(
            "no_rollback_proof",
            "green activation refuses: blue's tier-b restore payload is not recorded, and once green "
            f"is active only tier b restores blue ({exc.code}). To activate with no rollback for "
            f"blue, {no_rollback}",
        ) from exc
    return "activating with NO rollback for blue: the exact no_rollback_for ack was given"


def _candidate(
    record: Mapping[str, Any],
    restore_payload: Mapping[str, object],
    app_name: str,
    lakebase_instance: str,
) -> dict[str, Any]:
    try:
        return validated_v7_record(
            {
                **unsigned(record),
                "restore_payload": dict(restore_payload),
                "restore_payload_sha256": _payload_digest(dict(restore_payload)),
            },
            app_name=app_name,
            expected_lakebase_instance=lakebase_instance,
        )
    except (V7RecordError, RuntimeError) as exc:
        raise RestoreProofRefused(
            "payload_invalid", f"blue's restore payload refuses: {exc}"
        ) from exc


def _unacked_frontend(
    app_name: str, record: Mapping[str, Any], digest: str, ack: str | None, run_git_sha: str
) -> str | None:
    """None when blue's frontend is verified or its exact ack is given, else the ack to give.

    This run's commit: the same digest is the one its own tier-b restore asks for.
    """

    if record["frontend_provenance"] == "verified":
        return None
    plan = unverified_frontend_plan(
        app_name=app_name, record={**record, "restore_payload_sha256": digest}, run_git_sha=run_git_sha
    )
    try:
        require_ack("restore_unverified_frontend", plan, ack)
    except AckRefused:
        return ack_digest("restore_unverified_frontend", plan)
    return None


def verify_tier_proof(
    workspace: Any,
    record: Mapping[str, Any],
    *,
    app_name: str,
    gateway_proof: Callable[[Mapping[str, Any]], object],
) -> None:
    """A v7 record's identity and its own tier's resource proof (core never asks the Gateway)."""

    app = workspace.apps.get(app_name)
    identity = (
        _text(getattr(app, "service_principal_client_id", None)),
        _text(getattr(app, "service_principal_id", None)),
    )
    if identity != (
        record["app_service_principal_client_id"],
        record["app_service_principal_scim_id"],
    ):
        raise RestoreProofRefused(
            "identity_drift", "App service-principal identity drifted from the signed record"
        )
    if record["tier"] == "core":
        assert_core_resource_proof(record, live=app_resource_contract(workspace, app_name=app_name))
        return
    gateway_proof(record)


def _binding_env(path: Path, record: Mapping[str, Any]) -> None:
    values = {
        "MIP_APP_ROLLBACK_RECORD_VERSION": str(record["version"]),
        "MIP_APP_ROLLBACK_TIER": str(record["tier"]),
        "MIP_APP_ROLLBACK_PROVENANCE": str(record["provenance"]),
        "MIP_APP_ROLLBACK_DEPLOYMENT_ID": str(record["deployment_id"]),
        "MIP_APP_ROLLBACK_GENIE_SPACE_ID": str(record["genie_space_id"]),
        "MIP_APP_ROLLBACK_FRONTEND_PROVENANCE": str(record["frontend_provenance"]),
        "MIP_APP_ROLLBACK_RESTORE_PAYLOAD_SHA256": str(record["restore_payload_sha256"] or ""),
    }
    path.write_text(
        "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items()),
        encoding="utf-8",
    )


def inspect_v7(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    out_env: Path,
    gateway_proof: Callable[[Mapping[str, Any]], object],
) -> bool:
    """Verify and export a v7 record; False when the slot holds a v5 or v6 record."""

    record = load_signed_record(
        workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
    )
    if record is None or record["version"] != V7_RECORD_VERSION:
        return False
    if _current(record) is None:
        raise RuntimeError(
            "no server-owned last-good App rollback contract exists (a retired tombstone)"
        )
    verify_tier_proof(workspace, record, app_name=app_name, gateway_proof=gateway_proof)
    _binding_env(out_env, record)
    return True


def main(
    argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] | None = None
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("action", choices=("prepare-activation",))
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--source-git-sha", required=True)
    parser.add_argument("--release-id", required=True)
    # The profile's MIP_AGENTIC_TIER; scripts/deploy.sh passes off for the core tier.
    parser.add_argument("--agentic-tier", choices=("off", "on"), default="on")
    parser.add_argument("--restore-payload", type=Path, default=None)
    parser.add_argument("--no-rollback-ack", default="")
    # A release over the other tier's blue (blue_tier_gate.py): no shell passes it yet.
    parser.add_argument("--tier-transition-ack", default="")
    # MIP_ACK_RESTORE_UNVERIFIED_FRONTEND: an unverified blue is restorable only with it.
    parser.add_argument("--restore-unverified-frontend-ack", default="")
    args = parser.parse_args(argv)
    if workspace_factory is None:
        from tools.databricks.workspace_auth import deployment_workspace_client

        workspace_factory = deployment_workspace_client
    workspace = workspace_factory()
    from tools.databricks import app_deployment_lease

    try:
        payload = None
        if args.restore_payload is not None:
            payload = json.loads(args.restore_payload.read_text(encoding="utf-8"))
        note = prepare_activation(
            workspace,
            app_name=args.app_name,
            scope=args.scope,
            lakebase_instance=args.lakebase_instance,
            lease=app_deployment_lease.held_assertion(
                workspace,
                app_name=args.app_name,
                lease_id=args.lease_id,
                source_git_sha=args.source_git_sha,
            ),
            agentic_tier=args.agentic_tier,
            green_git_sha=args.source_git_sha,
            green_release_id=args.release_id,
            restore_payload=payload,
            no_rollback_ack=args.no_rollback_ack,
            tier_transition_ack=args.tier_transition_ack,
            unverified_frontend_ack=args.restore_unverified_frontend_ack,
        )
    except RestoreProofRefused as exc:
        print(f"{_PREFIX} REFUSED {exc.code}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        print(
            f"{_PREFIX} REFUSED: the pre-activation proof could not complete ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    print(f"{_PREFIX} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
