"""Stat-only fingerprint of the operator's ``.env.local`` for the session guard in tests/conftest.py.

The file holds the operator's secrets, so nothing here ever opens it. The
fingerprint carries both sides of a possible symlink: ``os.lstat`` describes
the path itself and ``os.stat`` follows it. A write through a symlinked
``.env.local`` (``> .env.local`` follows the link and truncates the real
target) leaves the lstat side unchanged; only the stat side sees it.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

#: (st_dev, st_ino, st_size, st_mtime_ns, st_mode) of one side, or None when absent.
StatSide = tuple[int, int, int, int, int] | None
#: (the path itself via os.lstat, what it resolves to via os.stat).
EnvLocalFingerprint = tuple[StatSide, StatSide]

ABSENT: EnvLocalFingerprint = (None, None)


def _one_side(stat_fn: Callable[[Path], os.stat_result], path: Path) -> StatSide:
    try:
        st = stat_fn(path)
    except FileNotFoundError:
        # Absent, or (for os.stat) a dangling symlink. Any other OSError,
        # such as a denied stat, propagates: an unreadable fingerprint must
        # not compare equal to itself and pass the guard.
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_mode)


def stat_fingerprint(path: Path) -> EnvLocalFingerprint:
    """``(lstat side, stat side)`` of ``path``; ``ABSENT`` when it does not exist. Never opens it."""

    return (_one_side(os.lstat, path), _one_side(os.stat, path))


def describe_change(before: EnvLocalFingerprint, after: EnvLocalFingerprint) -> str | None:
    """None when unchanged, else 'created', 'removed' or 'rewrote or replaced'."""

    if after == before:
        return None
    if before[0] is None:
        return "created"
    if after[0] is None:
        return "removed"
    return "rewrote or replaced"
