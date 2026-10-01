"""Pin the Storybook ruling (audit quality-06, decision D-platform-process-c).

The repo does not build a separate component workbench: the fixture-mode VRT
(frontend/tests/e2e/fixture/visual.fixture.spec.ts + fixtureStates.ts) is the
component and state catalogue, rendering the real production build inside the
AppShell. This test keeps the dead wording and config from coming back.

It enumerates TRACKED files (``git ls-files``; a plain walk that skips
node_modules, dist, test-results, playwright-report and .git when the tree is
not a git work tree, as in a ``git archive`` export), so third-party packages
that ship their own stories never count.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORD = "storybook"
SELF = "tests/unit/test_no_storybook.py"
STORIES = re.compile(r"(^|/)[^/]+\.stories\.[^/]+$")
WALK_SKIP = {"node_modules", "dist", "test-results", "playwright-report", ".git", ".venv", "__pycache__", ".claude"}

# Files that may still say the word, each with its reason:
#   CLAUDE.md: its line-89 wording is changed by the integrator, never by a
#     lane (W5a integrator decision 1);
#   docs/audits/**: the audit record quotes the finding and its ruling;
#   this test file, which has to name the word.
EXEMPT_FILES = {"CLAUDE.md", SELF}
EXEMPT_PREFIXES = ("docs/audits/",)

# Shrink-only: files another lane owns this wave whose comment still says the
# word. Each entry must still contain it (a reworded file fails as stale and
# its entry is removed in the same change); never add one.
PENDING_WORDING: dict[str, str] = {}


def tracked_files(root: Path = ROOT) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True, timeout=60
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in WALK_SKIP]
            base = Path(dirpath).relative_to(root)
            files.extend((base / name).as_posix() for name in filenames)
        return sorted(files)
    return sorted(item for item in completed.stdout.decode("utf-8").split("\0") if item)


def _read_text(root: Path) -> Callable[[str], str | None]:
    def read(rel: str) -> str | None:
        try:
            return (root / rel).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    return read


def storybook_problems(
    paths: Iterable[str],
    read: Callable[[str], str | None],
    pending: dict[str, str] | None = None,
) -> list[str]:
    """Every tracked stories file, .storybook/ path or unexempted mention."""

    pending = PENDING_WORDING if pending is None else pending
    listed = set(paths)
    problems: list[str] = []
    for rel in sorted(listed):
        if STORIES.search(rel) or rel.startswith(".storybook/") or "/.storybook/" in rel:
            problems.append(f"{rel}: a tracked Storybook artefact")
        if rel in EXEMPT_FILES or rel.startswith(EXEMPT_PREFIXES) or rel in pending:
            continue
        text = read(rel)
        if text is not None and WORD in text.lower():
            problems.append(f"{rel}: mentions {WORD}")
    for rel, owner in sorted(pending.items()):
        text = read(rel) if rel in listed else None
        if text is None or WORD not in text.lower():
            problems.append(f"{rel}: PENDING_WORDING entry ({owner}) no longer applies: remove it")
    return problems


def test_no_tracked_storybook_artefact_or_wording() -> None:
    paths = tracked_files()
    assert {"frontend/eslint.config.js", "docs/implementation-plan.md"} <= set(paths), (
        "non-vacuity: the enumeration must see the tracked tree"
    )
    assert storybook_problems(paths, _read_text(ROOT)) == []


def test_no_storybook_dependency_or_eslint_exemption() -> None:
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    names = {*package.get("dependencies", {}), *package.get("devDependencies", {})}
    assert names, "non-vacuity: package.json declares dependencies"
    assert sorted(name for name in names if WORD in name.lower()) == []
    assert ".stories" not in (ROOT / "frontend" / "eslint.config.js").read_text(encoding="utf-8")


def test_the_scan_rejects_planted_cases() -> None:
    planted = {
        "frontend/src/components/x.stories.tsx": "import { LEADS } from '../mocks/fixtureData';",
        ".storybook/main.ts": "export default {};",
        "frontend/.storybook/preview.ts": "export default {};",
        "docs/guide.md": "Open Storybook to browse states.",
        "CLAUDE.md": "exist for unit tests and Storybook only",
        "docs/audits/finding.md": "quality-06: Storybook ruling",
        "frontend/src/ok.ts": "export const ok = 1;",
        "pending.tsx": "// (tests, Storybook)",
        "reworded.tsx": "// (tests, fixture specs)",
    }
    problems = storybook_problems(
        planted,
        planted.get,
        pending={"pending.tsx": "some-lane", "reworded.tsx": "other-lane"},
    )
    assert problems == [
        ".storybook/main.ts: a tracked Storybook artefact",
        "docs/guide.md: mentions storybook",
        "frontend/.storybook/preview.ts: a tracked Storybook artefact",
        "frontend/src/components/x.stories.tsx: a tracked Storybook artefact",
        "reworded.tsx: PENDING_WORDING entry (other-lane) no longer applies: remove it",
    ]
