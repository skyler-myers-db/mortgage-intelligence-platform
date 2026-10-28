"""``app_runtime_closure stage`` refuses a ref before it writes anything.

Junk, a hidden path, a symlink or a submodule tracked at the ref, a missing
required entry, a stale deps-digest line and an archive that is not the plan
are each refused, and ``--out`` is left as it was.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from tests.fixtures.app_runtime_stage_repo import (
    FAKE_VERIFIER,
    INCLUDES,
    ROOT,
    STAMP,
    git,
    make_dist,
    make_repo,
    nothing_written,
    write_files,
)
from tools.databricks import app_runtime_closure as closure


@pytest.fixture(autouse=True)
def brotli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(closure, "brotli_verifier", lambda: FAKE_VERIFIER)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    return make_dist(tmp_path)


def test_stage_refuses_junk_tracked_at_the_ref(repo: Path, dist: Path, tmp_path: Path) -> None:
    write_files(repo, {"backend/__pycache__/m.cpython-311.pyc": "x"})
    git(repo, "add", "-f", "backend/__pycache__/m.cpython-311.pyc")
    git(repo, "commit", "-q", "-m", "junk")

    with pytest.raises(closure.StageError, match="junk tracked at HEAD"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


@pytest.mark.parametrize("path", ["backend/.databrickscfg", "jobs/.cache/state.json"])
def test_stage_refuses_a_hidden_path_tracked_in_the_closure(
    repo: Path, dist: Path, tmp_path: Path, path: str
) -> None:
    # git archive would ship it; nothing in the App source is a dotfile.
    write_files(repo, {path: "x\n"})
    git(repo, "add", "-f", path)
    git(repo, "commit", "-q", "-m", "dotfile")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match=f"hidden path tracked at HEAD.*: {path}"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert nothing_written(tmp_path, out)


def test_no_hidden_path_is_tracked_in_the_closure_at_head() -> None:
    _, found = closure.tracked_files(ROOT, "HEAD")

    assert [name for names in found.values() for name in names if closure.is_hidden(name)] == []


def test_stage_refuses_a_ref_missing_a_required_entry(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    git(repo, "rm", "-q", "genie/sample_questions.md")
    git(repo, "commit", "-q", "-m", "drop")

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
    # The bytes of requirements.txt would not change, and the build compares
    # only those bytes (confirmed 2026-10-28), so it would skip the reinstall
    # and the App would keep its old packages.
    write_files(repo, changes)
    git(repo, "commit", "-q", "-am", case)
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="stale or missing deps-digest line"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert not out.exists()


def test_the_cli_exits_2_on_a_stale_digest(
    repo: Path, dist: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_files(repo, {"requirements.txt": INCLUDES})
    git(repo, "commit", "-q", "-am", "legacy requirements.txt")
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist)]

    assert closure.main([*args, "--out", str(tmp_path / "out")]) == 2
    assert "python tools/requirements_digest.py --write" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["backend/zz_link", "frontend/public/link.json"])
def test_stage_refuses_a_tracked_symlink_before_writing_anything(
    repo: Path, dist: Path, tmp_path: Path, path: str
) -> None:
    os.symlink("../app.yaml" if path.startswith("backend") else "us-counties.json", repo / path)
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", "symlink")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match=f"{path} \\(symlink\\)"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert nothing_written(tmp_path, out)


def test_stage_refuses_a_tracked_submodule_before_writing_anything(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},jobs/vendored")
    git(repo, "commit", "-q", "-m", "gitlink")
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="jobs/vendored \\(submodule\\)"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert nothing_written(tmp_path, out)


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

    assert nothing_written(tmp_path, out)
