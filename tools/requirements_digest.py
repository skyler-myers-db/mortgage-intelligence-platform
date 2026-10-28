#!/usr/bin/env python3
"""Stamp requirements.txt with the sha256 of the two files it includes.

Why: the Databricks Apps build decides whether to reinstall Python
dependencies by comparing requirements.txt with the previous deployment's
copy ("Requirements have not changed. Skipping installation.") and does not
follow its ``-c uv.lock`` / ``-r requirements.in`` includes. requirements.txt
is only those two include lines, so a lock refresh (security floors included)
never reached a running App, whose Python environment persists across
deployments. A generated first line carrying the digest of both included
files turns every lock or input change into a requirements.txt change:

  python tools/requirements_digest.py --write   # after any uv.lock / requirements.in change
  python tools/requirements_digest.py --check   # exit 1, with the remedy, when stale

The line is a comment, so pip, pip-audit and tools/python_deps_check.py read
requirements.txt exactly as before.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_TXT = "requirements.txt"
DIGESTED_FILES: tuple[str, ...] = ("uv.lock", "requirements.in")
PREFIX = "# deps-digest:"
REMEDY = "python tools/requirements_digest.py --write"
LINE_PATTERN = re.compile(
    r"^# deps-digest: uv\.lock sha256=[0-9a-f]{64} requirements\.in sha256=[0-9a-f]{64}$"
)
EXIT_CURRENT = 0
EXIT_STALE = 1


def digest_line(root: Path) -> str:
    """The first line requirements.txt must carry for the files under ``root``."""

    parts = [
        f"{name} sha256={hashlib.sha256((root / name).read_bytes()).hexdigest()}"
        for name in DIGESTED_FILES
    ]
    return f"{PREFIX} {' '.join(parts)}"


def render(current: str, line: str) -> str:
    """``current`` with every old digest line dropped and ``line`` prepended."""

    kept = [raw for raw in current.splitlines(keepends=True) if not raw.startswith(PREFIX)]
    return f"{line}\n{''.join(kept)}"


def check(root: Path) -> tuple[int, str]:
    text = (root / REQUIREMENTS_TXT).read_text(encoding="utf-8")
    first = text.splitlines()[0] if text else ""
    if first == digest_line(root):
        return EXIT_CURRENT, "requirements.txt deps-digest matches uv.lock and requirements.in"
    if first.startswith(PREFIX):
        reason = "requirements.txt deps-digest is stale: uv.lock or requirements.in changed"
    else:
        reason = "requirements.txt has no deps-digest first line"
    return EXIT_STALE, (
        f"{reason}; the Databricks Apps build would skip the dependency reinstall.\n"
        f"  fix: {REMEDY}"
    )


def write(root: Path) -> tuple[int, str]:
    path = root / REQUIREMENTS_TXT
    current = path.read_text(encoding="utf-8")
    updated = render(current, digest_line(root))
    if updated == current:
        return EXIT_CURRENT, "requirements.txt deps-digest already current"
    path.write_text(updated, encoding="utf-8")
    return EXIT_CURRENT, "requirements.txt deps-digest updated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite the digest line")
    mode.add_argument("--check", action="store_true", help="exit 1 when the digest is stale")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    code, message = write(args.root) if args.write else check(args.root)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
