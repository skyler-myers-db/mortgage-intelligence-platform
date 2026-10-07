#!/usr/bin/env python3
"""Does frontend/node_modules match frontend/package-lock.json?

scripts/deploy.sh step 0 asks this before step 1 builds the App source
(W5a carryover, audit stack-10), so a refreshed lock is never built over an
older install. Standard library only: pure functions plus
``python -m tools.frontend_deps_check``.

Exit codes are distinct so the caller never mistakes an error for "stale":
  0   fresh: every non-optional lock entry is installed at its locked version
      (an optional entry, such as another platform's native binding, counts
      only when it is installed);
  10  missing or stale: prints the first 20 mismatches, package names only;
  11  frontend/node_modules is a symlink, i.e. a shared install that a deploy
      must never mutate.
Any other code (argparse's 2, an uncaught error's 1) is a hard error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND = REPO_ROOT / "frontend"
EXIT_FRESH = 0
EXIT_STALE = 10
EXIT_SHARED = 11
MAX_LISTED = 20


def lock_entries(lock: dict[str, object]) -> dict[str, tuple[str, bool]]:
    """``node_modules/...`` lock paths -> (locked version, optional)."""

    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("package-lock.json has no packages map (lockfileVersion >= 2 required)")
    entries: dict[str, tuple[str, bool]] = {}
    for key, meta in packages.items():
        if not key.startswith("node_modules/") or not isinstance(meta, dict) or meta.get("link"):
            continue
        optional = bool(meta.get("optional") or meta.get("devOptional"))
        entries[key] = (str(meta.get("version", "")), optional)
    return entries


def installed_version(frontend_dir: Path, lock_path: str) -> str | None:
    manifest = frontend_dir / lock_path / "package.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version"))
    except (OSError, ValueError):
        return None


def _name(lock_path: str) -> str:
    return lock_path.rsplit("node_modules/", 1)[-1]


def mismatches(frontend_dir: Path, lock: dict[str, object]) -> list[str]:
    """Each missing or stale non-optional entry, and each stale installed optional one."""

    found: list[str] = []
    for lock_path, (version, optional) in sorted(lock_entries(lock).items()):
        installed = installed_version(frontend_dir, lock_path)
        if installed is None:
            if not optional:
                found.append(f"missing: {_name(lock_path)}")
        elif installed != version:
            found.append(f"stale: {_name(lock_path)}")
    return found


def check(frontend_dir: Path) -> tuple[int, list[str]]:
    node_modules = frontend_dir / "node_modules"
    if node_modules.is_symlink():
        return EXIT_SHARED, ["frontend/node_modules is a shared/symlinked install"]
    if not node_modules.is_dir():
        return EXIT_STALE, ["frontend/node_modules is missing"]
    lock = json.loads((frontend_dir / "package-lock.json").read_text(encoding="utf-8"))
    found = mismatches(frontend_dir, lock)
    if not found:
        return EXIT_FRESH, ["frontend/node_modules matches frontend/package-lock.json"]
    listed = found[:MAX_LISTED]
    more = len(found) - len(listed)
    return EXIT_STALE, [*listed, *([f"... and {more} more"] if more else [])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--frontend-dir", type=Path, default=DEFAULT_FRONTEND)
    args = parser.parse_args(argv)
    code, lines = check(args.frontend_dir)
    for line in lines:
        print(f"  {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
