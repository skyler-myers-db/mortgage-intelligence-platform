"""Read, compare and copy the source folder of the deployment adopt-live signs (M2-PR7, attack G13).

adopt-live trusts nothing the live folder says about itself:

* ``read_folder`` downloads every file of a ``/Workspace/...`` folder (an
  exact recursive listing; anything that is not a FILE refuses);
  ``folder_digest`` is ``app_start_deployment.tree_digest``'s formula over
  those bytes, so the digest a later start, re-pin or restore recomputes
  remotely is the same number.
* ``closure_differences`` compares every file outside ``frontend/dist`` with
  the App runtime closure git tracks at the health sha
  (``app_runtime_closure.tracked_files``): the same path set, byte for byte.
  ``app.yaml`` is compared on its own (``app_yaml_equal``), so its refusal
  names it.
* ``frontend_provenance`` compares ``frontend/dist`` with a dist built from
  the health sha: ``verified`` when the two hold the same paths and bytes,
  ``unverified`` otherwise, or when no such build was given. No CI job keeps
  a dist artifact (ci.yml uploads only source maps), so the build artifact is
  a local build of a checkout of that sha (``npm --prefix frontend ci && npm
  --prefix frontend run build``) passed as ``--reference-dist``; a different
  Node or a re-compressed ``.br`` reads as ``unverified``, never as verified.
* ``copy_to`` stages the downloaded bytes and publishes them create-only into
  the SP-owned ``app-src/adopted-<id12>-<digest12>`` with
  ``app_release_source``'s own ``check_stage`` and ``upload_release`` (exact
  listing and a sha256 download of every file); the caller then re-hashes
  the copy remotely and refuses on any difference.

Nothing here stops, starts, deploys or updates an App.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from tools.databricks import app_release_contract as contract
from tools.databricks import app_runtime_closure as closure
from tools.databricks.app_release_retry import RATE_LIMIT_RETRY, RateLimitRetry
from tools.databricks.app_release_source import (
    DEFAULT_WORKERS,
    PublishPlan,
    _download,
    _parallel,
    check_stage,
    remote_files,
    upload_release,
)
from tools.databricks.app_start_deployment import digest_entries

DIST_PREFIX = f"{closure.DIST_PATH}/"
APP_YAML = "app.yaml"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_folder(
    workspace: Any,
    workspace_path: str,
    *,
    workers: int = DEFAULT_WORKERS,
    retry: RateLimitRetry = RATE_LIMIT_RETRY,
) -> dict[str, bytes]:
    """Every file below a ``/Workspace/...`` folder, by relative path."""

    api_root = contract.api_path(workspace_path)
    relpaths = sorted(remote_files(workspace, api_root, retry=retry))

    def one(relpath: str) -> tuple[str, bytes]:
        return relpath, _download(workspace, f"{api_root}/{relpath}", retry)

    return dict(_parallel(workers, one, relpaths))


def folder_digest(files: Mapping[str, bytes]) -> str:
    """The full-tree digest of downloaded bytes (``tree_digest``'s formula)."""

    return digest_entries((path, len(data), _sha256(data)) for path, data in files.items())


def _shown(paths: list[str]) -> str:
    more = f" (+{len(paths) - 3} more)" if len(paths) > 3 else ""
    return ", ".join(paths[:3]) + more


def _git_blob(repo: Path, commit: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def closure_differences(files: Mapping[str, bytes], *, repo: Path, commit: str) -> list[str]:
    """How the folder outside ``frontend/dist`` (``app.yaml`` aside) differs from the closure."""

    resolved, by_entry = closure.tracked_files(repo, commit)
    tracked = sorted(name for names in by_entry.values() for name in names if name != APP_YAML)
    blobs = closure._blobs(repo, resolved, tracked)
    ours = sorted(path for path in files if not path.startswith(DIST_PREFIX) and path != APP_YAML)
    missing = sorted(set(tracked) - set(ours))
    extra = sorted(set(ours) - set(tracked))
    changed = sorted(path for path in set(tracked) & set(ours) if blobs[path] != files[path])
    lines = []
    if missing:
        lines.append(f"missing from the folder: {_shown(missing)}")
    if extra:
        lines.append(f"not in the closure at the health sha: {_shown(extra)}")
    if changed:
        lines.append(f"bytes differ from the health sha: {_shown(changed)}")
    return lines


def app_yaml_equal(files: Mapping[str, bytes], *, repo: Path, commit: str) -> bool:
    """``app.yaml`` byte-equal to the repo's at the health sha."""

    expected = _git_blob(repo, commit, APP_YAML)
    return expected is not None and files.get(APP_YAML) == expected


def frontend_provenance(files: Mapping[str, bytes], reference_dist: Path | None) -> tuple[str, str]:
    """``verified`` when ``frontend/dist`` equals the build artifact, else ``unverified``; and why."""

    ours = {
        path.removeprefix(DIST_PREFIX): _sha256(data)
        for path, data in files.items()
        if path.startswith(DIST_PREFIX)
    }
    if reference_dist is None:
        return "unverified", "no build artifact for the health sha was given (--reference-dist)"
    try:
        reference = {
            entry.path: entry.sha256 for entry in contract.manifest_entries(reference_dist)
        }
    except contract.ReleaseContractError as exc:
        return "unverified", f"the build artifact is unreadable: {exc}"
    if ours == reference:
        return "verified", f"frontend/dist equals the build artifact ({len(ours)} files)"
    differ = sorted(
        path for path in set(ours) | set(reference) if ours.get(path) != reference.get(path)
    )
    return "unverified", f"frontend/dist differs from the build artifact in {len(differ)} files"


def copy_to(
    workspace: Any,
    files: Mapping[str, bytes],
    *,
    release_path: str,
    commit: str,
    workers: int = DEFAULT_WORKERS,
    retry: RateLimitRetry = RATE_LIMIT_RETRY,
) -> None:
    """Publish ``files`` create-only into ``release_path`` with app_release_source's own checks."""

    with tempfile.TemporaryDirectory(prefix="mip-adopted-copy-") as private:
        stage = Path(private) / "source"
        for relpath, data in files.items():
            target = stage / contract.check_relpath(relpath)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        entries = contract.manifest_entries(stage)
        manifest = contract.Manifest(
            contract.check_commit(commit), PurePosixPath(release_path).name, entries
        )
        check_stage(stage, manifest)
        upload_release(workspace, PublishPlan(manifest, stage, release_path, workers), retry=retry)
