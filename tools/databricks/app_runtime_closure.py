#!/usr/bin/env python3
"""The Databricks App's runtime source closure, and a slim per-release stager.

This is the single source of truth for the repo paths the running App needs:
the upload set (the App source the platform builds and starts) plus the files
the backend reads outside ``backend/``. tests/unit/test_app_runtime_closure_reads.py
checks it against two static scans of backend/ and the first-party modules it
imports: a flow scan that counts anything it cannot follow as a read of the
whole subtree and reports the roots it is known not to follow (such as
``backend.__file__``, a frame or ``sys.argv``) as unhandled, and a sweep of
the string literals that name a repo path (a ``/`` path, a dotted first-party
module, or a name in a path call or ``/`` join). It fails when either finds a
read not covered here, or an entry here is no longer read.

The two nets are a tripwire for accidental reads, not a proof: their residual
list is KNOWN, NOT COMPLETE. Known to escape both: a callee the flow scan
cannot name; a bare top-level name such as ``"docs"`` handed to a library
call or held in a container or a named tuple a loop walks; a cwd path built
with ``str.format``, ``%`` or ``str.join``; a starred argument; an environment
variable with no default; ``getattr``, ``exec``, ``eval`` or a computed module
name; a module or frame file reached through a helper the scan does not list
(``traceback.extract_tb``, ``findCaller``, ``pkgutil.get_loader``) or a
``find_spec`` result held anywhere but a plain name; the cwd reached through
``os.getcwdb()`` or an argument-less ``os.listdir()``/``os.scandir()``; a
``nonlocal`` bound two scopes up; and a folder name unpacked with ``**{...}``,
computed (``"DOCS".lower()``) or in bytes (tests/fixtures/app_runtime_reads.py names them,
tests/unit/test_app_runtime_read_residuals.py pins them, and no backend read
uses them today). A new App read outside ``backend/`` must use the shapes the
scans follow, a module-level ``Path(__file__)``-rooted constant plus literal
segments (``ROOT / "sql" / "x.sql"``), and must be added to READS below.

Why a slim folder: the App compute re-downloads every source file, at about
10 files/s, inside its 10-minute start window. The bundle files root only
grows (``workspace import-dir`` never deletes) and reached 6,018 files on
2026-10-28, when a deployment from it was recorded FAILED. A per-release folder
holding only this closure is about 1,200 files (about 970 when the .br files
are left out because no ``node`` is on PATH to check them).

  python -m tools.databricks.app_runtime_closure list [--files [--ref REF]]
  python -m tools.databricks.app_runtime_closure stage --ref REF --dist DIR --out DIR

``stage`` builds the folder from ``git archive REF`` (tracked files only, so
untracked optional entries are skipped and reported) plus the given prebuilt
``frontend/dist``, which must hold only governed build output
(tools/databricks/app_runtime_dist.py: Vite's hashed asset names, a ``.gz``
or ``.br`` only when it decodes to its source, no dotfiles, source maps or
build metadata, and exactly the ``frontend/public`` files tracked at REF). A
``.br`` is checked with ``node`` (tools/databricks/app_runtime_brotli.py; never
the in-process ``brotli`` module, which has no output cap); one that fails is
refused, and without ``node`` every ``.br`` is left out and reported. Before
it writes anything, it refuses junk files, a hidden path (a segment starting
with ``.``), a symlink or submodule tracked at
REF, a missing required entry, an ``--out`` inside ``--dist`` or around it
(compared by file identity, so a symlink, ``..``, another letter case or a
firmlink cannot hide it), a requirements.txt whose deps-digest line does
not match uv.lock and requirements.in at REF (the Apps build can then skip
the reinstall: observed 2026-10-28, its byte comparison inferred and not yet
confirmed) and a non-empty ``--out``. It then copies the dist into a
temporary sibling of ``--out``, reviews that copy (the copy is what ships),
refuses a folder over its file budgets, adds the tracked files and renames
the folder into place, so any refusal or failure leaves ``--out`` as it was.
It never calls Databricks.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from tools import requirements_digest
from tools.databricks.app_runtime_brotli import brotli_verifier
from tools.databricks.app_runtime_dist import DistReview, review_dist, snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_PATH = "frontend/dist"
PUBLIC_PATH = "frontend/public"
# Measured 2026-10-28: about 10 files/s inside a 600 s start window, and a
# Vite build emits about 700 dist files (694 on ab6235e0). 1,300 staged files
# is about 130 s of download, leaving the rest of the window to the pip
# install and boot, and stays under the ~1,400-file tree the source-snapshot
# lister is recorded to have listed in time (docs/deployment.md, "Source
# lister budget"). The tracked share is capped on its own so closure growth
# is caught even when a small dist would hide it inside the total.
FILES_PER_SECOND = 10
START_WINDOW_S = 600
MAX_STAGED_FILES = 1_300
DIST_FILE_ALLOWANCE = 700
MAX_TRACKED_FILES = MAX_STAGED_FILES - DIST_FILE_ALLOWANCE
# git ls-tree modes that git archive would write as something other than a file.
REFUSED_MODES = {"120000": "symlink", "160000": "submodule"}
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
    ClosureEntry(
        "requirements.txt", "upload", True,
        "Databricks Apps build: pip install -r; services/dependency_lock_check.py at boot",
    ),
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
        "services/lifecycle_sync.py, from the source root (rendered at deploy, never in git)",
    ),
    ClosureEntry(
        "sql/transformations/gold_funnel_snapshot_daily.sql", "read", True,
        "services/lifecycle_sync.py, fallback from the source root",
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
    unchecked_brotli: tuple[str, ...] = ()
    verified_brotli: tuple[str, ...] = ()
    brotli_method: str = ""

    @property
    def total(self) -> int:
        return len(self.tracked) + len(self.dist)


def is_junk(relpath: str) -> bool:
    parts = PurePosixPath(relpath).parts
    return any(part in JUNK_NAMES for part in parts) or relpath.endswith(JUNK_SUFFIXES)


def is_hidden(relpath: str) -> bool:
    """A path segment starting with ``.`` (``backend/.env.local``, ``jobs/.cache/``)."""

    return any(part.startswith(".") for part in PurePosixPath(relpath).parts)


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=False
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise StageError(f"git {args[0]} failed: {detail}")
    return result.stdout


def owning_entry(path: str, entries: tuple[ClosureEntry, ...]) -> ClosureEntry | None:
    for entry in entries:
        if path == entry.path or path.startswith(f"{entry.path}/"):
            return entry
    return None


TRACKED_ENTRIES: tuple[ClosureEntry, ...] = tuple(e for e in CLOSURE if e.path != DIST_PATH)


def _commit(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def _modes(repo: Path, commit: str, pathspecs: Sequence[str]) -> dict[str, str]:
    """Every path git tracks under ``pathspecs`` at ``commit``, with its mode."""

    listing = _git(repo, "ls-tree", "-r", "-z", commit, "--", *pathspecs)
    modes: dict[str, str] = {}
    for record in filter(None, listing.decode("utf-8").split("\0")):
        meta, name = record.split("\t", 1)
        modes[name] = meta.split(" ", 1)[0]
    return modes


def _by_entry(names: Sequence[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {entry.path: [] for entry in TRACKED_ENTRIES}
    for name in names:
        owner = owning_entry(name, TRACKED_ENTRIES)
        if owner is not None:
            found[owner.path].append(name)
    return found


def tracked_files(repo: Path, ref: str) -> tuple[str, dict[str, list[str]]]:
    """The commit ``ref`` names and the closure files it tracks, per git entry."""

    commit = _commit(repo, ref)
    names = _modes(repo, commit, [entry.path for entry in TRACKED_ENTRIES])
    return commit, _by_entry(sorted(names))


def _blobs(repo: Path, commit: str, names: Sequence[str]) -> dict[str, bytes]:
    """The contents of ``names`` at ``commit``, from one ``git cat-file --batch``."""

    request = "".join(f"{commit}:{name}\n" for name in names).encode("utf-8")
    raw = _git(repo, "cat-file", "--batch", stdin=request)
    blobs: dict[str, bytes] = {}
    offset = 0
    for name in names:
        end = raw.index(b"\n", offset)
        header = raw[offset:end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise StageError(f"{name} at {commit[:8]} is not a regular file")
        start = end + 1
        blobs[name] = raw[start : start + int(header[2])]
        offset = start + int(header[2]) + 1
    return blobs


def _check_digest(blobs: Mapping[str, bytes], ref: str) -> None:
    """Refuse a ref whose requirements.txt digest line is stale or missing."""

    text = blobs[requirements_digest.REQUIREMENTS_TXT].decode("utf-8", "replace")
    first = text.splitlines()[0] if text else ""
    if first != requirements_digest.digest_line_for(blobs):
        raise StageError(
            f"requirements.txt at {ref} has a stale or missing deps-digest line, so the "
            f"Apps build can skip the dependency reinstall; run {requirements_digest.REMEDY} "
            "and commit"
        )


@dataclass(frozen=True)
class _Tracked:
    """The git side of a stage: everything decided before any file is written."""

    commit: str
    files: tuple[str, ...]
    skipped: tuple[str, ...]
    pathspecs: tuple[str, ...]
    public: tuple[str, ...]  # frontend/public files tracked at the commit


def _copy_dist(repo: Path, tracked: _Tracked, dist: Path, copy: Path) -> DistReview:
    """Copy ``dist`` to ``copy``, review the copy, and drop what is left out.

    Returns the review: the copy's files to stage, the ``.br`` files checked
    and those left out unchecked. Refuses unless the copy holds only governed
    build output for the commit. The copy, not ``dist``, is what ships.
    """

    problems = snapshot(dist, copy)
    blobs = _blobs(repo, tracked.commit, tracked.public)
    public = {name.removeprefix(f"{PUBLIC_PATH}/"): blobs[name] for name in tracked.public}
    review = review_dist(copy, public, brotli=brotli_verifier())
    problems += review.problems
    if problems:
        shown = "; ".join(problems[:5])
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        raise StageError(f"--dist {dist} is not a governed build output: {shown}{more}")
    for relpath in review.omitted:
        (copy / relpath).unlink()
    for relpath in review.files:
        (copy / relpath).chmod(0o644)
    return review


def _check_budgets(plan: StageResult) -> None:
    if len(plan.tracked) > MAX_TRACKED_FILES:
        raise StageError(
            f"{len(plan.tracked)} tracked closure files exceed the {MAX_TRACKED_FILES}-file "
            "allowance: trim the closure or re-measure the start window"
        )
    if plan.total > MAX_STAGED_FILES:
        raise StageError(f"{plan.total} files exceed the {MAX_STAGED_FILES}-file budget")


def _plan(repo: Path, ref: str) -> _Tracked:
    commit = _commit(repo, ref)
    modes = _modes(repo, commit, [*(entry.path for entry in TRACKED_ENTRIES), PUBLIC_PATH])
    refused = sorted(
        f"{name} ({REFUSED_MODES[mode]})" for name, mode in modes.items() if mode in REFUSED_MODES
    )
    if refused:
        raise StageError(
            f"refusing what git archive would not write as a file at {ref}: "
            f"{', '.join(refused[:5])}"
        )
    found = _by_entry(sorted(modes))
    public = sorted(name for name in modes if name.startswith(f"{PUBLIC_PATH}/"))
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
    hidden = [name for name in tracked if is_hidden(name)]
    if hidden:
        raise StageError(
            f"hidden path tracked at {ref} (a dotfile never belongs in the App source): "
            f"{', '.join(hidden[:5])}"
        )
    digest_inputs = (requirements_digest.REQUIREMENTS_TXT, *requirements_digest.DIGESTED_FILES)
    _check_digest(_blobs(repo, commit, digest_inputs), ref)
    pathspecs = tuple(path for path, names in found.items() if names)
    return _Tracked(commit, tuple(tracked), tuple(skipped), pathspecs, tuple(public))


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


def _write(repo: Path, tracked: _Tracked, dist: Path, work: Path) -> StageResult:
    review = _copy_dist(repo, tracked, dist, work / DIST_PATH)
    plan = StageResult(
        tracked.commit, tracked.files, review.files, tracked.skipped, review.omitted,
        review.verified, review.brotli_method,
    )
    _check_budgets(plan)
    archive = _git(repo, "archive", "--format=tar", plan.commit, "--", *tracked.pathspecs)
    _extract(archive, work, set(plan.tracked))
    return plan


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_dev, status.st_ino


def _contains(outer: Path, inner: Path) -> bool:
    """``outer`` is ``inner`` or one of its existing ancestors, by file identity.

    ``resolve()`` folds symlinks and ``..`` but not another spelling of the
    same folder, such as ``DIST`` for ``dist`` on a case-insensitive volume or
    a macOS firmlink, so the device and inode numbers decide.
    """

    target = _identity(outer)
    if target is None:
        return False
    resolved = inner.resolve()
    return any(_identity(candidate) == target for candidate in (resolved, *resolved.parents))


def _check_apart(dist: Path, out: Path) -> None:
    """Refuse an ``--out`` inside ``--dist`` (the copy would copy itself) or around it."""

    if _contains(dist, out):
        raise StageError(f"--out {out} is inside --dist {dist}; stage into a separate folder")
    if _contains(out, dist):
        raise StageError(f"--dist {dist} is inside --out {out}; stage into a separate folder")


def stage(*, repo: Path, ref: str, dist: Path, out: Path) -> StageResult:
    """Write the closure at ``ref`` plus ``dist`` into the empty ``out``.

    ``out`` and ``dist`` must not contain each other. The git side is checked
    before anything is written. The dist is copied into a temporary sibling of
    ``out`` and that copy is reviewed and shipped, so the review and the
    shipped bytes cannot diverge; any refusal removes the temporary folder and
    leaves ``out`` as it was.
    """

    _check_apart(dist, out)
    tracked = _plan(repo, ref)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise StageError(f"--out {out} exists and is not an empty directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{out.name}.staging-", dir=out.parent))
    try:
        plan = _write(repo, tracked, dist, work)
        # POSIX rename replaces an empty directory and fails on a non-empty one.
        work.rename(out)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
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
    if result.verified_brotli:
        print(
            f"checked {len(result.verified_brotli)} .br files against their sources with "
            f"{result.brotli_method}: all shipped"
        )
    if result.unchecked_brotli:
        print(
            f"left out {len(result.unchecked_brotli)} .br files: without node on PATH they "
            "cannot be checked against their source, so the App serves their .gz"
        )
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
