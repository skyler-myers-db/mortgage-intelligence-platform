"""``app_runtime_closure`` check, notebook refusal, manifest and required .br check.

``check --ref`` is the git side of ``stage`` with no dist (deploy.sh
preflight runs it on the deploy commit); ``stage --manifest-out`` records the
release manifest (tools/databricks/app_release_contract.py) of exactly the
folder it staged; ``stage --require-brotli-check`` refuses before writing
anything when no ``.br`` checker is on PATH. The repo and dist come from
tests/fixtures/app_runtime_stage_repo.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.app_runtime_stage_repo import (
    FAKE_VERIFIER,
    ROOT,
    TRACKED,
    dist_names,
    files_under,
    git,
    make_dist,
    make_repo,
    nothing_written,
    write_files,
)
from tools.databricks import app_release_contract as contract
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


def _commit(repo: Path, files: dict[str, str]) -> None:
    write_files(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")


def test_check_passes_a_stageable_ref_without_a_dist(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert closure.check(repo=repo, ref="HEAD") == (git(repo, "rev-parse", "HEAD"), len(TRACKED))
    assert closure.main(["--repo", str(repo), "check", "--ref", "HEAD"]) == 0
    assert f"{len(TRACKED)} tracked files within the" in capsys.readouterr().out


def test_check_passes_the_real_head() -> None:
    commit, count = closure.check(repo=ROOT, ref="HEAD")

    assert commit == git(ROOT, "rev-parse", "HEAD")
    assert 0 < count <= closure.MAX_TRACKED_FILES


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"backend/__pycache__/x.pyc": "x"}, "junk tracked"),
        ({"jobs/.cache/x.py": "x"}, "hidden path"),
        ({"requirements.txt": "fastapi\n"}, "deps-digest"),
        ({"backend/nb.ipynb": "{}"}, "notebook"),
    ],
)
def test_check_refuses_with_exit_2_what_stage_would_refuse(
    repo: Path, change: dict[str, str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, change)

    with pytest.raises(closure.StageError, match=message):
        closure.check(repo=repo, ref="HEAD")
    assert closure.main(["--repo", str(repo), "check", "--ref", "HEAD"]) == 2
    assert capsys.readouterr().err.startswith("refused: ")


def test_check_refuses_more_tracked_files_than_the_allowance(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(closure, "MAX_TRACKED_FILES", len(TRACKED) - 1)

    with pytest.raises(closure.StageError, match="tracked closure files exceed"):
        closure.check(repo=repo, ref="HEAD")


def test_check_refuses_an_unknown_ref(repo: Path) -> None:
    assert closure.main(["--repo", str(repo), "check", "--ref", "0" * 40]) == 2


@pytest.mark.parametrize(
    "files",
    [
        {"backend/analysis.ipynb": "{}\n"},
        {"jobs/archive.DBC": "x"},
        {"lakebase/report.Rmd": "x"},
        {"jobs/notebook.py": "# Databricks notebook source\nprint(1)\n"},
        {"lakebase/notebook.sql": "-- Databricks notebook source\nselect 1;\n"},
        {"backend/notebook.py": "﻿\n  # DATABRICKS NOTEBOOK SOURCE\n"},
        {"jobs/notebook.scala": "// Databricks notebook source\n"},
    ],
)
def test_stage_refuses_a_tracked_file_auto_import_would_make_a_notebook(
    repo: Path, dist: Path, tmp_path: Path, files: dict[str, str]
) -> None:
    _commit(repo, files)
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="would make a notebook of"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)
    assert nothing_written(tmp_path, out)


def test_a_notebook_header_after_the_first_line_is_an_ordinary_file(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    _commit(repo, {"jobs/doc.py": '"""Not a notebook."""\n# Databricks notebook source\n'})

    closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


def test_require_brotli_check_refuses_before_writing_anything_without_a_checker(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(closure, "brotli_verifier", lambda: None)
    out = tmp_path / "out"
    manifest = tmp_path / "manifest.json"
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist), "--out", str(out)]

    with pytest.raises(closure.StageError, match="no .br checker"):
        closure.stage(
            repo=repo, ref="HEAD", dist=dist, out=out, manifest_out=manifest,
            require_brotli_check=True,
        )
    assert closure.main([*args, "--manifest-out", str(manifest), "--require-brotli-check"]) == 2
    assert nothing_written(tmp_path, out) and not manifest.exists()
    # Without the flag the same host stages and leaves every .br out.
    assert closure.main(args) == 0
    assert not any(name.endswith(".br") for name in files_under(out))


def test_require_brotli_check_ships_every_br_it_checked(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    result = closure.stage(
        repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out", require_brotli_check=True
    )

    assert result.unchecked_brotli == () and len(result.verified_brotli) == 1


def test_manifest_out_records_exactly_the_staged_folder(
    repo: Path, dist: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "release" / "source"
    manifest_path = tmp_path / "release" / "manifest.json"
    commit = git(repo, "rev-parse", "HEAD")

    assert closure.main([
        "--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist), "--out", str(out),
        "--manifest-out", str(manifest_path), "--require-brotli-check",
    ]) == 0

    manifest = contract.read_manifest(manifest_path, expected_commit=commit)
    assert manifest.entries == contract.manifest_entries(out)
    assert set(manifest.paths) == set(TRACKED) | dist_names()
    assert f"release {manifest.release_id}" in capsys.readouterr().out
    assert files_under(tmp_path / "release") == {"manifest.json"} | {
        f"source/{name}" for name in manifest.paths
    }


def test_the_release_id_is_deterministic_and_moves_with_one_byte_or_one_path(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    def staged(name: str, source: Path = dist) -> str:
        result = closure.stage(
            repo=repo, ref="HEAD", dist=source, out=tmp_path / name,
            manifest_out=tmp_path / f"{name}.json",
        )
        assert result.manifest is not None
        return result.manifest.release_id

    first = staged("a")
    assert staged("b") == first
    _commit(repo, {"backend/services/reader.py": "X = 2\n"})
    byte = staged("c")
    git(repo, "mv", "backend/services/reader.py", "backend/services/reader2.py")
    git(repo, "commit", "-q", "-m", "move")
    moved = staged("d")

    assert len({first, byte, moved}) == 3
    assert first[13:] != byte[13:] and byte[13:] != moved[13:]


@pytest.mark.parametrize("where", ["exists", "inside_out", "inside_dist", "is_out"])
def test_manifest_out_refuses_an_existing_file_or_a_place_that_would_ship(
    repo: Path, dist: Path, tmp_path: Path, where: str
) -> None:
    out = tmp_path / "out"
    manifest = {
        "exists": tmp_path / "manifest.json",
        "inside_out": out / "manifest.json",
        "inside_dist": dist / "manifest.json",
        "is_out": out,
    }[where]
    if where == "exists":
        manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(closure.StageError, match="--manifest-out"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out, manifest_out=manifest)
    assert nothing_written(tmp_path, out)


def test_a_staged_folder_that_is_not_the_plan_writes_no_manifest(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = closure._write

    def write_and_add(*args: object) -> closure.StageResult:
        plan = real_write(*args)  # type: ignore[arg-type]
        work = args[3]
        assert isinstance(work, Path)
        (work / "backend" / "extra.py").write_text("x\n", encoding="utf-8")
        return plan

    monkeypatch.setattr(closure, "_write", write_and_add)
    out = tmp_path / "out"
    manifest = tmp_path / "manifest.json"

    with pytest.raises(closure.StageError, match="not exactly the tracked files"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out, manifest_out=manifest)
    assert nothing_written(tmp_path, out) and not manifest.exists()
    assert [p.name for p in tmp_path.iterdir() if ".pending-" in p.name] == []


def test_a_folder_edited_after_staging_no_longer_matches_its_manifest(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    manifest_path = tmp_path / "manifest.json"
    closure.stage(repo=repo, ref="HEAD", dist=dist, out=out, manifest_out=manifest_path)
    manifest = contract.read_manifest(manifest_path, expected_commit=None)
    (out / "app.yaml").write_text("command: ['other']\n", encoding="utf-8")

    edited = contract.manifest_entries(out)

    assert edited != manifest.entries
    assert contract.release_id(manifest.commit, edited) != manifest.release_id
