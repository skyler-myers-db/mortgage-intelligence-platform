"""Adopt an existing install into the deployer's bundle state by exact bind (M2-PR6).

``scripts/deploy.sh -t client --phase adopt`` (and its exact inverse,
``--phase unadopt``) runs this tool twice:

* ``plan`` before the signed lease, read-only: the pre-bind bundle plan
  (``bundle_env plan -t client -o json --adopt-acked-genie-pin``, which binds
  the one Genie title match the shell's exact ``MIP_GENIE_PIN_ACK`` names
  while nothing pins the space), this deployer's state, the legacy
  state under ``MIP_LEGACY_BUNDLE_ROOT`` and one GET by id per legacy object
  give the canonical plan of tools/databricks/adopt_legacy_map.py. It prints
  that plan's sha256. Given ``--ack-plan`` it refuses unless the digest is
  that value.
* ``apply --ack-plan <sha256>`` under the lease: it recomputes the plan
  immediately before the first state edit and refuses with nothing changed
  unless the digest is the acknowledged one (the substitute for the deferred
  owner operation attestation; the review mode is single_maintainer_audited).
  ``adopt`` then binds exactly the plan's keys
  (``bundle_env deployment bind <key> <id> -t client --auto-approve``), proves
  the state binds each to its id, plans again and judges that plan with the
  bundle plan gate's adopt mode (``bundle_plan_adopt``: 0 delete, 0 recreate,
  no Lakebase instance change, exactly the expected creates, no dashboard
  move, every rename looked up first). Any failure after the first bind
  attempt runs the inverse: each attempted key the state binds to its planned
  id is unbound, and the state is read again to prove it. ``unadopt`` unbinds
  exactly what adopt bound (each legacy key the state binds to its legacy id).

Neither phase stops, starts or deploys the App, changes a grant or deletes
anything: the only mutation is the deployer's own bundle state, through the
bundle CLI. bundle_env admits a non-App bind or unbind only when this run's
plan names it, its digest is the operator's ``MIP_ADOPT_PLAN_ACK``, it names
this install and the signed lease is held (``bundle_env_adopt``); the CLI
never inherits the proof-signing key, and its output (which names ids) stays
out of the log.

Signing the live deployment as an adopted blue (``adopt-live``: the
full-tree digest, the immutable ``app-src/adopted-*`` copy, supersede and
freeze) is M2-PR7's; this tool only binds. Exit codes: 0 done (or planned),
2 refused with nothing left changed, 3 refused with the inverse unproven (the
report names what the state may still bind: run ``--phase unadopt``, whose
live checks, like adopt's, accept that half-bound state).

A plan the parser refuses (``PlanShapeError``) prints only ``malformed_plan``,
the symbolic resource and the field class: the repository's run logs are
public, and the parser's message can name a change path that carries a remote
map key. The message and the document's value-free skeleton go to the private
report (``malformed_plan``), as the CLI's own output does on a failed call
(``cli_errors``). Before the binds that ends the run, exit 2; after them it is
``bind_failed`` with the undo. Under GitHub Actions the deploy publishes the
report create-only to the deployer's plan-reports folder
(scripts/lib/deploy_plan_report_lifecycle.sh).

A plan another CLI made refuses so too, before any state read: the parser and
``compute_plan`` each hold its ``cli_version`` to exactly
``REVIEWED_CLI_VERSION``; the public line adds both version strings.

Every bundle_env call (each bind and each of the undo's unbinds included)
re-resolves that title match: the space list, its permissions and the
deploying identity. A direct CAN_MANAGE holder that changes mid-run makes the
pin plan another one, so each later call refuses ``the ack names another
plan``; when that lands among the binds, the undo's unbinds refuse too and the
run exits 3, half-bound. The recovery is ``--phase unadopt`` with a fresh
``MIP_GENIE_PIN_ACK`` (the new pin plan's sha256, which the deploy's step-0
pin check prints).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks import adopt_legacy_map as legacy_map  # noqa: E402
from tools.databricks import bundle_env_adopt, bundle_plan_gate  # noqa: E402
from tools.databricks.bundle_plan_adopt import POSITION, AdoptExpectation  # noqa: E402
from tools.databricks.bundle_plan_shape import (  # noqa: E402
    BundlePlan,
    PlanShapeError,
    parse_plan,
    require_reviewed_cli,
)
from tools.databricks.bundle_state import STATE_FILE, bound_id, read_state, state_path  # noqa: E402

APP_KEY = "mip_app"
_PREFIX = "[mip-adopt]"
MALFORMED_REMEDY = (
    f"{bundle_plan_gate.REMEDIES['malformed_plan']}; the parser's message and the plan's "
    "value-free skeleton are in the private report (malformed_plan)"
)
# Never inherited by the bundle CLI: the deploy's signing authorities.
_PRIVATE_ENV = ("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY")


class BundleCliError(RuntimeError):
    """A bundle CLI call failed (the message names the call, never a value)."""


class AdoptRefused(RuntimeError):
    """An apply refused; ``code`` is a reviewed refusal code, ``result`` what it got to."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        inverse_proven: bool = True,
        result: ApplyResult | None = None,
        malformed: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.inverse_proven = inverse_proven
        self.result = result
        self.malformed = malformed  # a post-bind PlanShapeError's private record


@dataclass(frozen=True)
class Authority:
    """The acknowledged plan on disk and the held lease, for bundle_env to verify before any state edit."""

    plan_path: Path
    sha256: str
    lease_id: str
    source_git_sha: str


class HeldLease(Protocol):
    """The signed lease this run holds: asserted before every edit here, and in bundle_env again."""

    lease_id: str
    source_git_sha: str

    def __call__(self) -> None: ...


@dataclass(frozen=True)
class SignedLease:
    check: Callable[[], None]
    lease_id: str
    source_git_sha: str

    def __call__(self) -> None:
        self.check()


class BundleCli(Protocol):
    def plan(self) -> BundlePlan: ...

    def bind(self, key: str, live_id: str, authority: Authority) -> None: ...

    def unbind(self, key: str, authority: Authority) -> None: ...


class BundleEnvCli:
    """``python -m tools.databricks.bundle_env`` with the client target's own variables.

    Every call passes ``bundle_env_adopt.ADOPT_GENIE_FLAG``: before an adoption
    nothing pins the governed Genie space, so bundle_env binds the one title
    match the shell's exact ``MIP_GENIE_PIN_ACK`` names (a pin, once one
    exists, wins). Each call re-resolves it, the undo's unbinds included.
    """

    def __init__(self, python: str = sys.executable) -> None:
        self.python = python
        self.errors: list[str] = []

    def _run(self, call: str, args: list[str]) -> str:
        env = {name: value for name, value in os.environ.items() if name not in _PRIVATE_ENV}
        result = subprocess.run(
            [self.python, "-m", "tools.databricks.bundle_env", *args],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.errors.append(f"{call}: exit {result.returncode}: {result.stderr[-4000:]}")
            raise BundleCliError(f"bundle {call} failed (exit {result.returncode})")
        return result.stdout

    def plan(self) -> BundlePlan:
        args = ["plan", "-t", "client", "-o", "json", bundle_env_adopt.ADOPT_GENIE_FLAG]
        return parse_plan(self._run("plan", args))

    def bind(self, key: str, live_id: str, authority: Authority) -> None:
        self._run(
            f"bind of {key}",
            ["deployment", "bind", key, live_id, "-t", "client", "--auto-approve", *_flags(authority)],
        )

    def unbind(self, key: str, authority: Authority) -> None:
        self._run(f"unbind of {key}", ["deployment", "unbind", key, "-t", "client", *_flags(authority)])


def _flags(authority: Authority) -> list[str]:
    """The wrapper-only flags of one state edit: the adopt authority's four, then the Genie flag."""

    values = (str(authority.plan_path), authority.sha256, authority.lease_id, authority.source_git_sha)
    pairs = [part for flag, value in zip(bundle_env_adopt.FLAGS, values, strict=True) for part in (flag, value)]
    return [*pairs, bundle_env_adopt.ADOPT_GENIE_FLAG]


def inputs_from_profile(profile: Any) -> legacy_map.AdoptInputs:
    deployer = profile.identity_ids.deployer
    return legacy_map.AdoptInputs(
        client_key=profile.client,
        deployer_root=state_path(deployer=deployer, client_key=profile.client).removesuffix(f"/{STATE_FILE}"),
        legacy_root=profile.legacy_bundle_root,
        dashboard_parent_path=profile.dashboard_parent_path,
    )


def _states(workspace: Any, inputs: legacy_map.AdoptInputs) -> tuple[Any, Any]:
    ours = read_state(workspace, f"{inputs.deployer_root}/{STATE_FILE}")
    legacy = read_state(workspace, f"{inputs.legacy_root}/{STATE_FILE}") if inputs.legacy_root else None
    return ours, legacy


def compute_plan(phase: str, inputs: legacy_map.AdoptInputs, cli: BundleCli, workspace: Any) -> legacy_map.AdoptPlan:
    """The read-only plan: the pre-bind bundle plan, both states and the live objects."""

    bundle = require_reviewed_cli(cli.plan())
    ours, legacy = _states(workspace, inputs)
    return legacy_map.compute(phase, inputs, bundle, ours, legacy, workspace)


def _write_private(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, 0o600)


def _bound(
    workspace: Any, inputs: legacy_map.AdoptInputs, edits: Sequence[legacy_map.Binding]
) -> list[legacy_map.Binding]:
    """The edits this deployer's state binds to their planned ids, from one fresh read."""

    state = read_state(workspace, f"{inputs.deployer_root}/{STATE_FILE}")
    return [edit for edit in edits if bound_id(state, f"resources.{edit.resource}") == edit.live_id]


def _inverse(
    attempted: Sequence[legacy_map.Binding],
    cli: BundleCli,
    authority: Authority,
    workspace: Any,
    inputs: legacy_map.AdoptInputs,
    lease: Callable[[], None],
) -> bool:
    """Unbind every attempted key the state binds to its planned id; True once proven."""

    try:
        bound = _bound(workspace, inputs, attempted)
    except Exception:  # noqa: BLE001 - unreadable: try every attempted key
        bound = list(attempted)
    for edit in reversed(bound):
        try:
            lease()
            cli.unbind(edit.key, authority)
        except Exception:  # noqa: BLE001 - proven or not below
            continue
    try:
        return not _bound(workspace, inputs, attempted)
    except Exception:  # noqa: BLE001 - a state that cannot be read proves nothing
        return False


@dataclass
class ApplyResult:
    plan: legacy_map.AdoptPlan
    gate: Any = None  # the post-bind PlanReport, adopt only


def apply(
    phase: str,
    ack: str,
    inputs: legacy_map.AdoptInputs,
    cli: BundleCli,
    workspace: Any,
    *,
    lease: HeldLease,
    plan_dir: Path,
) -> ApplyResult:
    """Recompute, compare with ``ack``, then edit exactly the plan's state; raises AdoptRefused."""

    lease()
    plan = compute_plan(phase, inputs, cli, workspace)
    result = ApplyResult(plan)
    if plan.refused:
        _print_plan(plan)
        raise AdoptRefused("plan_refused", f"the {phase} plan refuses ({len(plan.refusals)} refusals)", result=result)
    if plan.sha256 != ack:
        raise AdoptRefused(
            "ack_mismatch",
            f"the recomputed {phase} plan is {plan.sha256}, not the acknowledged digest; nothing was changed",
            result=result,
        )
    path = plan_dir / f"adopt-plan-{plan.sha256[:12]}.json"
    _write_private(path, plan.document)
    authority = Authority(path, plan.sha256, lease.lease_id, lease.source_git_sha)
    if phase == "unadopt":
        try:
            for edit in plan.edits:
                lease()
                cli.unbind(edit.key, authority)
        except Exception as exc:
            # No automatic re-bind: the inverse of a partial unadopt is a reviewed adopt.
            raise AdoptRefused(
                "unbind_failed", f"{type(exc).__name__} during the unbinds", inverse_proven=False, result=result
            ) from exc
        if _bound(workspace, inputs, plan.edits):
            raise AdoptRefused(
                "state_unverified", "the state still binds a key unadopt unbound", inverse_proven=False, result=result
            )
        return result
    attempted: list[legacy_map.Binding] = []
    try:
        for edit in plan.edits:
            lease()
            attempted.append(edit)
            cli.bind(edit.key, edit.live_id, authority)
        if len(_bound(workspace, inputs, plan.edits)) != len(plan.edits):
            raise AdoptRefused("state_unverified", "the state does not bind every planned key to its id")
        expectation = AdoptExpectation(frozenset(plan.bound_after), frozenset(plan.expected_creates))
        result.gate = bundle_plan_gate.evaluate(
            cli.plan(),
            position=POSITION,
            app_key=APP_KEY,
            acknowledgement="",
            workspace_factory=lambda: workspace,
            adopt=expectation,
        )
        if result.gate.refused:
            raise AdoptRefused("gate_refused", f"the post-bind plan refuses ({', '.join(result.gate.codes)})")
    except Exception as exc:
        proven = _inverse(attempted, cli, authority, workspace, inputs, lease)
        code = exc.code if isinstance(exc, AdoptRefused) else "bind_failed"
        message = str(exc) if isinstance(exc, AdoptRefused) else f"{type(exc).__name__} during the binds"
        malformed = None
        if isinstance(exc, PlanShapeError):
            # Public: one class (any subclass) and its public reason; the message and skeleton are private.
            message = f"PlanShapeError during the binds (malformed_plan {exc.public})"
            malformed = exc.private()
        raise AdoptRefused(code, message, inverse_proven=proven, result=result, malformed=malformed) from exc
    return result


def _print_plan(plan: legacy_map.AdoptPlan) -> None:
    summary = plan.public_summary()
    counts = ", ".join(f"{count} {name}" for name, count in summary["counts"].items())
    print(f"{_PREFIX} {plan.phase} plan {plan.sha256}: {counts}")
    if plan.phase == "adopt":
        print(
            f"{_PREFIX} expected creates after the binds: {', '.join(plan.expected_creates) or 'none'}; "
            f"dashboard parent_path {legacy_map.DASHBOARD_POLICY} (spike S4: a move recreates)"
        )
    for refusal in plan.refusals:
        detail = f" ({refusal.reason})" if refusal.reason else ""
        print(f"{_PREFIX} REFUSED {refusal.code} {refusal.resource}{detail}: {refusal.remedy}", file=sys.stderr)
    print(f"{_PREFIX} public summary: {json.dumps(summary, sort_keys=True)}")


def _profile_inputs() -> legacy_map.AdoptInputs:
    from tools.clients.environment import from_local, resolve

    return inputs_from_profile(resolve(from_local(os.environ, REPO / ".env.local")))


def _workspace() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def _held_lease(workspace: Any, args: argparse.Namespace) -> HeldLease:
    from tools.databricks import app_deployment_lease

    check = app_deployment_lease.held_assertion(
        workspace, app_name=args.app_name, lease_id=args.lease_id, source_git_sha=args.source_git_sha
    )
    return SignedLease(check, args.lease_id, args.source_git_sha)


def main(
    argv: list[str] | None = None,
    *,
    inputs: Callable[[], legacy_map.AdoptInputs] = _profile_inputs,
    workspace_factory: Callable[[], Any] = _workspace,
    cli_factory: Callable[[], BundleCli] = BundleEnvCli,
    lease_factory: Callable[[Any, argparse.Namespace], HeldLease] = _held_lease,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--phase", required=True, choices=legacy_map.PHASES)
        command.add_argument("--report-out", type=Path)
        command.add_argument("--ack-plan", required=name == "apply", default="")
        if name == "apply":
            for flag in ("--app-name", "--lease-id", "--source-git-sha"):
                command.add_argument(flag, required=True)
    args = parser.parse_args(argv)
    from tools.clients.errors import ProfileRefusal
    from tools.databricks.bundle_state import BundleStateError

    report: dict[str, Any] = {}
    cli = cli_factory()
    try:
        adopt_inputs = inputs()
        workspace = workspace_factory()
        if args.command == "plan":
            plan = compute_plan(args.phase, adopt_inputs, cli, workspace)
            report = plan.full()
            _print_plan(plan)
            if plan.refused:
                return 2
            if args.ack_plan and args.ack_plan != plan.sha256:
                print(f"{_PREFIX} REFUSED ack_mismatch: MIP_ADOPT_PLAN_ACK is not this plan's sha256", file=sys.stderr)
                return 2
            if not args.ack_plan:
                print(f"{_PREFIX} read-only; to apply, review the private report, then set MIP_ADOPT_PLAN_ACK={plan.sha256}")
            return 0
        with tempfile.TemporaryDirectory(prefix="mip-adopt-") as private:
            result = apply(
                args.phase,
                args.ack_plan,
                adopt_inputs,
                cli,
                workspace,
                lease=lease_factory(workspace, args),
                plan_dir=Path(private),
            )
        report = result.plan.full()
        if result.gate is not None:
            report["post_bind_gate"] = result.gate.full()
            print(f"{_PREFIX} post-bind plan gate: {json.dumps(result.gate.public_summary(), sort_keys=True)}")
        verb = "bound" if args.phase == "adopt" else "unbound"
        print(f"{_PREFIX} {args.phase} applied: {len(result.plan.edits)} keys {verb} (plan {result.plan.sha256})")
        return 0
    except AdoptRefused as refusal:
        partial = refusal.result
        if partial is not None:
            report = partial.plan.full()
            if partial.gate is not None:
                report["post_bind_gate"] = partial.gate.full()
                print(f"{_PREFIX} post-bind plan gate: {json.dumps(partial.gate.public_summary(), sort_keys=True)}")
        if refusal.malformed is not None:
            report["malformed_plan"] = refusal.malformed
        print(f"{_PREFIX} REFUSED {refusal.code}: {refusal}", file=sys.stderr)
        if not refusal.inverse_proven:
            again = " again" if args.phase == "unadopt" else ""
            print(
                f"{_PREFIX} the inverse is unproven: the state may still bind keys this run bound; "
                f"read the private report, then run --phase unadopt{again}",
                file=sys.stderr,
            )
            return 3
        print(f"{_PREFIX} nothing is left changed: every bind this run made was undone", file=sys.stderr)
        return 2
    except ProfileRefusal as refusal:
        print(f"{_PREFIX} client profile refused:", file=sys.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=sys.stderr)
        return 2
    except PlanShapeError as exc:
        # Before any edit (the plan, or apply's recompute): nothing changed.
        report["malformed_plan"] = exc.private()
        print(f"{_PREFIX} REFUSED malformed_plan {exc.public}: {MALFORMED_REMEDY}", file=sys.stderr)
        return 2
    except (BundleCliError, BundleStateError, ValueError) as exc:
        print(f"{_PREFIX} REFUSED: {exc if not isinstance(exc, ValueError) else type(exc).__name__}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        print(f"{_PREFIX} REFUSED: could not read the workspace ({type(exc).__name__})", file=sys.stderr)
        return 2
    finally:
        errors = getattr(cli, "errors", None)
        if errors:
            report["cli_errors"] = list(errors)
        if args.report_out is not None and report:
            _write_private(args.report_out, report)


if __name__ == "__main__":
    raise SystemExit(main())
