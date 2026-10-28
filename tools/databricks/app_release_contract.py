#!/usr/bin/env python3
"""Workspace path and manifest contract for the governed Databricks App source.

Paths, all under one bundle root
``/Workspace/Users/<principal>/.bundle/mortgage-intelligence-platform/<leaf>``
(``<leaf>`` is the target, or the isolated CI install's own leaf such as
``dev-ci``; tools/databricks/ci_install_isolation.py):

* ``files_root``: ``<bundle root>/files``, bundle sync's upload root. Jobs read
  it, and pre-release deployments may still name it.
* ``release_root``: ``<bundle root>/app-src``, a sibling of ``files/`` that
  bundle sync never touches.
* ``release_path``: ``<release root>/<release id>``, one immutable folder per
  release. It has exactly 9 path segments, none ``.`` or ``..``, and never
  contains ``/src/``, so it can never pass for a deployment's immutable
  ``/src/`` snapshot (tools/databricks/app_deployment_state.immutable_source).

A release id is ``<commit[:12]>-<digest[:12]>``, where the digest is the
sha256 of one ``<path>\\t<size>\\t<sha256>\\n`` line per file, in path order.
File modes are not part of it. The manifest (``write_manifest``) records the
commit, the id and those entries; ``read_manifest`` refuses anything but
version 1, a 40-hex commit, canonical, unique, sorted relative paths and an id
that recomputes from the entries.

  python -m tools.databricks.app_release_contract release-id --manifest M --expected-commit SHA

prints the id of a manifest staged for that commit and exits 2 on any refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

BUNDLE_NAME = "mortgage-intelligence-platform"
RELEASE_DIR = "app-src"
RELEASE_ID_RE = re.compile(r"[0-9a-f]{12}-[0-9a-f]{12}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MANIFEST_VERSION = 1
RELEASE_PATH_SEGMENTS = 9  # "/", Workspace, Users, <principal>, .bundle, <bundle>, <leaf>, app-src, <id>
_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_MANIFEST_KEYS = frozenset({"version", "commit", "release_id", "files"})
_ENTRY_KEYS = frozenset({"path", "size", "sha256"})
_CHUNK = 1 << 20


class ReleaseContractError(RuntimeError):
    """A refusal: the input breaks the App release contract."""


def bundle_root(*, principal: str, target: str, root_leaf: str | None = None) -> str:
    """The bundle root; ``root_leaf`` (default: the target) is its last segment."""
    if (
        type(principal) is not str
        or not principal
        or principal != principal.strip()
        or principal in {".", ".."}
        or "/" in principal
        or any(character.isspace() or ord(character) < 32 for character in principal)
    ):
        raise ReleaseContractError("expected App source principal is not canonical")
    if type(target) is not str or _SEGMENT_RE.fullmatch(target) is None:
        raise ReleaseContractError("expected App source target is not canonical")
    leaf = target if root_leaf is None else root_leaf
    if type(leaf) is not str or _SEGMENT_RE.fullmatch(leaf) is None:
        raise ReleaseContractError("expected App source root leaf is not canonical")
    return f"/Workspace/Users/{principal}/.bundle/{BUNDLE_NAME}/{leaf}"


def files_root(*, principal: str, target: str, root_leaf: str | None = None) -> str:
    """The bundle files root; ``root_leaf`` (default: the target) is its last root segment.

    The live dev install keeps ``.../dev/files``; the isolated CI staging
    install (W5e, tools/databricks/ci_install_isolation.py) passes its own
    leaf, e.g. ``dev-ci``.
    """
    return f"{bundle_root(principal=principal, target=target, root_leaf=root_leaf)}/files"


def release_root(*, principal: str, target: str, root_leaf: str | None = None) -> str:
    """``<bundle root>/app-src``: the parent of every per-release folder."""
    return f"{bundle_root(principal=principal, target=target, root_leaf=root_leaf)}/{RELEASE_DIR}"


def check_commit(commit: object) -> str:
    if type(commit) is not str or COMMIT_RE.fullmatch(commit) is None:
        raise ReleaseContractError("release commit is not a 40-hex git commit")
    return commit


def check_release_id(release_id: object, *, commit: str | None = None) -> str:
    """A ``<12 hex>-<12 hex>`` id, carrying ``commit``'s prefix when one is given."""
    if type(release_id) is not str or RELEASE_ID_RE.fullmatch(release_id) is None:
        raise ReleaseContractError("App release id is not <12 hex>-<12 hex>")
    if commit is not None and release_id[:12] != check_commit(commit)[:12]:
        raise ReleaseContractError("App release id does not carry its commit's prefix")
    return release_id


def _check_release_shape(path: str) -> None:
    parsed = PurePosixPath(path)
    parts = parsed.parts
    if (
        str(parsed) != path
        or len(parts) != RELEASE_PATH_SEGMENTS
        or parts[:3] != ("/", "Workspace", "Users")
        or parts[4:6] != (".bundle", BUNDLE_NAME)
        or parts[7] != RELEASE_DIR
        or RELEASE_ID_RE.fullmatch(parts[8]) is None
        or any(part in {".", ".."} for part in parts)
        or "/src/" in path
    ):
        raise ReleaseContractError("App release path is not a governed per-release folder")


def release_path(
    *,
    principal: str,
    target: str,
    release_id: str,
    root_leaf: str | None = None,
    commit: str | None = None,
) -> str:
    """``<release root>/<release id>``, checked segment by segment."""
    check_release_id(release_id, commit=commit)
    root = release_root(principal=principal, target=target, root_leaf=root_leaf)
    path = f"{root}/{release_id}"
    _check_release_shape(path)
    return path


def release_id_of(
    path: object, *, principal: str, target: str, root_leaf: str | None = None
) -> str | None:
    """The id when ``path`` is exactly a release folder of this bundle root, else None."""
    if type(path) is not str:
        return None
    prefix = f"{release_root(principal=principal, target=target, root_leaf=root_leaf)}/"
    candidate = path.removeprefix(prefix)
    if candidate == path or RELEASE_ID_RE.fullmatch(candidate) is None:
        return None
    try:
        expected = release_path(
            principal=principal, target=target, root_leaf=root_leaf, release_id=candidate
        )
    except ReleaseContractError:
        return None
    return candidate if expected == path else None


def api_path(path: str) -> str:
    """The Workspace API path of a ``/Workspace/...`` path."""
    if type(path) is not str or not path.startswith("/Workspace/"):
        raise ReleaseContractError("workspace path does not start with /Workspace/")
    return path.removeprefix("/Workspace")


@dataclass(frozen=True, order=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    commit: str
    release_id: str
    entries: tuple[ManifestEntry, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)

    def by_path(self) -> dict[str, ManifestEntry]:
        return {entry.path: entry for entry in self.entries}


def check_relpath(path: object) -> str:
    """A canonical relative POSIX file path with no hidden, empty or dot segment."""
    if (
        type(path) is not str
        or not path
        or "\\" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or path.startswith("/")
        or PurePosixPath(path).as_posix() != path
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.split("/"))
    ):
        raise ReleaseContractError(f"manifest path is not canonical: {path!r}")
    return path


def _check_entries(entries: Iterable[ManifestEntry]) -> tuple[ManifestEntry, ...]:
    checked = tuple(entries)
    if not checked:
        raise ReleaseContractError("an App release manifest lists no files")
    for entry in checked:
        check_relpath(entry.path)
        if type(entry.size) is not int or entry.size < 0:
            raise ReleaseContractError(f"manifest size is invalid for {entry.path}")
        if type(entry.sha256) is not str or SHA256_RE.fullmatch(entry.sha256) is None:
            raise ReleaseContractError(f"manifest sha256 is invalid for {entry.path}")
    paths = [entry.path for entry in checked]
    if any(earlier >= later for earlier, later in zip(paths, paths[1:], strict=False)):
        raise ReleaseContractError("manifest paths are not unique and sorted")
    return checked


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def manifest_entries(folder: Path) -> tuple[ManifestEntry, ...]:
    """Every regular file under ``folder``, sorted; any symlink or special file refuses."""
    if folder.is_symlink() or not folder.is_dir():
        raise ReleaseContractError(f"{folder} is not a real directory")
    entries: list[ManifestEntry] = []
    for current, dirnames, filenames in os.walk(folder, followlinks=False):
        base = Path(current)
        for name in dirnames:
            if (base / name).is_symlink():
                raise ReleaseContractError(f"symlink in the release folder: {base / name}")
        for name in filenames:
            path = base / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ReleaseContractError(f"not a regular file in the release folder: {path}")
            relpath = check_relpath(path.relative_to(folder).as_posix())
            size, sha256 = _hash_file(path)
            entries.append(ManifestEntry(relpath, size, sha256))
    return _check_entries(sorted(entries))


def release_id(commit: str, entries: Iterable[ManifestEntry]) -> str:
    """``commit[:12]-sha256(lines)[:12]`` over the sorted entries."""
    checked = _check_entries(entries)
    lines = "".join(f"{entry.path}\t{entry.size}\t{entry.sha256}\n" for entry in checked)
    digest = hashlib.sha256(lines.encode("utf-8")).hexdigest()
    return f"{check_commit(commit)[:12]}-{digest[:12]}"


def write_manifest(path: Path, *, commit: str, entries: Iterable[ManifestEntry]) -> Manifest:
    checked = _check_entries(entries)
    manifest = Manifest(check_commit(commit), release_id(commit, checked), checked)
    document = {
        "version": MANIFEST_VERSION,
        "commit": manifest.commit,
        "release_id": manifest.release_id,
        "files": [
            {"path": entry.path, "size": entry.size, "sha256": entry.sha256} for entry in checked
        ],
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _entry(item: object) -> ManifestEntry:
    if not isinstance(item, dict) or set(item) != _ENTRY_KEYS:
        raise ReleaseContractError("manifest file entry is not {path, size, sha256}")
    return ManifestEntry(item["path"], item["size"], item["sha256"])


def read_manifest(path: Path, *, expected_commit: str | None) -> Manifest:
    """A version-1 manifest whose id recomputes; ``expected_commit`` must match when given."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ReleaseContractError(f"cannot read the App release manifest {path}: {exc}") from exc
    if not isinstance(document, dict) or set(document) != _MANIFEST_KEYS:
        raise ReleaseContractError("App release manifest is not {version, commit, release_id, files}")
    version = document["version"]
    if type(version) is not int or version != MANIFEST_VERSION:
        raise ReleaseContractError(f"App release manifest version is not {MANIFEST_VERSION}")
    commit = check_commit(document["commit"])
    if expected_commit is not None and commit != check_commit(expected_commit):
        raise ReleaseContractError("App release manifest was staged for another commit")
    files = document["files"]
    if not isinstance(files, list):
        raise ReleaseContractError("App release manifest files is not a list")
    entries = _check_entries(_entry(item) for item in files)
    stated = check_release_id(document["release_id"], commit=commit)
    if stated != release_id(commit, entries):
        raise ReleaseContractError("App release manifest id does not recompute from its files")
    return Manifest(commit, stated, entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    rid = commands.add_parser("release-id", help="print a staged manifest's release id")
    rid.add_argument("--manifest", type=Path, required=True)
    rid.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = read_manifest(args.manifest, expected_commit=check_commit(args.expected_commit))
    except ReleaseContractError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(manifest.release_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
