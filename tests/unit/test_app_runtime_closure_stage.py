"""``app_runtime_closure stage`` builds exactly the slim per-release folder.

The folder is the closure at a git ref plus a prebuilt frontend/dist, and
nothing else: no untracked or uncommitted file, no junk, no second upload
into a used folder. The file budget is pinned against the measured start
window (about 10 files/s inside 10 minutes; a dist build adds about 700).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools import requirements_digest as digest
from tools.databricks import app_runtime_closure as closure

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
DIST = {"index.html": "<html></html>\n", "assets/app-1.js": "1;\n", "assets/app-1.js.br": "br"}


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
    return path


def test_stage_writes_exactly_the_closure_at_the_ref_plus_the_dist(
    repo: Path, dist: Path, tmp_path: Path
) -> None:
    # Untracked and uncommitted working-tree state must not leak into the folder.
    (repo / "genie" / "space_id.txt").write_text("01ef-untracked\n", encoding="utf-8")
    (repo / "backend" / "services" / "reader.py").write_text("X = 'dirty'\n", encoding="utf-8")
    out = tmp_path / "out"

    result = closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    expected = set(TRACKED) | {f"frontend/dist/{name}" for name in DIST}
    assert _files(out) == expected
    assert result.total == len(expected)
    assert result.commit == _git(repo, "rev-parse", "HEAD")
    assert (out / "backend/services/reader.py").read_text(encoding="utf-8") == "X = 1\n"
    assert (out / "frontend/dist/assets/app-1.js.br").read_text(encoding="utf-8") == "br"
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


@pytest.mark.parametrize("junk", [".DS_Store", "assets/__pycache__/m.txt", "assets/m.pyc"])
def test_stage_refuses_junk_in_the_dist(
    repo: Path, dist: Path, tmp_path: Path, junk: str
) -> None:
    _write(dist, {junk: "x"})
    out = tmp_path / "out"

    with pytest.raises(closure.StageError, match="junk in --dist"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=out)

    assert not out.exists()


def test_stage_refuses_junk_tracked_at_the_ref(repo: Path, dist: Path, tmp_path: Path) -> None:
    _write(repo, {"backend/__pycache__/m.cpython-311.pyc": "x"})
    _git(repo, "add", "-f", "backend/__pycache__/m.cpython-311.pyc")
    _git(repo, "commit", "-q", "-m", "junk")

    with pytest.raises(closure.StageError, match="junk tracked at HEAD"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


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
    with pytest.raises(closure.StageError, match="symlink in --dist"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "b")


def test_stage_refuses_a_folder_over_the_file_budget(
    repo: Path, dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(closure, "MAX_STAGED_FILES", len(TRACKED) + len(DIST) - 1)

    with pytest.raises(closure.StageError, match="file budget"):
        closure.stage(repo=repo, ref="HEAD", dist=dist, out=tmp_path / "out")


def test_the_cli_reports_the_count_and_exits_2_on_a_refusal(
    repo: Path, dist: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--repo", str(repo), "stage", "--ref", "HEAD", "--dist", str(dist)]

    assert closure.main([*args, "--out", str(tmp_path / "out")]) == 0
    assert f"staged {len(TRACKED) + len(DIST)} files" in capsys.readouterr().out
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


def test_stage_of_head_carries_every_tracked_closure_file(dist: Path, tmp_path: Path) -> None:
    commit, found = closure.tracked_files(ROOT, "HEAD")
    out = tmp_path / "out"

    result = closure.stage(repo=ROOT, ref=commit, dist=dist, out=out)

    tracked = {name for names in found.values() for name in names}
    assert _files(out) == tracked | {f"frontend/dist/{name}" for name in DIST}
    assert set(result.tracked) == tracked


def test_the_tracked_closure_stays_under_its_file_budget() -> None:
    # About 10 files/s inside a 600 s start window; 1,400 staged files is about
    # 140 s of download, under a quarter of the window, and a Vite build takes
    # about 700 of them (694 on ab6235e0).
    assert closure.MAX_STAGED_FILES / closure.FILES_PER_SECOND <= closure.START_WINDOW_S / 4
    assert closure.MAX_TRACKED_FILES == closure.MAX_STAGED_FILES - closure.DIST_FILE_ALLOWANCE
    _, found = closure.tracked_files(ROOT, "HEAD")

    tracked = sum(len(names) for names in found.values())

    assert 0 < tracked <= closure.MAX_TRACKED_FILES, (
        f"{tracked} tracked closure files exceed {closure.MAX_TRACKED_FILES}: trim the "
        "closure or re-measure the start window before raising the budget"
    )
