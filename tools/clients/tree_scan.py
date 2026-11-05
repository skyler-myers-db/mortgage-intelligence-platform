"""Scan the tracked tree for a resolved identifying value (plan §4.5 check 11, §4.16; attack G11).

The repository is public, so no client's host, id, email or other
identifying value may be committed. The deploy job resolves its client's
profile and then looks for every identifying leaf (and each spelling the
deploy derives from one, tools/clients/profile.derived_masks) in every file
``git ls-files`` lists. A hit names the profile field and the file, never the
value: the value is already public in that file, and the report says where to
purge it. Generic leaves never become needles (profile._maskable), so schema
vocabulary and platform names do not trip it.

Two kinds of leaf are not a client's own, so they never refuse:

* a cloud's default account-console host (``accounts.cloud.databricks.com``
  and its peers, tools/clients/shapes.CLOUDS), which most profiles hold and
  the docs name: it is no needle at all;
* the closed set of group names the repository itself defines, each only in
  its own field (``CONVENTION_NAMES``): ``<prefix>-admin`` in
  ``MIP_ADMIN_GROUP_NAME`` and ``<prefix>-approver`` in
  ``MIP_APPROVER_GROUP_NAME`` (backend/config/settings.py's defaults under the
  default prefix) and ``<prefix>-data-owners`` in
  ``MIP_UC_APPROVED_OWNER_PRINCIPALS`` (docs/clients/bootstrap.md), with
  ``<prefix>`` the profile's public ``MIP_IDENTITY_PREFIX``. A hit on one is
  ``conventional``: reported for review, never a refusal. Any other spelling
  that starts with the prefix, and any convention name some other field also
  holds, is a needle like every other leaf; an email, host, id or share field
  is never exempt.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tools.clients import profile as views
from tools.clients.schema import ClientProfile
from tools.clients.shapes import CLOUDS

_DEFAULT_HOSTS = frozenset(
    spelling for _suffix, host, _scheme in CLOUDS.values() for spelling in (host, f"https://{host}")
)
# Field -> the one ``<prefix>-<role>`` name the repository defines for it.
CONVENTION_NAMES = {
    "MIP_ADMIN_GROUP_NAME": "admin",
    "MIP_APPROVER_GROUP_NAME": "approver",
    "MIP_UC_APPROVED_OWNER_PRINCIPALS": "data-owners",
}


@dataclass(frozen=True, order=True)
class Hit:
    field: str
    path: str
    conventional: bool = False


@dataclass(frozen=True)
class Needle:
    field: str
    conventional: bool


def needles(profile: ClientProfile) -> dict[str, Needle]:
    """Each needle to the profile field it came from (on a repeat, the first field that is not exempt)."""

    found: dict[str, Needle] = {}
    derived = set(views.derived_masks(profile))
    for name, value in views.identifying_leaves(profile):
        role = CONVENTION_NAMES.get(name)
        convention = f"{profile.identity_prefix}-{role}".casefold() if role else None
        for spelling in (value, value.lower(), value.casefold()):
            if spelling != value and spelling not in derived:
                continue
            if spelling.casefold() in _DEFAULT_HOSTS:
                continue
            needle = Needle(name, spelling.casefold() == convention)
            # A spelling another field also holds refuses: never exempt by field order.
            if spelling not in found or (found[spelling].conventional and not needle.conventional):
                found[spelling] = needle
    return found


def tracked_files(repo: Path) -> list[str]:
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True, timeout=120)
    return [name for name in listing.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def scan(repo: Path, by_value: dict[str, Needle], files: Iterable[str] | None = None) -> list[Hit]:
    """Every (field, file) pair where a needle occurs in a tracked file."""

    encoded = [(value.encode("utf-8"), needle) for value, needle in by_value.items() if value]
    hits: set[Hit] = set()
    for rel in tracked_files(repo) if files is None else files:
        path = repo / rel
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        hits.update(Hit(needle.field, rel, needle.conventional) for value, needle in encoded if value in data)
    return sorted(hits)
