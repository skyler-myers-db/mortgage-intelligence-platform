"""Refuse a bundle plan that deletes, recreates or collides, before the lease (M0-PR4).

``scripts/deploy.sh`` runs ``bundle_env plan -t <target> -o json`` and hands
the document to ``check`` twice: before the signed workspace lease and every
App stop (``--position pre-lease``), and again under the lease immediately
before the non-App bundle apply (``--position pre-apply``), so a plan that
changed in between is judged again. ``--phase plan`` stops after the first.

The document is parsed fail closed (``bundle_plan_shape``). Each entry's kind
must be in ``KIND_LOOKUPS`` (``bundle_plan_lookups``), a closed table built
from the kinds databricks.yml declares
(``tests/unit/test_bundle_plan_gate_kinds.py`` pins the two equal). Per entry:

* ``delete`` and ``recreate`` refuse, a state-only ``gone`` delete included;
  ``update_id`` (a rename of an identity field) refuses too.
* ``create`` looks its name up with the table's read-only GET or list. A name
  that already exists refuses as a ``collision``; a lookup that errors, a
  name the plan has not resolved, or a workspace-scoped name looked up by a
  caller that is not a workspace admin (a list shows only what the caller may
  see) refuses as ``lookup_failed``.
* ``update`` or ``resize`` of a ``database_instances`` entry refuses unless
  ``MIP_ACK_DATABASE_INSTANCE_UPDATE`` equals the sha256 of what the update
  will do (``instance_update_digest``): the whole ``new_state`` it sends,
  every change skip included, the instance name and the state lineage. The
  pre-apply run recomputes it, so an acknowledgement covers exactly the
  update that was reviewed, of this install only.

**Not adopted** (attack O14). When the plan does not bind ``apps.<app key>``
(its entry plans ``create``, so the deployer's state holds no live id for
it), that App's own lookup finds it existing, and every refusal is a
collision, the deployer is looking at an install it never adopted. The
collisions fold into one ``not_adopted`` refusal that lists them as detail.
With no App there is no install to adopt: a first install that meets a stray
same-name object keeps its plain ``collision`` refusals and their remedy.

**Journal-owned App** (pre-lease only). An interrupted first install can
leave the App created but unbound from bundle state (a lost create response,
a crash between the journal claim and the bind, a cleanup that unbound the
App and then failed to delete it); deploy.sh leaves it, with its signed
first-install journal, for the recovery under the next lease
(``deploy_step_signed_blue_proof.sh``). Given ``--first-install-app-name``
and the journal's scope and instance, an App collision whose journal reads
``unclaimed`` or ``recover`` (``app_first_install_journal.recovery_state``)
is deferred, not refused, so the run reaches that recovery. The pre-apply
gate never defers: the recovery has proven the App absent by then.

**App ACL preview** (pre-lease only, ``bundle_plan_app_acl``). Given the App
name and the four release identities, the report previews what the run's
runtime release access convergence does to the App's direct ACL, and an ACL
that converger would refuse refuses as ``app_acl``.

Exit codes: 0 the plan passes; 2 refused (each refusal printed with its
remedy) or the plan could not be read. Printed lines carry codes, counts,
digests and symbolic names only (``PlanReport.public_summary``); resolved
names go to ``--report-out`` (``PlanReport.full``), written mode 0600. The
check never writes to the workspace: every SDK call it can make, the journal
read included, is a GET or a list.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks.bundle_plan_instance_ack import (  # noqa: E402
    ACK_ENV,
    instance_update,
    instance_update_digest,
)
from tools.databricks.bundle_plan_lookups import (  # noqa: E402
    KIND_LOOKUPS,
    KindLookup,
    prove_full_visibility,
)
from tools.databricks.bundle_plan_shape import (  # noqa: E402
    BundlePlan,
    PlanEntry,
    PlanShapeError,
    parse_plan,
)
from tools.databricks.deploy_plan_report import (  # noqa: E402
    COUNTED_ACTIONS,
    Deferral,
    ExecuteGapPrediction,
    LookupRecord,
    PlanReport,
    Refusal,
)

DEFAULT_APP_KEY = "mip_app"
# The signed journal states deploy.sh reconciles after the lease (recover-claim
# then delete for "unclaimed", delete for "recover").
JOURNAL_OWNED_STATES = frozenset({"unclaimed", "recover"})
JOURNAL_OWNED_NOTE = (
    "the App is this deployer's interrupted first install, owned by its signed first-install "
    "journal; the recovery under the lease reconciles (deletes) it, and the pre-apply gate "
    "judges the create again, strictly"
)
PLAN_RESOURCE = "bundle.plan"
PUBLIC_REASON_CODES = frozenset({"malformed_plan", "lookup_failed"})
_PREFIX = "[mip-plan-gate]"

REMEDIES: Mapping[str, str] = MappingProxyType(
    {
        "malformed_plan": (
            "the bundle plan JSON is not the shape this gate reviewed (Databricks CLI "
            "v1.10.0, plan_version 2), so it refuses rather than guess; run the reviewed CLI, "
            "or extend tools/databricks/bundle_plan_shape.py from a live capture"
        ),
        "unknown_kind": (
            "the plan names a resource kind with no reviewed existence lookup; add it to "
            "KIND_LOOKUPS in tools/databricks/bundle_plan_lookups.py with a test, then re-run"
        ),
        "delete": (
            "a governed deploy never deletes a bundle resource, even a state-only delete of "
            "an object already gone; restore it in databricks.yml or retire it through a "
            "reviewed change outside the deploy, then re-run"
        ),
        "recreate": (
            "the plan deletes and recreates this resource, losing its id, history and grants; "
            "change the configuration so it updates in place, then re-run"
        ),
        "id_change": (
            "the plan renames an identity field of this resource (update_id); rename it "
            "through a reviewed change outside the deploy, then re-run"
        ),
        "collision": (
            "the plan creates this resource under a name that already exists (the full plan "
            "report names it); bind the existing object into this deployer's state or "
            "remove it, then re-run"
        ),
        "lookup_failed": (
            "the gate could not prove this resource's name is free (the full plan report "
            "says why); fix the read access (a workspace-scoped name is proven free only by a "
            "workspace admin, who sees every object) or the unresolved name, then re-run. It "
            "never creates blind"
        ),
        "instance_update_unacked": (
            "the plan updates the Lakebase database instance, which sends its whole configured "
            "state; review instance_update in the full plan report (every change, skip "
            "included: a skipped remote drift is reverted too), then re-run with "
            f"{ACK_ENV} set to its sha256"
        ),
        "not_adopted": (
            "this deployer's bundle state does not bind the App, which already exists, and "
            "every other refusal is a name collision: an install this deployer never adopted. "
            "Adopt it into this deployer's state (bundle deployment bind, through a reviewed "
            "change; the governed adoption flow is plan M2-PR6) before any mutating phase"
        ),
    }
)


def _names(entry: PlanEntry, spec: KindLookup) -> tuple[str, ...]:
    if entry.new_value is None:
        raise ValueError("the create carries no new_state")
    names = []
    for name_field in spec.name_fields:
        if name_field in entry.new_vars:
            raise ValueError(f"{name_field} is still an unresolved reference")
        value = entry.new_value.get(name_field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name_field} is missing or empty")
        names.append(value)
    return tuple(names)


def _refuse(code: str, resource: str, **detail: Any) -> Refusal:
    return Refusal(code=code, resource=resource, remedy=REMEDIES[code], detail=detail)


def _journal(read: Callable[[Any, str], str], workspace: Any, app_name: str) -> str:
    """The journal state of a colliding App; a failed read is never journal-owned."""

    try:
        return str(read(workspace, app_name))
    except Exception as exc:  # noqa: BLE001 - recorded; the collision then stands
        return f"failed: {type(exc).__name__}"


def evaluate(
    plan: BundlePlan,
    *,
    position: str,
    app_key: str,
    acknowledgement: str,
    workspace_factory: Callable[[], Any],
    journal_state: Callable[[Any, str], str] | None = None,
) -> PlanReport:
    """Judge one parsed plan; ``workspace_factory`` is called once, only if a create needs it.

    ``journal_state(workspace, app_name)`` reads the signed first-install
    journal of a colliding App; it is consulted only at ``pre-lease``.
    """

    app_resource = f"apps.{app_key}"
    refusals: list[Refusal] = []
    deferrals: list[Deferral] = []
    lookups: list[LookupRecord] = []
    counts = dict.fromkeys(COUNTED_ACTIONS, 0)
    built: list[Any] = []
    proven: list[Exception | None] = []

    def client() -> Any:
        # Built once; a factory failure (a workspace other than the install's,
        # say) is kept and re-raised, so every create refuses on the same cause.
        if not built:
            try:
                built.append(workspace_factory())
            except Exception as exc:  # noqa: BLE001 - re-raised for each lookup below
                built.append(exc)
        if isinstance(built[0], Exception):
            raise built[0]
        return built[0]

    def visible(workspace: Any) -> None:
        # Proven once per plan, like the client; a failure refuses every
        # workspace-scoped create on the same cause.
        if not proven:
            try:
                prove_full_visibility(workspace)
            except Exception as exc:  # noqa: BLE001 - re-raised for each lookup below
                proven.append(exc)
            else:
                proven.append(None)
        if proven[0] is not None:
            raise proven[0]

    for entry in plan.entries:
        counts[entry.action] += 1
        spec = KIND_LOOKUPS.get(entry.kind)
        if spec is None:
            refusals.append(_refuse("unknown_kind", entry.symbolic, kind=entry.kind))
            continue
        if entry.action in {"delete", "recreate"}:
            refusals.append(_refuse(entry.action, entry.symbolic, gone=entry.gone))
        elif entry.action == "update_id":
            refusals.append(_refuse("id_change", entry.symbolic))
        elif entry.action == "create":
            try:
                names = _names(entry, spec)
            except ValueError as exc:
                refusals.append(_refuse("lookup_failed", entry.symbolic, reason=str(exc)))
                continue
            try:
                workspace = client()
                if spec.requires_admin_visibility:
                    visible(workspace)
                exists = spec.exists(workspace, names)
            except Exception as exc:  # noqa: BLE001 - any failure is unproven, never "absent"
                outcome = f"failed: {type(exc).__name__}"
                refusals.append(
                    _refuse("lookup_failed", entry.symbolic, reason=outcome, error=str(exc))
                )
            else:
                outcome = "exists" if exists else "absent"
                journal = ""
                if (
                    exists
                    and entry.symbolic == app_resource
                    and position == "pre-lease"
                    and journal_state is not None
                ):
                    journal = _journal(journal_state, workspace, names[0])
                if journal in JOURNAL_OWNED_STATES:
                    outcome = f"journal-owned ({journal})"
                    deferrals.append(
                        Deferral(
                            code="journal_owned",
                            resource=entry.symbolic,
                            note=JOURNAL_OWNED_NOTE,
                            detail={"journal_state": journal, "names": list(names)},
                        )
                    )
                elif exists:
                    refusals.append(
                        _refuse(
                            "collision",
                            entry.symbolic,
                            kind=entry.kind,
                            names=list(names),
                            unique_within=spec.unique_within,
                            lookup=spec.method,
                            **({"journal_state": journal} if journal else {}),
                        )
                    )
            lookups.append(
                LookupRecord(
                    entry.symbolic, entry.kind, names, spec.unique_within, spec.method, outcome
                )
            )

    diff = instance_update(plan)
    digest = instance_update_digest(plan)
    for item in diff:
        if item["new_state"] is None:
            # Without the body the update sends, no digest says what it does.
            refusals.append(
                _refuse(
                    "malformed_plan",
                    str(item["resource"]),
                    reason=f"{item['resource']} {item['action']} carries no new_state",
                )
            )
    if digest and acknowledgement != digest:
        for item in diff:
            refusals.append(
                _refuse(
                    "instance_update_unacked",
                    str(item["resource"]),
                    digest=digest,
                    acknowledged=bool(acknowledgement),
                )
            )

    app_entry = plan.entry(f"resources.apps.{app_key}")
    if app_entry is None:
        refusals.append(_refuse("malformed_plan", PLAN_RESOURCE, reason=f"no {app_resource}"))
        app_bound = False
    else:
        app_bound = app_entry.action != "create"
    # An install exists only when the App does: its own lookup must have found it.
    install_found = any(
        record.resource == app_resource and record.outcome == "exists" for record in lookups
    )
    if (
        refusals
        and not app_bound
        and install_found
        and all(r.code == "collision" for r in refusals)
    ):
        refusals = [
            Refusal(
                code="not_adopted",
                resource=app_resource,
                remedy=REMEDIES["not_adopted"],
                related=tuple(r.resource for r in refusals),
                detail={"collisions": [dict(r.detail, resource=r.resource) for r in refusals]},
            )
        ]
    return PlanReport(
        position=position,
        app_resource=app_resource,
        app_bound=app_bound,
        plan_sha256=plan.sha256,
        plan_version=plan.plan_version,
        cli_version=plan.cli_version,
        lineage=plan.lineage,
        serial=plan.serial,
        counts=counts,
        refusals=tuple(refusals),
        instance_update_sha256=digest,
        instance_update=diff,
        lookups=tuple(lookups),
        deferrals=tuple(deferrals),
    )


def malformed(position: str, app_key: str, reason: str) -> PlanReport:
    """The report for a plan the parser refused."""

    return PlanReport(
        position=position,
        app_resource=f"apps.{app_key}",
        app_bound=False,
        refusals=(_refuse("malformed_plan", PLAN_RESOURCE, reason=reason),),
    )


def first_install_journal_reader(
    app_name: str, rollback_scope: str, lakebase_instance: str
) -> Callable[[Any, str], str]:
    """Read the signed first-install journal of ``app_name``, the App this deploy reconciles."""

    def read(workspace: Any, resolved_name: str) -> str:
        if resolved_name != app_name:
            raise ValueError("the plan's App is not the App this deploy's journal names")
        # Imported here: the journal's crypto stack is needed only for a colliding App.
        from tools.databricks.app_first_install_journal import recovery_state

        return recovery_state(
            workspace,
            app_name=app_name,
            rollback_scope=rollback_scope,
            expected_lakebase_instance=lakebase_instance,
        )

    return read


def bound_workspace_client() -> Any:
    """The SDK client, proven to resolve the install's workspace before any lookup."""

    from tools.databricks.provision_runtime_secrets import bound_workspace

    workspace, _host = bound_workspace()
    return workspace


def _write_private(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, 0o600)


def _refusal_line(report: PlanReport, refusal: Refusal) -> str:
    """One public line: code, symbolic names, a value-free reason, the remedy."""

    extra = ""
    if refusal.related:
        extra = f" ({len(refusal.related)} collisions: {', '.join(refusal.related)})"
    elif refusal.code in PUBLIC_REASON_CODES:
        # These reasons name plan fields and error classes only, never a value.
        extra = f" ({refusal.detail.get('reason', '')})"
    remedy = refusal.remedy
    if refusal.code == "instance_update_unacked":
        remedy += f" ({ACK_ENV}={report.instance_update_sha256})"
    return f"{_PREFIX} REFUSED {refusal.code} {refusal.resource}{extra}: {remedy}"


def _print_report(report: PlanReport) -> None:
    summary = report.public_summary()
    counts = summary["counts"]
    print(
        f"{_PREFIX} {report.position}: plan {report.plan_sha256[:12] or '(unread)'} "
        f"(plan_version {report.plan_version}, cli {report.cli_version or 'unknown'}): "
        + ", ".join(f"{counts[action]} {action}" for action in COUNTED_ACTIONS)
    )
    if report.app_acl is not None:
        acl = summary["app_acl"]
        print(
            f"{_PREFIX} app ACL preview: {acl['code']} ("
            + ", ".join(f"{acl[name]} {name}" for name in ("keep", "add", "remove", "change"))
            + "; principals in the private report)"
        )
    if report.execute_gap is not None:
        gap = summary["execute_gap"]
        print(
            f"{_PREFIX} catalog bootstrap: {gap['code']} ({len(gap['functions'])} republished "
            f"functions, EXECUTE gap at most {gap['predicted_seconds']} s)"
        )
    for deferral in report.deferrals:
        state = deferral.detail.get("journal_state", "")
        print(f"{_PREFIX} DEFERRED {deferral.code} {deferral.resource} ({state}): {deferral.note}")
    for refusal in report.refusals:
        print(_refusal_line(report, refusal), file=sys.stderr)
    print(f"{_PREFIX} public summary: {json.dumps(summary, sort_keys=True)}")


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] = bound_workspace_client,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="judge one bundle plan JSON document")
    check.add_argument("--plan", required=True, type=Path)
    check.add_argument("--position", required=True, choices=("pre-lease", "pre-apply"))
    check.add_argument("--app-key", default=DEFAULT_APP_KEY)
    check.add_argument("--report-out", type=Path)
    journal = check.add_argument_group(
        "first-install journal (pre-lease only; all three or none)"
    )
    journal.add_argument("--first-install-app-name")
    journal.add_argument("--rollback-scope")
    journal.add_argument("--lakebase-instance")
    acl = check.add_argument_group("App ACL preview (pre-lease only; all five or none)")
    acl.add_argument("--app-acl-app-name")
    for role in ("release-probe", "normal", "operator2", "admin"):
        acl.add_argument(f"--{role}-application-id")
    check.add_argument(
        "--execute-gap",
        type=Path,
        help="catalog_bootstrap_skip_rule's report: the EXECUTE-gap prediction (M0-PR8)",
    )
    args = parser.parse_args(argv)
    acl_values = (
        args.app_acl_app_name,
        args.release_probe_application_id,
        args.normal_application_id,
        args.operator2_application_id,
        args.admin_application_id,
    )
    if any(acl_values) and not all(acl_values):
        parser.error("the App ACL preview arguments go together")
    if any(acl_values) and args.position != "pre-lease":
        parser.error("the App ACL is previewed only before the lease (pre-lease)")
    journal_values = (args.first_install_app_name, args.rollback_scope, args.lakebase_instance)
    if any(journal_values) and not all(journal_values):
        parser.error("the first-install journal arguments go together")
    if any(journal_values) and args.position != "pre-lease":
        parser.error("the first-install journal is read only before the lease (pre-lease)")
    reader = first_install_journal_reader(*journal_values) if all(journal_values) else None

    try:
        plan = parse_plan(args.plan.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        report = malformed(args.position, args.app_key, f"unreadable: {type(exc).__name__}")
    except PlanShapeError as exc:
        report = malformed(args.position, args.app_key, str(exc))
    else:
        report = evaluate(
            plan,
            position=args.position,
            app_key=args.app_key,
            acknowledgement=os.environ.get(ACK_ENV, "").strip(),
            workspace_factory=workspace_factory,
            journal_state=reader,
        )
    if all(acl_values) and report.plan_sha256:
        from tools.databricks.bundle_plan_app_acl import with_app_acl_preview

        report = with_app_acl_preview(
            report,
            workspace_factory=workspace_factory,
            app_name=args.app_acl_app_name,
            release_probe_application_id=args.release_probe_application_id,
            normal_application_id=args.normal_application_id,
            operator2_application_id=args.operator2_application_id,
            admin_application_id=args.admin_application_id,
        )
    if args.execute_gap is not None:
        try:
            skip_rule = json.loads(args.execute_gap.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skip_rule = None
        report = dataclasses.replace(
            report, execute_gap=ExecuteGapPrediction.from_skip_rule(skip_rule)
        )
    if args.report_out is not None:
        _write_private(args.report_out, report.full())
    _print_report(report)
    if report.refused:
        print(
            f"{_PREFIX} nothing was changed by this check; it made only GET and list calls.",
            file=sys.stderr,
        )
        return 2
    print(f"{_PREFIX} {report.position} plan gate passed (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
