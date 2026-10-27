"""The session guard's .env.local fingerprint (tests/fixtures/env_local_guard.py).

Every file here is a neutral tmp_path name; no test touches a ``.env*`` path.
"""

from __future__ import annotations

import builtins
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures import env_local_guard
from tests.fixtures.env_local_guard import ABSENT, describe_change, stat_fingerprint


def _bump_mtime(path: Path) -> None:
    """Move the mtime well past the filesystem's timestamp granularity."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - 5_000_000_000))


def test_an_absent_path_fingerprints_as_absent_on_both_sides(tmp_path: Path) -> None:
    assert stat_fingerprint(tmp_path / "missing") == ABSENT == (None, None)


def test_a_write_through_a_symlink_changes_the_fingerprint(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("SYNTHETIC_ONLY=placeholder-value-for-a-guard-test\n", encoding="utf-8")
    _bump_mtime(target)
    link = tmp_path / "link"
    link.symlink_to(target)

    before = stat_fingerprint(link)
    # The writer's shell redirection: it follows the link and truncates the target.
    subprocess.run(["bash", "-c", ": > link; echo X=1 > link"], cwd=tmp_path, check=True)
    after = stat_fingerprint(link)

    assert after != before
    assert describe_change(before, after) == "rewrote or replaced"
    # The link itself is untouched: only the os.stat side sees the write.
    assert after[0] == before[0]
    assert after[1] is not None and after[1][2] == len("X=1\n")


def test_a_rewrite_of_a_regular_file_changes_the_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "plain"
    path.write_text("SYNTHETIC_ONLY=1\n", encoding="utf-8")
    _bump_mtime(path)
    before = stat_fingerprint(path)
    subprocess.run(["bash", "-c", "echo SYNTHETIC_ONLY=1 > plain"], cwd=tmp_path, check=True)
    assert describe_change(before, stat_fingerprint(path)) == "rewrote or replaced"


def test_creation_and_removal_are_named(tmp_path: Path) -> None:
    path = tmp_path / "plain"
    before = stat_fingerprint(path)
    path.write_text("SYNTHETIC_ONLY=1\n", encoding="utf-8")
    present = stat_fingerprint(path)
    assert describe_change(before, present) == "created"
    path.unlink()
    assert describe_change(present, stat_fingerprint(path)) == "removed"
    assert describe_change(present, present) is None


def test_a_dangling_symlink_is_present_on_the_lstat_side_only(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "nowhere")
    lstat_side, stat_side = stat_fingerprint(link)
    assert lstat_side is not None
    assert stat_side is None
    assert describe_change(ABSENT, (lstat_side, stat_side)) == "created"


def test_the_fingerprint_never_opens_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.write_text("SYNTHETIC_ONLY=1\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the .env.local guard opened the file")

    monkeypatch.setattr(builtins, "open", _refuse)
    monkeypatch.setattr(os, "open", _refuse)
    monkeypatch.setattr(Path, "open", _refuse)
    monkeypatch.setattr(Path, "read_text", _refuse)
    monkeypatch.setattr(Path, "read_bytes", _refuse)
    assert stat_fingerprint(link)[1] is not None


def test_a_denied_stat_propagates_instead_of_comparing_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _denied(_path: object) -> os.stat_result:
        raise PermissionError("denied")

    monkeypatch.setattr(env_local_guard, "os", SimpleNamespace(lstat=os.lstat, stat=_denied))
    with pytest.raises(PermissionError):
        stat_fingerprint(tmp_path / "anything")
