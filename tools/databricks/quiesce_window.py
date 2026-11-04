#!/usr/bin/env python3
"""Plan, open and close the one bounded quiesce window of a release (M0-PR8).

  python -m tools.databricks.quiesce_window plan --position pre-lease|pre-apply ...
  python -m tools.databricks.quiesce_window open --plan P ...       # journal, then stop
  python -m tools.databricks.quiesce_window start ...               # apps start, no wait
  python -m tools.databricks.quiesce_window await ...               # wait, require, prove
  python -m tools.databricks.quiesce_window close --repinned ID ... # after the re-pin
  python -m tools.databricks.quiesce_window status --out-env F      # for the EXIT trap
  python -m tools.databricks.quiesce_window cancel-deadman --run-id N # after the close

``plan`` is read-only (GET and list calls, and file downloads for the tree
digests). It prints the public summary and writes the full plan mode 0600.
``open`` re-validates that plan (pre-apply, its digest, the exact digest ack
in ``MIP_ACK_QUIESCE_WINDOW``), admits it against the job budget
(``quiesce_window_budget``, M0-PR9: before any stop), re-checks under the
lease that blue is still the App's last deployment, arms the dead-man
(``quiesce_window_deadman``: publish this release, then one ``jobs submit``),
journals ``window_open`` and only then stops the App, identity-pinned.
``cancel-deadman`` cancels that run once the journal is closed. ``start`` journals
``window_starting``, starts, and records the deployment the start created
(a rerun resumes from the App's pending deployment, never a second start).
``await`` waits for exactly that
deployment to be terminal, requires SUCCEEDED and proves blue's restore
source, the folder's unchanged full-tree digest, the snapshot's equal digest
and the signed record's env-name set. Any failure after the start call, a
failed proof or anything else that raises, stops the App again and journals
``window_refused`` for the dead-man. ``app_deployment_rollback repin --to <id>``
then re-signs the record and checks health against the re-pinned id, and
``close`` closes the journal.

``scripts/lib/deploy_quiesce_window_lifecycle.sh`` is the only caller of the
mutating modes (``tools/databricks/stop_primitive_map.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NoReturn

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks import quiesce_window_deadman as deadman
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_release_contract import ReleaseContractError
from tools.databricks.app_rollback_record_contract import _load_record, _text
from tools.databricks.app_start_deployment import (
    StartProofError,
    env_names,
    last_deployment_is,
    prove_start_deployment,
    resume_start,
    start_app,
    tree_digest,
    wait_for_compute_active,
    wait_for_terminal,
)
from tools.databricks.quiesce_window_budget import admission, parse_deadline, require_admitted
from tools.databricks.quiesce_window_facts import (
    app_or_none,
    collect_facts,
    takes_unsigned_rebase,
)
from tools.databricks.quiesce_window_plan import (
    ACK_ENV,
    body_sha256,
    window_plan,
    with_admission,
)

_PREFIX = "[mip-quiesce-window]"
# The run never starts blue this close to the job deadline: the dead-man does.
DEADMAN_GUARD_S = 120


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, 0o600)


def _validated_plan(plan: dict[str, Any], *, acknowledgement: str) -> dict[str, Any]:
    canonical = plan.get("canonical")
    if plan.get("position") != "pre-apply" or plan.get("code") != "window":
        raise RuntimeError("only a passed pre-apply window plan can open a window")
    if not isinstance(canonical, dict) or body_sha256(canonical) != plan.get("window_sha256"):
        raise RuntimeError("the window plan's canonical body does not match its digest")
    if acknowledgement != plan["window_sha256"]:
        raise RuntimeError(f"{ACK_ENV} is not the exact window digest")
    if plan.get("reasons") != canonical.get("reasons") or not canonical.get("reasons"):
        raise RuntimeError("the window plan's reasons are not its canonical reasons")
    return canonical


def open_window(
    workspace: Any,
    *,
    app_name: str,
    plan: dict[str, Any],
    acknowledgement: str,
    lease_id: str,
    source_git_sha: str,
    assert_lease_held: Callable[[], None],
    stop: Callable[[], str],
    admit: Callable[[Mapping[str, Any]], object],
    arm: Callable[[Mapping[str, Any]], str],
    cancel: Callable[[str], object],
) -> dict[str, Any]:
    """Journal ``window_open`` for exactly the acknowledged plan, then stop the App.

    ``admit`` decides the job-budget admission of the canonical body (M0-PR9)
    before anything is read or written: a refusal stops and submits nothing.
    ``arm`` submits the dead-man after the blue re-check and before the
    journal write, so an arm that fails journals and stops nothing; a journal
    write that fails cancels the armed run before it raises.
    """

    canonical = _validated_plan(plan, acknowledgement=acknowledgement)
    admit(canonical)
    blue_id = str(canonical["blue_deployment_id"])
    if not last_deployment_is(workspace, app_name=app_name, deployment_id=blue_id):
        raise RuntimeError("blue is no longer the App's last deployment; the window is refused")
    current = journal.read(workspace, app_name=app_name)
    if journal.state_of(current) not in {"absent", "closed"}:
        raise RuntimeError("an earlier window's journal is still open; the window is refused")
    run_id = arm(canonical)
    try:
        opened = _journal_open(
            workspace,
            app_name=app_name,
            current=current,
            plan=plan,
            canonical=canonical,
            lease_id=lease_id,
            source_git_sha=source_git_sha,
            assert_lease_held=assert_lease_held,
        )
    except Exception:
        try:
            cancel(run_id)
        except Exception as exc:  # noqa: BLE001 - it reads no window_open and exits
            print(f"{_PREFIX} the armed dead-man was not cancelled ({type(exc).__name__})")
        raise
    if stop() != "stopped":
        raise RuntimeError("the App did not stop inside the journaled window")
    return opened


def _journal_open(
    workspace: Any,
    *,
    app_name: str,
    current: dict[str, Any] | None,
    plan: dict[str, Any],
    canonical: Mapping[str, Any],
    lease_id: str,
    source_git_sha: str,
    assert_lease_held: Callable[[], None],
) -> dict[str, Any]:
    blue_id = str(canonical["blue_deployment_id"])
    return journal.write(
        workspace,
        app_name=app_name,
        expected=current,
        changes={
            "state": "window_open",
            "window_sha256": plan["window_sha256"],
            "reasons": list(canonical["reasons"]),
            "blue_deployment_id": blue_id,
            "restore_source": str(canonical["restore_source"]),
            "source_tree_sha256": str(canonical["source_tree_sha256"]),
            "blue_restorable": bool(canonical["blue_restorable"]),
            "lease_id": lease_id,
            "source_git_sha": source_git_sha,
        },
        assert_lease_held=assert_lease_held,
    )


def _refuse_start(
    workspace: Any,
    *,
    app_name: str,
    exc: Exception,
    stop: Callable[[], str],
    assert_lease_held: Callable[[], None],
) -> NoReturn:
    """Stop the App, identity-pinned, journal ``window_refused`` and raise.

    Every failure after the start call lands here, not only a failed proof: an
    unproven start never keeps serving, whatever raised.
    """

    code = exc.code if isinstance(exc, StartProofError) else "start_unreadable"
    detail = str(exc) if isinstance(exc, StartProofError) else type(exc).__name__
    stopped = stop()
    latest = journal.read(workspace, app_name=app_name)
    if journal.state_of(latest) in {"window_starting", "window_started"}:
        journal.write(
            workspace,
            app_name=app_name,
            expected=latest,
            changes={"state": "window_refused", "refusal": code},
            assert_lease_held=assert_lease_held,
        )
    raise RuntimeError(
        f"{code}: the start is not provably blue ({detail}); the App is {stopped}, "
        "the window stays open"
    ) from exc


def start_window(
    workspace: Any,
    *,
    app_name: str,
    assert_lease_held: Callable[[], None],
    stop: Callable[[], str],
    deadline: int | None = None,
    clock: Callable[[], float] = time.time,
) -> str:
    """Journal the intent, start the App, journal the deployment the start created.

    ``window_starting`` is written before ``apps start``, so a start whose id
    was never journaled is not mistaken for "never started": a rerun resumes
    from the App's pending deployment and never starts a second time. Within
    ``DEADMAN_GUARD_S`` of the job deadline the dead-man owns the start
    (M0-PR9): the run journals no intent and leaves the window open for it,
    so the two never both start the App.
    """

    current = journal.read(workspace, app_name=app_name)
    state = journal.state_of(current)
    if state not in {"window_open", "window_starting"} or not (current or {}).get(
        "blue_restorable"
    ):
        raise RuntimeError("no restorable window is open to start")
    resumed = state == "window_starting"
    if not resumed and deadline is not None and clock() >= deadline - DEADMAN_GUARD_S:
        raise RuntimeError("the job deadline is too close: the dead-man owns this window's start")
    if not resumed:
        current = journal.write(
            workspace,
            app_name=app_name,
            expected=current,
            changes={"state": "window_starting"},
            assert_lease_held=assert_lease_held,
        )
    try:
        created = (resume_start if resumed else start_app)(workspace, app_name=app_name)
        journal.write(
            workspace,
            app_name=app_name,
            expected=current,
            changes={"state": "window_started", "start_deployment_id": created},
            assert_lease_held=assert_lease_held,
        )
    except Exception as exc:  # noqa: BLE001 - a start not journaled is stopped again
        _refuse_start(
            workspace,
            app_name=app_name,
            exc=exc,
            stop=stop,
            assert_lease_held=assert_lease_held,
        )
    return created


def await_window(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    assert_lease_held: Callable[[], None],
    stop: Callable[[], str],
    digest: Callable[[Any, str], str] = tree_digest,
    wait: Callable[..., object] = wait_for_terminal,
    wait_active: Callable[..., None] = wait_for_compute_active,
) -> str:
    """Prove the start's deployment is blue; on any failure keep the App stopped."""

    current = journal.read(workspace, app_name=app_name)
    if journal.state_of(current) != "window_started" or current is None:
        raise RuntimeError("no started window to await")
    try:
        record = _load_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
        if record["deployment_id"] != current["blue_deployment_id"]:
            raise StartProofError(
                "start_record_changed",
                "the signed record no longer names the window's blue deployment",
            )
        deployment = wait(
            workspace, app_name=app_name, deployment_id=current["start_deployment_id"]
        )
        try:
            folder = digest(workspace, current["restore_source"])
        except (NotFound, ResourceDoesNotExist, ReleaseContractError) as exc:
            raise StartProofError(
                "start_folder_digest", "blue's restore source is gone or unreadable"
            ) from exc
        if folder != current["source_tree_sha256"]:
            raise StartProofError(
                "start_folder_digest", "blue's restore source changed in the window"
            )
        proof = prove_start_deployment(
            workspace,
            app_name=app_name,
            deployment=deployment,
            restore_source=current["restore_source"],
            expected_tree_sha256=current["source_tree_sha256"],
            expected_env_names=env_names(record["payload"]),
            digest=digest,
        )
        wait_active(workspace, app_name=app_name)
    except Exception as exc:  # noqa: BLE001 - an unproven start never keeps serving
        _refuse_start(
            workspace,
            app_name=app_name,
            exc=exc,
            stop=stop,
            assert_lease_held=assert_lease_held,
        )
    journal.write(
        workspace,
        app_name=app_name,
        expected=current,
        changes={"state": "window_proven"},
        assert_lease_held=assert_lease_held,
    )
    return proof.deployment_id


def close_window(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    repinned: str,
    superseded_by_activation: bool,
    assert_lease_held: Callable[[], None],
) -> None:
    current = journal.read(workspace, app_name=app_name)
    if superseded_by_activation:
        if (
            journal.state_of(current) != "window_open"
            or current is None
            or current.get("blue_restorable")
        ):
            raise RuntimeError("only an open contract window is superseded by green activation")
        # closed_by green_activation must be true: green is the signed record
        # and the App's active deployment, never the window's blue.
        from tools.databricks.app_deployment_state import active_deployment_id

        record = _load_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
        if record["deployment_id"] == current["blue_deployment_id"] or (
            active_deployment_id(workspace, app_name=app_name) != record["deployment_id"]
        ):
            raise RuntimeError(
                "green is not the signed record the App serves; the contract window stays open"
            )
        changes = {"state": "closed", "closed_by": "green_activation"}
    else:
        if journal.state_of(current) != "window_proven" or current is None:
            raise RuntimeError("only a proven window closes on its re-pin")
        record = _load_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
        if (
            repinned != current["start_deployment_id"]
            or record["deployment_id"] != repinned
            or record.get("predecessor_deployment_id") != current["blue_deployment_id"]
        ):
            raise RuntimeError(
                "the signed record is not re-pinned to the window's start deployment"
            )
        changes = {"state": "closed", "repinned_deployment_id": repinned, "closed_by": "repin"}
    journal.write(
        workspace,
        app_name=app_name,
        expected=current,
        changes=changes,
        assert_lease_held=assert_lease_held,
    )


def assert_serving(workspace: Any, *, app_name: str, scope: str, lakebase_instance: str) -> None:
    """Read-only: the App is ACTIVE, nothing pending, serving the signed record's deployment."""

    from tools.databricks.app_deployment_state import active_deployment_id

    record = _load_record(
        workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
    )
    if active_deployment_id(workspace, app_name=app_name) != record["deployment_id"]:
        raise RuntimeError("the App's active deployment is not the signed record's")
    state = _text(
        getattr(getattr(workspace.apps.get(app_name), "compute_status", None), "state", None)
    )
    if state.split(".")[-1].upper() != "ACTIVE":
        raise RuntimeError("the App compute is not ACTIVE")


def _env_lines(values: dict[str, str]) -> str:
    return "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items())


def _arm(workspace: Any, args: argparse.Namespace, canonical: Mapping[str, Any]) -> str:
    """Publish this release and submit the dead-man; the run id reaches the shell at once."""

    run_id = deadman.arm_for_release(
        workspace,
        app_name=args.app_name,
        app_id=args.expected_app_id,
        canonical=canonical,
        window_sha256=str(_load_json(args.plan)["window_sha256"]),
        lease_id=args.deployment_lease_id,
        deadline=args.deadline_epoch,
        release={
            "release_id": args.release_id,
            "stage_dir": Path(args.stage_dir),
            "manifest_path": Path(args.manifest),
            "expected_commit": args.deployment_source_git_sha,
            "principal": args.expected_principal,
            "target": args.expected_target,
            "root_leaf": args.expected_root_leaf,
        },
        admit=lambda: require_admitted(canonical, args.deadline_epoch, now=time.time()),
    )
    # Before the journal write and the stop: a later failure still lets the
    # EXIT trap cancel exactly this run.
    if args.out_env is not None:
        args.out_env.write_text(
            _env_lines({"MIP_QUIESCE_WINDOW_DEADMAN_RUN_ID": run_id}), encoding="utf-8"
        )
        args.out_env.chmod(0o600)
    return run_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "applicable",
        "plan",
        "open",
        "start",
        "await",
        "close",
        "status",
        "assert-serving",
        "cancel-deadman",
    ):
        mode = sub.add_parser(name)
        mode.add_argument("--app-name", required=True)
        mode.add_argument("--scope", default="mip-app-rollback")
        mode.add_argument("--lakebase-instance", default="")
        mode.add_argument("--deployment-lease-id", default="")
        mode.add_argument("--deployment-source-git-sha", default="")
        mode.add_argument("--out-env", type=Path)
        for flag in ("--expected-app-id", "--expected-client-id", "--expected-scim-id"):
            mode.add_argument(flag, default="")
    plan = sub.choices["plan"]
    plan.add_argument("--position", required=True, choices=("pre-lease", "pre-apply"))
    plan.add_argument("--plan-gate-report", required=True, type=Path)
    plan.add_argument("--binding-payload", type=Path)
    plan.add_argument("--role-check", choices=("exact", "drift"))
    plan.add_argument("--verifier-role-check", choices=("exact", "drift"))
    plan.add_argument("--jobs-reader-role-check", choices=("exact", "drift"))
    plan.add_argument("--migration-plan", type=Path)
    # The job's deadline (MIP_JOB_DEADLINE_EPOCH, M0-PR9); empty refuses a window.
    for mode in (plan, sub.choices["open"], sub.choices["start"]):
        mode.add_argument("--deadline-epoch", default="")
    # open publishes this run's staged release for the dead-man (M0-PR9).
    opener = sub.choices["open"]
    for flag in (
        "--release-id",
        "--stage-dir",
        "--manifest",
        "--expected-principal",
        "--expected-target",
        "--expected-root-leaf",
    ):
        opener.add_argument(flag, required=flag != "--expected-root-leaf", default=None)
    sub.choices["cancel-deadman"].add_argument("--run-id", required=True)
    # plan: the rebase step 0a takes; applicable: the operator's request, which
    # it resolves against the first-install journal (MIP_QUIESCE_WINDOW_REBASE).
    for mode in (plan, sub.choices["applicable"]):
        mode.add_argument("--rebase-unverified-app", choices=("0", "1"), default="0")
    plan.add_argument("--out", required=True, type=Path)
    sub.choices["open"].add_argument("--plan", required=True, type=Path)
    close = sub.choices["close"]
    close.add_argument("--repinned", default="")
    close.add_argument("--superseded-by-activation", action="store_true")
    return parser


def _print_plan(summary: dict[str, Any], *, refused: bool) -> None:
    reasons = ",".join(summary["reasons"]) or "none"
    print(
        f"{_PREFIX} {summary['position']}: {summary['code']} (reasons {reasons}, "
        f"predicted {summary['predicted_seconds']} s, blue restorable "
        f"{str(summary['blue_restorable']).lower()}, ack {summary['ack']})"
    )
    admitted = summary["admission"]
    if admitted.get("state") not in {"not_evaluated", "not_required"}:
        print(
            f"{_PREFIX} admission {admitted['state']}: {admitted['remaining_seconds']} s left, "
            f"{admitted['required_seconds']} s required (window + restore + margin)"
        )
    if summary["deadman_recovery"]:
        print(
            f"{_PREFIX} an earlier window's dead-man restarted blue: this run re-pins it under "
            "the lease (repin --from-deadman, the window's own proofs) before step 0a"
        )
    if summary["reasons"]:
        print(f"{_PREFIX} window sha256 {summary['window_sha256']}: acknowledge with {ACK_ENV}")
    for code in summary["refusals"]:
        print(f"{_PREFIX} REFUSED {code} (remedy in the private plan)", file=sys.stderr)
    print(f"{_PREFIX} public summary: {json.dumps(summary, sort_keys=True)}")
    if refused:
        print(f"{_PREFIX} nothing was changed; the App keeps serving", file=sys.stderr)


def main(
    argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] | None = None
) -> int:
    args = _parser().parse_args(argv)
    if workspace_factory is None:
        from tools.databricks.workspace_auth import deployment_workspace_client

        workspace_factory = deployment_workspace_client
    workspace = workspace_factory()
    app_name = args.app_name

    def lease() -> None:
        from tools.databricks.app_deployment_lease import assert_held

        assert_held(
            workspace,
            app_name=app_name,
            lease_id=args.deployment_lease_id,
            source_git_sha=args.deployment_source_git_sha,
        )

    def stop() -> str:
        from tools.databricks.stop_app_fail_closed import stop_app_fail_closed

        return stop_app_fail_closed(
            app_name=app_name,
            workspace=workspace,
            expected_app_id=args.expected_app_id,
            expected_client_id=args.expected_client_id,
            expected_scim_id=args.expected_scim_id,
        )

    if args.command == "applicable":
        app = app_or_none(workspace, app_name=app_name)
        client_id = _text(getattr(app, "service_principal_client_id", None)) if app else ""
        rebase = app is not None and takes_unsigned_rebase(
            workspace,
            app_name=app_name,
            scope=args.scope,
            lakebase_instance=args.lakebase_instance,
            requested=args.rebase_unverified_app == "1",
        )
        values = {
            "MIP_QUIESCE_WINDOW_APP_PRESENT": "1" if app is not None else "0",
            "MIP_QUIESCE_WINDOW_APP_CLIENT_ID": client_id,
            "MIP_QUIESCE_WINDOW_REBASE": "1" if rebase else "0",
        }
        if args.out_env is not None:
            args.out_env.write_text(_env_lines(values), encoding="utf-8")
            args.out_env.chmod(0o600)
        print(f"{_PREFIX} applicable: App {'present' if app is not None else 'absent'}")
        return 0
    if args.command == "plan":
        facts = collect_facts(
            workspace,
            app_name=app_name,
            scope=args.scope,
            lakebase_instance=args.lakebase_instance,
            gate_report=_load_json(args.plan_gate_report),
            binding_payload=_load_json(args.binding_payload) if args.binding_payload else None,
            role_state=args.role_check,
            verifier_role_state=args.verifier_role_check,
            jobs_reader_role_state=args.jobs_reader_role_check,
            migration_plan=_load_json(args.migration_plan) if args.migration_plan else None,
            rebase_unverified_app=args.rebase_unverified_app == "1",
        )
        result = window_plan(
            facts, position=args.position, acknowledgement=os.environ.get(ACK_ENV, "").strip()
        )
        decided = admission(
            result.canonical.get("budget") if result.reasons else None,
            args.deadline_epoch,
            now=time.time(),
        )
        result = with_admission(result, decided.public(), admitted=decided.admitted)
        _write_private(args.out, result.full())
        _print_plan(result.public_summary(), refused=result.refused)
        return 2 if result.refused else 0
    if args.command == "assert-serving":
        assert_serving(
            workspace,
            app_name=app_name,
            scope=args.scope,
            lakebase_instance=args.lakebase_instance,
        )
        print(f"{_PREFIX} assert-serving: the App serves the signed record's deployment")
        return 0
    if args.command == "cancel-deadman":
        outcome = deadman.cancel_deadman(workspace, app_name=app_name, run_id=args.run_id)
        print(f"{_PREFIX} cancel-deadman: {outcome}")
        return 0
    if args.command != "status" and not (
        args.deployment_lease_id and args.deployment_source_git_sha
    ):
        raise SystemExit(f"{_PREFIX} {args.command} requires the deployment lease and source SHA")
    if args.command == "open":
        armed: dict[str, str] = {}
        opened = open_window(
            workspace,
            app_name=app_name,
            plan=_load_json(args.plan),
            acknowledgement=os.environ.get(ACK_ENV, "").strip(),
            lease_id=args.deployment_lease_id,
            source_git_sha=args.deployment_source_git_sha,
            assert_lease_held=lease,
            stop=stop,
            admit=lambda canonical: require_admitted(
                canonical, args.deadline_epoch, now=time.time()
            ),
            arm=lambda canonical: armed.setdefault("run_id", _arm(workspace, args, canonical)),
            cancel=lambda run_id: deadman.cancel_deadman(
                workspace, app_name=app_name, run_id=run_id
            ),
        )
        values = {
            "MIP_QUIESCE_WINDOW_DEADMAN_RUN_ID": armed["run_id"],
            "MIP_QUIESCE_WINDOW_SHA256": opened["window_sha256"],
            "MIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID": opened["blue_deployment_id"],
            "MIP_QUIESCE_WINDOW_TREE_SHA256": opened["source_tree_sha256"],
            "MIP_QUIESCE_WINDOW_BLUE_RESTORABLE": "1" if opened["blue_restorable"] else "0",
        }
    elif args.command == "start":
        deadline = parse_deadline(args.deadline_epoch)
        if deadline is None:
            raise SystemExit(f"{_PREFIX} start requires the job deadline the dead-man was armed with")
        values = {
            "MIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID": start_window(
                workspace,
                app_name=app_name,
                assert_lease_held=lease,
                stop=stop,
                deadline=deadline,
            )
        }
    elif args.command == "await":
        values = {
            "MIP_QUIESCE_WINDOW_PROVEN_DEPLOYMENT_ID": await_window(
                workspace,
                app_name=app_name,
                scope=args.scope,
                lakebase_instance=args.lakebase_instance,
                assert_lease_held=lease,
                stop=stop,
            )
        }
    elif args.command == "close":
        close_window(
            workspace,
            app_name=app_name,
            scope=args.scope,
            lakebase_instance=args.lakebase_instance,
            repinned=args.repinned,
            superseded_by_activation=args.superseded_by_activation,
            assert_lease_held=lease,
        )
        values = {"MIP_QUIESCE_WINDOW_JOURNAL_STATE": "closed"}
    else:
        current = journal.read(workspace, app_name=app_name)
        values = {
            "MIP_QUIESCE_WINDOW_JOURNAL_STATE": journal.state_of(current),
            "MIP_QUIESCE_WINDOW_BLUE_RESTORABLE": "1"
            if (current or {}).get("blue_restorable")
            else "0",
            "MIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID": str(
                (current or {}).get("start_deployment_id") or ""
            ),
            "MIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID": str(
                (current or {}).get("blue_deployment_id") or ""
            ),
            "MIP_QUIESCE_WINDOW_TREE_SHA256": str((current or {}).get("source_tree_sha256") or ""),
        }
        # The dead-man's marker for this window (M0-PR9): a pointer, never proof.
        marker_state, marker_start = deadman.deadman_status(
            workspace, app_name=app_name, current=current
        )
        values["MIP_QUIESCE_WINDOW_DEADMAN_STATE"] = marker_state
        values["MIP_QUIESCE_WINDOW_DEADMAN_START_DEPLOYMENT_ID"] = marker_start
        # Codes and booleans only: the deploy workflow's always() step records it.
        print(
            f"{_PREFIX} status: journal {values['MIP_QUIESCE_WINDOW_JOURNAL_STATE']}, blue "
            f"restorable {values['MIP_QUIESCE_WINDOW_BLUE_RESTORABLE'] == '1'}, dead-man "
            f"{marker_state}"
        )
    if args.out_env is not None:
        args.out_env.write_text(_env_lines(values), encoding="utf-8")
        args.out_env.chmod(0o600)
    print(f"{_PREFIX} {args.command}: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
