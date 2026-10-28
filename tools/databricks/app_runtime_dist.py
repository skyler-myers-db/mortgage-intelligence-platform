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
* a ``.gz`` beside such an asset that decompresses to exactly its bytes, and a
  ``.br`` that does too (tools/precompress_assets.mjs). Without the optional
  ``brotli`` module a ``.br`` cannot be checked, so it is left out of the
  folder and reported, and the App serves the checked ``.gz`` instead;
* exactly the ``frontend/public`` files tracked at the staged ref, all of
  them, byte for byte: Vite copies ``public/`` verbatim to the dist root, so an
  untracked, edited or missing public file means the dist was not built from
  that ref;
* one ``branding/lender-mark.png`` or ``.webp`` (the lender co-branding mark;
  postbuild_artifacts.mjs refuses an unvalidated one at build time).

It refuses, anywhere in the dist: a symlink, a path segment that starts with
``.`` (``.env``, ``.vite/``, ``.DS_Store``), ``__pycache__`` and ``*.pyc``, a
``*.map`` in any letter case, ``build-manifest.json`` or ``build-modules.json``,
and a JS, CSS or HTML file carrying a ``sourceMappingURL`` comment anywhere in
it.
"""

from __future__ import annotations

import importlib
import os
import re
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

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
BROTLI_SUFFIX = ".br"
# tools/postbuild_artifacts.mjs META_FILES: build metadata that must never ship.
META_FILES = frozenset({"build-manifest.json", "build-modules.json"})
PYTHON_JUNK = frozenset({"__pycache__"})
PYTHON_JUNK_SUFFIXES = (".pyc", ".pyo")
BRANDING_PREFIX = "branding/"
BRANDING_MARK = re.compile(r"^branding/lender-mark\.(png|webp)$")
# Browsers honour the comment in any line or block comment, not only at the
# start of a line, so look for it anywhere (postbuild_artifacts.mjs anchors
# its JS check to the start of a line).
SOURCE_MAP_COMMENT = re.compile(rb"(?://|/\*)[ \t]*[#@][ \t]*sourceMappingURL=")
SOURCE_MAP_CHECKED = frozenset({".js", ".mjs", ".css", ".html"})

# A .br the review cannot decode (no brotli module): left out, not refused.
UNCHECKED = "unchecked"

Decoder = Callable[[bytes], bytes]


@dataclass(frozen=True)
class DistReview:
    """The dist-relative files to stage, what is left out, and every refusal."""

    files: tuple[str, ...]
    problems: tuple[str, ...]
    omitted: tuple[str, ...] = ()


def brotli_decoder() -> Decoder | None:
    """``brotli.decompress`` when the optional module is installed."""

    try:
        module = importlib.import_module("brotli")
    except ImportError:
        return None
    return cast(Decoder, module.decompress)


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
    folded = relpath.lower()
    if any(part.startswith(".") for part in parts):
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


def _compressed(
    dist: Path, relpath: str, present: set[str], brotli: Decoder | None
) -> str | None:
    """None for a ``.gz``/``.br`` that decodes to its hashed source."""

    source = relpath.removesuffix(GZIP_SUFFIX).removesuffix(BROTLI_SUFFIX)
    if not HASHED_ASSET.match(source):
        return "not a governed build output"
    if source not in present:
        return "compressed file without its source"
    if relpath.endswith(BROTLI_SUFFIX) and brotli is None:
        return UNCHECKED
    data, original = (dist / relpath).read_bytes(), (dist / source).read_bytes()
    if relpath.endswith(GZIP_SUFFIX):
        same = gzip_decodes_to(data, original)
    else:
        try:
            same = brotli is not None and brotli(data) == original
        except Exception:  # noqa: BLE001 -- any decoder error is a mismatch
            same = False
    return None if same else "compressed file does not decode to its source"


def _classify(
    dist: Path, relpath: str, public: Mapping[str, bytes], present: set[str],
    brotli: Decoder | None,
) -> str | None:
    """None for an allowed build output, else the reason it is refused."""

    if relpath in public:
        same = (dist / relpath).read_bytes() == public[relpath]
        return None if same else f"differs from frontend/public/{relpath} at the ref"
    if relpath in SHELLS or BRANDING_MARK.match(relpath) or HASHED_ASSET.match(relpath):
        return None
    if relpath.startswith(ASSETS_PREFIX) and relpath.endswith((GZIP_SUFFIX, BROTLI_SUFFIX)):
        return _compressed(dist, relpath, present, brotli)
    return "not a governed build output"


def _source_map_comment(dist: Path, relpath: str) -> bool:
    if PurePosixPath(relpath).suffix not in SOURCE_MAP_CHECKED:
        return False
    return SOURCE_MAP_COMMENT.search((dist / relpath).read_bytes()) is not None


def review_dist(
    dist: Path, public: Mapping[str, bytes], *, brotli: Decoder | None = None
) -> DistReview:
    """Review ``dist`` against the ``frontend/public`` blobs tracked at the ref.

    ``public`` maps each tracked public file's path relative to
    ``frontend/public`` (its path in the dist) to its bytes at the ref.
    ``brotli`` checks each ``.br``; without it every ``.br`` is ``omitted``.
    """

    files, links = _walk(dist)
    present = set(files)
    problems = [f"symlink: {relpath}" for relpath in links]
    if REQUIRED_SHELL not in present:
        problems.append(f"no {REQUIRED_SHELL}: build the frontend first")
    omitted: list[str] = []
    for relpath in files:
        reason = _refused_name(relpath) or _classify(dist, relpath, public, present, brotli)
        if reason == UNCHECKED:
            omitted.append(relpath)
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
    left_out = set(omitted)
    kept = tuple(relpath for relpath in files if relpath not in left_out)
    return DistReview(files=kept, problems=tuple(problems), omitted=tuple(omitted))
