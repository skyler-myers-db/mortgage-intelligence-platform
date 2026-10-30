#!/usr/bin/env python3
"""Publish a staged App release folder create-only, and prove it byte for byte.

  python -m tools.databricks.app_release_source publish --release-id ID \\
      --stage-dir DIR --manifest M --files-root P --expected-commit SHA \\
      --expected-principal U --expected-target T [--expected-root-leaf L]
  python -m tools.databricks.app_release_source verify-deployment \\
      --app-name N --manifest M --release-id ID [--deployment-id D]

``publish`` uploads the folder ``app_runtime_closure stage --manifest-out``
wrote to ``<bundle root>/app-src/<id>`` (tools/databricks/app_release_contract.py).
Local checks run first, and any refusal exits 2 before any workspace call:
the manifest is version 1, staged for ``--expected-commit``, and its id
recomputes and equals ``--release-id``; ``--files-root`` is the bundle files
root of the expected principal, target and leaf; the stage directory holds
exactly the manifest's regular files, sizes and sha256 (no symlink, hidden
segment or junk); at most MAX_FILES files and none over MAX_FILE_BYTES;
``app.yaml``, ``requirements.txt`` and a non-empty ``frontend/dist/index.html``
are present and a root ``package.json`` or ``package-lock.json`` is not; and
no file is notebook-shaped (app_release_contract.notebook_shape).

Upload: ``mkdirs`` for each directory, then one create-only AUTO import per
file (``overwrite=False``, the repo's workspace-file convention; the SDK's
own default, SOURCE, would make a notebook of every ``.py`` and ``.sql``).
An object that already exists is downloaded and compared: equal bytes are
reused, different bytes are refused and nothing is touched. An upload that
fails is reconciled the same way. Verify: a recursive list must show
exactly the manifest's paths, each a FILE of the manifest's size, and every
file is then downloaded and its sha256 compared. Publish never deletes and
never overwrites, so a re-run of the same release uploads nothing.

Rate limits (tools/databricks/app_release_retry.py): an upload is retried on
HTTP 429 alone, six attempts, exponential backoff with jitter, the larger of
the backoff and the Retry-After, cut to 30 s per wait; the SDK sends an import
once. A retried upload is still create-only, so a throttled attempt that
landed meets AlreadyExists and the byte compare above. A download or listing
is a GET, which the SDK retries itself on a 429 until its
``retry_timeout_seconds`` (300 s by default); when that runs out it is refused
at once. Either way a call still throttled is a refusal (exit 2) naming the
object and the budget spent, with no reconcile; any other error is handled
exactly as above, on its first occurrence.

``verify-deployment`` is read-only, for the live proof (not wired into
deploy.sh): the deployment (``--deployment-id``, else the App's active one)
must name ``.../app-src/<id>`` as its source, its
``deployment_artifacts.source_code_path`` must be a ``/src/`` snapshot, and
every file in that snapshot must match the manifest's sha256: the bytes the
running App actually uses.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from databricks.sdk.errors import AlreadyExists, NotFound, ResourceAlreadyExists
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks import app_release_contract as contract
from tools.databricks.app_deployment_state import immutable_source
from tools.databricks.app_release_retry import (
    RATE_LIMIT_RETRY,
    RateLimitExhausted,
    RateLimitRetry,
)
from tools.databricks.app_runtime_closure import MAX_STAGED_FILES, is_junk
from tools.databricks.workspace_auth import deployment_workspace_client

ReleaseContractError = contract.ReleaseContractError
MAX_FILES = MAX_STAGED_FILES
MAX_FILE_BYTES = 10 * 1024 * 1024
REQUIRED_FILES = ("app.yaml", "requirements.txt")
INDEX = "frontend/dist/index.html"
ROOT_NODE_MANIFESTS = ("package.json", "package-lock.json")
# 2026-10-29: 8 workers, all one principal, drew HTTP 429 (RESOURCE_EXHAUSTED,
# a per-user limit) part way through a 1,177-file release. Every worker draws
# on that one user's limit, so the default halves the burst; RateLimitRetry
# absorbs what is left. --workers still overrides it.
DEFAULT_WORKERS = 4
MAX_WORKERS = 16
_HEAD_BYTES = 64 * 1024
_CHUNK = 1 << 20
_T = TypeVar("_T")
_R = TypeVar("_R")


def _shown(items: Iterable[str]) -> str:
    listed = list(items)
    more = f" (+{len(listed) - 5} more)" if len(listed) > 5 else ""
    return ", ".join(listed[:5]) + more


def check_release_files(paths: dict[str, int]) -> None:
    """The shape every release folder must have, by relative path and size."""
    if len(paths) > MAX_FILES:
        raise ReleaseContractError(f"{len(paths)} files exceed the {MAX_FILES}-file release budget")
    big = sorted(path for path, size in paths.items() if size > MAX_FILE_BYTES)
    if big:
        raise ReleaseContractError(f"files over {MAX_FILE_BYTES} bytes: {_shown(big)}")
    junk = sorted(path for path in paths if is_junk(path))
    if junk:
        raise ReleaseContractError(f"junk in the release folder: {_shown(junk)}")
    missing = [path for path in (*REQUIRED_FILES, INDEX) if path not in paths]
    if missing:
        raise ReleaseContractError(f"the release folder is missing {', '.join(missing)}")
    if paths[INDEX] <= 0:
        raise ReleaseContractError(f"the release folder has an empty {INDEX}")
    manifests = [path for path in ROOT_NODE_MANIFESTS if path in paths]
    if manifests:
        raise ReleaseContractError(
            f"the release folder carries root Node manifests ({', '.join(manifests)}), which "
            "would make the Apps build run npm"
        )


def check_stage(stage_dir: Path, manifest: contract.Manifest) -> None:
    """``stage_dir`` holds exactly the manifest's files, in a publishable shape."""
    entries = contract.manifest_entries(stage_dir)
    if entries != manifest.entries:
        local = {entry.path: entry for entry in entries}
        expected = manifest.by_path()
        differ = sorted(
            path for path in set(local) | set(expected) if local.get(path) != expected.get(path)
        )
        raise ReleaseContractError(f"the stage directory does not match its manifest: {_shown(differ)}")
    check_release_files({entry.path: entry.size for entry in entries})
    notebooks = []
    for entry in entries:
        with (stage_dir / entry.path).open("rb") as handle:
            shape = contract.notebook_shape(entry.path, handle.read(_HEAD_BYTES))
        if shape is not None:
            notebooks.append(shape)
    if notebooks:
        raise ReleaseContractError(f"an AUTO import would make notebooks of: {_shown(notebooks)}")


def _object_type(info: object) -> str:
    kind = getattr(info, "object_type", None)
    return str(getattr(kind, "value", kind) or "").upper()


def remote_files(
    workspace: Any, api_root: str, *, retry: RateLimitRetry = RATE_LIMIT_RETRY
) -> dict[str, Any]:
    """Every object below ``api_root`` by relative path; each must be a FILE."""
    prefix = f"{api_root}/"
    try:
        # A recursive listing is many GETs. The SDK retries each one's 429 and
        # its timeout is refused at once; a 429 that reaches here restarts the walk.
        listing = retry.call(
            f"listing of {api_root}",
            lambda: list(workspace.workspace.list(api_root, recursive=True)),
        )
    except NotFound as exc:
        raise ReleaseContractError(f"the release folder {api_root} does not exist") from exc
    found: dict[str, Any] = {}
    for info in listing:
        path = getattr(info, "path", None)
        if type(path) is not str or not path.startswith(prefix):
            raise ReleaseContractError(f"listing of {api_root} returned a foreign path {path!r}")
        relpath = contract.check_relpath(path.removeprefix(prefix))
        if _object_type(info) != "FILE":
            raise ReleaseContractError(f"{relpath} is a {_object_type(info) or 'untyped object'}, not a FILE")
        if relpath in found:
            raise ReleaseContractError(f"listing of {api_root} returned {relpath} twice")
        found[relpath] = info
    return found


def compare_listing(found: dict[str, Any], manifest: contract.Manifest) -> list[str]:
    """Refuse a path-set or size difference; return the paths listed without a size."""
    expected = manifest.by_path()
    missing = sorted(set(expected) - set(found))
    extra = sorted(set(found) - set(expected))
    if missing or extra:
        raise ReleaseContractError(
            f"remote files differ from the manifest: missing {_shown(missing) or 'none'}; "
            f"unexpected {_shown(extra) or 'none'}"
        )
    unsized: list[str] = []
    for relpath, info in sorted(found.items()):
        size = getattr(info, "size", None)
        if size is None:
            unsized.append(relpath)
        elif type(size) is not int or size != expected[relpath].size:
            raise ReleaseContractError(f"remote {relpath} is {size!r} bytes, not {expected[relpath].size}")
    return unsized


def _download(workspace: Any, api_file: str, retry: RateLimitRetry) -> bytes:
    def read() -> bytes:
        stream = workspace.workspace.download(api_file)
        chunks = []
        while chunk := stream.read(_CHUNK):
            chunks.append(chunk)
        return b"".join(chunks)

    return retry.call(f"download of {api_file}", read)


def _parallel(workers: int, function: Callable[[_T], _R], items: Iterable[_T]) -> list[_R]:
    """``map`` on a pool; the first failure cancels every call not yet started."""
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        return list(pool.map(function, items))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def verify_hashes(
    workspace: Any,
    api_root: str,
    entries: Iterable[contract.ManifestEntry],
    *,
    workers: int,
    retry: RateLimitRetry = RATE_LIMIT_RETRY,
) -> None:
    """Download each file and compare its size and sha256 with the manifest."""

    def differs(entry: contract.ManifestEntry) -> str | None:
        data = _download(workspace, f"{api_root}/{entry.path}", retry)
        same = len(data) == entry.size and hashlib.sha256(data).hexdigest() == entry.sha256
        return None if same else entry.path

    mismatched = [path for path in _parallel(workers, differs, entries) if path is not None]
    if mismatched:
        raise ReleaseContractError(f"remote bytes differ from the manifest: {_shown(mismatched)}")


def _upload_one(workspace: Any, api_file: str, data: bytes, retry: RateLimitRetry) -> str:
    """``uploaded`` or ``reused``; refuses an existing object with other bytes.

    Every attempt is create-only, so an object a throttled attempt did land
    meets AlreadyExists on the retry and is byte-compared like any other.
    """

    def create() -> None:
        workspace.workspace.upload(
            api_file, io.BytesIO(data), format=ImportFormat.AUTO, overwrite=False
        )

    try:
        retry.call(f"upload of {api_file}", create)
        return "uploaded"
    except (AlreadyExists, ResourceAlreadyExists):
        try:
            existing = _download(workspace, api_file, retry)
        except RateLimitExhausted:
            raise
        except Exception as exc:  # noqa: BLE001 - an unreadable object is never reused
            raise ReleaseContractError(f"{api_file} exists and cannot be read back") from exc
        if existing != data:
            raise ReleaseContractError(
                f"{api_file} already exists with different bytes; nothing was overwritten"
            ) from None
        return "reused"
    except RateLimitExhausted:
        raise
    except Exception as upload_error:  # noqa: BLE001 - reconcile an ambiguous upload
        try:
            existing = _download(workspace, api_file, retry)
        except Exception:  # noqa: BLE001
            raise upload_error from None
        if existing != data:
            raise upload_error from None
        return "uploaded"


@dataclass(frozen=True)
class PublishPlan:
    """A release that passed every local check; nothing remote has been read."""

    manifest: contract.Manifest
    stage_dir: Path
    release_path: str
    workers: int


@dataclass(frozen=True)
class PublishReport:
    release_path: str
    uploaded: int
    reused: int


def prepare_publish(
    *,
    release_id: str,
    stage_dir: Path,
    manifest_path: Path,
    files_root: str,
    expected_commit: str,
    principal: str,
    target: str,
    root_leaf: str | None = None,
    workers: int = DEFAULT_WORKERS,
) -> PublishPlan:
    """Every local check, before any workspace client exists."""
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise ReleaseContractError(f"--workers must be 1..{MAX_WORKERS}")
    manifest = contract.read_manifest(manifest_path, expected_commit=expected_commit)
    if manifest.release_id != contract.check_release_id(release_id, commit=expected_commit):
        raise ReleaseContractError("--release-id is not the manifest's release id")
    expected_files = contract.files_root(principal=principal, target=target, root_leaf=root_leaf)
    if files_root != expected_files:
        raise ReleaseContractError("--files-root is not the expected bundle files root")
    release = contract.release_path(
        principal=principal, target=target, root_leaf=root_leaf,
        release_id=release_id, commit=expected_commit,
    )
    check_stage(stage_dir, manifest)
    return PublishPlan(manifest, stage_dir, release, workers)


def upload_release(
    workspace: Any, plan: PublishPlan, *, retry: RateLimitRetry = RATE_LIMIT_RETRY
) -> PublishReport:
    """Create-only uploads, then the listing and sha256 proof of the whole folder."""
    manifest, api_root = plan.manifest, contract.api_path(plan.release_path)
    folders = sorted({str(PurePosixPath(f"{api_root}/{path}").parent) for path in manifest.paths})
    for folder in folders:
        workspace.workspace.mkdirs(folder)

    def upload(entry: contract.ManifestEntry) -> str:
        data = (plan.stage_dir / entry.path).read_bytes()
        if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
            raise ReleaseContractError(f"{entry.path} changed after the stage check")
        return _upload_one(workspace, f"{api_root}/{entry.path}", data, retry)

    outcomes = _parallel(plan.workers, upload, manifest.entries)
    compare_listing(remote_files(workspace, api_root, retry=retry), manifest)
    verify_hashes(workspace, api_root, manifest.entries, workers=plan.workers, retry=retry)
    return PublishReport(plan.release_path, outcomes.count("uploaded"), outcomes.count("reused"))


def publish(
    workspace: Any, *, retry: RateLimitRetry = RATE_LIMIT_RETRY, **kwargs: Any
) -> PublishReport:
    """``prepare_publish`` then ``upload_release``; kwargs as ``prepare_publish``."""
    return upload_release(workspace, prepare_publish(**kwargs), retry=retry)


def verify_deployment(
    workspace: Any,
    *,
    app_name: str,
    manifest: contract.Manifest,
    release_id: str,
    deployment_id: str | None = None,
    workers: int = DEFAULT_WORKERS,
    retry: RateLimitRetry = RATE_LIMIT_RETRY,
) -> str:
    """The deployment runs this release's bytes; returns its snapshot path."""
    if manifest.release_id != contract.check_release_id(release_id, commit=manifest.commit):
        raise ReleaseContractError("--release-id is not the manifest's release id")
    if deployment_id:
        deployment = workspace.apps.get_deployment(app_name, deployment_id)
    else:
        deployment = getattr(workspace.apps.get(app_name), "active_deployment", None)
        if deployment is None:
            raise ReleaseContractError(f"App {app_name} has no active deployment")
    source = getattr(deployment, "source_code_path", None)
    if contract.release_id_in(source) != release_id:
        raise ReleaseContractError(f"the deployment's source is not .../app-src/{release_id}")
    try:
        snapshot = immutable_source(deployment)
    except RuntimeError as exc:
        raise ReleaseContractError(str(exc)) from exc
    api_snapshot = contract.api_path(snapshot)
    found = remote_files(workspace, api_snapshot, retry=retry)
    compare_listing(found, manifest)
    verify_hashes(workspace, api_snapshot, manifest.entries, workers=workers, retry=retry)
    return snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pub = commands.add_parser("publish", help="upload a staged release create-only and verify it")
    pub.add_argument("--release-id", required=True)
    pub.add_argument("--stage-dir", type=Path, required=True)
    pub.add_argument("--manifest", type=Path, required=True)
    pub.add_argument("--files-root", required=True)
    pub.add_argument("--expected-commit", required=True)
    pub.add_argument("--expected-principal", required=True)
    pub.add_argument("--expected-target", required=True)
    pub.add_argument("--expected-root-leaf", default=None)
    pub.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ver = commands.add_parser("verify-deployment", help="read-only: the App runs this release")
    ver.add_argument("--app-name", required=True)
    ver.add_argument("--manifest", type=Path, required=True)
    ver.add_argument("--release-id", required=True)
    ver.add_argument("--deployment-id", default=None)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    # The governed deployer binding: one cached github-oidc exchange serves
    # every upload worker instead of two extra HTTP calls per request.
    client: Callable[[], Any] = deployment_workspace_client,
    retry: RateLimitRetry = RATE_LIMIT_RETRY,
) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "publish":
            plan = prepare_publish(
                release_id=args.release_id, stage_dir=args.stage_dir,
                manifest_path=args.manifest, files_root=args.files_root,
                expected_commit=args.expected_commit, principal=args.expected_principal,
                target=args.expected_target, root_leaf=args.expected_root_leaf,
                workers=args.workers,
            )
            report = upload_release(client(), plan, retry=retry)
            print(
                f"[app-release-source] {report.release_path}: {report.uploaded} uploaded, "
                f"{report.reused} already present with equal bytes; listing and sha256 verified"
            )
            return 0
        manifest = contract.read_manifest(args.manifest, expected_commit=None)
        snapshot = verify_deployment(
            client(), app_name=args.app_name, manifest=manifest,
            release_id=args.release_id, deployment_id=args.deployment_id, retry=retry,
        )
        print(
            f"[app-release-source] {snapshot} holds exactly release {args.release_id} "
            f"({len(manifest.entries)} files, sha256 verified)"
        )
        return 0
    except ReleaseContractError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
