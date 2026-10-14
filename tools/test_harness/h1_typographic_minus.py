"""H1 transform: move signed-number test pins to the typographic minus.

responsive-04 (W5e): ``signedPct``, ``signedBps``, ``signedBpsLabel`` and
``signedCount`` in ``frontend/src/lib/formatters.ts`` print U+2212 (``−``)
for a negative value. Every test line that pinned their output with the
ASCII hyphen-minus moves to U+2212. Only formatter OUTPUT moves: an input
literal (``signedBps(-422)``), an unsigned formatter's output
(``formatUsd(-1_250)`` stays ``'-$1,250'``) and a server-formatted token
are never touched, which is why this is a reviewed table and not a regex.

Each entry is ``(file, old, new, count)``: the file must contain ``old``
exactly ``count`` times (or already contain ``new`` that many times, when
the transform was applied before), and after the rewrite ``old`` is gone.

Usage (from the repository root)::

    python3 tools/test_harness/h1_typographic_minus.py           # rewrite
    python3 tools/test_harness/h1_typographic_minus.py --check   # report only
    python3 tools/test_harness/h1_typographic_minus.py --scan    # list candidates

``--scan`` lists test lines where a hyphen-minus sits directly before a
digit inside a string literal on an assertion line: candidates for review
after another lane's merge, never an automatic rewrite. A real miss shows
up first as a red vitest or fixture spec; convert it by hand and add its
row here.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MINUS = "−"

_F = "frontend/src/lib/formatters.test.ts"
_LQ = "frontend/src/routes/lead-queue"
_MAP = "frontend/src/components/mortgage"

# (file, old, new, expected count). Grouped by file; keep it sorted.
TABLE: tuple[tuple[str, str, str, int], ...] = (
    (f"{_MAP}/LastLoginSummary.test.tsx", "toBe('-40 bps 30-year par", f"toBe('{MINUS}40 bps 30-year par", 1),
    (f"{_MAP}/LastLoginSummary.test.tsx", "'6.30%', '-40 bps']", f"'6.30%', '{MINUS}40 bps']", 1),
    (
        f"{_MAP}/LeadTableRow.render.test.tsx",
        "it('renders a below-market spread as -422, never +-422'",
        f"it('renders a below-market spread as {MINUS}422, never +{MINUS}422'",
        1,
    ),
    (
        f"{_MAP}/LeadTableRow.render.test.tsx",
        "toContain('-422');\n    expect(document.body.textContent).not.toContain('+-422');",
        f"toContain('{MINUS}422');\n    expect(document.body.textContent).not.toContain('+{MINUS}422');"
        "\n    expect(document.body.textContent).not.toContain('-422');",
        1,
    ),
    (f"{_MAP}/LeadTableRow.render.test.tsx", "toContain('-422 bps')", f"toContain('{MINUS}422 bps')", 1),
    (
        f"{_MAP}/RateScenarioControl.test.tsx",
        "'Largest in-the-money drops: IL -200'",
        f"'Largest in-the-money drops: IL {MINUS}200'",
        1,
    ),
    (
        "frontend/tests/e2e/fixture/home-geo-lever.fixture.spec.ts",
        "locator('.evidence-chip')).toHaveText('-19 bps')",
        f"locator('.evidence-chip')).toHaveText('{MINUS}19 bps')",
        1,
    ),
    (
        "frontend/tests/e2e/fixture/rate-lever.fixture.spec.ts",
        "part.replace(/ [+-][\\d,]+$/, '')",
        f"part.replace(/ [+{MINUS}][\\d,]+$/, '')",
        1,
    ),
    (_F, "signedBps(-422)).toBe('-422')", f"signedBps(-422)).toBe('{MINUS}422')", 1),
    (_F, "signedBps(-167.6)).toBe('-168')", f"signedBps(-167.6)).toBe('{MINUS}168')", 1),
    (
        _F,
        "it('signs a change in a population with grouping and an ASCII minus'",
        "it('signs a change in a population with grouping and the typographic minus'",
        1,
    ),
    (_F, "signedCount(-1234)).toBe('-1,234')", f"signedCount(-1234)).toBe('{MINUS}1,234')", 1),
    (_F, "signedCount(-1234).charCodeAt(0)).toBe(45)", "signedCount(-1234).charCodeAt(0)).toBe(0x2212)", 1),
    (_F, "signedCount(-2.6)).toBe('-3')", f"signedCount(-2.6)).toBe('{MINUS}3')", 1),
    (_F, "signedBpsLabel(-422)).toBe('-422 bps')", f"signedBpsLabel(-422)).toBe('{MINUS}422 bps')", 1),
    (_F, "signedPct(-1.5)).toBe('-1.5%')", f"signedPct(-1.5)).toBe('{MINUS}1.5%')", 1),
    (_F, "signedBps(-1_250)).toBe('-1,250')", f"signedBps(-1_250)).toBe('{MINUS}1,250')", 1),
    (f"{_LQ}.activeFilters.test.ts", ".toBe('≤ -10 bps')", f".toBe('≤ {MINUS}10 bps')", 1),
    (f"{_LQ}.activeFilters.test.ts", ".toBe('-25 bps – +1,500 bps')", f".toBe('{MINUS}25 bps – +1,500 bps')", 1),
    (f"{_LQ}.filterBar.test.tsx", "'Remove RATE SPREAD: ≥ -25 bps filter'", f"'Remove RATE SPREAD: ≥ {MINUS}25 bps filter'", 1),
)

_SCAN_GLOBS = (
    "frontend/src/**/*.test.ts",
    "frontend/src/**/*.test.tsx",
    "frontend/tests/e2e/**/*.spec.ts",
)
_ASSERTION_RE = re.compile(r"\b(toBe|toEqual|toContain|toHaveText|toContainText|toMatch|getByText|name:)\b")
_CANDIDATE_RE = re.compile(r"""(['"`])[^'"`\n]*?(?<![\w-])-(?=[$]?\d)[^'"`\n]*\1""")


def apply(check: bool) -> int:
    by_file: dict[str, list[tuple[str, str, int]]] = {}
    for rel, old, new, count in TABLE:
        by_file.setdefault(rel, []).append((old, new, count))
    pending = 0
    for rel, rows in sorted(by_file.items()):
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        before = text
        for old, new, count in rows:
            found = text.count(old)
            if found == 0:
                # Already applied: the new spelling carries the full count.
                assert text.count(new) >= count, f"{rel}: neither {old!r} nor {count}x {new!r}"
                continue
            assert found == count, f"{rel}: expected {count}x {old!r}, found {found}"
            text = text.replace(old, new)
            assert old not in text, f"{rel}: {old!r} survived"
        if text != before:
            pending += 1
            print(f"{'would rewrite' if check else 'rewrote'} {rel}")
            if not check:
                path.write_text(text, encoding="utf-8")
    print(f"{pending} file(s) {'pending' if check else 'rewritten'}; {len(TABLE)} table rows")
    return 1 if (check and pending) else 0


def scan() -> int:
    hits = 0
    for pattern in _SCAN_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if "node_modules" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if _ASSERTION_RE.search(line) and _CANDIDATE_RE.search(line):
                    hits += 1
                    print(f"{path.relative_to(ROOT).as_posix()}:{lineno}: {line.strip()}")
    print(f"{hits} candidate line(s); review each, convert only formatter output")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--scan", action="store_true")
    args = parser.parse_args(argv)
    return scan() if args.scan else apply(args.check)


if __name__ == "__main__":
    sys.exit(main())
