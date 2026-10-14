"""H3 transform: drop the three test-only store seams from frontend tests.

W5a w5-identity-foundation reviewNonBlocking[4] / W5d w5-actor-binding
deferred[4]. ``clearSingleKeyShortcutsPreference`` (lib/keymapPreference),
``clearPinnedInsights`` (lib/pinnedInsights) and ``clearQueueContext``
(lib/queueContext) existed only so tests could drop a store's cache. The
actor-scope seam already does that for every suite: ``src/test/setup.ts``
runs ``_resetActorScopeForTests({ status: 'open', owner: NOBODY })`` before
each test, and its 'restamped' event drops each store's cache through the
store's ``subscribeActorScope`` handler. A suite that needs fresh storage
calls ``installLocalStorage()`` (happy-dom's real localStorage leaks across
full-suite workers).

The rewrite, per file: delete each bare call line (``  clearX();``) and the
name from its import (a one-name import line goes entirely). Per-file count
assert: the number of call lines removed plus import specifiers removed
equals the number of occurrences found, and the file names none of the
three afterwards. A call used any other way (an argument, a test name)
fails the assert, so a human converts it.

Usage (from the repository root)::

    python3 tools/test_harness/h3_seam_removal.py          # rewrite
    python3 tools/test_harness/h3_seam_removal.py --check  # report only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEAMS: tuple[str, ...] = ("clearSingleKeyShortcutsPreference", "clearPinnedInsights", "clearQueueContext")
GLOBS: tuple[str, ...] = ("frontend/src/**/*.test.ts", "frontend/src/**/*.test.tsx", "frontend/src/**/*.test-support.tsx")


def rewrite(text: str) -> tuple[str, int, int]:
    """Return (new_text, occurrences_before, occurrences_removed)."""
    before = sum(len(re.findall(rf"\b{name}\b", text)) for name in SEAMS)
    removed = 0
    for name in SEAMS:
        # A bare call statement on its own line.
        text, n = re.subn(rf"^[ \t]*{name}\(\);[ \t]*\n", "", text, flags=re.MULTILINE)
        removed += n
        # import { name } from '...';  (the only specifier)
        text, n = re.subn(rf"^import \{{ {name} \}} from '[^']+';\n", "", text, flags=re.MULTILINE)
        removed += n
        # import { name, other } / { other, name } / { a, name, b } on one line.
        text, n = re.subn(rf"(^import \{{[^}}\n]*?)\b{name}, ", r"\1", text, flags=re.MULTILINE)
        removed += n
        text, n = re.subn(rf"(^import \{{[^}}\n]*?), {name}\b", r"\1", text, flags=re.MULTILINE)
        removed += n
        # A multi-line import list entry.
        text, n = re.subn(rf"^[ \t]+{name},[ \t]*\n", "", text, flags=re.MULTILINE)
        removed += n
    return text, before, removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    changed = 0
    for pattern in GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            new_text, before, removed = rewrite(text)
            if before == 0:
                continue
            rel = path.relative_to(ROOT).as_posix()
            assert removed == before, f"{rel}: removed {removed} of {before} seam references; convert the rest by hand"
            assert not any(re.search(rf"\b{name}\b", new_text) for name in SEAMS), f"{rel}: a seam reference survived"
            changed += 1
            print(f"{removed:3d}  {rel}")
            if not args.check:
                path.write_text(new_text, encoding="utf-8")
    print(f"{'would rewrite' if args.check else 'rewrote'} {changed} file(s)")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
