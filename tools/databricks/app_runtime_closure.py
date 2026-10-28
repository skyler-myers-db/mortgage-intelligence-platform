#!/usr/bin/env python3
"""The Databricks App's runtime source closure, and a slim per-release stager.

This is the single source of truth for the repo paths the running App needs:
the upload set (the App source the platform builds and starts) plus every file
the backend reads outside ``backend/``. tests/unit/test_app_runtime_closure_reads.py
scans backend/ and fails when a read is missing here or an entry here is no
longer read, so the list cannot rot in either direction.

Why a slim folder: the App compute re-downloads every source file, at about
10 files/s, inside its 10-minute start window. The bundle files root only
grows (``workspace import-dir`` never deletes) and reached 6,018 files on
2026-10-28, when a deployment from it was recorded FAILED. A per-release folder
holding only this closure is about 1,200 files.

  python -m tools.databricks.app_runtime_closure list [--files [--ref REF]]
  python -m tools.databricks.app_runtime_closure stage --ref REF --dist DIR --out DIR

``stage`` builds the folder from ``git archive REF`` (tracked files only, so
untracked optional entries are skipped and reported) plus the given prebuilt
``frontend/dist``. It refuses junk files, symlinks, a missing required entry,
a non-empty ``--out`` and a folder over the file budget, before writing
anything. It never calls Databricks.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_PATH = "frontend/dist"
# Measured 2026-10-28: about 10 files/s inside a 600 s start window, and a
# Vite build emits about 700 dist files (694 on ab6235e0). 1,400 staged files
# is about 140 s of download, leaving the rest of the window to the pip
# install and boot; it is also the largest tree the source-snapshot lister is
# recorded to have listed in time (docs/deployment.md, "Source lister budget").
FILES_PER_SECOND = 10
START_WINDOW_S = 600
MAX_STAGED_FILES = 1_400
DIST_FILE_ALLOWANCE = 700
MAX_TRACKED_FILES = MAX_STAGED_FILES - DIST_FILE_ALLOWANCE
JUNK_NAMES = frozenset({"__pycache__", ".DS_Store"})
JUNK_SUFFIXES = (".pyc", ".pyo")


@dataclass(frozen=True)
class ClosureEntry:
    """One repo-relative POSIX path (file or directory) in the closure."""

    path: str
    kind: Literal["upload", "read"]
    required: bool
    reader: str


UPLOAD: tuple[ClosureEntry, ...] = (
    ClosureEntry("app.yaml", "upload", True, "Databricks Apps: runs python -m backend.runtime"),
    ClosureEntry("requirements.txt", "upload", True, "Databricks Apps build: pip install -r"),
    ClosureEntry(
        "requirements.in", "upload", True,
        "requirements.txt -r include; services/dependency_lock_check.py at boot",
    ),
    ClosureEntry(
        "uv.lock", "upload", True,
        "requirements.txt -c include; services/dependency_lock_check.py at boot",
    ),
    ClosureEntry("backend", "upload", True, "the FastAPI App"),
    ClosureEntry("jobs", "upload", True, "staged upload set; lifecycle_sync imports a job module"),
    ClosureEntry("lakebase", "upload", True, "staged upload set (schema and seed SQL)"),
    ClosureEntry(DIST_PATH, "upload", True, "backend/main.py serves the prebuilt SPA (--dist)"),
)
READS: tuple[ClosureEntry, ...] = (
    ClosureEntry(
        "frontend/public/us-counties.json", "read", True,
        "services/county_names.py, schemas/marketing_selection_reviewed_places.py",
    ),
    ClosureEntry("genie/sample_questions.md", "read", True, "services/genie_answers.py"),
    ClosureEntry(
        "genie/space_id.txt", "read", False,
        "services/genie_client_registry.py (untracked fallback; the env var wins)",
    ),
    ClosureEntry("sql/metric_views", "read", True, "services/capabilities.py"),
    ClosureEntry("sql/uc_functions", "read", True, "services/capabilities.py"),
    ClosureEntry(
        "tests/eval/golden_agent_cases.jsonl", "read", True, "services/capabilities.py (exists)"
    ),
    ClosureEntry("tests/eval/scorers.py", "read", True, "services/capabilities.py (exists)"),
    ClosureEntry(
        "sql/_rendered/transformations/gold_funnel_snapshot_daily.sql", "read", False,
        "services/lifecycle_sync.py, cwd-relative (rendered at deploy, never in git)",
    ),
    ClosureEntry(
        "sql/transformations/gold_funnel_snapshot_daily.sql", "read", True,
        "services/lifecycle_sync.py, cwd-relative fallback",
    ),
)
CLOSURE: tuple[ClosureEntry, ...] = UPLOAD + READS


class StageError(RuntimeError):
    """A refusal: nothing was written."""


@dataclass(frozen=True)
class StageResult:
    commit: str
    tracked: tuple[str, ...]
    dist: tuple[str, ...]
    skipped_optional: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.tracked) + len(self.dist)


def is_junk(relpath: str) -> bool:
    parts = PurePosixPath(relpath).parts
    return any(part in JUNK_NAMES for part in parts) or relpath.endswith(JUNK_SUFFIXES)


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise StageError(f"git {args[0]} failed: {detail}")
    return result.stdout


def owning_entry(path: str, entries: tuple[ClosureEntry, ...]) -> ClosureEntry | None:
    for entry in entries:
        if path == entry.path or path.startswith(f"{entry.path}/"):
            return entry
    return None


def tracked_files(repo: Path, ref: str) -> tuple[str, dict[str, list[str]]]:
    """The commit ``ref`` names and the closure files it tracks, per git entry."""

    commit = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    entries = tuple(entry for entry in CLOSURE if entry.path != DIST_PATH)
    listing = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--",
                   *(entry.path for entry in entries))
    found: dict[str, list[str]] = {entry.path: [] for entry in entries}
    for name in filter(None, listing.decode("utf-8").split("\0")):
        owner = owning_entry(name, entries)
        if owner is not None:
            found[owner.path].append(name)
    return commit, found


def _dist_files(dist: Path) -> list[str]:
    if not (dist / "index.html").is_file():
        raise StageError(f"--dist {dist} has no index.html; build the frontend first")
    files: list[str] = []
    for current, dirs, names in os.walk(dist):
        for name in sorted(dirs + names):
            path = Path(current) / name
            relpath = path.relative_to(dist).as_posix()
            if is_junk(relpath):
                raise StageError(f"junk in --dist: {relpath}")
            if path.is_symlink():
                raise StageError(f"symlink in --dist: {relpath}")
            if path.is_file():
                files.append(relpath)
    return sorted(files)


def _plan(repo: Path, ref: str, dist: Path) -> tuple[StageResult, list[str]]:
    commit, found = tracked_files(repo, ref)
    skipped: list[str] = []
    for entry in CLOSURE:
        if entry.path == DIST_PATH or found[entry.path]:
            continue
        if entry.required:
            raise StageError(f"required closure path absent at {ref}: {entry.path}")
        skipped.append(entry.path)
    tracked = sorted(name for names in found.values() for name in names)
    junk = [name for name in tracked if is_junk(name)]
    if junk:
        raise StageError(f"junk tracked at {ref}: {', '.join(junk[:5])}")
    plan = StageResult(commit, tuple(tracked), tuple(_dist_files(dist)), tuple(skipped))
    if plan.total > MAX_STAGED_FILES:
        raise StageError(f"{plan.total} files exceed the {MAX_STAGED_FILES}-file budget")
    pathspecs = [path for path, names in found.items() if names]
    return plan, pathspecs


def _extract(archive: bytes, out: Path, expected: set[str]) -> None:
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if member.name == "pax_global_header" or member.isdir():
                continue
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or ".." in name.parts:
                raise StageError(f"refusing archive member {member.name!r}")
            source = tar.extractfile(member)
            if source is None:
                raise StageError(f"archive member {member.name!r} has no payload")
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            seen.add(name.as_posix())
    if seen != expected:
        raise StageError("git archive did not produce the planned file set")


def stage(*, repo: Path, ref: str, dist: Path, out: Path) -> StageResult:
    """Write the closure at ``ref`` plus ``dist`` into the empty ``out``."""

    plan, pathspecs = _plan(repo, ref, dist)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise StageError(f"--out {out} exists and is not an empty directory")
    out.mkdir(parents=True, exist_ok=True)
    archive = _git(repo, "archive", "--format=tar", plan.commit, "--", *pathspecs)
    _extract(archive, out, set(plan.tracked))
    for relpath in plan.dist:
        target = out / DIST_PATH / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dist / relpath, target)
        target.chmod(0o644)
    return plan


def _list(args: argparse.Namespace) -> int:
    if not args.files:
        for entry in CLOSURE:
            need = "required" if entry.required else "optional"
            print(f"{entry.path}\t{entry.kind}\t{need}\t{entry.reader}")
        return 0
    _, found = tracked_files(args.repo, args.ref)
    for name in sorted(name for names in found.values() for name in names):
        print(name)
    return 0


def _stage(args: argparse.Namespace) -> int:
    result = stage(repo=args.repo, ref=args.ref, dist=args.dist, out=args.out)
    print(
        f"staged {result.total} files into {args.out}: {len(result.tracked)} from "
        f"{args.ref} ({result.commit[:8]}) + {len(result.dist)} from {args.dist}"
    )
    if result.skipped_optional:
        print(f"skipped optional (absent at {args.ref}): {', '.join(result.skipped_optional)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="print the closure entries (or files)")
    listing.add_argument("--files", action="store_true", help="tracked files at --ref")
    listing.add_argument("--ref", default="HEAD")
    staging = commands.add_parser("stage", help="build a slim per-release source folder")
    staging.add_argument("--ref", required=True)
    staging.add_argument("--dist", type=Path, required=True, help="a built frontend/dist")
    staging.add_argument("--out", type=Path, required=True, help="a new or empty directory")
    args = parser.parse_args(argv)
    try:
        return _list(args) if args.command == "list" else _stage(args)
    except StageError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
