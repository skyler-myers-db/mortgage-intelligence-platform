"""Archive an adopted install's legacy root, never deleting anything (M2-PR6, cutover step 16).

After the adoption (``./scripts/deploy.sh -t client --phase adopt``) and seven
green days, the legacy root the adoption bound from
(``MIP_LEGACY_BUNDLE_ROOT``) is retired. This tool plans that, read-only by
default:

* the **moves** are the legacy root's top-level children (``--only <name>``
  narrows them to the named ones), each to
  ``<deployer root>/legacy-archive/<legacy leaf>/<name>``;
* the **protected paths** are every workspace path a resource this deployer's
  state binds still uses, read live by id (a dashboard's file, an
  experiment's path, the App's default and deployed source folders, and every
  workspace path a bound job's settings or pipeline's spec names), plus the
  legacy ``state`` folder, which ``--phase unadopt`` reads to know exactly
  what the adoption bound;
* a move whose path set intersects a protected path (either inside the
  other) refuses (``bound_path``), and so does the whole plan: an adopted
  install's dashboards stay in their legacy folder permanently, since spike
  S4 measured that a move recreates them (tools/clients/rules.py), so
  ``--only`` must leave that folder out.

``--apply --ack-plan <sha256>`` recomputes the plan and refuses unless its
sha256 is the acknowledged one. The Workspace API has no move call (SDK
0.103.0 and CLI v1.10.0 offer list, get-status, export, import, mkdirs and
delete), so the archive is a create-only, byte-verified copy of each file and
notebook into the archive folder. This tool never deletes: removing the
legacy originals stays the owner's step of the cutover, from the plan's list.
It refuses before any copy unless this deployer's state binds the App (the
install is adopted). Printed lines carry codes, counts and the digest; the
paths go only to ``--report-out`` (mode 0600).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from databricks.sdk.errors import NotFound, ResourceAlreadyExists, ResourceDoesNotExist
from databricks.sdk.service.workspace import ExportFormat, ImportFormat, Language

from tools.clients.errors import ProfileRefusal
from tools.databricks.adopt_legacy_map import state_entries
from tools.databricks.app_release_contract import api_path
from tools.databricks.bundle_plan_shape import sha256_hex
from tools.databricks.bundle_state import STATE_FILE, BundleStateError, read_state, state_path

REPO = Path(__file__).resolve().parents[2]
SCHEMA = "mip.archive-legacy-root.plan.v1"
ARCHIVE_DIR = "legacy-archive"
APP_RESOURCE = "apps.mip_app"
COPYABLE = frozenset({"FILE", "NOTEBOOK"})
_WORKSPACE_ROOTS = ("/Workspace/", "/Users/", "/Shared/", "/Repos/")
_ABSENT = (NotFound, ResourceDoesNotExist)
_PREFIX = "[mip-archive]"


class ArchiveRefused(RuntimeError):
    """The archive refused; the message names no path or id."""


@dataclass(frozen=True)
class Refusal:
    code: str
    where: str  # a move index or a symbolic resource key; never a path
    reason: str


@dataclass
class ArchivePlan:
    document: dict[str, Any]
    refusals: list[Refusal] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        return sha256_hex(self.document)

    def public(self) -> dict[str, Any]:
        moves = self.document.get("moves", [])
        return {
            "plan_sha256": self.sha256,
            "moves": len(moves),
            "objects": sum(len(move["objects"]) for move in moves),
            "protected": len(self.document.get("protected", [])),
            "refusals": [{"code": r.code, "where": r.where} for r in self.refusals],
        }


def _norm(path: str) -> str:
    path = path if path.startswith("/Workspace/") else f"/Workspace{path}"
    return path.rstrip("/").casefold()


def intersects(move: str, protected: str) -> bool:
    """Either path inside the other, or the same path (case-insensitively)."""

    left, right = _norm(move), _norm(protected)
    return left == right or right.startswith(f"{left}/") or left.startswith(f"{right}/")


def _plain(item: Any) -> Any:
    as_dict = getattr(item, "as_dict", None)
    return as_dict() if callable(as_dict) else item


def _paths_in(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.startswith(_WORKSPACE_ROOTS):
            yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _paths_in(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _paths_in(item)


def resource_paths(workspace: Any, kind: str, live_id: str) -> list[str]:
    """Every workspace path one bound resource uses; a not-found raises (the state is stale)."""

    if kind == "dashboards":
        dashboard = workspace.lakeview.get(dashboard_id=live_id)
        path = str(getattr(dashboard, "path", "") or "")
        parent, name = (str(getattr(dashboard, attr, "") or "") for attr in ("parent_path", "display_name"))
        return [path or f"{parent.rstrip('/')}/{name}.lvdash.json"]
    if kind == "experiments":
        name = str(getattr(getattr(workspace.experiments.get_experiment(experiment_id=live_id), "experiment", None), "name", "") or "")
        return [name] if name.startswith("/") else []
    if kind == "apps":
        app = workspace.apps.get(name=live_id)
        found = [str(getattr(app, "default_source_code_path", "") or "")]
        for deployment in ("active_deployment", "pending_deployment"):
            found.append(str(getattr(getattr(app, deployment, None), "source_code_path", "") or ""))
        return [path for path in found if path]
    if kind == "jobs":
        return sorted(set(_paths_in(_plain(getattr(workspace.jobs.get(job_id=int(live_id)), "settings", None)))))
    if kind == "pipelines":
        return sorted(set(_paths_in(_plain(getattr(workspace.pipelines.get(pipeline_id=live_id), "spec", None)))))
    return []


def _kind(item: Any) -> str:
    kind = getattr(item, "object_type", None)
    return str(getattr(kind, "value", kind) or "")


def _language(item: Any) -> str:
    language = getattr(item, "language", None)
    return str(getattr(language, "value", language) or "")


@dataclass(frozen=True)
class Roots:
    legacy: str
    deployer: str

    @property
    def archive(self) -> str:
        return f"{self.deployer}/{ARCHIVE_DIR}/{self.legacy.rstrip('/').rsplit('/', 1)[-1]}"


def compute(workspace: Any, roots: Roots, only: tuple[str, ...] = ()) -> ArchivePlan:
    """The read-only plan: protected paths, the moves and every object each would copy."""

    refusals: list[Refusal] = []
    state = read_state(workspace, f"{roots.deployer}/{STATE_FILE}")
    bound, _children, malformed = state_entries(state, "deployer.state")
    refusals += [Refusal(r.code, r.resource, r.reason) for r in malformed]
    if APP_RESOURCE not in bound:
        refusals.append(Refusal("not_adopted", APP_RESOURCE, "this deployer's state binds no App: adopt first"))
    protected = {f"{roots.legacy.rstrip('/')}/state": "legacy.state"}
    for resource, live_id in sorted(bound.items()):
        try:
            for path in resource_paths(workspace, resource.split(".", 1)[0], live_id):
                protected.setdefault(path, resource)
        except _ABSENT:
            refusals.append(Refusal("bound_object_missing", resource, "the state binds an object that is gone"))
        except Exception as exc:  # noqa: BLE001 - unproven paths are never "none"
            refusals.append(Refusal("read_failed", resource, type(exc).__name__))
    children = {
        str(getattr(item, "path", "")).rsplit("/", 1)[-1]: item
        for item in workspace.workspace.list(api_path(roots.legacy))
    }
    names = list(only) or sorted(children)
    moves = []
    for index, name in enumerate(names, start=1):
        if name not in children:
            refusals.append(Refusal("unknown_child", f"move.{index}", "--only names no child of the legacy root"))
            continue
        source = f"{roots.legacy.rstrip('/')}/{name}"
        for path, owner in sorted(protected.items()):
            if intersects(source, path):
                refusals.append(Refusal("bound_path", f"move.{index}", f"covers a path {owner} uses"))
        item = children[name]
        listed = (
            list(workspace.workspace.list(api_path(source), recursive=True))
            if _kind(item) == "DIRECTORY"
            else [item]
        )
        objects = []
        for entry in sorted(listed, key=lambda value: str(getattr(value, "path", ""))):
            kind, language = _kind(entry), _language(entry)
            if kind not in COPYABLE or (kind == "NOTEBOOK" and not language):
                refusals.append(Refusal("unsupported_object", f"move.{index}", f"holds a {kind or 'untyped'} object"))
            objects.append({"path": str(getattr(entry, "path", "")), "type": kind, "language": language})
        moves.append({"source": source, "dest": f"{roots.archive}/{name}", "objects": objects})
    document = {
        "schema": SCHEMA,
        "legacy_root": roots.legacy,
        "archive_root": roots.archive,
        "protected": sorted(protected),
        "moves": moves,
    }
    return ArchivePlan(document, refusals)


def _read(workspace: Any, path: str, kind: str) -> bytes:
    stream = workspace.workspace.download(path, format=ExportFormat.SOURCE) if kind == "NOTEBOOK" else workspace.workspace.download(path)
    return bytes(stream.read())


def _copy(workspace: Any, source_api: str, dest_api: str, kind: str, language: str) -> str:
    """Create-only copy then byte verification; an existing equal copy is kept."""

    data = _read(workspace, source_api, kind)
    workspace.workspace.mkdirs(dest_api.rsplit("/", 1)[0])
    outcome = "copied"
    try:
        if kind == "NOTEBOOK":
            workspace.workspace.upload(dest_api, data, format=ImportFormat.SOURCE, language=Language(language), overwrite=False)
        else:
            workspace.workspace.upload(dest_api, data, format=ImportFormat.AUTO, overwrite=False)
    except ResourceAlreadyExists:
        outcome = "kept"
    status = workspace.workspace.get_status(dest_api)
    if _kind(status) != kind or _read(workspace, dest_api, kind) != data:
        raise ArchiveRefused("an archived copy is not byte-equal to its original")
    return outcome


def apply(workspace: Any, roots: Roots, ack: str, only: tuple[str, ...] = ()) -> dict[str, int]:
    """Recompute, compare with ``ack``, then copy create-only; raises on any refusal."""

    plan = compute(workspace, roots, only)
    if plan.refusals:
        raise ArchiveRefused(f"the archive plan refuses ({len(plan.refusals)} refusals)")
    if plan.sha256 != ack:
        raise ArchiveRefused("the recomputed archive plan is not the acknowledged digest; nothing was copied")
    counts = {"copied": 0, "kept": 0}
    for move in plan.document["moves"]:
        source_root, dest_root = api_path(move["source"]), api_path(move["dest"])
        for item in move["objects"]:
            relative = item["path"][len(source_root) :] if item["path"].startswith(source_root) else ""
            if item["path"] != source_root and not relative.startswith("/"):
                raise ArchiveRefused("a listed object is outside its move")
            counts[_copy(workspace, item["path"], dest_root + relative, item["type"], item["language"])] += 1
    return counts


def _write_private(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, 0o600)


def _profile_roots() -> Roots:
    from tools.clients.environment import from_local, resolve

    profile = resolve(from_local(os.environ, REPO / ".env.local"))
    if not profile.legacy_bundle_root:
        raise ArchiveRefused("MIP_LEGACY_BUNDLE_ROOT is unset: there is no legacy root to archive")
    deployer = state_path(deployer=profile.identity_ids.deployer, client_key=profile.client)
    return Roots(profile.legacy_bundle_root, deployer.removesuffix(f"/{STATE_FILE}"))


def _workspace() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def main(
    argv: list[str] | None = None,
    *,
    roots: Callable[[], Roots] = _profile_roots,
    workspace_factory: Callable[[], Any] = _workspace,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", default=[], metavar="NAME")
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ack-plan", default="")
    args = parser.parse_args(argv)
    if args.apply != bool(args.ack_plan):
        parser.error("--apply and --ack-plan go together")
    try:
        legacy = roots()
        workspace = workspace_factory()
        plan = compute(workspace, legacy, tuple(args.only))
        if args.report_out is not None:
            _write_private(args.report_out, {**plan.document, "refusals": [vars(r) for r in plan.refusals]})
        print(f"{_PREFIX} plan: {json.dumps(plan.public(), sort_keys=True)}")
        for refusal in plan.refusals:
            print(f"{_PREFIX} REFUSED {refusal.code} {refusal.where}: {refusal.reason}", file=sys.stderr)
        if plan.refusals:
            return 2
        if not args.apply:
            print(f"{_PREFIX} read-only; to copy, review the report, then re-run with --apply --ack-plan {plan.sha256}")
            return 0
        counts = apply(workspace, legacy, args.ack_plan, tuple(args.only))
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry a path
        detail = str(exc) if isinstance(exc, ArchiveRefused | ProfileRefusal | BundleStateError) else type(exc).__name__
        print(f"{_PREFIX} REFUSED: {detail}", file=sys.stderr)
        return 2
    print(f"{_PREFIX} archived: {counts['copied']} copied, {counts['kept']} already archived; nothing was deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
