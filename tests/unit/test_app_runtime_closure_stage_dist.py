"""``app_runtime_closure stage`` refuses a dist a governed build would not ship.

Each refusal leaves ``--out`` as it was. tools/databricks/app_runtime_dist.py
holds the rules and tests/unit/test_app_runtime_closure_dist.py pins them file
by file; these tests drive them through ``stage``.
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest

from tests.fixtures.app_runtime_stage_repo import (
    BROTLI_MAGIC,
    GZ_NAME,
    fake_brotli,
    git,
    make_dist,
    make_repo,
    nothing_written,
    write_files,
)
from tools.databricks import app_runtime_closure as closure


@pytest.fixture(autouse=True)
def brotli(monkeypatch: pytest.MonkeyPatch) -> None:
    # The optional brotli module is not a dependency; a stand-in decoder makes
    # every test independent of whether it happens to be installed.
    monkeypatch.setattr(closure, "brotli_decoder", lambda: fake_brotli)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    return make_dist(tmp_path)


# Each would be a public URL: backend/main.py's _spa_fallback serves any real
# file under frontend/dist (GET /.env, GET /assets/a.js.map).
POLLUTED = {
    ".env": ("MIP_SECRET=x\n", "hidden path: .env"),
    "assets/a.js.map": ("{}", "source map: assets/a.js.map"),
    "assets/foo.JS.MAP": ("{}", "source map: assets/foo.JS.MAP"),
    "build-manifest.json": ("{}", "build metadata: build-manifest.json"),
    ".vite/manifest.json": ("{}", "hidden path: .vite/manifest.json"),
    "assets/app-Bx1yZ9aQ.js": (
        "2;\n//# sourceMappingURL=app-2.js.map\n",
        "sourceMappingURL comment: assets/app-Bx1yZ9aQ.js",
    ),
    "assets/app-Cx1yZ9aQ.js": (
        "3;\n  //# sourceMappingURL=app-3.js.map\n",
        "sourceMappingURL comment: assets/app-Cx1yZ9aQ.js",
    ),
    "notes.txt": ("an untracked frontend/public file\n", "not a governed build output: notes.txt"),
    ".DS_Store": ("x", "hidden path: .DS_Store"),
    "assets/__pycache__/m.txt": ("x", "python junk: assets/__pycache__/m.txt"),
    # Served at /assets/<name> to any App user: only hashed Vite output may sit there.
    "assets/creds.txt": ("token\n", "not a governed build output: assets/creds.txt"),
    "assets/databrickscfg": ("[x]\n", "not a governed build output: assets/databrickscfg"),
    "assets/env.local": ("X=1\n", "not a governed build output: assets/env.local"),
    # Served in place of its source to a client accepting the encoding.
    "assets/app-DnQ3_k-1.js.br": (
        BROTLI_MAGIC + "alert('swapped');\n",
        "compressed file does not decode to its source: assets/app-DnQ3_k-1.js.br",
    ),
}


@pytest.mark.parametrize("name", sorted(POLLUTED))
def test_stage_refuses_a_dist_a_governed_build_would_not_ship(
    repo: Path, dist: Path, tmp_path: Path, name: str
) -> None:
    text, problem = POLLUTED[name]
    write_files(dist, {name: text})
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="is not a governed build output") as refused:
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert problem in str(refused.value)
    assert not out.exists()


def test_stage_refuses_a_gz_that_does_not_decode_to_its_source(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    (dist / GZ_NAME).write_bytes(gzip.compress(b"alert('swapped');\n", mtime=0))
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="compressed file does not decode to its source"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert not out.exists()


def test_stage_refuses_a_dist_entry_that_is_not_a_regular_file(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    os.mkfifo(dist / "assets" / "pipe-DnQ3_k-1.js")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="not a regular file: assets/pipe-DnQ3_k-1.js"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert nothing_written(tmp_path, out)


def test_stage_refuses_a_dist_built_from_another_commit(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    # Vite copies frontend/public verbatim, so an older build carries the
    # older public file.
    write_files(repo, {"frontend/public/other.json": '{"v": 2}\n'})
    git(repo, "commit", "-q", "-am", "public file changed")

    with pytest.raises(closure.StageError, match="differs from frontend/public/other.json"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


def test_stage_refuses_an_unbuilt_dist_and_a_symlink(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    with pytest.raises(closure.StageError, match="no index.html"):
        closure.stage(repo=repo, ref="HEAD", dist=tmp_path / "missing", out=tmp_path / "a")
    os.symlink(dist / "index.html", dist / "assets" / "link.html")
    with pytest.raises(closure.StageError, match="symlink: assets/link.html"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "b")


def test_stage_refuses_a_symlinked_directory_in_the_dist(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    # The copy keeps the link as a link, so the review of the copy sees it.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret-DnQ3_k-1.js").write_text("x\n", encoding="utf-8")
    os.symlink(outside, dist / "assets" / "linked")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="symlink: assets/linked"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert nothing_written(tmp_path, out)
