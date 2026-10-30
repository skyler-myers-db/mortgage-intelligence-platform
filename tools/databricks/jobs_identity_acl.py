"""Grant the non-admin jobs identity the workspace ACLs its bundle jobs use (M2-PR5).

The jobs identity (``var.jobs_principal``) runs the App-triggerable jobs, the
weekly refresh, the KPI snapshot it starts and the feature pipeline. It holds
no workspace admin, so every workspace object those runs touch needs a grant:

* the bundle files folder (``workspace.file_path``), CAN_READ: every task's
  Python and SQL file, jobs/install_pins.json and the pipeline's library live
  under the deployer's own home folder, which no other identity can read;
* the SQL warehouse a ``sql_task`` names, CAN_USE;
* the pipeline a ``pipeline_task`` starts, CAN_RUN;
* the job a ``run_job_task`` starts, CAN_MANAGE_RUN. That job must itself run
  as the jobs identity: a non-admin run never starts an admin one;
* the Lakebase instance a ``--lakebase-instance`` parameter names, CAN_USE.

:func:`required_grants` derives exactly that set from databricks.yml and
refuses any other ``${resources.*}`` reference or task kind in a jobs-identity
job or pipeline, so a new dependency cannot ship without its grant.
:func:`converge` adds each grant the identity does not already hold at that
level or above, with the permissions API's PATCH, and then re-reads every
object. It never removes or lowers a grant: the App's CAN_MANAGE_RUN bindings,
the verifier's warehouse CAN_USE and any other team's grants stay as they are.
Bundle ``permissions:`` blocks would express the same grants, but the bundle
sets each object's whole ACL on every deploy and would drop those, so this is a
deploy step (scripts/lib/deploy_step_bundle_apply.sh), not bundle config.

``plan`` reads only; ``apply`` mutates. Output names bundle keys, never object
ids or principals. Exit codes: 0 converged (or nothing to add); 2 the bundle or
its summary refused, nothing granted; 3 a grant or its postflight failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROG = "jobs-identity-acl"
JOBS_IDENTITY = {"service_principal_name": "${var.jobs_principal}"}
LAKEBASE_PARAMETER = "--lakebase-instance=${var.lakebase_instance_name}"
FILES = ("workspace", "file_path")
# Bundle resource kind -> the permissions API object type.
OBJECT_TYPES = {
    "workspace": "directories",
    "sql_warehouses": "warehouses",
    "pipelines": "pipelines",
    "jobs": "jobs",
    "database_instances": "database-instances",
}
# Object type -> required level -> the levels that already include it.
SATISFIES = {
    "directories": {"CAN_READ": frozenset({"CAN_READ", "CAN_RUN", "CAN_EDIT", "CAN_MANAGE"})},
    "warehouses": {"CAN_USE": frozenset({"CAN_USE", "CAN_MANAGE", "IS_OWNER"})},
    "pipelines": {"CAN_RUN": frozenset({"CAN_RUN", "CAN_MANAGE", "IS_OWNER"})},
    "jobs": {"CAN_MANAGE_RUN": frozenset({"CAN_MANAGE_RUN", "CAN_MANAGE", "IS_OWNER"})},
    "database-instances": {"CAN_USE": frozenset({"CAN_USE", "CAN_MANAGE", "IS_OWNER"})},
}
TASK_KINDS = frozenset({"sql_task", "pipeline_task", "run_job_task", "spark_python_task", "condition_task"})
_REFERENCE = re.compile(r"\$\{resources\.(?P<kind>[a-z_]+)\.(?P<key>[A-Za-z0-9_]+)\.(?P<field>[a-z_]+)\}")
_PLACEHOLDER = re.compile(r"\A(0+(-0+)*|.*PLACEHOLDER.*|<.*>)\Z", re.IGNORECASE)


class Refused(RuntimeError):
    """The bundle or its summary is not one this step has reviewed grants for."""


@dataclass(frozen=True, order=True)
class Grant:
    kind: str
    key: str
    level: str

    @property
    def label(self) -> str:
        return f"{self.kind}.{self.key} {self.level}"


@dataclass(frozen=True)
class Target:
    grant: Grant
    object_type: str
    reference: str  # an object id, or the files folder's path


def _references(node: Any) -> set[tuple[str, str]]:
    """Every ``(kind, key)`` a ``${resources.<kind>.<key>.<field>}`` string anywhere in ``node`` names."""

    if isinstance(node, dict):
        return set().union(*(_references(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_references(value) for value in node))
    if isinstance(node, str):
        return {(m.group("kind"), m.group("key")) for m in _REFERENCE.finditer(node)}
    return set()


def _target_key(value: Any, kind: str, field: str) -> str:
    match = _REFERENCE.fullmatch(str(value))
    if match is None or match.group("kind") != kind or match.group("field") != field:
        raise Refused(f"a jobs-identity task names a {kind} that is not a bundle resource")
    return match.group("key")


def required_grants(bundle: dict[str, Any]) -> frozenset[Grant]:
    """Every grant the jobs identity needs, from databricks.yml; :class:`Refused` on anything unreviewed."""

    resources = bundle["resources"]
    jobs, pipelines = resources.get("jobs") or {}, resources.get("pipelines") or {}
    instances = [
        key for key, instance in (resources.get("database_instances") or {}).items()
        if instance.get("name") == "${var.lakebase_instance_name}"
    ]
    on_jobs = sorted(key for key, job in jobs.items() if job.get("run_as") == JOBS_IDENTITY)
    on_pipelines = sorted(key for key, p in pipelines.items() if p.get("run_as") == JOBS_IDENTITY)
    grants: set[Grant] = {Grant(*FILES, "CAN_READ")} if on_jobs or on_pipelines else set()
    for key in on_pipelines:
        if _references(pipelines[key]):
            raise Refused(f"pipelines.{key} references a bundle resource this step has no grant for")
    for key in on_jobs:
        covered: set[tuple[str, str]] = set()
        for task in jobs[key]["tasks"]:
            kinds = [field for field in task if field.endswith("_task")]
            if len(kinds) != 1 or kinds[0] not in TASK_KINDS:
                raise Refused(f"jobs.{key}.{task.get('task_key')} is a task kind this step has not reviewed")
            body = task[kinds[0]]
            if kinds[0] == "sql_task":
                grant = Grant("sql_warehouses", _target_key(body.get("warehouse_id"), "sql_warehouses", "id"),
                              "CAN_USE")
            elif kinds[0] == "pipeline_task":
                grant = Grant("pipelines", _target_key(body.get("pipeline_id"), "pipelines", "id"), "CAN_RUN")
                if grant.key not in on_pipelines:
                    raise Refused(f"jobs.{key} starts pipelines.{grant.key}, which does not run as the jobs identity")
            elif kinds[0] == "run_job_task":
                grant = Grant("jobs", _target_key(body.get("job_id"), "jobs", "id"), "CAN_MANAGE_RUN")
                if grant.key not in on_jobs:
                    raise Refused(f"jobs.{key} starts jobs.{grant.key}, which does not run as the jobs identity")
            elif kinds[0] == "spark_python_task":
                lakebase = [p for p in body.get("parameters", []) if str(p).startswith("--lakebase-instance")]
                if not lakebase:
                    continue
                if lakebase != [LAKEBASE_PARAMETER] or len(instances) != 1:
                    raise Refused(f"jobs.{key} names a Lakebase instance this step cannot resolve")
                grant = Grant("database_instances", instances[0], "CAN_USE")
            else:
                continue
            grants.add(grant)
            covered.add((grant.kind, grant.key))
        stray = _references(jobs[key]) - covered
        if stray:
            names = ", ".join(f"{kind}.{name}" for kind, name in sorted(stray))
            raise Refused(f"jobs.{key} references {names}, which this step has no grant for")
    return frozenset(grants)


def resolve(grants: frozenset[Grant], summary: dict[str, Any], *, jobs_principal: str, bundle: dict[str, Any]) -> list[Target]:
    """Each grant's live object, from ``databricks bundle summary -o json``; :class:`Refused` on a gap."""

    principal = jobs_principal.strip()
    if not principal or _PLACEHOLDER.match(principal):
        raise Refused("--jobs-principal must be the profile's jobs service principal, not a placeholder")
    resources = summary.get("resources")
    if not isinstance(resources, dict):
        raise Refused("the bundle summary exposes no resources")
    for kind in ("jobs", "pipelines"):
        for key, resource in (bundle["resources"].get(kind) or {}).items():
            if resource.get("run_as") != JOBS_IDENTITY:
                continue
            live = ((resources.get(kind) or {}).get(key) or {}).get("run_as") or {}
            if str(live.get("service_principal_name") or "").casefold() != principal.casefold():
                raise Refused(f"the bundle summary's {kind}.{key} does not run as --jobs-principal")
    targets = []
    for grant in sorted(grants):
        if (grant.kind, grant.key) == FILES:
            reference = str((summary.get("workspace") or {}).get("file_path") or "").strip()
        else:
            entry = (resources.get(grant.kind) or {}).get(grant.key) or {}
            field = "name" if grant.kind == "database_instances" else "id"
            reference = str(entry.get(field) or "").strip()
        if not reference or "${" in reference:
            raise Refused(f"the bundle summary has no concrete object for {grant.kind}.{grant.key}")
        targets.append(Target(grant, OBJECT_TYPES[grant.kind], reference))
    return targets


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _object_id(client: Any, target: Target) -> str:
    if target.object_type != "directories":
        return target.reference
    object_id = str(getattr(client.workspace.get_status(target.reference), "object_id", "") or "").strip()
    if not object_id:
        raise RuntimeError("the bundle files folder has no workspace object id")
    return object_id


def _held(client: Any, target: Target, object_id: str, principal: str) -> set[str]:
    """Every level the principal's own ACL entry holds on the object, inherited ones included."""

    permissions = client.permissions.get(target.object_type, object_id)
    return {
        _text(getattr(permission, "permission_level", None))
        for entry in (getattr(permissions, "access_control_list", None) or [])
        if str(getattr(entry, "service_principal_name", "") or "").casefold() == principal.casefold()
        for permission in (getattr(entry, "all_permissions", None) or [])
    }


def missing(client: Any, targets: list[Target], principal: str) -> list[tuple[Target, str]]:
    """(target, object id) for every grant the principal does not hold at its level or above; read-only."""

    gaps = []
    for target in targets:
        object_id = _object_id(client, target)
        if not _held(client, target, object_id, principal) & SATISFIES[target.object_type][target.grant.level]:
            gaps.append((target, object_id))
    return gaps


def converge(client: Any, targets: list[Target], principal: str) -> list[Target]:
    """Add each missing grant (PATCH, never a set), then prove every grant; the targets granted."""

    from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

    gaps = missing(client, targets, principal)
    for target, object_id in gaps:
        client.permissions.update(
            target.object_type,
            object_id,
            access_control_list=[
                AccessControlRequest(
                    service_principal_name=principal, permission_level=PermissionLevel(target.grant.level)
                )
            ],
        )
    left = missing(client, targets, principal)
    if left:
        labels = ", ".join(target.grant.label for target, _ in left)
        raise RuntimeError(f"the jobs identity's postflight found grants still missing: {labels}")
    return [target for target, _ in gaps]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan", "apply"))
    parser.add_argument("--bundle-summary", type=Path, required=True, help="databricks bundle summary -o json")
    parser.add_argument("--jobs-principal", required=True, help="the jobs service principal's application id")
    parser.add_argument("--bundle", type=Path, default=Path("databricks.yml"))
    return parser


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bundle = yaml.safe_load(args.bundle.read_text(encoding="utf-8"))
        summary = json.loads(args.bundle_summary.read_text(encoding="utf-8"))
        targets = resolve(required_grants(bundle), summary, jobs_principal=args.jobs_principal, bundle=bundle)
    except (Refused, OSError, ValueError, KeyError) as exc:
        print(f"[{PROG}] REFUSED: {exc}; nothing was granted", file=sys.stderr)
        return 2
    if client is None:
        from databricks.sdk import WorkspaceClient

        client = WorkspaceClient()
    principal = args.jobs_principal.strip()
    try:
        if args.command == "plan":
            gaps = missing(client, targets, principal)
            for target, _ in gaps:
                print(f"[{PROG}] would grant {target.grant.label}")
            print(f"[{PROG}] {len(gaps)} of {len(targets)} grant(s) to add")
            return 0
        granted = converge(client, targets, principal)
    except Exception as exc:  # noqa: BLE001 - a stable, value-free diagnostic; the deploy stops here
        # This module's own errors name bundle keys only; an API error's text
        # can carry object ids, so only its type is printed.
        detail = f": {exc}" if type(exc) is RuntimeError else ""
        print(f"[{PROG}] FAILED ({type(exc).__name__}){detail}", file=sys.stderr)
        return 3
    for target in granted:
        print(f"[{PROG}] granted {target.grant.label}")
    print(f"[{PROG}] the jobs identity holds all {len(targets)} grant(s) its jobs and pipeline use")
    return 0


if __name__ == "__main__":
    sys.exit(main())
