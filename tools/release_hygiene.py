#!/usr/bin/env python3
"""Fail release source zips that contain local state or generated artifacts."""

from __future__ import annotations

import argparse
import sys
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

BANNED_DIR_NAMES = {
    ".bundle",
    ".claude",
    ".databricks",
    ".git",
    ".hypothesis",
    ".idea",
    ".mypy_cache",
    ".playwright-mcp",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "coverage",
    "node_modules",
    "playwright-report",
    "screenshots",
    "test-results",
    "traces",
}

BANNED_FILE_NAMES = {
    ".DS_Store",
    ".mcp.json",
    "trace.zip",
}

BANNED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".tsbuildinfo",
}


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def _normalized_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    return tuple(part for part in path.parts if part not in {"", "."})


def _is_rendered_sql(parts: tuple[str, ...]) -> bool:
    return any(left == "sql" and right == "_rendered" for left, right in zip(parts, parts[1:], strict=False))


def _env_reason(basename: str) -> str | None:
    if basename == ".env.example":
        return None
    if basename == ".env" or basename.startswith(".env."):
        return "local env file; only .env.example is allowed"
    return None


def inspect_zip(path: str) -> list[Finding]:
    findings: list[Finding] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            parts = _normalized_parts(info.filename)
            if not parts:
                continue

            basename = parts[-1]
            lower_basename = basename.lower()

            if any(part in BANNED_DIR_NAMES for part in parts):
                offending = next(part for part in parts if part in BANNED_DIR_NAMES)
                findings.append(Finding(info.filename, f"banned directory or path segment: {offending}"))
                continue

            if _is_rendered_sql(parts):
                findings.append(Finding(info.filename, "rendered SQL artifact: sql/_rendered"))
                continue

            reason = _env_reason(basename)
            if reason:
                findings.append(Finding(info.filename, reason))
                continue

            if basename in BANNED_FILE_NAMES:
                findings.append(Finding(info.filename, f"banned file: {basename}"))
                continue

            if any(lower_basename.endswith(suffix) for suffix in BANNED_SUFFIXES):
                findings.append(Finding(info.filename, f"banned generated suffix: {lower_basename}"))

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zipfile", help="source release zip to inspect")
    args = parser.parse_args(argv)

    try:
        findings = inspect_zip(args.zipfile)
    except FileNotFoundError:
        print(f"release hygiene: missing artifact: {args.zipfile}", file=sys.stderr)
        return 2
    except zipfile.BadZipFile as exc:
        print(f"release hygiene: invalid zip: {args.zipfile}: {exc}", file=sys.stderr)
        return 2

    if findings:
        print("release hygiene: FAIL; banned files found:")
        for finding in findings:
            print(f"  - {finding.path} ({finding.reason})")
        return 1

    print(f"release hygiene: OK: {args.zipfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
