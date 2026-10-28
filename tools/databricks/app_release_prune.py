#!/usr/bin/env python3
"""Prune App release folders that no retained deployment references (best effort).

deploy.sh runs this after a green capture, never on the rollback branch, and
only warns when it fails. It deletes, as the deploy identity, only DIRECTORY
children of the exact ``<bundle root>/app-src`` whose names are release ids
(tools/databricks/app_release_contract.py), and keeps:

* the current release, which must be the App's active deployment source;
* the folder of the signed rollback record's deployment, when it is one;
* the folder the App's ``default_source_code_path`` names, if any;
* the KEEP_PREVIOUS newest other release folders the App's deployments
  reference (every page of ``list_deployments``, newest ``create_time``
  first);
* any folder a listed deployment's ``/src/`` snapshot lies in.

The rollback record's and the App's default source are matched by any path
segment that is a release id, so a trailing slash, a missing ``/Workspace``
prefix or another case of the principal still keeps the folder.

Before each delete it re-asserts the signed deployment lease, re-reads the
App (no pending deployment, the active one still the current release, and
neither it nor the App's default source naming the candidate) and re-derives
the candidate's path; then it deletes
recursively and requires the folder to be gone. A delete whose outcome is
ambiguous is reconciled by reading the folder back; one that cannot be
reconciled stops the run. At most MAX_DELETIONS folders go per run, and no
delete starts once TIME_BUDGET_SECONDS have passed since the run began (each
SDK call carries its own timeout), so a throttled workspace cannot hold a
green deploy open.

Anything uncertain in the reads before the first delete skips the whole run
and deletes nothing (exit 0): no lease or a lease that is not held, an App or
deployment read that fails, an App id that does not match, a pending
deployment, an active deployment that is not the current release or is
missing from the listing, a listed deployment without a source or a
``create_time``, a rollback deployment id that is not listed, or a release
root that cannot be listed (a missing one just means nothing to prune). An
uncertainty the re-check before a later delete finds (a lost lease, a new
pending deployment, the time budget spent) stops the run there and deletes
nothing more; each folder already deleted passed its own re-check.
``files/``, ``/src/`` snapshots, names that are not release ids and plain
files are never touched.

Exit codes: 0 pruned, nothing to prune, or skipped; 1 a deletion could not be
reconciled; 2 bad arguments.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from tools.databricks import app_deployment_lease
from tools.databricks import app_release_contract as contract

KEEP_PREVIOUS = 2
MAX_DELETIONS = 20
TIME_BUDGET_SECONDS = 600
PREFIX = "[app-release-prune]"


class Skip(Exception):
    """A precondition is uncertain: delete nothing more."""


class Unreconciled(RuntimeError):
    """A deletion's outcome could not be proven: stop the run."""


@dataclass(frozen=True)
class Target:
    """Validated arguments: what this run may consider at all."""

    app_name: str
    expected_app_id: str
    current_release: str
    rollback_deployment_id: str
    principal: str
    target: str
    root_leaf: str | None

    def release_path(self, release_id: str) -> str:
        return contract.release_path(
            principal=self.principal, target=self.target, root_leaf=self.root_leaf,
            release_id=release_id,
        )

    def release_id_of(self, path: object) -> str | None:
        return contract.release_id_of(
            path, principal=self.principal, target=self.target, root_leaf=self.root_leaf
        )

    @property
    def api_release_root(self) -> str:
        return contract.api_path(
            contract.release_root(principal=self.principal, target=self.target, root_leaf=self.root_leaf)
        )


@dataclass
class PruneReport:
    skipped: str = ""
    kept: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


def check_args(
    *,
    app_name: str,
    expected_app_id: str,
    current_release: str,
    rollback_deployment_id: str,
    source_git_sha: str,
    files_root: str,
    principal: str,
    target: str,
    root_leaf: str | None,
) -> Target:
    """Refuse bad arguments (exit 2); nothing remote is read."""
    if not app_name or app_name != app_name.strip() or not expected_app_id.strip():
        raise contract.ReleaseContractError("--app-name and --expected-app-id are required")
    if files_root != contract.files_root(principal=principal, target=target, root_leaf=root_leaf):
        raise contract.ReleaseContractError("--files-root is not the expected bundle files root")
    contract.release_path(
        principal=principal, target=target, root_leaf=root_leaf,
        release_id=current_release, commit=contract.check_commit(source_git_sha),
    )
    return Target(
        app_name, expected_app_id.strip(), current_release, rollback_deployment_id.strip(),
        principal, target, root_leaf,
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _object_type(info: object) -> str:
    kind = getattr(info, "object_type", None)
    return str(getattr(kind, "value", kind) or "").upper()


def _created(deployment: object) -> datetime:
    raw = _text(getattr(deployment, "create_time", None))
    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
    except ValueError:
        created = None
    if created is None or created.tzinfo is None:
        raise Skip("a listed deployment has no usable create_time")
    return created


def _named_release_ids(source: object) -> set[str]:
    """Every path segment of ``source`` that is a release id, in any spelling of the path.

    Only for keeping: matching more than the canonical path can only keep more.
    """
    return {part for part in _text(source).casefold().split("/") if contract.RELEASE_ID_RE.fullmatch(part)}


def _current_app(workspace: Any, target: Target) -> object:
    """The App, with no pending deployment and the current release active."""
    try:
        app = workspace.apps.get(target.app_name)
    except Exception as exc:  # noqa: BLE001 - any read failure is uncertainty
        raise Skip(f"the App could not be read ({type(exc).__name__})") from exc
    if _text(getattr(app, "id", None)) != target.expected_app_id:
        raise Skip("the App id does not match the deployed App")
    if getattr(app, "pending_deployment", None) is not None:
        raise Skip("the App has a pending deployment")
    active = getattr(app, "active_deployment", None)
    if _text(getattr(active, "source_code_path", None)) != target.release_path(target.current_release):
        raise Skip("the active deployment is not the current release")
    if not _text(getattr(active, "deployment_id", None)):
        raise Skip("the active deployment has no id")
    return app


def _retained(workspace: Any, target: Target, active_id: str) -> tuple[dict[str, str], set[str]]:
    """(kept release ids with their reason, every listed deployment's /src/ snapshot)."""
    try:
        deployments = list(workspace.apps.list_deployments(target.app_name))
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"the App deployments could not be listed ({type(exc).__name__})") from exc
    by_id: dict[str, object] = {}
    snapshots: set[str] = set()
    for item in deployments:
        deployment_id = _text(getattr(item, "deployment_id", None))
        source = _text(getattr(item, "source_code_path", None))
        if not deployment_id or not source or deployment_id in by_id:
            raise Skip("a listed deployment has no id or source, or a repeated id")
        _created(item)
        by_id[deployment_id] = item
        snapshot = getattr(getattr(item, "deployment_artifacts", None), "source_code_path", None)
        if _text(snapshot):
            snapshots.add(_text(snapshot))
    if active_id not in by_id:
        raise Skip("the active deployment is missing from the deployment listing")
    kept = {target.current_release: "current"}
    if target.rollback_deployment_id:
        rollback = by_id.get(target.rollback_deployment_id)
        if rollback is None:
            raise Skip("the rollback record's deployment is not listed")
        for rollback_id in sorted(_named_release_ids(getattr(rollback, "source_code_path", None))):
            kept.setdefault(rollback_id, "rollback record")
    newest = sorted(by_id.items(), key=lambda pair: (_created(pair[1]), pair[0]), reverse=True)
    previous: list[str] = []
    for _, item in newest:
        release_id = target.release_id_of(_text(getattr(item, "source_code_path", None)))
        if release_id is None or release_id == target.current_release or release_id in previous:
            continue
        previous.append(release_id)
        if len(previous) == KEEP_PREVIOUS:
            break
    for release_id in previous:
        kept.setdefault(release_id, "previous")
    return kept, snapshots


def _status(workspace: Any, path: str) -> object | None:
    try:
        return workspace.workspace.get_status(path)
    except NotFound:
        return None


def _candidates(
    workspace: Any, target: Target, report: PruneReport, snapshots: set[str]
) -> list[str]:
    root = target.api_release_root
    try:
        children = list(workspace.workspace.list(root))
    except NotFound:
        return []
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"{root} could not be listed ({type(exc).__name__})") from exc
    candidates: list[str] = []
    for child in children:
        path = getattr(child, "path", None)
        name = path.removeprefix(f"{root}/") if isinstance(path, str) else ""
        if (
            not isinstance(path, str)
            or not path.startswith(f"{root}/")
            or _object_type(child) != "DIRECTORY"
            or contract.RELEASE_ID_RE.fullmatch(name) is None
            or contract.api_path(target.release_path(name)) != path
        ):
            report.ignored.append(str(path))
            continue
        workspace_path = f"/Workspace{path}"
        if name in report.kept:
            continue
        if any(item == workspace_path or item.startswith(f"{workspace_path}/") for item in snapshots):
            report.kept[name] = "holds a deployment snapshot"
            continue
        candidates.append(name)
    return sorted(candidates)


def _delete(workspace: Any, api: str) -> None:
    """Delete one release folder and prove it is gone."""
    try:
        workspace.workspace.delete(api, recursive=True)
    except Exception as delete_error:  # noqa: BLE001 - reconcile an ambiguous deletion
        try:
            remaining = _status(workspace, api)
        except Exception as read_error:  # noqa: BLE001
            raise Unreconciled(f"{api}: deletion failed and could not be read back") from read_error
        if remaining is not None:
            raise Unreconciled(f"{api}: deletion failed and the folder remains") from delete_error
        return
    try:
        remaining = _status(workspace, api)
    except Exception as read_error:  # noqa: BLE001
        raise Unreconciled(f"{api}: deleted but could not be read back") from read_error
    if remaining is not None:
        raise Unreconciled(f"{api}: the folder remains after deletion")


def _recheck(
    workspace: Any, target: Target, lease_check: Callable[[], None], path: str
) -> object | None:
    """Before one delete: the lease, the App and the folder; the folder's status or None."""
    try:
        lease_check()
    except Exception as exc:  # noqa: BLE001 - a lease that is not proven held stops the run
        raise Skip(f"the deployment lease is not proven held ({exc})") from exc
    app = _current_app(workspace, target)
    sources = (
        getattr(getattr(app, "active_deployment", None), "source_code_path", None),
        getattr(getattr(app, "pending_deployment", None), "source_code_path", None),
        getattr(app, "default_source_code_path", None),
    )
    if any(PurePosixPath(path).name in _named_release_ids(source) for source in sources):
        raise Skip("the App's deployment or default source names the folder")
    try:
        status = _status(workspace, path)
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"the folder could not be read ({type(exc).__name__})") from exc
    if status is not None and (_object_type(status) != "DIRECTORY" or getattr(status, "path", None) != path):
        raise Skip("the folder is not the listed directory")
    return status


def prune(
    workspace: Any,
    target: Target,
    *,
    lease_check: Callable[[], None],
    clock: Callable[[], float] = time.monotonic,
) -> PruneReport:
    """Delete unreferenced release folders; any uncertainty stops it, deleting nothing more."""
    started = clock()
    report = PruneReport()
    try:
        try:
            lease_check()
        except Exception as exc:  # noqa: BLE001
            raise Skip(f"the deployment lease is not proven held ({exc})") from exc
        app = _current_app(workspace, target)
        active_id = _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))
        report.kept, snapshots = _retained(workspace, target, active_id)
        for name in sorted(_named_release_ids(getattr(app, "default_source_code_path", None))):
            report.kept.setdefault(name, "App default source")
        candidates = _candidates(workspace, target, report, snapshots)
    except Skip as skip:
        report.skipped = str(skip)
        return report
    report.left = candidates[MAX_DELETIONS:]
    for index, name in enumerate(candidates[:MAX_DELETIONS]):
        if name in report.kept:
            continue
        if clock() - started >= TIME_BUDGET_SECONDS:
            report.skipped = f"stopped before {name}: the {TIME_BUDGET_SECONDS} s time budget is spent"
            report.left = candidates[index:]
            return report
        api = contract.api_path(target.release_path(name))
        try:
            status = _recheck(workspace, target, lease_check, api)
        except Skip as skip:
            report.skipped = f"stopped before {name}: {skip}"
            report.left = candidates[index:]
            return report
        if status is None:
            continue
        _delete(workspace, api)
        report.deleted.append(name)
    return report


def _print(report: PruneReport) -> None:
    if report.kept:
        kept = ", ".join(f"{name} ({why})" for name, why in sorted(report.kept.items()))
        print(f"{PREFIX} kept: {kept}")
    for name in report.deleted:
        print(f"{PREFIX} deleted: {name}")
    if report.left:
        print(f"{PREFIX} left for a later run: {len(report.left)}")
    if report.ignored:
        print(f"{PREFIX} ignored (not a release folder): {len(report.ignored)}")
    if report.skipped:
        print(f"{PREFIX} skipped: {report.skipped}; nothing more was deleted")
    elif not report.deleted:
        print(f"{PREFIX} nothing to prune")


def main(argv: list[str] | None = None, *, client: Callable[[], Any] = WorkspaceClient) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--expected-app-id", required=True)
    parser.add_argument("--current-release", required=True)
    parser.add_argument("--rollback-deployment-id", default="")
    parser.add_argument("--deployment-lease-id", default="")
    parser.add_argument("--source-git-sha", required=True)
    parser.add_argument("--files-root", required=True)
    parser.add_argument("--expected-principal", required=True)
    parser.add_argument("--expected-target", required=True)
    parser.add_argument("--expected-root-leaf", default=None)
    args = parser.parse_args(argv)
    try:
        target = check_args(
            app_name=args.app_name, expected_app_id=args.expected_app_id,
            current_release=args.current_release, rollback_deployment_id=args.rollback_deployment_id,
            source_git_sha=args.source_git_sha, files_root=args.files_root,
            principal=args.expected_principal, target=args.expected_target,
            root_leaf=args.expected_root_leaf,
        )
    except contract.ReleaseContractError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    lease_id = args.deployment_lease_id.strip()
    if not lease_id:
        _print(PruneReport(skipped="no deployment lease id was given"))
        return 0
    try:
        workspace = client()
    except Exception as exc:  # noqa: BLE001 - no client, no proof: skip
        _print(PruneReport(skipped=f"no workspace client ({type(exc).__name__})"))
        return 0

    def lease_check() -> None:
        app_deployment_lease.assert_held(
            workspace, app_name=target.app_name, lease_id=lease_id,
            source_git_sha=args.source_git_sha,
        )

    try:
        report = prune(workspace, target, lease_check=lease_check)
    except Unreconciled as exc:
        print(f"{PREFIX} stopped: {exc}", file=sys.stderr)
        return 1
    _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
