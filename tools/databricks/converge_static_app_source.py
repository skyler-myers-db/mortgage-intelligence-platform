#!/usr/bin/env python3
"""Converge uploaded App source to a prebuilt, Python-served frontend.

Two modes, both bound to the authenticated bundle identity (principal,
target and root leaf):

* files-root mode (``--source-code-path`` is the bundle ``files/`` root):
  require a non-empty ``frontend/dist/index.html`` and delete only the exact
  root ``package.json`` and ``package-lock.json``. Kept for manual cleanup.
* release mode (``--release-id``, ``--release-manifest`` and
  ``--expected-git-sha``, all three together): the path must be exactly
  ``<bundle root>/app-src/<id>`` for that commit
  (tools/databricks/app_release_contract.py); the manifest must be staged for
  the commit with that id; and an exact recursive listing must show the
  manifest's paths, each a FILE of the manifest's size (a file listed
  without a size is downloaded and hashed). The manifest's
  ``frontend/dist/index.html`` must be non-empty. Root Node manifests are
  refused, never deleted: a release folder is immutable. Each root manifest
  name is also probed with ``get_status``, because a recursive listing never
  shows a directory, so an empty directory under that name is refused too.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks import app_release_contract
from tools.databricks import app_release_source as release_source

_ROOT_NODE_MANIFESTS = ("package.json", "package-lock.json")


def _expected_source_path(*, principal: str, target: str, root_leaf: str | None = None) -> str:
    """The bundle files root (tools/databricks/app_release_contract.files_root)."""
    return app_release_contract.files_root(principal=principal, target=target, root_leaf=root_leaf)


def _workspace_api_path(
    source_code_path: str,
    *,
    expected_principal: str,
    expected_target: str,
    expected_root_leaf: str | None = None,
) -> str:
    if (
        type(source_code_path) is not str
        or not source_code_path
        or source_code_path != source_code_path.strip()
    ):
        raise RuntimeError("App source path is not the exact MIP bundle files root")
    source = source_code_path
    expected_source = _expected_source_path(
        principal=expected_principal,
        target=expected_target,
        root_leaf=expected_root_leaf,
    )
    if source != expected_source:
        raise RuntimeError("App source path does not match the authenticated bundle identity")
    parsed = PurePosixPath(source)
    parts = parsed.parts
    if (
        str(parsed) != source
        or len(parts) != 8
        or parts[:3] != ("/", "Workspace", "Users")
        or not parts[3]
        or parts[4:6] != (".bundle", "mortgage-intelligence-platform")
        or parts[7] != "files"
        or any(part in {".", ".."} for part in parts)
    ):
        raise RuntimeError("App source path is not the exact MIP bundle files root")
    api_path = source.removeprefix("/Workspace")
    return api_path


def _status_or_none(workspace: Any, path: str) -> object | None:
    try:
        status = workspace.workspace.get_status(path)
    except (NotFound, ResourceDoesNotExist):
        return None
    except Exception as exc:  # noqa: BLE001 - source proof is fail-closed
        raise RuntimeError(f"could not read uploaded App source object {path}") from exc
    if status is None:
        raise RuntimeError(f"uploaded App source returned no status for {path}")
    actual_path = getattr(status, "path", None)
    object_type = str(
        getattr(getattr(status, "object_type", None), "value", getattr(status, "object_type", ""))
        or ""
    ).upper()
    if type(actual_path) is not str or actual_path != path or object_type != "FILE":
        raise RuntimeError(f"uploaded App source object has invalid identity: {path}")
    return status


def _delete_exact_file(workspace: Any, path: str) -> None:
    before = _status_or_none(workspace, path)
    if before is None:
        return
    try:
        workspace.workspace.delete(path, recursive=False)
    except Exception as delete_error:  # noqa: BLE001 - reconcile ambiguous deletion
        try:
            remaining = _status_or_none(workspace, path)
        except Exception as read_error:  # noqa: BLE001
            raise RuntimeError(
                f"could not authenticate App source after ambiguous deletion: {path}"
            ) from read_error
        if remaining is None:
            return
        raise RuntimeError(f"root Node manifest remained after deletion: {path}") from delete_error
    if _status_or_none(workspace, path) is not None:
        raise RuntimeError(f"root Node manifest remained after deletion: {path}")


def converge_static_app_source(
    workspace: Any,
    *,
    source_code_path: str,
    expected_principal: str,
    expected_target: str,
    expected_root_leaf: str | None = None,
) -> None:
    """Require a built SPA and remove only exact root npm build manifests."""

    root = _workspace_api_path(
        source_code_path,
        expected_principal=expected_principal,
        expected_target=expected_target,
        expected_root_leaf=expected_root_leaf,
    )
    index_path = f"{root}/frontend/dist/index.html"
    index_status = _status_or_none(workspace, index_path)
    index_size = getattr(index_status, "size", None) if index_status is not None else None
    if index_status is None or type(index_size) is not int or index_size <= 0:
        raise RuntimeError("uploaded App source is missing a non-empty frontend/dist/index.html")
    for manifest in _ROOT_NODE_MANIFESTS:
        _delete_exact_file(workspace, f"{root}/{manifest}")


def _object_at(workspace: Any, path: str) -> bool:
    """Whether any object, a directory included, is at ``path``; unreadable refuses."""
    try:
        workspace.workspace.get_status(path)
    except (NotFound, ResourceDoesNotExist):
        return False
    except Exception as exc:  # noqa: BLE001 - absence must be proven
        raise app_release_contract.ReleaseContractError(
            f"could not prove {path} absent ({type(exc).__name__})"
        ) from exc
    return True


def converge_release_source(
    workspace: Any,
    *,
    source_code_path: str,
    release_id: str,
    manifest_path: Path,
    expected_git_sha: str,
    expected_principal: str,
    expected_target: str,
    expected_root_leaf: str | None = None,
) -> app_release_contract.Manifest:
    """Refuse an activation unless the release folder still matches its manifest."""

    expected = app_release_contract.release_path(
        principal=expected_principal,
        target=expected_target,
        root_leaf=expected_root_leaf,
        release_id=release_id,
        commit=expected_git_sha,
    )
    if source_code_path != expected:
        raise app_release_contract.ReleaseContractError(
            "App source path is not the exact release folder for this commit and bundle identity"
        )
    manifest = app_release_contract.read_manifest(manifest_path, expected_commit=expected_git_sha)
    if manifest.release_id != release_id:
        raise app_release_contract.ReleaseContractError(
            "App release id is not the manifest's release id"
        )
    by_path = manifest.by_path()
    carried = [name for name in _ROOT_NODE_MANIFESTS if name in by_path]
    index = by_path.get(release_source.INDEX)
    if carried or index is None or index.size <= 0:
        raise app_release_contract.ReleaseContractError(
            "App release manifest needs a non-empty frontend/dist/index.html and no root "
            "Node manifest"
        )
    api_root = app_release_contract.api_path(expected)
    found = release_source.remote_files(workspace, api_root)
    present = [
        name for name in _ROOT_NODE_MANIFESTS if name in found or _object_at(workspace, f"{api_root}/{name}")
    ]
    if present:
        raise app_release_contract.ReleaseContractError(
            f"release folder carries root Node manifests ({', '.join(present)}); an "
            "immutable release folder is refused, never edited"
        )
    unsized = release_source.compare_listing(found, manifest)
    if unsized:
        release_source.verify_hashes(
            workspace, api_root, [by_path[name] for name in unsized],
            workers=release_source.DEFAULT_WORKERS,
        )
    return manifest


def main(argv: list[str] | None = None, *, client: Callable[[], Any] = WorkspaceClient) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--source-code-path", required=True)
    parser.add_argument("--release-id", default=None, help="release mode: the per-release folder id")
    parser.add_argument("--release-manifest", type=Path, default=None, help="release mode: its manifest")
    parser.add_argument("--expected-git-sha", default=None, help="release mode: the deploy commit")
    parser.add_argument("--expected-principal", required=True)
    parser.add_argument("--expected-target", required=True)
    parser.add_argument("--expected-root-leaf", default=None, help="the bundle root leaf (default: the target)")
    args = parser.parse_args(argv)
    release_args = (args.release_id, args.release_manifest, args.expected_git_sha)
    if any(value is not None for value in release_args):
        if any(value is None for value in release_args):
            parser.error("--release-id, --release-manifest and --expected-git-sha go together")
        try:
            manifest = converge_release_source(
                client(),
                source_code_path=args.source_code_path,
                release_id=args.release_id,
                manifest_path=args.release_manifest,
                expected_git_sha=args.expected_git_sha,
                expected_principal=args.expected_principal,
                expected_target=args.expected_target,
                expected_root_leaf=args.expected_root_leaf,
            )
        except app_release_contract.ReleaseContractError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(
            f"[static-app-source] release {manifest.release_id} matches its manifest "
            f"({len(manifest.entries)} files by path, type and size); prebuilt frontend "
            "present; root Node manifests absent"
        )
        return 0
    converge_static_app_source(
        client(),
        source_code_path=args.source_code_path,
        expected_principal=args.expected_principal,
        expected_target=args.expected_target,
        expected_root_leaf=args.expected_root_leaf,
    )
    print("[static-app-source] prebuilt frontend present; root Node manifests absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
