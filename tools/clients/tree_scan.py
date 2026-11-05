"""Scan the tracked tree for a resolved identifying value (plan §4.5 check 11, §4.16; attack G11).

The repository is public, so no client's host, id, email or other
identifying value may be committed. The deploy job resolves its client's
profile and then looks for every identifying leaf (and each spelling the
deploy derives from one, tools/clients/profile.derived_masks) in every file
``git ls-files`` lists. A hit names the profile field and the file, never the
value: the value is already public in that file, and the report says where to
purge it. Generic leaves never become needles (profile._maskable), so schema
vocabulary and platform names do not trip it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tools.clients import profile as views
from tools.clients.schema import ClientProfile


@dataclass(frozen=True, order=True)
class Hit:
    field: str
    path: str


def needles(profile: ClientProfile) -> dict[str, str]:
    """Each needle to the profile field it came from (the first field, on a repeat)."""

    found: dict[str, str] = {}
    derived = set(views.derived_masks(profile))
    for name, value in views.identifying_leaves(profile):
        found.setdefault(value, name)
        for spelling in (value.lower(), value.casefold()):
            if spelling in derived:
                found.setdefault(spelling, name)
    return found


def tracked_files(repo: Path) -> list[str]:
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True, timeout=120)
    return [name for name in listing.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def scan(repo: Path, by_value: dict[str, str], files: Iterable[str] | None = None) -> list[Hit]:
    """Every (field, file) pair where a needle occurs in a tracked file."""

    encoded = [(value.encode("utf-8"), field) for value, field in by_value.items() if value]
    hits: set[Hit] = set()
    for rel in tracked_files(repo) if files is None else files:
        path = repo / rel
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        hits.update(Hit(field, rel) for needle, field in encoded if needle in data)
    return sorted(hits)
