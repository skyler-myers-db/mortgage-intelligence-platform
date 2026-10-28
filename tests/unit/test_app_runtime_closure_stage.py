"""``app_runtime_closure stage`` builds exactly the slim per-release folder.

The folder is the closure at a git ref plus a prebuilt frontend/dist, and
nothing else: no untracked or uncommitted file, no junk, no file a governed
build would not ship (tests/unit/test_app_runtime_closure_dist.py holds the
dist rules), no second upload into a used folder. The file budget is pinned against the measured start
window (about 10 files/s inside 10 minutes; a dist build adds about 700).
"""

from __future__ import annotations

import gzip
import io
import os
import subprocess
import tarfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from tools import requirements_digest as digest
from tools.databricks import app_runtime_closure as closure
from tools.databricks.app_runtime_dist import Decoder, DistReview

ROOT = Path(__file__).resolve().parents[2]
INPUTS = {"requirements.in": "fastapi\n", "uv.lock": "fastapi==1.0\n"}
INCLUDES = "-c uv.lock\n-r requirements.in\n"
STAMP = digest.digest_line_for({name: text.encode() for name, text in INPUTS.items()})
TRACKED = {
    "app.yaml": "command: ['python', '-m', 'backend.runtime']\n",
    "requirements.txt": f"{STAMP}\n{INCLUDES}",
    **INPUTS,
    "backend/__init__.py": "",
    "backend/services/reader.py": "X = 1\n",
    "jobs/sync_lifecycle_state.py": "Y = 2\n",
    "lakebase/schema.sql": "select 1;\n",
    "frontend/public/us-counties.json": "{}\n",
    "genie/sample_questions.md": "- a question\n",
    "sql/metric_views/view.sql": "-- view\n",
    "sql/uc_functions/fn_a.sql": "-- fn\n",
    "tests/eval/golden_agent_cases.jsonl": "{}\n",
    "tests/eval/scorers.py": "",
    "sql/transformations/gold_funnel_snapshot_daily.sql": "-- funnel\n",
}
OUTSIDE = {
    "docs/guide.md": "x\n",
    "frontend/src/main.tsx": "x\n",
    "frontend/public/other.json": "{}\n",
    "genie/other.md": "x\n",
    "sql/transformations/other.sql": "x\n",
    "tests/unit/test_x.py": "x\n",
    "tools/x.py": "x\n",
}
APP_JS = "app();\n" * 200
BROTLI_MAGIC = "fake-br:"
# A governed build: the shell, hashed assets with compressed variants that
# decode to them, and frontend/public copied verbatim.
DIST = {
    "index.html": "<html></html>\n",
    "assets/app-DnQ3_k-1.js": APP_JS,
    "assets/app-DnQ3_k-1.js.br": BROTLI_MAGIC + APP_JS,
    "us-counties.json": TRACKED["frontend/public/us-counties.json"],
    "other.json": OUTSIDE["frontend/public/other.json"],
}
GZ_NAME = "assets/app-DnQ3_k-1.js.gz"


def _fake_brotli(data: bytes) -> bytes:
    if not data.startswith(BROTLI_MAGIC.encode()):
        raise ValueError("not a fake brotli stream")
    return data.removeprefix(BROTLI_MAGIC.encode())


@pytest.fixture(autouse=True)
def brotli(monkeypatch: pytest.MonkeyPatch) -> None:
    # The optional brotli module is not a dependency; a stand-in decoder makes
    # every test independent of whether it happens to be installed.
    monkeypatch.setattr(closure, "brotli_decoder", lambda: _fake_brotli)


def _git(repo: Path, *args: str) -> str:
    identity = ["-c", "user.email=closure@example.invalid", "-c", "user.name=closure-test",
                "-c", "commit.gpgsign=false"]
    result = subprocess.run(["git", "-C", str(repo), *identity, *args],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _write(base: Path, files: dict[str, str]) -> None:
    for relpath, text in files.items():
        path = base / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _files(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _write(path, {**TRACKED, **OUTSIDE})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "fixture")
    return path


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    path = tmp_path / "dist"
    _write(path, DIST)
    (path / GZ_NAME).write_bytes(gzip.compress(APP_JS.encode(), mtime=0))
    return path


def _dist_names() -> set[str]:
    return {f"frontend/dist/{name}" for name in (*DIST, GZ_NAME)}


def test_stage_writes_exactly_the_closure_at_the_ref_plus_the_dist(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    # Untracked and uncommitted working-tree state must not leak into the folder.
    (repo / "genie" / "space_id.txt").write_text("01ef-untracked\n", encoding="utf-8")
    (repo / "backend" / "services" / "reader.py").write_text("X = 'dirty'\n", encoding="utf-8")
    out = tmp_path / "out"

    result = closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    expected = set(TRACKED) | _dist_names()
    assert _files(out) == expected
    assert result.total == len(expected)
    assert result.commit == _git(repo, "rev-parse", "HEAD")
    assert (out / "backend/services/reader.py").read_text(encoding="utf-8") == "X = 1\n"
    assert (out / "frontend/dist" / GZ_NAME).read_bytes() == (dist / GZ_NAME).read_bytes()
    assert result.skipped_optional == (
        "genie/space_id.txt",
        "sql/_rendered/transformations/gold_funnel_snapshot_daily.sql",
    )


def test_stage_refuses_a_non_empty_out_and_leaves_it_alone(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    _write(out, {"earlier/upload.txt": "keep\n"})

    with pytest.raises(closure.StageError, match="not an empty directory"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert _files(out) == {"earlier/upload.txt"}


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
    _write(dist, {name: text})
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


def test_stage_leaves_out_a_br_it_cannot_check_and_says_so(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(closure, "brotli_decoder", lambda: None)
    out = tmp_path / "out"
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist)]

    assert closure.main([*args, "--out", str(out)]) == 0

    assert _files(out) == set(TRACKED) | _dist_names() - {"frontend/dist/assets/app-DnQ3_k-1.js.br"}
    assert "left out 1 .br files" in capsys.readouterr().out


def test_stage_ships_the_dist_copy_it_reviewed_not_the_live_directory(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A writer that swaps a .gz in the live dist right after the review must
    # not change what ships: stage reviews a private copy and ships that copy.
    real_review = closure.review_dist
    reviewed: list[Path] = []

    def review_then_swap(
        path: Path, public: Mapping[str, bytes], *, brotli: Decoder | None = None
    ) -> DistReview:
        review = real_review(path, public, brotli=brotli)
        reviewed.append(path)
        (dist / GZ_NAME).write_bytes(gzip.compress(b"alert('swapped');\n", mtime=0))
        return review

    monkeypatch.setattr(closure, "review_dist", review_then_swap)
    out = tmp_path / "out"

    closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert reviewed and dist not in reviewed
    assert gzip.decompress((out / "frontend/dist" / GZ_NAME).read_bytes()) == APP_JS.encode()


def test_stage_refuses_a_dist_entry_that_is_not_a_regular_file(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    os.mkfifo(dist / "assets" / "pipe-DnQ3_k-1.js")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="not a regular file: assets/pipe-DnQ3_k-1.js"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert _nothing_written(tmp_path, out)


def test_stage_refuses_a_dist_built_from_another_commit(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    # Vite copies frontend/public verbatim, so an older build carries the
    # older public file.
    _write(repo, {"frontend/public/other.json": '{"v": 2}\n'})
    _git(repo, "commit", "-q", "-am", "public file changed")

    with pytest.raises(closure.StageError, match="differs from frontend/public/other.json"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


def test_stage_refuses_junk_tracked_at_the_ref(repo: Path, dist: Path, tmp_path: Path) -> None:
    _write(repo, {"backend/__pycache__/m.cpython-311.pyc": "x"})
    _git(repo, "add", "-f", "backend/__pycache__/m.cpython-311.pyc")
    _git(repo, "commit", "-q", "-m", "junk")

    with pytest.raises(closure.StageError, match="junk tracked at HEAD"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


@pytest.mark.parametrize("path", ["backend/.databrickscfg", "jobs/.cache/state.json"])
def test_stage_refuses_a_hidden_path_tracked_in_the_closure(
    repo: Path, dist: Path, tmp_path: Path, path: str
) -> None:
    # git archive would ship it; nothing in the App source is a dotfile.
    _write(repo, {path: "x\n"})
    _git(repo, "add", "-f", path)
    _git(repo, "commit", "-q", "-m", "dotfile")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match=f"hidden path tracked at HEAD.*: {path}"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert _nothing_written(tmp_path, out)


def test_no_hidden_path_is_tracked_in_the_closure_at_head() -> None:
    _, found = closure.tracked_files(ROOT, "HEAD")

    assert [name for names in found.values() for name in names if closure.is_hidden(name)] == []


def test_stage_refuses_a_ref_missing_a_required_entry(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    _git(repo, "rm", "-q", "genie/sample_questions.md")
    _git(repo, "commit", "-q", "-m", "drop")

    with pytest.raises(closure.StageError, match="absent at HEAD: genie/sample_questions.md"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


@pytest.mark.parametrize(
    ("changes", "case"),
    [
        ({"uv.lock": "fastapi==1.1\n"}, "lock bumped without a restamp"),
        ({"requirements.txt": INCLUDES}, "no digest line"),
        ({"requirements.txt": f"{STAMP[: -64]}{'0' * 64}\n{INCLUDES}"}, "zeroed digest"),
    ],
)
def test_stage_refuses_a_ref_whose_requirements_digest_is_stale(
    repo: Path, dist: Path, tmp_path: Path, changes: dict[str, str], case: str
) -> None:
    # The bytes of requirements.txt would not change, so the Apps build would
    # skip the reinstall and the App would keep its old packages.
    _write(repo, changes)
    _git(repo, "commit", "-q", "-am", case)
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="stale or missing deps-digest line"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert not out.exists()


def test_the_cli_exits_2_on_a_stale_digest(
    repo: Path, dist: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, {"requirements.txt": INCLUDES})
    _git(repo, "commit", "-q", "-am", "legacy requirements.txt")
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist)]

    assert closure.main([*args, "--out", str(tmp_path / "out")]) == 2
    assert "python tools/requirements_digest.py --write" in capsys.readouterr().err


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

    assert _nothing_written(tmp_path, out)


def _nothing_written(tmp_path: Path, out: Path) -> bool:
    staging = [p.name for p in tmp_path.iterdir() if p.name.startswith(f".{out.name}.staging-")]
    return not out.exists() and staging == []


@pytest.mark.parametrize("path", ["backend/zz_link", "frontend/public/link.json"])
def test_stage_refuses_a_tracked_symlink_before_writing_anything(
    repo: Path, dist: Path, tmp_path: Path, path: str
) -> None:
    os.symlink("../app.yaml" if path.startswith("backend") else "us-counties.json", repo / path)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", "symlink")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match=f"{path} \\(symlink\\)"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert _nothing_written(tmp_path, out)


def test_stage_refuses_a_tracked_submodule_before_writing_anything(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},jobs/vendored")
    _git(repo, "commit", "-q", "-m", "gitlink")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="jobs/vendored \\(submodule\\)"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert _nothing_written(tmp_path, out)


def _doctored_archive(monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    real_git = closure._git

    def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
        raw = real_git(repo, *args, stdin=stdin)
        if args[0] != "archive":
            return raw
        buffer = io.BytesIO()
        with (
            tarfile.open(fileobj=io.BytesIO(raw)) as source,
            tarfile.open(fileobj=buffer, mode="w") as doctored,
        ):
            for member in source.getmembers():
                if change == "missing" and member.name == "lakebase/schema.sql":
                    continue
                doctored.addfile(member, source.extractfile(member) if member.isfile() else None)
            if change == "extra":
                extra = tarfile.TarInfo("backend/unplanned.py")
                extra.size = 2
                doctored.addfile(extra, io.BytesIO(b"x\n"))
        return buffer.getvalue()

    monkeypatch.setattr(closure, "_git", git)


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_stage_refuses_an_archive_that_is_not_the_plan_and_leaves_out_alone(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _doctored_archive(monkeypatch, change)
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="did not produce the planned file set"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert _nothing_written(tmp_path, out)


def test_stage_fills_an_existing_empty_out(repo: Path, dist: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()

    closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert _files(out) == set(TRACKED) | _dist_names()
    assert [p.name for p in tmp_path.iterdir() if ".staging-" in p.name] == []


def test_stage_refuses_more_tracked_files_than_the_closure_allowance(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A small dist must not hide closure growth inside the total budget.
    monkeypatch.setattr(closure, "MAX_TRACKED_FILES", len(TRACKED) - 1)

    with pytest.raises(closure.StageError, match="tracked closure files exceed"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


def test_stage_refuses_a_folder_over_the_file_budget(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(closure, "MAX_STAGED_FILES", len(TRACKED) + len(_dist_names()) - 1)

    with pytest.raises(closure.StageError, match="file budget"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


def test_the_cli_reports_the_count_and_exits_2_on_a_refusal(
    repo: Path, dist: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist)]

    assert closure.main([*args, "--out", str(tmp_path / "out")]) == 0
    assert f"staged {len(TRACKED) + len(_dist_names())} files" in capsys.readouterr().out
    assert closure.main([*args, "--out", str(tmp_path / "out")]) == 2
    assert "refused: --out" in capsys.readouterr().err


def test_list_prints_every_entry_and_the_tracked_files(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert closure.main(["list"]) == 0
    rows = capsys.readouterr().out.splitlines()
    assert [row.split("\t")[0] for row in rows] == [entry.path for entry in closure.CLOSURE]

    assert closure.main(["--repo", str(repo), "list", "--files"]) == 0
    assert set(capsys.readouterr().out.splitlines()) == set(TRACKED)


def test_exactly_the_two_never_tracked_entries_are_optional() -> None:
    # stage skips a missing optional entry and refuses a missing required one,
    # so a reader file wrongly marked optional would be dropped from a slim
    # folder without a word. docs/deployment.md names exactly these two.
    optional = {entry.path for entry in closure.CLOSURE if not entry.required}

    assert optional == {
        "genie/space_id.txt",
        "sql/_rendered/transformations/gold_funnel_snapshot_daily.sql",
    }


def test_stage_of_head_carries_every_tracked_closure_file(tmp_path: Path) -> None:
    commit, found = closure.tracked_files(ROOT, "HEAD")
    dist = tmp_path / "dist"
    _write(dist, {"index.html": "<html></html>\n"})
    public = _git(ROOT, "ls-tree", "-r", "--name-only", commit, "--", "frontend/public")
    for name in public.splitlines():
        target = dist / name.removeprefix("frontend/public/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "blob", f"{commit}:{name}"],
            capture_output=True, check=True,
        ).stdout)
    out = tmp_path / "out"

    result = closure.stage(repo=ROOT, ref=commit, dist=dist, out=out)

    tracked = {name for names in found.values() for name in names}
    assert _files(out) == tracked | {f"frontend/dist/{p}" for p in _files(dist)}
    assert set(result.tracked) == tracked


def test_the_tracked_closure_stays_under_its_file_budget() -> None:
    # About 10 files/s inside a 600 s start window; 1,300 staged files is about
    # 130 s of download, under a quarter of the window, and a Vite build takes
    # about 700 of them (694 on ab6235e0).
    assert closure.MAX_STAGED_FILES / closure.FILES_PER_SECOND <= closure.START_WINDOW_S / 4
    assert closure.MAX_TRACKED_FILES == closure.MAX_STAGED_FILES - closure.DIST_FILE_ALLOWANCE
    _, found = closure.tracked_files(ROOT, "HEAD")

    tracked = sum(len(names) for names in found.values())

    assert 0 < tracked <= closure.MAX_TRACKED_FILES, (
        f"{tracked} tracked closure files exceed {closure.MAX_TRACKED_FILES}: trim the "
        "closure or re-measure the start window before raising the budget"
    )
