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
  ``unverified`` otherwise. No CI job keeps a dist artifact (ci.yml uploads
  only source maps), and a directory an operator names proves nothing (a
  copy of the live dist itself would compare equal), so the only reference is
  the one ``build_reference_dist`` builds here: ``git archive`` of that sha
  into a private temporary tree, then ``npm ci --ignore-scripts`` and
  ``npm run build`` there, with an allowlisted environment that holds no
  Databricks, proof or npm credential (``build_environment``). It is no
  sandbox: the build runs as the operator's OS user, like the deploy's own
  frontend build. No build, a failed one, a different Node, a re-compressed
  ``.br`` or a lender mark the build cannot stage all read as ``unverified``,
  never as verified.
* ``copy_to`` stages the downloaded bytes and publishes them create-only into
  the SP-owned ``app-src/adopted-<id12>-<digest12>`` with
  ``app_release_source``'s own ``check_stage`` and ``upload_release`` (exact
  listing and a sha256 download of every file); the caller then re-hashes
  the copy remotely and refuses on any difference.

Nothing here stops, starts, deploys or updates an App.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Mapping
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
# What the reference build may see of the environment: the tools, npm's
# registry, cache and proxy settings, and the reviewed tenant appearance the
# deploy exports for its own build (frontend/vite.config.ts). No Databricks,
# proof or npm credential: npm settings are an allowlist (never an auth token,
# password or client key), a URL that carries userinfo is dropped, and npm's
# user and global config are an empty private file, so ~/.npmrc is never read.
BUILD_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "CI",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "NODE_EXTRA_CA_CERTS",
        "MIP_DEFAULT_THEME",
        "MIP_DEFAULT_ACCENT",
        "MIP_LENDER_MARK_SHA256",
        "MIP_LENDER_NAME",
    }
)
BUILD_ENV_PREFIXES = ("npm_config_", "NPM_CONFIG_")
# The npm settings a build may take from the operator (lower case, ``-`` as ``_``).
BUILD_NPM_SETTINGS = frozenset(
    {
        "registry",
        "cache",
        "cafile",
        "strict_ssl",
        "proxy",
        "https_proxy",
        "noproxy",
        "prefer_offline",
        "offline",
        "fetch_retries",
        "fetch_retry_mintimeout",
        "fetch_retry_maxtimeout",
        "fetch_timeout",
        "loglevel",
        "progress",
    }
)
_URL_SETTINGS = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "registry", "proxy", "https_proxy"})
# ``npm ci`` runs no dependency install script: the lockfile's only one is
# fsevents, an optional dev file watcher the build never loads.
NPM_CI = ("ci", "--no-audit", "--no-fund", "--ignore-scripts")


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
        return "unverified", "no reference build of the health sha was given"
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


def _npm_setting(name: str) -> str | None:
    prefix = next((p for p in BUILD_ENV_PREFIXES if name.startswith(p)), None)
    return None if prefix is None else name.removeprefix(prefix).lower().replace("-", "_")


def build_environment(environ: Mapping[str, str], *, npmrc: Path) -> dict[str, str]:
    """The reference build's environment: no credential (see ``BUILD_ENV_NAMES``)."""

    env: dict[str, str] = {}
    for name, value in environ.items():
        setting = _npm_setting(name)
        if name not in BUILD_ENV_NAMES and setting not in BUILD_NPM_SETTINGS:
            continue
        if (name in _URL_SETTINGS or setting in _URL_SETTINGS) and "@" in value:
            continue  # userinfo in a registry or proxy URL is a credential
        env[name] = value
    return {**env, "npm_config_userconfig": str(npmrc), "npm_config_globalconfig": str(npmrc)}


def build_reference_dist(
    repo: Path,
    commit: str,
    workdir: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Build ``frontend/dist`` from ``git archive <commit>`` in ``workdir``; raise on any failure."""

    tree = workdir / "tree"
    tree.mkdir(parents=True)
    archive = run(
        ["git", "-C", str(repo), "archive", "--format=tar", contract.check_commit(commit)],
        capture_output=True,
        check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(tree, filter="data")
    npmrc = workdir / "npmrc"
    npmrc.write_bytes(b"")
    env = build_environment(os.environ if environ is None else environ, npmrc=npmrc)
    frontend = str(tree / "frontend")
    for step in ([*NPM_CI], ["run", "build"]):
        run(
            ["npm", "--prefix", frontend, *step], cwd=tree, env=env, capture_output=True, check=True
        )
    dist = tree / closure.DIST_PATH
    if not dist.is_dir():
        raise RuntimeError("the reference build wrote no frontend/dist")
    return dist


def dist_digest(files: Mapping[str, bytes]) -> str:
    """The full-tree digest of ``frontend/dist`` alone (``tree_digest``'s formula)."""

    return folder_digest(
        {path: data for path, data in files.items() if path.startswith(DIST_PREFIX)}
    )


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
