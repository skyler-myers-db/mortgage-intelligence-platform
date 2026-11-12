"""Sign the live deployment as an adopted blue: a v7 core record, never an App mutation (M2-PR7).

  python -m tools.databricks.adopt_live plan --mode adopt|supersede|unadopt ...
  python -m tools.databricks.adopt_live apply --mode adopt|supersede|unadopt --ack <sha256> ...

scripts/deploy.sh runs this in the adopt phases (scripts/lib/deploy_adopt_phase_lifecycle.sh),
``plan`` before the signed lease and ``apply`` under it, right after
install_state_adopt's bind (adopt) or unbind (unadopt). It never stops,
starts, deploys or updates the App: its only writes are the create-only
``app-src/adopted-*`` copy, the rollback scope (created when absent) and one
signed record version.

``plan`` (read-only) proves every precondition of tools/databricks/adopt_live_facts.py,
judges the record slot, and prints the plan (digests and reviewed words only)
and the sha256 of its purpose-named canonical body (tools/databricks/operation_ack.py):

* **adopt** (purpose ``adopt_live``): the slot holds no record, a retired
  tombstone (a later adopt starts clean) or this deployment's own adopted
  record (an idempotent re-run); any other record refuses
  (``record_present``). The profile's tier must be core (``tier_agentic``,
  adopt and supersede only): the adopted deployment carries no Gateway, so
  its record cannot carry the Gateway resource proof the agentic kind
  requires.
* **supersede** (purpose ``supersede``, ``--supersede <sha256 of the record's
  deployment id>``): the slot holds an adopted record for exactly that
  deployment and the active deployment is another one; the same proofs then
  re-sign onto the active deployment with ``predecessor_deployment_id``.
* **unadopt** (purpose ``unadopt``): the slot holds an adopted record (or
  already its tombstone); ``apply`` writes the tombstone version
  (``provenance: retired``). Nothing is deleted: the copy and every field stay.
  No record retires nothing, in either tier, so the bind's unadopt recovery
  (M2-PR6) still runs after an adopt refused past its binds; any other record
  refuses.

Whenever the slot holds a record, the plan names that exact signed version's
sha256 (``record_sha256``), so an ack covers one record version: an unadopt's
ack never replays after a re-adoption, nor an adopt's over another tombstone.

``apply`` asserts the signed lease, recomputes the plan immediately before the
first write and refuses on any difference from the acknowledged digest. adopt
and supersede then check the live bindings equal the profile's reviewed ones
(the bundle summary after the binds), copy the source folder into the
deployer's ``app-src/adopted-<id12>-<digest12>`` create-only with
app_release_source's publish, re-hash the copy remotely (a mismatch refuses
and no record is written), re-check that the active deployment is still the
one planned, and write the v7 core record (``provenance: adopted``,
``restore_payload`` null until a release proves it before activation) under
the lease, read back exactly.

Spike S7 is recorded (a throwaway App, 2026-11-10): ``default_source_code_path``
is not settable. The Apps API's create-update rejects the mask, its plain
update returns success and ignores the field, and only a deployment moves it
(a FAILED one too). So the App's default source is never pointed at the copy:
``S7_DEFAULT_SOURCE_SETTABLE`` stays False and ``point_default_source_at_copy``
refuses for good. The default source stays on D0's own folder, and any later
start (a window's tier a, the dead-man) snapshots that folder's CURRENT bytes,
which the start proofs' full-tree digests check.

Exit codes: 0 done (or planned), 2 refused with nothing changed by this run.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks import adopted_source_copy as source  # noqa: E402
from tools.databricks import app_release_contract as contract  # noqa: E402
from tools.databricks.adopt_live_facts import (  # noqa: E402
    AdoptLiveRefused,
    Facts,
    Readers,
    assert_profile_bindings,
    gather,
    health_reader,
    plan_facts,
    reviewed_bindings,
    sha256_text,
    stable_deployment,
    treatment_reader,
)
from tools.databricks.app_rollback_record_contract import (  # noqa: E402
    V7_RECORD_VERSION,
    _text,
    load_signed_record,
    save_versioned_record,
)
from tools.databricks.app_rollback_record_v7 import (  # noqa: E402
    V7RecordError,
    build_v7_record,
    tier_of_profile,
    unsigned,
)
from tools.databricks.operation_ack import AckRefused, ack_digest, require_ack  # noqa: E402

MODES = ("adopt", "supersede", "unadopt")
PURPOSE = {"adopt": "adopt_live", "supersede": "supersede", "unadopt": "unadopt"}
_PREFIX = "[mip-adopt-live]"
# Spike S7 (recorded 2026-11-10): NOT settable. The Apps API's create-update
# rejects the default_source_code_path mask, its plain update returns success
# and ignores the field, and only a deployment moves it. It stays False for good.
S7_DEFAULT_SOURCE_SETTABLE = False


def point_default_source_at_copy(
    workspace: Any, *, app_name: str, restore_source_path: str
) -> None:
    """Point the App's default source at the immutable copy: refused for good (spike S7)."""

    raise AdoptLiveRefused(
        "default_source_not_settable",
        "spike S7 measured that the Apps API cannot set default_source_code_path (create-update "
        "rejects the mask, update ignores the field; only a deployment moves it), so the App's "
        "default source stays as it is",
    )


@dataclass(frozen=True)
class Target:
    """Where the copy goes and which record slot this run judges."""

    app_name: str
    scope: str
    lakebase_instance: str
    principal: str
    root_leaf: str
    agentic_tier: str
    supersede: str = ""

    def folder_path(self, facts: Facts) -> str:
        name = contract.adopted_folder_name(
            deployment_id=facts.deployment_id, tree_sha256=facts.tree_sha256
        )
        return contract.adopted_folder_path(
            principal=self.principal, target="client", root_leaf=self.root_leaf, folder=name
        )


def _record(workspace: Any, target: Target) -> dict[str, Any] | None:
    try:
        return load_signed_record(
            workspace,
            app_name=target.app_name,
            scope=target.scope,
            expected_lakebase_instance=target.lakebase_instance,
        )
    except RuntimeError as exc:
        if "App rollback secret scope is absent" in str(exc):
            return None  # no scope holds no record; apply creates the scope
        raise AdoptLiveRefused(
            "record_unreadable", f"the rollback record does not verify: {exc}"
        ) from exc


def record_digest(record: Mapping[str, Any]) -> str:
    """The sha256 of one signed record version (its signature included), canonical JSON."""

    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256_text(canonical)


def _record_state(
    mode: str, record: Mapping[str, Any] | None, target: Target, facts: Facts | None
) -> str:
    """The slot's state for ``mode``, or a ``record_present``/``supersede_*`` refusal."""

    version = None if record is None else record.get("version")
    provenance = record.get("provenance") if version == V7_RECORD_VERSION and record else None
    if mode == "unadopt":
        # No record (an adopt refused after its binds) retires nothing, so the
        # unbind recovery of M2-PR6 still runs; a captured blue is not adopt's.
        if record is None or provenance in {"adopted", "retired"}:
            return "absent" if record is None else str(provenance)
        raise AdoptLiveRefused("record_present", "unadopt retires an adopted record only")
    if mode == "supersede":
        if provenance != "adopted" or sha256_text(str(record["deployment_id"])) != target.supersede:  # type: ignore[index]
            raise AdoptLiveRefused(
                "supersede_mismatch", "the slot holds no adopted record for the named deployment"
            )
        if facts is not None and facts.deployment_id == record["deployment_id"]:  # type: ignore[index]
            raise AdoptLiveRefused(
                "supersede_same_deployment", "the active deployment is the record's own"
            )
        return "adopted_other"
    if record is None or provenance == "retired":
        return "absent" if record is None else "retired"
    if (
        provenance == "adopted"
        and facts is not None
        and record["deployment_id"] == facts.deployment_id
    ):
        return "adopted_same"
    # The supersede's selector is a digest, never the id (deploy-dev.yml's
    # operation_acks key supersedes_deployment, B2).
    selector = (
        f"; through deploy-dev.yml: operation_acks supersedes_deployment="
        f"{sha256_text(str(record['deployment_id']))} (this adopted record's deployment sha256) "
        "with the supersede ack its read-only plan prints"
        if provenance == "adopted" and record is not None
        else ""
    )
    raise AdoptLiveRefused(
        "record_present",
        "a signed rollback record already names another deployment or provenance; adopt-live "
        f"signs only an install with no record (use --supersede for a newer active deployment){selector}",
    )


def compute_plan(
    workspace: Any, mode: str, target: Target, readers: Readers
) -> tuple[dict[str, object], Facts | None, dict[str, Any] | None]:
    """The canonical read-only plan for ``mode``, with the facts and record behind it."""

    if mode not in MODES:
        raise AdoptLiveRefused("mode_invalid", f"mode must be one of {', '.join(MODES)}")
    tier = tier_of_profile(target.agentic_tier)
    record = _record(workspace, target)
    if mode == "unadopt":
        # Any tier: an absent record retires nothing, so the unbind recovery of
        # M2-PR6 runs in the agentic tier too, and an adopted record is core.
        state = _record_state(mode, record, target, None)
        retired: dict[str, object] = {"mode": mode, "record_state": state}
        if record is not None:
            retired["record_sha256"] = record_digest(record)
            retired["record_deployment_sha256"] = sha256_text(str(record["deployment_id"]))
            retired["record_tree_sha256"] = str(record["source_tree_sha256"])
        return retired, None, record
    if tier != "core":
        raise AdoptLiveRefused(
            "tier_agentic",
            "adopt-live signs a core-tier record only: the adopted deployment carries no Gateway, "
            "and the agentic tier waits for the estate inventory (M4-PR1)",
        )
    facts = gather(workspace, app_name=target.app_name, readers=readers)
    state = _record_state(mode, record, target, facts)
    plan: dict[str, object] = {
        "mode": mode,
        "tier": "core",
        "record_state": state,
        # The exact record version this plan acts on, so an ack covers one version only.
        "record_sha256": None if record is None else record_digest(record),
        "restore_folder": target.folder_path(facts).rsplit("/", 1)[-1],
        "s7_default_source": "settable" if S7_DEFAULT_SOURCE_SETTABLE else "not_settable",
        **plan_facts(facts),
    }
    if mode == "supersede":
        plan["supersedes_deployment_sha256"] = target.supersede
    return plan, facts, record


def _copy_and_rehash(workspace: Any, facts: Facts, path: str, readers: Readers) -> None:
    try:
        source.copy_to(workspace, facts.files, release_path=path, commit=facts.git_sha)
    except Exception as exc:  # noqa: BLE001 - the class only: a message can name the folder
        raise AdoptLiveRefused(
            "copy_failed",
            f"the create-only copy failed ({type(exc).__name__}); an occupied adopted folder "
            "whose bytes differ is reused only byte for byte",
        ) from exc
    if readers.tree(workspace, path) != facts.tree_sha256:
        raise AdoptLiveRefused(
            "copy_rehash_mismatch", "the copy's full-tree digest is not the deployment's"
        )


def apply(
    workspace: Any,
    mode: str,
    target: Target,
    readers: Readers,
    *,
    ack: str,
    lease: Callable[[], None],
    reviewed_bindings: Callable[[], list[dict[str, object]]],
    ensure_scope: Callable[[], None],
) -> dict[str, Any] | None:
    """Recompute, require the exact ack, then copy and sign; the persisted record, if any."""

    lease()
    plan, facts, record = compute_plan(workspace, mode, target, readers)
    digest = require_ack(PURPOSE[mode], plan, ack)
    if mode == "unadopt":
        if record is None or record["provenance"] == "retired":
            return record  # nothing to retire
        tombstone = {
            **unsigned(record),
            "provenance": "retired",
            "ack_purpose": "unadopt",
            "ack_sha256": digest,
        }
        return _persist(workspace, target, tombstone, lease=lease, ensure_scope=ensure_scope)
    assert facts is not None
    assert_profile_bindings(facts, reviewed_bindings())
    path = target.folder_path(facts)
    lease()
    _copy_and_rehash(workspace, facts, path, readers)
    if S7_DEFAULT_SOURCE_SETTABLE:
        point_default_source_at_copy(workspace, app_name=target.app_name, restore_source_path=path)
    if (
        plan["record_state"] == "adopted_same"
        and record is not None
        and record["restore_source_path"] == path
    ):
        return record  # an idempotent re-run: the same deployment, bytes and copy
    signed = build_v7_record(
        expected_lakebase_instance=target.lakebase_instance,
        tier="core",
        provenance="adopted",
        app_name=target.app_name,
        deployment_id=facts.deployment_id,
        git_sha=facts.git_sha,
        app_service_principal_client_id=facts.app_client_id,
        app_service_principal_scim_id=facts.app_scim_id,
        app_resources=facts.live_resources,
        app_acl=facts.app_acl,
        treatment_mode=facts.treatment_mode,
        env_names=list(facts.env_names),
        source_code_path=facts.source_code_path,
        source_tree_sha256=facts.tree_sha256,
        restore_source_path=path,
        frontend_provenance=facts.frontend_provenance,
        frontend_reference_sha256=facts.frontend_reference_sha256,
        predecessor_deployment_id=record["deployment_id"]
        if mode == "supersede" and record
        else None,
        ack_purpose=PURPOSE[mode],
        ack_sha256=digest,
    )
    if (
        _text(
            getattr(stable_deployment(workspace, app_name=target.app_name), "deployment_id", None)
        )
        != facts.deployment_id
    ):
        raise AdoptLiveRefused(
            "deployment_unstable", "the active deployment changed during adopt-live"
        )
    return _persist(workspace, target, signed, lease=lease, ensure_scope=ensure_scope)


def _persist(
    workspace: Any,
    target: Target,
    record: dict[str, Any],
    *,
    lease: Callable[[], None],
    ensure_scope: Callable[[], None],
) -> dict[str, Any]:
    lease()
    ensure_scope()
    lease()
    save_versioned_record(workspace, scope=target.scope, record=record)
    persisted = _record(workspace, target)
    if persisted is None or unsigned(persisted) != unsigned(record):
        raise AdoptLiveRefused("record_unverified", "the signed record did not read back exactly")
    return persisted


# --- the command line ----------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--mode", required=True, choices=MODES)
        command.add_argument("--app-name", required=True)
        command.add_argument("--scope", required=True)
        command.add_argument("--lakebase-instance", required=True)
        command.add_argument("--expected-principal", required=True)
        command.add_argument("--expected-root-leaf", required=True)
        command.add_argument("--agentic-tier", required=True, choices=("off", "on"))
        command.add_argument("--catalog", default="mip")
        command.add_argument("--token-env", default="MIP_BEARER_TOKEN")
        command.add_argument("--main-ref", default="refs/remotes/origin/main")
        command.add_argument("--source-git-sha", required=True)
        # Build frontend/dist from the health sha here and compare (else: unverified).
        command.add_argument("--build-reference-dist", action="store_true")
        command.add_argument("--supersede", default="", metavar="SHA256")
        command.add_argument("--ack", default="", metavar="SHA256")
        if name == "apply":
            command.add_argument("--lease-id", required=True)
    return parser


def main(
    argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] | None = None
) -> int:
    args = _parser().parse_args(argv)
    if (args.mode == "supersede") != bool(args.supersede):
        print(
            f"{_PREFIX} REFUSED: --supersede <sha256> goes with --mode supersede only",
            file=sys.stderr,
        )
        return 2
    if workspace_factory is None:
        from tools.databricks.workspace_auth import deployment_workspace_client

        workspace_factory = deployment_workspace_client
    workspace = workspace_factory()
    target = Target(
        app_name=args.app_name,
        scope=args.scope,
        lakebase_instance=args.lakebase_instance,
        principal=args.expected_principal,
        root_leaf=args.expected_root_leaf,
        agentic_tier=args.agentic_tier,
        supersede=args.supersede,
    )
    with tempfile.TemporaryDirectory(prefix="mip-adopt-reference-") as scratch:
        readers = Readers(
            health_sha=health_reader(workspace, args.app_name, args.token_env),
            treatment=treatment_reader(workspace, args.catalog),
            repo=REPO,
            main_ref=args.main_ref,
            source_sha=args.source_git_sha,
            build_reference=(
                (lambda commit: source.build_reference_dist(REPO, commit, Path(scratch)))
                if args.build_reference_dist
                else None
            ),
        )
        return _run(workspace, args, target, readers)


def _run(workspace: Any, args: argparse.Namespace, target: Target, readers: Readers) -> int:
    purpose = PURPOSE[args.mode]
    try:
        if args.command == "plan":
            plan, facts, _record_now = compute_plan(workspace, args.mode, target, readers)
            digest = ack_digest(purpose, plan)
            print(f"{_PREFIX} {args.mode} plan: {json.dumps(plan, sort_keys=True)}")
            if facts is not None:
                print(f"{_PREFIX} frontend/dist: {facts.frontend_detail}")
            print(f"{_PREFIX} {purpose} plan sha256 {digest}")
            if args.ack:
                require_ack(purpose, plan, args.ack)
            return 0
        from tools.databricks import app_deployment_lease
        from tools.databricks.app_rollback_secret_scope import ensure_owned_app_rollback_scope

        lease = app_deployment_lease.held_assertion(
            workspace,
            app_name=args.app_name,
            lease_id=args.lease_id,
            source_git_sha=args.source_git_sha,
        )
        record = apply(
            workspace,
            args.mode,
            target,
            readers,
            ack=args.ack,
            lease=lease,
            reviewed_bindings=reviewed_bindings,
            ensure_scope=lambda: ensure_owned_app_rollback_scope(
                workspace, app_name=args.app_name, scope=args.scope
            ),
        )
        if record is None:
            print(f"{_PREFIX} unadopt applied: no adopted record, nothing to retire")
            return 0
        print(
            f"{_PREFIX} {args.mode} applied: a signed v7 {record['tier']} record "
            f"({record['provenance']}, frontend {record['frontend_provenance']}); the App was not "
            "stopped, started, deployed or updated"
        )
        return 0
    except (AdoptLiveRefused, AckRefused) as exc:
        print(f"{_PREFIX} REFUSED {exc.code}: {exc}", file=sys.stderr)
    except V7RecordError as exc:
        print(f"{_PREFIX} REFUSED record_invalid: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        print(
            f"{_PREFIX} REFUSED: could not complete the read or write ({type(exc).__name__})",
            file=sys.stderr,
        )
    print(
        f"{_PREFIX} no record version was written by the refused step, and the App was not "
        "stopped, started, deployed or updated (a create-only copy already made is reused or "
        "refused byte for byte on the next run)",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
