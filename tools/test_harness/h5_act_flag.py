"""H5 transform: delete the per-file React act-environment flag from frontend tests.

quality-06 part A (W5e). ``frontend/src/test/setup.ts`` sets
``IS_REACT_ACT_ENVIRONMENT = true`` once for the whole Vitest run (it is the
suite's only setup file), so the line every older suite repeats::

    (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

is dead weight, and a copy that drifts (sets it false, or sets it after a
render) would silently change what a suite tests.
``frontend/src/test/actEnvironmentGate.test.ts`` keeps the tree clean: only
setup.ts may assign the flag.

The rewrite deletes the assignment line in every file under
``frontend/src`` except setup.ts (both cast spellings the repo uses), and
one of the two blank lines it leaves when it sat between blank lines.
Per-file count assert: exactly the assignments found are removed and none
survives. Idempotent.

Usage (from the repository root)::

    python3 tools/test_harness/h5_act_flag.py          # rewrite
    python3 tools/test_harness/h5_act_flag.py --check  # report only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend" / "src"
SETUP = FRONTEND_SRC / "test" / "setup.ts"
# The gate spells the flag inside string literals (its inline cases).
GATE = FRONTEND_SRC / "test" / "actEnvironmentGate.test.ts"
GLOBS = ("**/*.ts", "**/*.tsx")

_FLAG = (
    r"\(globalThis as (?:unknown as \{ IS_REACT_ACT_ENVIRONMENT: boolean \}"
    r"|typeof globalThis & \{ IS_REACT_ACT_ENVIRONMENT\?: boolean \})\)"
    r"\.IS_REACT_ACT_ENVIRONMENT = true;"
)
# Between two blank lines: keep one of them.
_BETWEEN_BLANKS = re.compile(rf"\n\n[ \t]*{_FLAG}[ \t]*\n(?=\n)")
_ANYWHERE = re.compile(rf"^[ \t]*{_FLAG}[ \t]*\n", re.MULTILINE)
_ANY_ASSIGNMENT = re.compile(r"IS_REACT_ACT_ENVIRONMENT\s*=")


def rewrite(text: str) -> tuple[str, int, int]:
    """Return (new_text, assignments_found, assignments_removed)."""
    found = len(_ANY_ASSIGNMENT.findall(text))
    text, between = _BETWEEN_BLANKS.subn("\n", text)
    text, anywhere = _ANYWHERE.subn("", text)
    return text, found, between + anywhere


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    changed = 0
    for pattern in GLOBS:
        for path in sorted(FRONTEND_SRC.glob(pattern)):
            if path in (SETUP, GATE) or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "IS_REACT_ACT_ENVIRONMENT" not in text:
                continue
            new_text, found, removed = rewrite(text)
            rel = path.relative_to(ROOT).as_posix()
            if found == 0:
                continue
            assert removed == found, f"{rel}: removed {removed} of {found} act-flag assignments; convert the rest by hand"
            assert not _ANY_ASSIGNMENT.search(new_text), f"{rel}: an act-flag assignment survived"
            changed += 1
            if not args.check:
                path.write_text(new_text, encoding="utf-8")
    print(f"{'would rewrite' if args.check else 'rewrote'} {changed} file(s)")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
