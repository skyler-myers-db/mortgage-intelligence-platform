"""The SPA fallback's shell choice (2026-09-21 UI audit, ``bundle-02``).

``backend/services/spa_shell.py`` decides which built HTML a client route
gets: the Home variant (with Home's modulepreloads) for exactly ``/``, plain
``index.html`` for every deep link. The served layer on a real build is
pinned by ``tests/unit/test_frontend_build_artifacts.py``; these are the
rules on a synthetic dist, so they run in every backend job.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.services.spa_shell import (
    HOME_SHELL,
    INDEX_SHELL,
    SHELL_CACHE_CONTROL,
    SHELL_NAMES,
    is_shell_file,
    spa_shell_path,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    (tmp_path / INDEX_SHELL).write_text("<!doctype html><title>index</title>", encoding="utf-8")
    (tmp_path / HOME_SHELL).write_text("<!doctype html><title>home</title>", encoding="utf-8")
    return tmp_path


def test_exactly_root_serves_the_home_variant(dist: Path) -> None:
    assert spa_shell_path(dist, "") == dist / HOME_SHELL


@pytest.mark.parametrize(
    "full_path",
    ["lead-queue", "borrower-360/B-0OXOBYLW8MNCK", "analytics", "home", "segment-intelligence/"],
)
def test_every_deep_link_serves_the_plain_shell(dist: Path, full_path: str) -> None:
    assert spa_shell_path(dist, full_path) == dist / INDEX_SHELL


def test_root_falls_back_to_the_plain_shell_when_the_variant_is_absent(dist: Path) -> None:
    (dist / HOME_SHELL).unlink()

    assert spa_shell_path(dist, "") == dist / INDEX_SHELL


@pytest.mark.parametrize("name", [INDEX_SHELL, HOME_SHELL])
def test_a_shell_requested_by_name_is_a_shell_request(dist: Path, name: str) -> None:
    assert is_shell_file(dist / name)
    assert spa_shell_path(dist, name) == dist / INDEX_SHELL


def test_other_dist_files_are_not_shells(dist: Path) -> None:
    assert not is_shell_file(dist / "favicon.png")
    assert not is_shell_file(dist / "theme-boot.js")
    assert {INDEX_SHELL, HOME_SHELL} == SHELL_NAMES


def test_shells_are_never_cached() -> None:
    assert SHELL_CACHE_CONTROL == "no-cache, no-store, must-revalidate"


def test_the_spa_fallback_serves_through_these_rules() -> None:
    """``backend/main.py``'s catch-all uses the membership test and the chooser."""
    tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
    fallback = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_spa_fallback"
    )
    source = ast.unparse(fallback)

    assert "is_shell_file(candidate)" in source
    assert "spa_shell_path(_FRONTEND_DIST, full_path)" in source
    assert "SHELL_CACHE_CONTROL" in source
    assert "'index.html'" not in source, "the shell names live in backend/services/spa_shell.py"
