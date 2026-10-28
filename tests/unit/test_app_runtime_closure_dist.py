"""A staged frontend/dist holds only what a governed build emits.

backend/main.py's ``_spa_fallback`` serves any real file under frontend/dist,
so anything else in a staged dist is a public URL. The governed build moves
build metadata out (tools/postbuild_artifacts.mjs); ``stage`` accepts any
directory, so tools/databricks/app_runtime_dist.py reviews it against an
allowlist and against the ``frontend/public`` blobs tracked at the staged ref.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.databricks.app_runtime_dist import review_dist

PUBLIC = {"us-counties.json": b"{}\n", "theme-boot.js": b"boot();\n"}
GOVERNED = {
    "index.html": b"<html></html>\n",
    "index.home.html": b"<html>home</html>\n",
    "assets/app-1.js": b"1;\n",
    "assets/app-1.js.br": b"br",
    "assets/app-1.js.gz": b"gz",
    "assets/app-1.css": b"a{}\n",
    "assets/ca.topo-1.json": b"{}",
    **PUBLIC,
}


def _dist(tmp_path: Path, extra: dict[str, bytes] | None = None) -> Path:
    dist = tmp_path / "dist"
    for relpath, data in {**GOVERNED, **(extra or {})}.items():
        path = dist / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return dist


def test_a_governed_build_has_no_problems_and_lists_every_file(tmp_path: Path) -> None:
    review = review_dist(_dist(tmp_path), PUBLIC)

    assert review.problems == ()
    assert review.files == tuple(sorted(GOVERNED))


@pytest.mark.parametrize(
    ("relpath", "data", "problem"),
    [
        (".env", b"MIP_SECRET=x\n", "hidden path: .env"),
        ("assets/.env", b"x", "hidden path: assets/.env"),
        (".vite/manifest.json", b"{}", "hidden path: .vite/manifest.json"),
        (".DS_Store", b"x", "hidden path: .DS_Store"),
        ("assets/a.js.map", b"{}", "source map: assets/a.js.map"),
        ("a.css.map", b"{}", "source map: a.css.map"),
        ("build-manifest.json", b"{}", "build metadata: build-manifest.json"),
        ("assets/build-modules.json", b"{}", "build metadata: assets/build-modules.json"),
        ("assets/__pycache__/m.txt", b"x", "python junk: assets/__pycache__/m.txt"),
        ("assets/m.pyc", b"x", "python junk: assets/m.pyc"),
        (
            "assets/app-2.js",
            b"2;\n//# sourceMappingURL=app-2.js.map\n",
            "sourceMappingURL comment: assets/app-2.js",
        ),
        (
            "assets/app-3.js",
            b"3;\n//@ sourceMappingURL=app-3.js.map\n",
            "sourceMappingURL comment: assets/app-3.js",
        ),
        (
            "assets/app-2.css",
            b"a{}\n/*# sourceMappingURL=app-2.css.map */\n",
            "sourceMappingURL comment: assets/app-2.css",
        ),
        ("notes.txt", b"untracked public file\n", "not a governed build output: notes.txt"),
        ("docs/guide.md", b"x\n", "not a governed build output: docs/guide.md"),
        ("branding/evil.html", b"<script>", "not a governed build output: branding/evil.html"),
        (
            "assets/orphan.js.br",
            b"br",
            "compressed file without its source: assets/orphan.js.br",
        ),
        (
            "us-counties.json",
            b'{"edited": true}\n',
            "differs from frontend/public/us-counties.json at the ref: us-counties.json",
        ),
    ],
)
def test_each_file_a_governed_build_would_not_ship_is_refused(
    tmp_path: Path, relpath: str, data: bytes, problem: str
) -> None:
    review = review_dist(_dist(tmp_path, {relpath: data}), PUBLIC)

    assert review.problems == (problem,)


def test_a_tracked_public_file_missing_from_the_dist_is_refused(tmp_path: Path) -> None:
    # A dist built before the public file existed is not this ref's build.
    public = {**PUBLIC, "boot-watchdog.js": b"watch();\n"}

    review = review_dist(_dist(tmp_path), public)

    assert review.problems == (
        "frontend/public/boot-watchdog.js tracked at the ref is missing from the dist",
    )


def test_an_unbuilt_dist_is_refused(tmp_path: Path) -> None:
    review = review_dist(tmp_path / "missing", {})

    assert review.problems == ("no index.html: build the frontend first",)


def test_symlinked_files_and_directories_are_refused_and_not_followed(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    outside = tmp_path / "outside"
    (outside / "inner").mkdir(parents=True)
    (outside / "inner" / "secret.txt").write_text("x", encoding="utf-8")
    os.symlink(outside / "inner" / "secret.txt", dist / "assets" / "link.js")
    os.symlink(outside, dist / "assets" / "linked-dir")

    review = review_dist(dist, PUBLIC)

    assert review.problems == ("symlink: assets/link.js", "symlink: assets/linked-dir")
    assert not any("secret" in relpath for relpath in review.files)


@pytest.mark.parametrize("ext", ["png", "webp"])
def test_one_lender_mark_is_a_governed_build_output(tmp_path: Path, ext: str) -> None:
    mark = f"branding/lender-mark.{ext}"

    review = review_dist(_dist(tmp_path, {mark: b"\x89PNG"}), PUBLIC)

    assert review.problems == ()
    assert mark in review.files


def test_two_lender_marks_are_refused(tmp_path: Path) -> None:
    marks = {"branding/lender-mark.png": b"p", "branding/lender-mark.webp": b"w"}

    review = review_dist(_dist(tmp_path, marks), PUBLIC)

    assert review.problems == ("branding/ must hold one lender mark, found 2 files",)
