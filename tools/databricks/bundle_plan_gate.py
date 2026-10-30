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
  that already exists refuses as a ``collision``; a lookup that errors, or a
  name the plan has not resolved, refuses as ``lookup_failed``.
* ``update`` or ``resize`` of a ``database_instances`` entry refuses unless
  ``MIP_ACK_DATABASE_INSTANCE_UPDATE`` equals the sha256 of the canonical
  field diff (``instance_update_digest``). The pre-apply run recomputes the
  diff, so an acknowledgement covers exactly the diff that was reviewed.

**Not adopted** (attack O14). When the plan does not bind ``apps.<app key>``
(its entry plans ``create``, so the deployer's state holds no live id for
it), that App's own lookup finds it existing, and every refusal is a
collision, the deployer is looking at an install it never adopted. The
collisions fold into one ``not_adopted`` refusal that lists them as detail.
With no App there is no install to adopt: a first install that meets a stray
same-name object keeps its plain ``collision`` refusals and their remedy.

Exit codes: 0 the plan passes; 2 refused (each refusal printed with its
remedy) or the plan could not be read. Printed lines carry codes, counts,
digests and symbolic names only (``PlanReport.public_summary``); resolved
names go to ``--report-out`` (``PlanReport.full``), written mode 0600. The
check never writes to the workspace: every SDK call it can make is a GET or
a list.
"""

from __future__ import annotations

import argparse
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

from tools.databricks.bundle_plan_lookups import KIND_LOOKUPS, KindLookup  # noqa: E402
from tools.databricks.bundle_plan_shape import (  # noqa: E402
    BundlePlan,
    PlanEntry,
    PlanShapeError,
    parse_plan,
    sha256_hex,
)
from tools.databricks.deploy_plan_report import (  # noqa: E402
    COUNTED_ACTIONS,
    LookupRecord,
    PlanReport,
    Refusal,
)

ACK_ENV = "MIP_ACK_DATABASE_INSTANCE_UPDATE"
DEFAULT_APP_KEY = "mip_app"
INSTANCE_KIND = "database_instances"
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
            "says why); fix the read access or the unresolved name, then re-run. It never "
            "creates blind"
        ),
        "instance_update_unacked": (
            "the plan updates the Lakebase database instance; review the field diff in the "
            f"full plan report, then re-run with {ACK_ENV} set to its sha256"
        ),
        "not_adopted": (
            "this deployer's bundle state does not bind the App, which already exists, and "
            "every other refusal is a name collision: an install this deployer never adopted. "
            "Adopt it into this deployer's state (bundle deployment bind, through a reviewed "
            "change; the governed adoption flow is plan M2-PR6) before any mutating phase"
        ),
    }
)


def instance_update(plan: BundlePlan) -> tuple[Mapping[str, Any], ...]:
    """The canonical field diff of every database-instance update or resize."""

    items = []
    for entry in plan.entries:
        if entry.kind != INSTANCE_KIND or entry.action not in {"update", "resize"}:
            continue
        changes = {
            path: {
                "action": change.action,
                "reason": change.reason,
                "old": change.old,
                "new": change.new,
                "remote": change.remote,
            }
            for path, change in sorted(entry.changes.items())
            if change.action != "skip"
        }
        items.append({"resource": entry.symbolic, "action": entry.action, "changes": changes})
    return tuple(items)


def instance_update_digest(plan: BundlePlan) -> str:
    """sha256 of the canonical instance field diff; "" when no instance changes."""

    items = instance_update(plan)
    return sha256_hex(list(items)) if items else ""


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


def evaluate(
    plan: BundlePlan,
    *,
    position: str,
    app_key: str,
    acknowledgement: str,
    workspace_factory: Callable[[], Any],
) -> PlanReport:
    """Judge one parsed plan; ``workspace_factory`` is called once, only if a create needs it."""

    app_resource = f"apps.{app_key}"
    refusals: list[Refusal] = []
    lookups: list[LookupRecord] = []
    counts = dict.fromkeys(COUNTED_ACTIONS, 0)
    built: list[Any] = []

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
                exists = spec.exists(client(), names)
            except Exception as exc:  # noqa: BLE001 - any failure is unproven, never "absent"
                outcome = f"failed: {type(exc).__name__}"
                refusals.append(
                    _refuse("lookup_failed", entry.symbolic, reason=outcome, error=str(exc))
                )
            else:
                outcome = "exists" if exists else "absent"
                if exists:
                    refusals.append(
                        _refuse(
                            "collision",
                            entry.symbolic,
                            kind=entry.kind,
                            names=list(names),
                            unique_within=spec.unique_within,
                            lookup=spec.method,
                        )
                    )
            lookups.append(
                LookupRecord(
                    entry.symbolic, entry.kind, names, spec.unique_within, spec.method, outcome
                )
            )

    diff = instance_update(plan)
    digest = instance_update_digest(plan)
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
    )


def malformed(position: str, app_key: str, reason: str) -> PlanReport:
    """The report for a plan the parser refused."""

    return PlanReport(
        position=position,
        app_resource=f"apps.{app_key}",
        app_bound=False,
        refusals=(_refuse("malformed_plan", PLAN_RESOURCE, reason=reason),),
    )


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
    args = parser.parse_args(argv)

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
