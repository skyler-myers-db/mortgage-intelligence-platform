"""Scaffold sanity check.

Validates that the repository has the structural files the app relies on and
that no forbidden secret files are committed to git. Local-only files like
`.env.local` are expected to exist on a developer machine, so this script
inspects git's tracked set rather than the filesystem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "app.yaml",
    "databricks.yml",
    "frontend/src/app.tsx",
    "backend/main.py",
    "backend/runtime.py",
    "docs/implementation-plan.md",
    ".claude/settings.json",
    ".claude/skills/databricks-app/SKILL.md",
    "sql/ddl/001_catalogs_schemas.sql",
    "tests/unit/test_scoring.py",
]
FORBIDDEN_COMMITTED = [".env", ".env.local", "secrets"]


def _git_tracked_files() -> set[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def main() -> None:
    missing = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing:
        raise SystemExit(f"Missing required files: {missing}")

    tracked = _git_tracked_files()
    leaked = sorted(name for name in FORBIDDEN_COMMITTED if name in tracked)
    if leaked:
        raise SystemExit(f"Forbidden committed secret path(s) in git: {leaked}")

    print(f"MIP scaffold OK: {len(REQUIRED)} required files present, no forbidden paths committed.")


if __name__ == "__main__":
    main()
