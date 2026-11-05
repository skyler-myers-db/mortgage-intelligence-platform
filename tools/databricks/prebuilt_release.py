"""A prebuilt release: the build job's output, re-hashed against its sha256 manifest (M1-PR7).

deploy-dev.yml's ``build`` job holds no ID token. It runs ``npm ci``, the vite
build, ``tools.databricks.app_runtime_closure stage`` and a wheelhouse
download from requirements-deploy.hashes.txt, then ``manifest`` writes
``SHA256SUMS.json`` over every file it produced and prints the manifest's own
sha256, which the job hands the deploy job as an output. The ``deploy`` job
(id-token: write) has no Node and no npm. It runs ``verify`` before pip
installs a single wheel, and ``scripts/deploy.sh --prebuilt-release <dir>``
runs ``verify`` again before any Databricks call and ``install`` in step 1,
immediately before it relies on the release: each re-hashes every file and
refuses on any difference.

Layout, every path relative to the release root:

  SHA256SUMS.json             the manifest (never listed in itself)
  dist/**                     frontend/dist as the vite build left it
  app-release/source/**       app_runtime_closure stage --out
  app-release/manifest.json   its --manifest-out
  wheelhouse/*.whl            pip download --require-hashes of requirements-deploy.hashes.txt

The manifest names the schema, the commit (40 hex), the sha256 of the
requirements-deploy.hashes.txt the wheelhouse came from, and each file's
sha256 and size. ``verify`` and ``install`` refuse, naming a path or a field
and never file contents: a manifest whose own sha256 is not the expected one,
another schema, commit or requirements digest, a missing, extra, resized or
changed file, a symlink or any non-regular file, a path that is not a plain
relative POSIX path, and a layout part that is absent or empty. ``install``
then copies ``dist`` and ``app-release`` into place (each destination is
replaced whole) and re-hashes the copies against the same manifest.

Stdlib only, so the deploy job can verify the wheelhouse before installing
anything. Exit 0 on success, 2 on a refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

SCHEMA = "mip.prebuilt-release.v1"
MANIFEST = "SHA256SUMS.json"
PARTS = ("dist", "app-release", "wheelhouse")
INSTALLED_PARTS = ("dist", "app-release")
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_CHUNK = 1 << 20


class Refused(Exception):
    """A value-free refusal: it names a path or a field, never file contents."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plain_relative(rel: str) -> bool:
    pure = PurePosixPath(rel)
    return bool(rel) and not pure.is_absolute() and str(pure) == rel and ".." not in pure.parts


def walk(root: Path, parts: Sequence[str] = PARTS) -> dict[str, dict[str, int | str]]:
    """Every regular file under ``parts`` of ``root``, hashed; refuses any other entry."""

    files: dict[str, dict[str, int | str]] = {}
    for part in parts:
        base = root / part
        if base.is_symlink() or not base.is_dir():
            raise Refused(f"{part}: the release holds no {part} folder")
        for current, directories, names in os.walk(base, followlinks=False):
            for name in sorted(directories):
                if (Path(current) / name).is_symlink():
                    raise Refused(f"{(Path(current) / name).relative_to(root).as_posix()}: a symlink")
            for name in sorted(names):
                path = Path(current) / name
                rel = path.relative_to(root).as_posix()
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise Refused(f"{rel}: not a regular file")
                files[rel] = {"sha256": file_sha256(path), "size": path.stat().st_size}
        if not any(rel.startswith(f"{part}/") for rel in files):
            raise Refused(f"{part}: the release's {part} folder is empty")
    return dict(sorted(files.items()))


def manifest_bytes(git_sha: str, requirements_sha256: str, files: Mapping[str, Mapping[str, int | str]]) -> bytes:
    body = {"schema": SCHEMA, "git_sha": git_sha, "requirements_sha256": requirements_sha256, "files": files}
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("ascii")


def write_manifest(root: Path, *, git_sha: str, requirements: Path) -> str:
    """Write ``SHA256SUMS.json`` and return its sha256."""

    if _SHA.fullmatch(git_sha) is None:
        raise Refused("--git-sha: a 40-hex commit")
    if (root / MANIFEST).exists():
        raise Refused(f"{MANIFEST}: already written; a manifest is made once")
    data = manifest_bytes(git_sha, file_sha256(requirements), walk(root))
    (root / MANIFEST).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _load(root: Path, expected_manifest_sha256: str) -> dict[str, object]:
    if _DIGEST.fullmatch(expected_manifest_sha256) is None:
        raise Refused("the expected manifest sha256 is not 64 lowercase hex characters")
    path = root / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise Refused(f"{MANIFEST}: absent")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_manifest_sha256:
        raise Refused(f"{MANIFEST}: its sha256 is not the build job's")
    try:
        body = json.loads(data)
    except ValueError:
        raise Refused(f"{MANIFEST}: not JSON") from None
    if not isinstance(body, dict) or body.get("schema") != SCHEMA or not isinstance(body.get("files"), dict):
        raise Refused(f"{MANIFEST}: not a {SCHEMA} manifest")
    return body


def _compare(want: Mapping[str, object], have: Mapping[str, Mapping[str, int | str]], label: str) -> None:
    for rel in want:
        if not _plain_relative(rel):
            raise Refused(f"{MANIFEST}: a listed path is not a plain relative path")
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    changed = sorted(rel for rel in set(want) & set(have) if want[rel] != have[rel])
    for kind, paths in (("missing", missing), ("not in the manifest", extra), ("changed", changed)):
        if paths:
            raise Refused(f"{label}: {len(paths)} files {kind}, first {paths[0]}")


def verify(root: Path, *, git_sha: str, requirements: Path, expected_manifest_sha256: str) -> dict[str, object]:
    """Re-hash every file under ``root`` against its manifest; return the manifest."""

    body = _load(root, expected_manifest_sha256)
    if body.get("git_sha") != git_sha:
        raise Refused(f"{MANIFEST}: built from another commit than this checkout's")
    if body.get("requirements_sha256") != file_sha256(requirements):
        raise Refused(f"{MANIFEST}: its wheelhouse came from another requirements-deploy.hashes.txt")
    listed = body["files"]
    assert isinstance(listed, dict)
    _compare(listed, walk(root), "the prebuilt release")
    return body


def install(
    root: Path,
    *,
    git_sha: str,
    requirements: Path,
    expected_manifest_sha256: str,
    dist_out: Path,
    stage_out: Path,
) -> int:
    """Verify, copy dist and app-release into place, re-hash the copies; return the file count."""

    body = verify(root, git_sha=git_sha, requirements=requirements, expected_manifest_sha256=expected_manifest_sha256)
    listed = body["files"]
    assert isinstance(listed, dict)
    placed: dict[str, dict[str, int | str]] = {}
    for part, destination in (("dist", dist_out), ("app-release", stage_out)):
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise Refused(f"{part}: its destination is not a plain folder")
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root / part, destination, symlinks=True)
        for rel, entry in walk(destination.parent, (destination.name,)).items():
            placed[f"{part}/{PurePosixPath(rel).relative_to(destination.name)}"] = entry
    want = {rel: entry for rel, entry in listed.items() if rel.split("/", 1)[0] in INSTALLED_PARTS}
    _compare(want, placed, "the installed copy")
    return len(placed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.databricks.prebuilt_release", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("manifest", "verify", "install"):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True, type=Path)
        command.add_argument("--git-sha", required=True)
        command.add_argument("--requirements", required=True, type=Path)
        if name != "manifest":
            command.add_argument("--expect-manifest-sha256", required=True)
        if name == "install":
            command.add_argument("--dist-out", required=True, type=Path)
            command.add_argument("--stage-out", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "manifest":
            print(write_manifest(args.root, git_sha=args.git_sha, requirements=args.requirements))
            return 0
        common = {
            "git_sha": args.git_sha,
            "requirements": args.requirements,
            "expected_manifest_sha256": args.expect_manifest_sha256.strip(),
        }
        if args.command == "verify":
            body = verify(args.root, **common)
            files = body["files"]
            assert isinstance(files, dict)
            print(f"[prebuilt-release] verified {len(files)} files against {MANIFEST} (read-only)")
            return 0
        count = install(args.root, dist_out=args.dist_out, stage_out=args.stage_out, **common)
        print(f"[prebuilt-release] installed {count} files (dist and the staged App closure), re-hashed")
        return 0
    except Refused as refusal:
        print(f"[prebuilt-release] refused: {refusal}", file=sys.stderr)
        return 2
    except OSError as exc:  # the class only: a message names a runner path
        print(f"[prebuilt-release] refused: the release could not be read ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
