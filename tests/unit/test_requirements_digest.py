"""requirements.txt carries the digest of uv.lock and requirements.in.

The Databricks Apps build skipped the reinstall on 2026-10-28 for a deploy
whose uv.lock changed but whose requirements.txt did not: it compares
requirements.txt with the previous deployment's copy and never follows the
``-c uv.lock`` / ``-r requirements.in`` includes (the 32cc4384 deploy confirmed
that a digest-only change makes it reinstall). The generated first line
(tools/requirements_digest.py) makes every lock or input change a
requirements.txt change. These tests are the CI gate that keeps the stamp
current, and they prove the stamp leaves every requirements.txt reader intact.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from tools import python_deps_check
from tools import requirements_digest as digest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "requirements_digest.py"
FILES = ("requirements.txt", "uv.lock", "requirements.in")


def _run(*args: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    extra = ["--root", str(root)] if root is not None else []
    return subprocess.run(
        [sys.executable, str(TOOL), *args, *extra],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


def _copy_tree(tmp_path: Path) -> Path:
    for name in FILES:
        shutil.copyfile(ROOT / name, tmp_path / name)
    return tmp_path


def test_the_committed_tree_passes_check() -> None:
    result = _run("--check")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "matches uv.lock and requirements.in" in result.stdout


def test_the_digest_is_the_first_line_and_the_includes_follow_unchanged() -> None:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert digest.LINE_PATTERN.fullmatch(lines[0]), lines[0]
    assert lines[0] == digest.digest_line(ROOT)
    assert lines[1:] == ["-c uv.lock", "-r requirements.in"]


@pytest.mark.parametrize("name", ["uv.lock", "requirements.in"])
def test_a_changed_included_file_fails_check_with_the_exact_remedy(
    tmp_path: Path, name: str
) -> None:
    root = _copy_tree(tmp_path)
    assert _run("--check", root=root).returncode == 0
    path = root / name
    path.write_text(path.read_text(encoding="utf-8").replace("==", "== ", 1), encoding="utf-8")

    result = _run("--check", root=root)

    assert result.returncode == 1
    assert "deps-digest is stale" in result.stderr
    assert "  fix: python tools/requirements_digest.py --write" in result.stderr


def test_a_file_without_the_digest_line_fails_check(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    (root / "requirements.txt").write_text("-c uv.lock\n-r requirements.in\n", encoding="utf-8")

    result = _run("--check", root=root)

    assert result.returncode == 1
    assert "has no deps-digest first line" in result.stderr
    assert digest.REMEDY in result.stderr


def test_write_changes_only_the_digest_line_and_is_idempotent(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    before = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
    lock = root / "uv.lock"
    lock.write_text(lock.read_text(encoding="utf-8") + "# perturbed\n", encoding="utf-8")

    first = _run("--write", root=root)
    after = (root / "requirements.txt").read_bytes()
    second = _run("--write", root=root)

    assert first.returncode == 0 and "updated" in first.stdout
    assert second.returncode == 0 and "already current" in second.stdout
    assert (root / "requirements.txt").read_bytes() == after
    lines = after.decode("utf-8").splitlines()
    assert lines[0] != before[0] and digest.LINE_PATTERN.fullmatch(lines[0])
    assert lines[1:] == before[1:]
    assert _run("--check", root=root).returncode == 0


def test_write_stamps_a_legacy_two_line_file_and_drops_stray_digest_lines(
    tmp_path: Path,
) -> None:
    root = _copy_tree(tmp_path)
    stray = "# deps-digest: uv.lock sha256=old requirements.in sha256=old\n"
    (root / "requirements.txt").write_text(
        f"-c uv.lock\n{stray}-r requirements.in\n", encoding="utf-8"
    )

    assert _run("--write", root=root).returncode == 0

    text = (root / "requirements.txt").read_text(encoding="utf-8")
    assert text == f"{digest.digest_line(root)}\n-c uv.lock\n-r requirements.in\n"


def test_a_crlf_checkout_computes_and_checks_the_lf_digest(tmp_path: Path) -> None:
    # A core.autocrlf checkout must neither fail --check nor --write a digest
    # that is wrong for every LF checkout.
    root = _copy_tree(tmp_path)
    for name in FILES:
        path = root / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    assert digest.digest_line(root) == digest.digest_line(ROOT)
    assert _run("--check", root=root).returncode == 0


def test_gitattributes_checks_the_three_files_out_with_lf() -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-attr", "eol", "--", *FILES],
        capture_output=True, text=True, check=True,
    )

    assert result.stdout.splitlines() == [f"{name}: eol: lf" for name in FILES]


def test_the_deploy_preflight_pin_check_still_reads_the_stamped_file() -> None:
    # tools/python_deps_check.load_pins rejects any requirements.txt line that
    # is not -c/-r; the stamp must reach it as a comment.
    pins = python_deps_check.load_pins(ROOT)

    _, lock = python_deps_check.parse_lock((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert pins.lock == lock and lock
    assert any(req.name == "fastapi" for req in pins.requirements)


@pytest.mark.skipif(importlib.util.find_spec("pip") is None, reason="no pip in this interpreter")
def test_pip_parses_the_stamped_file(tmp_path: Path) -> None:
    # The Apps build and every CI job install with `pip install -r
    # requirements.txt`. A pin that is already satisfied resolves offline.
    pinned = f"pytest=={version('pytest')}"
    (tmp_path / "uv.lock").write_text(f"{pinned}\n", encoding="utf-8")
    (tmp_path / "requirements.in").write_text(f"{pinned}\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        "-c uv.lock\n-r requirements.in\n", encoding="utf-8"
    )
    assert _run("--write", root=tmp_path).returncode == 0

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--dry-run", "--no-index",
         "-r", str(tmp_path / "requirements.txt")],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Requirement already satisfied: pytest" in result.stdout
