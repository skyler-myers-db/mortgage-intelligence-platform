"""Production-source coverage for the file-size architecture gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools import check_file_sizes

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_file_size_roots_include_production_jobs_and_scripts() -> None:
    assert "jobs" in check_file_sizes.DEFAULT_INCLUDE_DIRS
    assert "scripts" in check_file_sizes.DEFAULT_INCLUDE_DIRS
    assert ".sh" in check_file_sizes.DEFAULT_SUFFIXES


def test_all_production_jobs_pass_hard_file_size_limit(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "check_file_sizes.py"),
            "--warn",
            "500",
            "--fail",
            "900",
            "--allowlist",
            str(tmp_path / "empty-allowlist.json"),
            "jobs",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_new_deploy_lifecycle_shell_libraries_pass_without_allowlist(tmp_path: Path) -> None:
    lifecycle_files = sorted((REPO_ROOT / "scripts" / "lib").glob("*.sh"))
    assert lifecycle_files

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "check_file_sizes.py"),
            "--warn",
            "500",
            "--fail",
            "900",
            "--allowlist",
            str(tmp_path / "empty-allowlist.json"),
            *(str(path.relative_to(REPO_ROOT)) for path in lifecycle_files),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
