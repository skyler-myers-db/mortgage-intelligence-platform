"""``app_runtime_closure stage`` builds exactly the slim per-release folder.

The folder is the closure at a git ref plus a prebuilt frontend/dist, and
nothing else: no untracked or uncommitted file, no second upload into a used
folder. tests/fixtures/app_runtime_stage_repo.py builds the repo and dist;
test_app_runtime_closure_stage_dist.py holds the dist refusals and
test_app_runtime_closure_stage_git.py the git-side ones. The file budget is
pinned against the measured start window (about 10 files/s inside 10 minutes;
a dist build adds about 700).
"""

from __future__ import annotations

import gzip
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.fixtures.app_runtime_stage_repo import (
    APP_JS,
    GZ_NAME,
    ROOT,
    TRACKED,
    dist_names,
    fake_brotli,
    files_under,
    git,
    make_dist,
    make_repo,
    write_files,
)
from tools.databricks import app_runtime_closure as closure
from tools.databricks.app_runtime_dist import Decoder, DistReview


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


def test_stage_writes_exactly_the_closure_at_the_ref_plus_the_dist(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    # Untracked and uncommitted working-tree state must not leak into the folder.
    (repo / "genie" / "space_id.txt").write_text("01ef-untracked\n", encoding="utf-8")
    (repo / "backend" / "services" / "reader.py").write_text("X = 'dirty'\n", encoding="utf-8")
    out = tmp_path / "out"

    result = closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    expected = set(TRACKED) | dist_names()
    assert files_under(out) == expected
    assert result.total == len(expected)
    assert result.commit == git(repo, "rev-parse", "HEAD")
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
    write_files(out, {"earlier/upload.txt": "keep\n"})

    with pytest.raises(closure.StageError, match="not an empty directory"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert files_under(out) == {"earlier/upload.txt"}


def test_stage_leaves_out_a_br_it_cannot_check_and_says_so(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(closure, "brotli_decoder", lambda: None)
    out = tmp_path / "out"
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist)]

    assert closure.main([*args, "--out", str(out)]) == 0

    assert files_under(out) == set(TRACKED) | dist_names() - {"frontend/dist/assets/app-DnQ3_k-1.js.br"}
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


def test_stage_fills_an_existing_empty_out(repo: Path, dist: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()

    closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert files_under(out) == set(TRACKED) | dist_names()
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
    monkeypatch.setattr(closure, "MAX_STAGED_FILES", len(TRACKED) + len(dist_names()) - 1)

    with pytest.raises(closure.StageError, match="file budget"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


def test_the_cli_reports_the_count_and_exits_2_on_a_refusal(
    repo: Path, dist: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist)]

    assert closure.main([*args, "--out", str(tmp_path / "out")]) == 0
    assert f"staged {len(TRACKED) + len(dist_names())} files" in capsys.readouterr().out
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
    write_files(dist, {"index.html": "<html></html>\n"})
    public = git(ROOT, "ls-tree", "-r", "--name-only", commit, "--", "frontend/public")
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
    assert files_under(out) == tracked | {f"frontend/dist/{p}" for p in files_under(dist)}
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


@pytest.mark.parametrize(
    ("layout", "message"),
    [
        ("out_inside_dist", "--out {out} is inside --dist {dist}"),
        ("out_is_dist", "--out {out} is inside --dist {dist}"),
        ("out_inside_dist_via_symlink", "--out {out} is inside --dist {dist}"),
        ("out_inside_dist_via_dotdot", "--out {out} is inside --dist {dist}"),
        ("dist_inside_out", "--dist {dist} is inside --out {out}"),
    ],
)
def test_the_cli_refuses_an_out_and_a_dist_that_contain_each_other(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], layout: str, message: str
) -> None:
    # Copying a dist into a folder inside itself walks its own copy; refuse
    # before anything is written, with exit 2 and no traceback.
    if layout == "dist_inside_out":
        out = tmp_path / "out"
        dist = make_dist(out)
    else:
        dist = make_dist(tmp_path)
        (tmp_path / "link").symlink_to(dist)
        out = {
            "out_inside_dist": dist / "out",
            "out_is_dist": dist,
            "out_inside_dist_via_symlink": tmp_path / "link" / "out",
            "out_inside_dist_via_dotdot": tmp_path / "elsewhere" / ".." / "dist" / "out",
        }[layout]
    def outside_the_repo() -> set[str]:  # git may write .git/ in the background
        return {name for name in files_under(tmp_path) if not name.startswith("repo/")}

    before = outside_the_repo()
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist), "--out", str(out)]

    assert closure.main(args) == 2

    err = capsys.readouterr().err
    assert err == f"refused: {message.format(out=out, dist=dist)}; stage into a separate folder\n"
    assert outside_the_repo() == before
