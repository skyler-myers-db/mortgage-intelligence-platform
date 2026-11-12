"""The core release's pre-activation rollback proof, predicted read-only before the lease (B3).

  python -m tools.databricks.activation_prediction predict --phase plan|release|complete ...
  python -m tools.databricks.activation_prediction env-names [--step-summary PATH]

An adopted blue's ``frontend/dist`` is ``unverified`` whenever CI adopted it
(the deploy job has no Node, by design), so the release's pre-activation proof
(tools/databricks/blue_restore_proof.py ``prepare_activation``, step 10b)
needs the exact ``restore_unverified_frontend`` ack. That proof runs after the
lease, the non-App bundle apply and the quiesce window. ``predict`` runs the
same decision before the lease, with the late gate's own loader and payload
checks, so a release that would refuse there refuses here with nothing changed:

* the record is read as the core release reads it (``core_blue.load_core_blue``:
  the tier gate first, so a v5/v6 record refuses with its reviewed code, as the
  rebuild before the late gate does). No record or a retired tombstone has no
  blue; then ``env_name_parity`` reports, names only, whether this profile
  rebuilds the live App's newest deployment's env-name set, as a note (no
  rollback scope yet is no record: a first install creates it under the lease);
* a recorded payload digest is taken as is; otherwise blue's restore payload is
  rebuilt in process exactly as ``app_core_rollback restore-payload`` rebuilds
  it (``core_blue.blue_restore_payload``, never written or printed) and checked
  with the late gate's own ``_candidate`` (``payload_invalid``). None rebuilt is
  ``no_rollback_proof``;
* a verified frontend passes. An unverified one needs the ack over
  ``unverified_frontend_plan`` (blue's immutable copy, the payload digest and
  this run's commit): a re-pin keeps all three, so the digest is the late
  gate's and the same run's tier-b restore's. ``--phase plan`` prints it
  (``restore_unverified_frontend=<sha256>``, to stdout and the step summary);
  a release or a run with no phase admits exactly it, else refuses
  ``unverified_frontend_unacked`` printing it with both routes. A blue whose
  env-name set holds the lease name binds the run's lease into the payload,
  which no one knows before the lease: ``frontend_ack_unpredictable``;
* ``--no-rollback-ack-given`` (``MIP_ACK_NO_ROLLBACK_FOR`` is set; its value is
  never read) defers every one of those three verdicts to the late gate, which
  admits that ack in their place. A plan never defers.

Not predicted (the late gate's alone): the copy's full-tree re-hash
(``copy_tampered``) and blue being the active deployment at activation (step 0a
may start and re-pin it first).

``env-names`` is the cutover's plans before an adoption (docs/clients/first-
client-cutover.md, steps 6 and 9): they stop at the client live checks, before
any record or payload step, so their refusal path runs this first. It resolves
the client profile, proves the workspace and the deployer as the live checks'
identity half does (tools/clients/live_identity.py), then compares names: the
payload the profile rebuilds (a sentinel copy and commit, blue's droppable
rule, and the names scripts/deploy.sh derives after the live checks,
``GOVERNED_DERIVED_NAMES``) against the App's newest deployment's set, which an
``apps start`` copies (spike S7). Step 12's tier-b restore depends on it.

Read-only: workspace GETs, the rollback slot's secret read, no signing key
(the verify key alone checks the record), no write but the step summary.
Messages carry codes, sha256 digests and env names, never an id, a path or a
value. Exit 0 passes (or defers), 2 refuses; an unexpected failure prints its
class only.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.databricks import core_blue
from tools.databricks.app_deployment_state import newest_deployment
from tools.databricks.app_rollback_record_contract import _text
from tools.databricks.app_rollback_record_v7 import V7RecordError
from tools.databricks.app_rollback_secret_scope import _scope_names, expected_app_rollback_scope
from tools.databricks.app_start_deployment import env_names
from tools.databricks.blue_restore_proof import (
    RestoreProofRefused,
    _candidate,
    unverified_frontend_plan,
)
from tools.databricks.blue_tier_gate import BlueTierRefused
from tools.databricks.operation_ack import AckRefused, ack_digest, require_ack

_PREFIX = "[mip-activation-prediction]"
PHASES = ("plan", "release", "complete")
PURPOSE = "restore_unverified_frontend"
# The builder inputs scripts/deploy.sh derives after the client live checks,
# each always non-empty by the activation (the preflight's Lakebase Sync
# defaults, the admin identities with the admin and release-probe ids). A plan
# that stops at the live checks has not derived them yet, so its names-only
# rebuild stands a sentinel in for an unset one (tests/unit/
# test_activation_prediction_deploy_contract.py pins this list to the shell).
GOVERNED_DERIVED_NAMES = (
    "MIP_ADMIN_IDENTITIES",
    "MIP_LAKEBASE_SYNC_CATALOG",
    "MIP_LAKEBASE_SYNC_SCHEMA",
    "MIP_LAKEBASE_SYNC_TABLES",
)
_SENTINEL = "activation-prediction-names-only"
_SENTINEL_SHA = "0" * 40
_SENTINEL_SOURCE = "/Workspace/Users/activation-prediction/app-src/names-only"


@dataclass(frozen=True)
class Prediction:
    """A verdict: ``code`` is ``pass``, ``deferred`` or a reviewed refusal code."""

    code: str
    message: str
    frontend_ack: str | None = None

    @property
    def refused(self) -> bool:
        return self.code not in {"pass", "deferred"}


def _names_only(why: str) -> str:
    """The rebuild's reason with any builder refusal cut to its class (a refusal may quote a value)."""

    head = "the payload builder refused ("
    return why[: why.index(":", len(head))] + ")" if why.startswith(head) and ":" in why else why


def _unverified(code: str, message: str, defer: bool, ack: str | None = None) -> Prediction:
    if defer:
        return Prediction(
            "deferred",
            f"{code} deferred to the pre-activation proof: MIP_ACK_NO_ROLLBACK_FOR is set there",
            ack,
        )
    return Prediction(code, message, ack)


def env_name_parity(
    workspace: Any,
    *,
    app_name: str,
    payload_inputs: Mapping[str, Any],
    derived_sentinels: bool = False,
    build: Callable[..., dict[str, object]] | None = None,
) -> tuple[str, str]:
    """(``same``, ``differs``, ``no_app`` or ``no_deployment``; a names-only message)."""

    from databricks.sdk.errors import NotFound, ResourceDoesNotExist

    try:
        workspace.apps.get(app_name)
    except (NotFound, ResourceDoesNotExist):
        return "no_app", "no App exists yet: there is no deployment whose env names to rebuild"
    newest = newest_deployment(workspace.apps.list_deployments(app_name))
    newest_id = _text(getattr(newest, "deployment_id", None))
    if not newest_id:
        return "no_deployment", "the App has no deployment: there are no env names to rebuild"
    live = env_names(workspace.apps.get_deployment(app_name, newest_id))
    overlay: dict[str, str | None] = {}
    if derived_sentinels:
        from tools.databricks.app_deploy_payload import _dotenv_overlay

        dotenv = _dotenv_overlay()
        overlay = {
            name: _SENTINEL
            for name in GOVERNED_DERIVED_NAMES
            if not (os.environ.get(name, "").strip() or dotenv.get(name, "").strip())
        }
    with core_blue._environ(overlay):
        payload, why = core_blue.rebuild_for_env_names(
            live,
            git_sha=_SENTINEL_SHA,
            source_code_path=_SENTINEL_SOURCE,
            lease=_SENTINEL if core_blue.LEASE_ENV in live else None,
            build=build,
            **payload_inputs,
        )
    if payload is None:
        return "differs", (
            f"env-name parity DIFFERS: {_names_only(why)}. Step 12's tier-b restore payload "
            "cannot be rebuilt from this profile; do not start step 7"
        )
    return "same", (
        f"env-name parity holds: this profile rebuilds the App's newest deployment's {len(live)} "
        "env names exactly (no env-name difference)"
    )


def predict(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    phase: str,
    run_git_sha: str,
    payload_inputs: Mapping[str, Any],
    frontend_ack: str | None = None,
    no_rollback_ack_given: bool = False,
    build: Callable[..., dict[str, object]] | None = None,
) -> Prediction:
    """The late gate's verdict over the record as it stands before the lease."""

    if phase not in PHASES:
        raise ValueError(f"--phase must be one of {', '.join(PHASES)}")
    defer = no_rollback_ack_given and phase != "plan"
    try:
        # No rollback scope holds no record: a first install creates it under the lease.
        absent = expected_app_rollback_scope(app_name) not in _scope_names(workspace)
        record = None if absent else core_blue.load_core_blue(
            workspace, app_name=app_name, scope=scope, lakebase_instance=lakebase_instance, agentic_tier="off"
        )
    except (BlueTierRefused, core_blue.CoreBlueRefused) as exc:
        return Prediction(exc.code, str(exc))
    except V7RecordError as exc:
        return Prediction("record_invalid", str(exc))
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        return Prediction("record_unreadable", f"the signed blue record could not be read ({type(exc).__name__})")
    if record is None:
        # Nothing to protect, so the late gate admits: the parity is a note, never a refusal here
        # (the plans before an adoption gate step 7 on it through ``env-names``).
        try:
            _verdict, message = env_name_parity(
                workspace, app_name=app_name, payload_inputs=payload_inputs, build=build
            )
        except Exception as exc:  # noqa: BLE001 - the class only
            message = f"env-name parity not measured ({type(exc).__name__})"
        return Prediction("pass", f"no signed blue to protect; {message}")
    names = frozenset(record["env_names"])
    lease_bound = record["restore_payload_sha256"] is None and core_blue.LEASE_ENV in names
    digest = record["restore_payload_sha256"]
    if digest is None:
        lease = {core_blue.LEASE_ENV: os.environ.get(core_blue.LEASE_ENV) or _SENTINEL} if lease_bound else {}
        with core_blue._environ(lease):
            payload, why = core_blue.blue_restore_payload(record, build=build, **payload_inputs)
        if payload is None:
            return _unverified(
                "no_rollback_proof",
                f"blue's restore payload is not recorded and cannot be rebuilt: {_names_only(why)}",
                defer,
            )
        try:
            digest = _candidate(record, payload, app_name, lakebase_instance)["restore_payload_sha256"]
        except RestoreProofRefused as exc:
            return Prediction(exc.code, str(exc))
    if record["frontend_provenance"] == "verified":
        return Prediction("pass", "blue's restore payload is recorded or rebuildable, and its frontend is verified")
    if lease_bound:
        return _unverified(
            "frontend_ack_unpredictable",
            "blue's env-name set holds the lease name, so its rebuilt payload carries the run's own "
            "lease and the restore_unverified_frontend digest exists only under that lease",
            defer,
        )
    try:
        plan = unverified_frontend_plan(
            app_name=app_name, record={**record, "restore_payload_sha256": digest}, run_git_sha=run_git_sha
        )
    except RestoreProofRefused as exc:
        return Prediction(exc.code, str(exc))
    expected = ack_digest(PURPOSE, plan)
    if phase == "plan":
        return Prediction(
            "pass", f"blue's frontend is unverified: the release needs {PURPOSE}={expected}", expected
        )
    try:
        require_ack(PURPOSE, plan, frontend_ack)
    except AckRefused as exc:
        return _unverified(
            "unverified_frontend_unacked",
            f"blue's frontend/dist is unverified and this run's {PURPOSE} ack is not its digest "
            f"({exc.code}). Dispatch with -f operation_acks=\"... {PURPOSE}={expected}\", or export "
            f"MIP_ACK_RESTORE_UNVERIFIED_FRONTEND={expected} for this run",
            defer,
            expected,
        )
    return Prediction("pass", f"the {PURPOSE} ack is the digest the pre-activation proof will demand", expected)


def _summary(path: Path | None, lines: list[str]) -> None:
    if path is not None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n### Activation prediction (B3, read-only)\n\n")
            handle.writelines(f"- {line}\n" for line in lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    names = sub.add_parser("env-names")
    names.add_argument("--step-summary", type=Path, default=None)
    run = sub.add_parser("predict")
    run.add_argument("--phase", required=True, choices=PHASES)
    # Exactly the flags scripts/deploy.sh hands app_core_rollback restore-payload.
    for flag in ("--app-name", "--scope", "--lakebase-instance", "--lakebase-database"):
        run.add_argument(flag, required=True)
    run.add_argument("--agentic-tier", required=True, choices=("off", "on"))
    for flag in ("--install-class", "--app-env", "--catalog", "--schema", "--source-git-sha"):
        run.add_argument(flag, required=True)
    run.add_argument("--restore-unverified-frontend-ack", default="")
    run.add_argument("--no-rollback-ack-given", action="store_true")
    run.add_argument("--step-summary", type=Path, default=None)
    return parser


def _env_names(args: argparse.Namespace, workspace_factory: Callable[[], Any]) -> int:
    from tools.clients.environment import from_local, resolve
    from tools.clients.errors import ProfileRefusal
    from tools.clients.live_identity import identity_lines
    from tools.databricks.app_deploy_payload import ENV_LOCAL

    try:
        profile = resolve(from_local(os.environ, ENV_LOCAL))
    except ProfileRefusal:
        print(f"{_PREFIX} env-name parity not measured: the client profile refuses", file=sys.stderr)
        return 2
    if profile.agentic_tier != "off":
        print(f"{_PREFIX} env-name parity: the agentic tier's blue is not rebuilt (nothing measured)")
        return 0
    workspace = workspace_factory()
    if identity_lines(profile, workspace):
        print(
            f"{_PREFIX} env-name parity not measured: the workspace or the deployer is not the profile's",
            file=sys.stderr,
        )
        return 2
    inputs = {
        "install_class": profile.install_class,
        "app_env": profile.app_env,
        "catalog": profile.default_catalog,
        "schema": os.environ.get("MIP_DEFAULT_SCHEMA", "").strip() or "gold",
        "lakebase_instance": profile.lakebase_instance,
        "lakebase_database": profile.lakebase_database,
    }
    verdict, message = env_name_parity(
        workspace, app_name=profile.app_name, payload_inputs=inputs, derived_sentinels=True
    )
    print(f"{_PREFIX} {message}", file=sys.stderr if verdict == "differs" else sys.stdout)
    _summary(args.step_summary, [message])
    return 2 if verdict == "differs" else 0


def _predict(args: argparse.Namespace, workspace_factory: Callable[[], Any]) -> int:
    if args.agentic_tier != "off":
        print(f"{_PREFIX} the agentic tier's v5/v6 blue carries its own restore payload: nothing to predict")
        return 0
    prediction = predict(
        workspace_factory(),
        app_name=args.app_name,
        scope=args.scope,
        lakebase_instance=args.lakebase_instance,
        phase=args.phase,
        run_git_sha=args.source_git_sha,
        payload_inputs={
            "install_class": args.install_class,
            "app_env": args.app_env,
            "catalog": args.catalog,
            "schema": args.schema,
            "lakebase_instance": args.lakebase_instance,
            "lakebase_database": args.lakebase_database,
        },
        frontend_ack=args.restore_unverified_frontend_ack,
        no_rollback_ack_given=args.no_rollback_ack_given,
    )
    lines = [f"{prediction.code}: {prediction.message}"]
    if args.phase == "plan" and prediction.frontend_ack is not None:
        lines.append(f"`{PURPOSE}={prediction.frontend_ack}` (carry it in step 12's operation_acks)")
        print(f"{PURPOSE}={prediction.frontend_ack}")
    if args.phase == "plan":
        _summary(args.step_summary, lines)
    if prediction.refused:
        print(f"{_PREFIX} REFUSED {prediction.code}: {prediction.message}", file=sys.stderr)
        return 2
    print(f"{_PREFIX} {prediction.code.upper()}: {prediction.message}")
    return 0


def main(argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] | None = None) -> int:
    args = _parser().parse_args(argv)
    if workspace_factory is None:
        from tools.databricks.workspace_auth import deployment_workspace_client

        workspace_factory = deployment_workspace_client
    try:
        if args.action == "env-names":
            return _env_names(args, workspace_factory)
        return _predict(args, workspace_factory)
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        what = "env-name parity not measured" if args.action == "env-names" else "REFUSED"
        print(f"{_PREFIX} {what}: the prediction could not complete ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
