"""What a prebuilt ``frontend/dist`` may carry into a slim App source folder.

backend/main.py's ``_spa_fallback`` serves any real file under
``frontend/dist`` and its ``/assets`` route serves any file under
``dist/assets``, so every file in the dist is a public URL. The governed build
moves build metadata out of the dist (tools/postbuild_artifacts.mjs), but
``app_runtime_closure stage`` accepts any directory, so it does not trust the
build. It allows only what a governed build emits:

* ``index.html`` (required) and ``index.home.html`` (frontend bootModulePlugin);
* ``assets/**``, the hashed Vite output, where a ``.br`` or ``.gz`` file must
  sit beside the file it compresses (tools/precompress_assets.mjs);
* exactly the ``frontend/public`` files tracked at the staged ref, all of
  them, byte for byte: Vite copies ``public/`` verbatim to the dist root, so an
  untracked, edited or missing public file means the dist was not built from
  that ref;
* one ``branding/lender-mark.png`` or ``.webp`` (the lender co-branding mark;
  postbuild_artifacts.mjs refuses an unvalidated one at build time).

It refuses, anywhere in the dist: a symlink, a path segment that starts with
``.`` (``.env``, ``.vite/``, ``.DS_Store``), ``__pycache__`` and ``*.pyc``, a
``*.map``, ``build-manifest.json`` or ``build-modules.json``, and a JS or CSS
file carrying a ``sourceMappingURL`` comment.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SHELLS = frozenset({"index.html", "index.home.html"})
REQUIRED_SHELL = "index.html"
ASSETS_PREFIX = "assets/"
COMPRESSED_SUFFIXES = (".br", ".gz")
# tools/postbuild_artifacts.mjs META_FILES: build metadata that must never ship.
META_FILES = frozenset({"build-manifest.json", "build-modules.json"})
PYTHON_JUNK = frozenset({"__pycache__"})
PYTHON_JUNK_SUFFIXES = (".pyc", ".pyo")
BRANDING_PREFIX = "branding/"
BRANDING_MARK = re.compile(r"^branding/lender-mark\.(png|webp)$")
# postbuild_artifacts.mjs SOURCE_MAP_COMMENT, plus the CSS comment form.
SOURCE_MAP_COMMENTS = {
    ".js": re.compile(rb"^//[#@]\s*sourceMappingURL=", re.MULTILINE),
    ".mjs": re.compile(rb"^//[#@]\s*sourceMappingURL=", re.MULTILINE),
    ".css": re.compile(rb"/\*[#@]\s*sourceMappingURL="),
}


@dataclass(frozen=True)
class DistReview:
    """The dist-relative files to stage, and every reason not to stage them."""

    files: tuple[str, ...]
    problems: tuple[str, ...]


def _walk(dist: Path) -> tuple[list[str], list[str]]:
    """Every file under ``dist`` (relative, POSIX) and every symlink found."""

    files: list[str] = []
    links: list[str] = []
    for current, dirs, names in os.walk(dist):
        for name in sorted(dirs + names):
            path = Path(current) / name
            relpath = path.relative_to(dist).as_posix()
            if path.is_symlink():
                links.append(relpath)
            elif path.is_file():
                files.append(relpath)
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
    return sorted(files), sorted(links)


def _refused_name(relpath: str) -> str | None:
    """Why this path may never ship, whatever the build produced."""

    parts = PurePosixPath(relpath).parts
    if any(part.startswith(".") for part in parts):
        return "hidden path"
    if any(part in PYTHON_JUNK for part in parts) or relpath.endswith(PYTHON_JUNK_SUFFIXES):
        return "python junk"
    if relpath.endswith(".map"):
        return "source map"
    if parts[-1] in META_FILES:
        return "build metadata"
    return None


def _source_map_comment(dist: Path, relpath: str) -> bool:
    pattern = SOURCE_MAP_COMMENTS.get(PurePosixPath(relpath).suffix)
    return pattern is not None and pattern.search((dist / relpath).read_bytes()) is not None


def _classify(dist: Path, relpath: str, public: Mapping[str, bytes], present: set[str]) -> str | None:
    """None for an allowed build output, else the reason it is refused."""

    if relpath in public:
        same = (dist / relpath).read_bytes() == public[relpath]
        return None if same else f"differs from frontend/public/{relpath} at the ref"
    if relpath in SHELLS or BRANDING_MARK.match(relpath):
        return None
    if relpath.startswith(ASSETS_PREFIX):
        source = relpath.removesuffix(".br").removesuffix(".gz")
        if relpath.endswith(COMPRESSED_SUFFIXES) and source not in present:
            return "compressed file without its source"
        return None
    return "not a governed build output"


def review_dist(dist: Path, public: Mapping[str, bytes]) -> DistReview:
    """Review ``dist`` against the ``frontend/public`` blobs tracked at the ref.

    ``public`` maps each tracked public file's path relative to
    ``frontend/public`` (its path in the dist) to its bytes at the ref.
    """

    files, links = _walk(dist)
    present = set(files)
    problems = [f"symlink: {relpath}" for relpath in links]
    if REQUIRED_SHELL not in present:
        problems.append(f"no {REQUIRED_SHELL}: build the frontend first")
    for relpath in files:
        reason = _refused_name(relpath) or _classify(dist, relpath, public, present)
        if reason is None and _source_map_comment(dist, relpath):
            reason = "sourceMappingURL comment"
        if reason is not None:
            problems.append(f"{reason}: {relpath}")
    problems.extend(
        f"frontend/public/{name} tracked at the ref is missing from the dist"
        for name in sorted(set(public) - present)
    )
    branding = [relpath for relpath in files if relpath.startswith(BRANDING_PREFIX)]
    if len(branding) > 1:
        problems.append(f"branding/ must hold one lender mark, found {len(branding)} files")
    return DistReview(files=tuple(files), problems=tuple(problems))
