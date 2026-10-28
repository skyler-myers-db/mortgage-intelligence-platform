"""A staged frontend/dist holds only what a governed build emits.

backend/main.py's ``_spa_fallback`` serves any real file under frontend/dist,
and its /assets route serves a ``.br``/``.gz`` sibling in place of the asset
when the client accepts it, so anything else in a staged dist is a public URL
or a substitute response. The governed build moves build metadata out
(tools/postbuild_artifacts.mjs); ``stage`` accepts any directory, so
tools/databricks/app_runtime_dist.py reviews it against an allowlist, the
hashed names frontend/vite.config.ts emits, the bytes each compressed variant
decodes to, and the ``frontend/public`` blobs tracked at the staged ref.
"""

from __future__ import annotations

import gzip
import importlib.util
import os
import re
from pathlib import Path

import pytest

from tools.databricks import app_runtime_dist as dist_rules
from tools.databricks.app_runtime_dist import Decoder, DistReview, review_dist

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = {"us-counties.json": b"{}\n", "theme-boot.js": b"boot();\n"}
APP_JS = b"app();\n" * 200
TOPO = b'{"type":"Topology"}\n' * 100
BROTLI_MAGIC = b"fake-br:"


def fake_brotli(data: bytes) -> bytes:
    """A stand-in decoder: tests never need the optional brotli module."""

    if not data.startswith(BROTLI_MAGIC):
        raise ValueError("not a fake brotli stream")
    return data.removeprefix(BROTLI_MAGIC)


def gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


GOVERNED = {
    "index.html": b"<html></html>\n",
    "index.home.html": b"<html>home</html>\n",
    "assets/app-DnQ3_k-1.js": APP_JS,
    "assets/app-DnQ3_k-1.js.br": BROTLI_MAGIC + APP_JS,
    "assets/app-DnQ3_k-1.js.gz": gz(APP_JS),
    "assets/app-B-Tz2Uom.css": b"a{}\n",
    "assets/CA.topo-NsXBl-tH.json": TOPO,
    "assets/CA.topo-NsXBl-tH.json.gz": gz(TOPO),
    "assets/geist-latin-wght-normal-Bc2Wx1Qp.woff2": b"wOF2",
    "assets/entrada-wordmark-6u0NZJJA.webp": b"RIFF",
    **PUBLIC,
}


def _dist(tmp_path: Path, extra: dict[str, bytes] | None = None) -> Path:
    dist = tmp_path / "dist"
    for relpath, data in {**GOVERNED, **(extra or {})}.items():
        path = dist / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return dist


def _review(
    dist: Path, public: dict[str, bytes] = PUBLIC, brotli: Decoder | None = fake_brotli
) -> DistReview:
    return review_dist(dist, public, brotli=brotli)


def test_a_governed_build_has_no_problems_and_lists_every_file(tmp_path: Path) -> None:
    review = _review(_dist(tmp_path))

    assert review.problems == ()
    assert review.files == tuple(sorted(GOVERNED))
    assert review.omitted == ()


@pytest.mark.parametrize(
    ("relpath", "data", "problem"),
    [
        (".env", b"MIP_SECRET=x\n", "hidden path: .env"),
        ("assets/.env", b"x", "hidden path: assets/.env"),
        (".vite/manifest.json", b"{}", "hidden path: .vite/manifest.json"),
        (".DS_Store", b"x", "hidden path: .DS_Store"),
        ("assets/a.js.map", b"{}", "source map: assets/a.js.map"),
        ("a.css.map", b"{}", "source map: a.css.map"),
        ("assets/foo.JS.MAP", b"{}", "source map: assets/foo.JS.MAP"),
        ("assets/app-DnQ3_k-1.js.Map", b"{}", "source map: assets/app-DnQ3_k-1.js.Map"),
        ("build-manifest.json", b"{}", "build metadata: build-manifest.json"),
        ("assets/build-modules.json", b"{}", "build metadata: assets/build-modules.json"),
        ("Build-Manifest.JSON", b"{}", "build metadata: Build-Manifest.JSON"),
        ("assets/__pycache__/m.txt", b"x", "python junk: assets/__pycache__/m.txt"),
        ("assets/m.pyc", b"x", "python junk: assets/m.pyc"),
        ("assets/m.PYC", b"x", "python junk: assets/m.PYC"),
        (
            "assets/app-Bx1yZ9aQ.js",
            b"2;\n//# sourceMappingURL=app-2.js.map\n",
            "sourceMappingURL comment: assets/app-Bx1yZ9aQ.js",
        ),
        (
            "assets/app-Cx1yZ9aQ.js",
            b"3;\n//@ sourceMappingURL=app-3.js.map\n",
            "sourceMappingURL comment: assets/app-Cx1yZ9aQ.js",
        ),
        (
            "assets/app-Dx1yZ9aQ.js",
            b"4;\n  //# sourceMappingURL=app-4.js.map\n",
            "sourceMappingURL comment: assets/app-Dx1yZ9aQ.js",
        ),
        (
            "assets/app-Ex1yZ9aQ.js",
            b"5;//# sourceMappingURL=app-5.js.map",
            "sourceMappingURL comment: assets/app-Ex1yZ9aQ.js",
        ),
        (
            "assets/app-Fx1yZ9aQ.css",
            b"a{}\n/*# sourceMappingURL=app-2.css.map */\n",
            "sourceMappingURL comment: assets/app-Fx1yZ9aQ.css",
        ),
        (
            "index.home.html",
            b"<script>1;\n//# sourceMappingURL=/m.js.map\n</script>",
            "sourceMappingURL comment: index.home.html",
        ),
        ("notes.txt", b"untracked public file\n", "not a governed build output: notes.txt"),
        ("docs/guide.md", b"x\n", "not a governed build output: docs/guide.md"),
        ("branding/evil.html", b"<script>", "not a governed build output: branding/evil.html"),
        # The reviewer's pollution battery: assets/ holds only hashed Vite output.
        ("assets/creds.txt", b"token\n", "not a governed build output: assets/creds.txt"),
        ("assets/databrickscfg", b"[x]\n", "not a governed build output: assets/databrickscfg"),
        ("assets/env.local", b"X=1\n", "not a governed build output: assets/env.local"),
        (
            "assets/config.local.txt", b"x\n",
            "not a governed build output: assets/config.local.txt",
        ),
        ("assets/app-1.js", b"1;\n", "not a governed build output: assets/app-1.js"),
        (
            "assets/app-DnQ3_k-1.html", b"<p>",
            "not a governed build output: assets/app-DnQ3_k-1.html",
        ),
        (
            "assets/nested/app-DnQ3_k-1.js", b"1;\n",
            "not a governed build output: assets/nested/app-DnQ3_k-1.js",
        ),
        (
            "assets/creds.txt.gz", gz(b"token\n"),
            "not a governed build output: assets/creds.txt.gz",
        ),
        (
            "assets/orphan-DnQ3_k-1.js.br",
            BROTLI_MAGIC + b"x",
            "compressed file without its source: assets/orphan-DnQ3_k-1.js.br",
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
    review = _review(_dist(tmp_path, {relpath: data}))

    assert review.problems == (problem,)


@pytest.mark.parametrize(
    ("relpath", "data"),
    [
        ("assets/app-DnQ3_k-1.js.gz", gz(b"alert('swapped');\n")),
        ("assets/app-DnQ3_k-1.js.gz", gz(APP_JS) + gz(b"tail")),
        ("assets/app-DnQ3_k-1.js.gz", gz(APP_JS)[:-4]),
        ("assets/app-DnQ3_k-1.js.gz", b"not gzip at all"),
        ("assets/app-DnQ3_k-1.js.gz", gz(APP_JS * 64)),
        ("assets/app-DnQ3_k-1.js.br", BROTLI_MAGIC + b"alert('swapped');\n"),
        ("assets/app-DnQ3_k-1.js.br", b"not a brotli stream"),
    ],
)
def test_a_compressed_variant_that_is_not_its_source_is_refused(
    tmp_path: Path, relpath: str, data: bytes
) -> None:
    # /assets serves this file in place of its source to a client accepting
    # the encoding, so it must decode to exactly the source bytes.
    review = _review(_dist(tmp_path, {relpath: data}))

    assert review.problems == (f"compressed file does not decode to its source: {relpath}",)


def test_without_a_brotli_decoder_every_br_is_left_out_and_reported(tmp_path: Path) -> None:
    review = _review(_dist(tmp_path), brotli=None)

    assert review.problems == ()
    assert review.omitted == ("assets/app-DnQ3_k-1.js.br",)
    assert review.files == tuple(sorted(set(GOVERNED) - {"assets/app-DnQ3_k-1.js.br"}))


def test_without_a_brotli_decoder_a_br_beside_junk_is_still_refused(tmp_path: Path) -> None:
    review = _review(_dist(tmp_path, {"assets/creds.txt.br": b"x"}), brotli=None)

    assert review.problems == ("not a governed build output: assets/creds.txt.br",)


def test_the_brotli_decoder_is_the_optional_module_or_none() -> None:
    installed = importlib.util.find_spec("brotli") is not None

    assert (dist_rules.brotli_decoder() is not None) == installed


def _vite_templates() -> set[str]:
    config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    body = config.split("function chunkFileName(", 1)[1].split("\n}\n", 1)[0]
    return set(re.findall(r'return "(assets/[^"]+)";', body))


def test_the_hashed_asset_rule_follows_the_vite_naming_config() -> None:
    config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    # chunkFileName is the only naming override; entries and assets keep Vite's
    # defaults (assets/[name]-[hash].js, assets/[name]-[hash][extname]) and the
    # default 8-character base64url [hash]. A new override must update
    # HASHED_ASSET in tools/databricks/app_runtime_dist.py.
    for option in ("entryFileNames", "assetFileNames", "hashCharacters", "assetsDir"):
        assert option not in config, option
    assert "chunkFileNames: chunkFileName" in config
    templates = _vite_templates() | {"assets/[name]-[hash].js", "assets/[name]-[hash][extname]"}
    assert templates == {
        "assets/[name]-[hash].js", "assets/genie-answer-[hash].js",
        "assets/[name]-[hash][extname]",
    }
    names = {
        template.replace("[name]", "AdminAuditExplorer.export")
        .replace("[hash]", "CDfJ-uv-")
        .replace("[extname]", f".{ext}")
        for template in templates
        for ext in dist_rules.ASSET_EXTENSIONS
    }
    assert {name for name in names if not dist_rules.HASHED_ASSET.match(name)} == set()


def test_a_tracked_public_file_missing_from_the_dist_is_refused(tmp_path: Path) -> None:
    # A dist built before the public file existed is not this ref's build.
    public = {**PUBLIC, "boot-watchdog.js": b"watch();\n"}

    review = _review(_dist(tmp_path), public)

    assert review.problems == (
        "frontend/public/boot-watchdog.js tracked at the ref is missing from the dist",
    )


def test_an_unbuilt_dist_is_refused(tmp_path: Path) -> None:
    review = _review(tmp_path / "missing", {})

    assert review.problems == ("no index.html: build the frontend first",)


def test_symlinked_files_and_directories_are_refused_and_not_followed(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    outside = tmp_path / "outside"
    (outside / "inner").mkdir(parents=True)
    (outside / "inner" / "secret.txt").write_text("x", encoding="utf-8")
    os.symlink(outside / "inner" / "secret.txt", dist / "assets" / "link-DnQ3_k-1.js")
    os.symlink(outside, dist / "assets" / "linked-dir")

    review = _review(dist)

    assert review.problems == (
        "symlink: assets/link-DnQ3_k-1.js", "symlink: assets/linked-dir",
    )
    assert not any("secret" in relpath for relpath in review.files)


@pytest.mark.parametrize("ext", ["png", "webp"])
def test_one_lender_mark_is_a_governed_build_output(tmp_path: Path, ext: str) -> None:
    mark = f"branding/lender-mark.{ext}"

    review = _review(_dist(tmp_path, {mark: b"\x89PNG"}))

    assert review.problems == ()
    assert mark in review.files


def test_two_lender_marks_are_refused(tmp_path: Path) -> None:
    marks = {"branding/lender-mark.png": b"p", "branding/lender-mark.webp": b"w"}

    review = _review(_dist(tmp_path, marks))

    assert review.problems == ("branding/ must hold one lender mark, found 2 files",)
