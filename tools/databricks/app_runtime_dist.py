"""What a prebuilt ``frontend/dist`` may carry into a slim App source folder.

backend/main.py's ``_spa_fallback`` serves any real file under
``frontend/dist`` and its ``/assets`` route serves any file under
``dist/assets``, preferring a ``.br`` or ``.gz`` sibling over the file itself
when the client accepts that encoding (backend/services/static_assets.py), so
every file in the dist is a public URL. The governed build moves build
metadata out of the dist (tools/postbuild_artifacts.mjs), but
``app_runtime_closure stage`` accepts any directory, so it does not trust the
build. It allows only what a governed build emits:

* ``index.html`` (required) and ``index.home.html`` (frontend bootModulePlugin);
* ``assets/<name>-<hash>.<ext>``, the flat, hashed Vite output, with ``<ext>``
  one of ``ASSET_EXTENSIONS`` (``HASHED_ASSET`` below; how it follows from
  frontend/vite.config.ts is pinned by tests/unit/test_app_runtime_closure_dist.py);
* a ``.gz`` (exactly one compression suffix) beside such an asset that
  decompresses to exactly its bytes and
  whose gzip header is bare (FLG 0: no file name, comment, extra field or
  header CRC, as Node's zlib.gzip writes it), and a ``.br`` that decodes to
  exactly its bytes too (tools/precompress_assets.mjs). A ``.br`` is decoded by
  ``node`` (tools/databricks/app_runtime_brotli.py); without it a ``.br``
  cannot be checked, so it is left out of the folder and reported, and the App
  serves the checked ``.gz`` instead. One that fails the check is refused;
* exactly the ``frontend/public`` files tracked at the staged ref, all of
  them, byte for byte: Vite copies ``public/`` verbatim to the dist root, so an
  untracked, edited or missing public file means the dist was not built from
  that ref;
* one ``branding/lender-mark.png`` or ``.webp`` (the lender co-branding mark;
  postbuild_artifacts.mjs refuses an unvalidated one at build time).

It refuses, anywhere in the dist: a symlink, a path segment that starts with
``.`` (``.env``, ``.vite/``, ``.DS_Store``, an empty ``.git/objects``),
``__pycache__`` and ``*.pyc``, a
``*.map`` in any letter case, ``build-manifest.json`` or ``build-modules.json``,
and a JS, CSS or HTML file carrying a ``sourceMappingURL`` comment anywhere in
it. ``app_runtime_closure stage`` reviews a private copy (``snapshot``) and
ships that copy, so a dist that changes after the review cannot change what
ships.

The ``assets/`` rule checks name shape, compression integrity and gzip header
flags only, never asset content: a hashed ``.js`` built from another commit
passes it. Only the ``frontend/public`` files are compared with the ref, so the
dist must come from the governed build (``npm --prefix frontend run build``)
of the same ref.
"""

from __future__ import annotations

import os
import re
import shutil
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tools.databricks.app_runtime_brotli import BrotliCheckError, BrotliVerifier

SHELLS = frozenset({"index.html", "index.home.html"})
REQUIRED_SHELL = "index.html"
ASSETS_PREFIX = "assets/"
# frontend/vite.config.ts names chunks ``assets/[name]-[hash].js`` (the Genie
# answer chunk ``assets/genie-answer-[hash].js``) and leaves entryFileNames and
# assetFileNames at Vite's defaults, ``assets/[name]-[hash].js`` and
# ``assets/[name]-[hash][extname]``, whose ``[hash]`` is 8 base64url
# characters. The extensions are what the frontend imports emit today (js,
# css, json, webp, woff2) plus the other static types Vite emits as files.
HASH = r"[A-Za-z0-9_-]{8}"
ASSET_EXTENSIONS = ("js", "css", "json", "webp", "png", "svg", "woff2", "woff", "wasm")
HASHED_ASSET = re.compile(rf"^assets/[A-Za-z0-9_.-]+-{HASH}\.(?:{'|'.join(ASSET_EXTENSIONS)})$")
GZIP_SUFFIX = ".gz"
# ID1 ID2 CM=deflate FLG=0: tools/precompress_assets.mjs (Node zlib.gzip) sets no
# optional header field, so a FNAME, FCOMMENT, FEXTRA or FHCRC is not its output.
GZIP_BARE_HEADER = b"\x1f\x8b\x08\x00"
BROTLI_SUFFIX = ".br"
# tools/postbuild_artifacts.mjs META_FILES: build metadata that must never ship.
META_FILES = frozenset({"build-manifest.json", "build-modules.json"})
PYTHON_JUNK = frozenset({"__pycache__"})
PYTHON_JUNK_SUFFIXES = (".pyc", ".pyo")
BRANDING_PREFIX = "branding/"
BRANDING_MARK = re.compile(r"^branding/lender-mark\.(png|webp)$")
# Browsers honour the comment in any line or block comment, not only at the
# start of a line, so look for it anywhere (tools/postbuild_artifacts.mjs
# SOURCE_MAP_COMMENT and SOURCE_MAP_CHECKED are the same rule).
SOURCE_MAP_COMMENT = re.compile(rb"(?://|/\*)[ \t]*[#@][ \t]*sourceMappingURL=")
SOURCE_MAP_CHECKED = frozenset({".js", ".mjs", ".css", ".html"})

# A .br whose name and source passed: the brotli verifier decides it.
PENDING = "pending"


@dataclass(frozen=True)
class DistReview:
    """The dist-relative files to stage, what is left out, and every refusal.

    ``verified`` lists the ``.br`` files checked against their sources (they
    are in ``files``); ``omitted`` the ones left out unchecked.
    """

    files: tuple[str, ...]
    problems: tuple[str, ...]
    omitted: tuple[str, ...] = ()
    verified: tuple[str, ...] = ()
    brotli_method: str = ""


def gzip_decodes_to(data: bytes, source: bytes) -> bool:
    """True when ``data`` is one gzip member holding exactly ``source``.

    Decodes at most one byte more than ``source``, so a large expansion is
    refused without being inflated.
    """

    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decoder.decompress(data, len(source) + 1)
    except zlib.error:
        return False
    return decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and (
        out == source
    )


def gzip_header_is_bare(data: bytes) -> bool:
    """True when the gzip member's FLG byte is 0 (no optional header field)."""

    return data[: len(GZIP_BARE_HEADER)] == GZIP_BARE_HEADER


def _walk(dist: Path) -> tuple[list[str], list[str], list[str]]:
    """Every file, symlink and directory under ``dist`` (relative, POSIX)."""

    files: list[str] = []
    links: list[str] = []
    folders: list[str] = []
    for current, dirs, names in os.walk(dist):
        for name in sorted(dirs + names):
            path = Path(current) / name
            relpath = path.relative_to(dist).as_posix()
            if path.is_symlink():
                links.append(relpath)
            elif path.is_file():
                files.append(relpath)
            elif path.is_dir():
                folders.append(relpath)
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
    return sorted(files), sorted(links), sorted(folders)


def _is_hidden(relpath: str) -> bool:
    return any(part.startswith(".") for part in PurePosixPath(relpath).parts)


def _hidden_folders(folders: list[str], files: list[str]) -> list[str]:
    """Hidden directories that hold no file (``.git/objects``), outermost only.

    A hidden directory that holds a file is refused through that file.
    """

    empty = [d for d in folders if _is_hidden(d) and not any(f.startswith(f"{d}/") for f in files)]
    return [d for d in empty if not any(d.startswith(f"{outer}/") for outer in empty)]


def snapshot(dist: Path, target: Path) -> list[str]:
    """Copy ``dist`` into ``target`` without following a symlink.

    A symlink is copied as a symlink, so ``review_dist`` of the copy refuses it.
    Returns a problem for each entry that is neither a file, a directory nor a
    symlink (a FIFO, a socket), which is not copied.
    """

    problems: list[str] = []
    if not dist.is_dir():
        return problems
    for current, dirs, names in os.walk(dist):
        base = Path(current)
        here = target / base.relative_to(dist)
        here.mkdir(parents=True, exist_ok=True)
        for name in sorted(dirs + names):
            source, copy = base / name, here / name
            if source.is_symlink():
                copy.symlink_to(os.readlink(source))
            elif name in names and source.is_file():
                shutil.copyfile(source, copy, follow_symlinks=False)
            elif name in names:
                problems.append(f"not a regular file: {copy.relative_to(target).as_posix()}")
    return problems


def _refused_name(relpath: str) -> str | None:
    """Why this path may never ship, whatever the build produced."""

    parts = PurePosixPath(relpath).parts
    folded = relpath.lower()
    if _is_hidden(relpath):
        return "hidden path"
    if any(part.lower() in PYTHON_JUNK for part in parts) or folded.endswith(
        PYTHON_JUNK_SUFFIXES
    ):
        return "python junk"
    if folded.endswith(".map"):
        return "source map"
    if parts[-1].lower() in META_FILES:
        return "build metadata"
    return None


def _compressed(dist: Path, relpath: str, present: set[str]) -> str | None:
    """None for a ``.gz`` that decodes to its hashed source; PENDING for a ``.br``."""

    suffix = GZIP_SUFFIX if relpath.endswith(GZIP_SUFFIX) else BROTLI_SUFFIX
    source = relpath.removesuffix(suffix)
    if source.endswith((GZIP_SUFFIX, BROTLI_SUFFIX)):
        return "more than one compression suffix"
    if not HASHED_ASSET.match(source):
        return "not a governed build output"
    if source not in present:
        return "compressed file without its source"
    if relpath.endswith(BROTLI_SUFFIX):
        return PENDING
    data, original = (dist / relpath).read_bytes(), (dist / source).read_bytes()
    if not gzip_decodes_to(data, original):
        return "compressed file does not decode to its source"
    if not gzip_header_is_bare(data):
        return "gzip header carries an optional field (FLG must be 0)"
    return None


def _classify(
    dist: Path, relpath: str, public: Mapping[str, bytes], present: set[str]
) -> str | None:
    """None for an allowed build output, else the reason it is refused."""

    if relpath in public:
        same = (dist / relpath).read_bytes() == public[relpath]
        return None if same else f"differs from frontend/public/{relpath} at the ref"
    if relpath in SHELLS or BRANDING_MARK.match(relpath) or HASHED_ASSET.match(relpath):
        return None
    if relpath.startswith(ASSETS_PREFIX) and relpath.endswith((GZIP_SUFFIX, BROTLI_SUFFIX)):
        return _compressed(dist, relpath, present)
    return "not a governed build output"


def _source_map_comment(dist: Path, relpath: str) -> bool:
    if PurePosixPath(relpath).suffix not in SOURCE_MAP_CHECKED:
        return False
    return SOURCE_MAP_COMMENT.search((dist / relpath).read_bytes()) is not None


def _verify_brotli(
    dist: Path, pending: list[str], brotli: BrotliVerifier
) -> tuple[list[str], list[str]]:
    """The ``.br`` files that decode to their sources, and a problem per other one."""

    pairs = [(dist / relpath, dist / relpath.removesuffix(BROTLI_SUFFIX)) for relpath in pending]
    try:
        verdicts = brotli.check(pairs)
    except BrotliCheckError as exc:
        return [], [f"could not check the .br files with {brotli.method}: {exc}"]
    if len(verdicts) != len(pending):
        return [], [f"could not check the .br files with {brotli.method}: wrong verdict count"]
    verified = [relpath for relpath, same in zip(pending, verdicts, strict=True) if same]
    problems = [
        f"compressed file does not decode to its source: {relpath}"
        for relpath, same in zip(pending, verdicts, strict=True) if not same
    ]
    return verified, problems


def review_dist(
    dist: Path, public: Mapping[str, bytes], *, brotli: BrotliVerifier | None = None
) -> DistReview:
    """Review ``dist`` against the ``frontend/public`` blobs tracked at the ref.

    ``public`` maps each tracked public file's path relative to
    ``frontend/public`` (its path in the dist) to its bytes at the ref.
    ``brotli`` checks every ``.br`` whose name passed; one that does not decode
    to its source is a problem. Without it every ``.br`` is ``omitted``.
    """

    files, links, folders = _walk(dist)
    present = set(files)
    problems = [f"symlink: {relpath}" for relpath in links]
    problems += [f"hidden path: {folder}/" for folder in _hidden_folders(folders, files)]
    if REQUIRED_SHELL not in present:
        problems.append(f"no {REQUIRED_SHELL}: build the frontend first")
    pending: list[str] = []
    for relpath in files:
        reason = _refused_name(relpath) or _classify(dist, relpath, public, present)
        if reason == PENDING:
            pending.append(relpath)
            continue
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
    verified: list[str] = []
    omitted: list[str] = pending if brotli is None else []
    if pending and brotli is not None:
        verified, failed = _verify_brotli(dist, pending, brotli)
        problems += failed
    left_out = set(omitted)
    kept = tuple(relpath for relpath in files if relpath not in left_out)
    return DistReview(
        files=kept, problems=tuple(problems), omitted=tuple(omitted), verified=tuple(verified),
        brotli_method=brotli.method if brotli is not None and pending else "",
    )
