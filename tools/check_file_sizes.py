#!/usr/bin/env python3
"""Warn or fail on oversized source files.

Large production files make Module 0 harder to review safely. This gate fails
files over the hard limit unless they are temporarily allowlisted with an
unexpired date in tools/file_size_allowlist.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = ROOT / "tools" / "file_size_allowlist.json"
DEFAULT_INCLUDE_DIRS = ("backend", "frontend/src", "tools")
DEFAULT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".css"}
DEFAULT_EXCLUDE_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "coverage",
    "playwright-report",
    "test-results",
}


@dataclass(frozen=True)
class FileSize:
    path: Path
    rel: str
    lines: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warn", type=int, default=500, help="line-count warning threshold")
    parser.add_argument("--fail", type=int, default=900, help="line-count failure threshold")
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=DEFAULT_ALLOWLIST,
        help="JSON file with {'expires': {'path': 'YYYY-MM-DD'}}",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_INCLUDE_DIRS),
        help="source roots or files to inspect",
    )
    return parser.parse_args()


def _load_allowlist(path: Path) -> dict[str, date]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("expires", {})
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain an 'expires' object")

    allowlist: dict[str, date] = {}
    for rel, expiry in raw.items():
        if not isinstance(rel, str) or not isinstance(expiry, str):
            raise ValueError(f"{path} entries must map paths to YYYY-MM-DD strings")
        allowlist[rel] = date.fromisoformat(expiry)
    return allowlist


def _is_excluded(path: Path) -> bool:
    return any(part in DEFAULT_EXCLUDE_PARTS for part in path.parts)


def _iter_source_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = (ROOT / raw).resolve()
        if not path.exists():
            raise FileNotFoundError(raw)
        if path.is_file():
            if path.suffix in DEFAULT_SUFFIXES and not _is_excluded(path):
                files.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix in DEFAULT_SUFFIXES and not _is_excluded(child):
                files.append(child)
    return sorted(files)


def _count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _collect(paths: list[str], warn_threshold: int) -> list[FileSize]:
    entries: list[FileSize] = []
    for path in _iter_source_files(paths):
        lines = _count_lines(path)
        if lines >= warn_threshold:
            rel = path.relative_to(ROOT).as_posix()
            entries.append(FileSize(path=path, rel=rel, lines=lines))
    return sorted(entries, key=lambda item: (-item.lines, item.rel))


def main() -> int:
    args = _parse_args()
    if args.warn <= 0 or args.fail <= 0 or args.warn > args.fail:
        raise SystemExit("--warn and --fail must be positive, with --warn <= --fail")

    allowlist = _load_allowlist(args.allowlist)
    today = date.today()
    oversized = _collect(args.paths, args.warn)
    failures: list[FileSize] = []
    expired: list[str] = []

    for entry in oversized:
        expiry = allowlist.get(entry.rel)
        allowed = expiry is not None and expiry >= today
        status = "ALLOWLISTED" if allowed else "WARN"
        if expiry is not None and expiry < today:
            expired.append(f"{entry.rel} expired {expiry.isoformat()}")
        if entry.lines >= args.fail and not allowed:
            status = "FAIL"
            failures.append(entry)
        print(f"{status}: {entry.rel}: {entry.lines} lines")

    unused = sorted(set(allowlist) - {entry.rel for entry in oversized})
    for rel in unused:
        print(f"ALLOWLIST-STALE: {rel}", file=sys.stderr)
        failures.append(FileSize(path=ROOT / rel, rel=rel, lines=0))
    for rel in expired:
        print(f"ALLOWLIST-EXPIRED: {rel}", file=sys.stderr)
    if expired:
        failures.append(FileSize(path=ROOT, rel="expired allowlist", lines=0))

    if failures:
        print(
            f"file-size gate failed: {len(failures)} item(s) need split or allowlist refresh",
            file=sys.stderr,
        )
        return 1
    if not oversized:
        print("file-size gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
