"""H2 transform: swap real-domain identities in tests for synthetic aliases.

W5c fix round 1 NB-a (decision 6). Test fixtures, fake rosters and
request headers must never carry a real operator address. This script
rewrites every ``<local>@entrada.ai`` literal under the scanned test
roots to ``<alias>@summit.example`` (``skyler`` maps to ``operator``;
every other local part, including f-string tails such as
``integration-s4+{...}@entrada.ai``, keeps its local part).

The one exemption is the documented security-contact pin
(``security@entrada.ai`` in ``tests/unit/test_documentation_contract.py``),
which mirrors SECURITY.md and is the real reporting address.

Per-file count assert: for every file it touches, the number of
replacements must equal the number of matches found before the rewrite,
and the rewritten file must contain no non-exempt real-domain address.
The script is idempotent: a second run finds nothing to do.

Usage (from the repository root)::

    python3 tools/test_harness/h2_synthetic_identities.py          # rewrite
    python3 tools/test_harness/h2_synthetic_identities.py --check  # report only

``tests/unit/test_synthetic_test_identities.py`` is the gate that keeps
the tree clean afterwards; this script is how the integrator converts
other lanes' new tests after a merge.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The same roots the gate scans (tests/unit/test_synthetic_test_identities.py).
SCAN_GLOBS: tuple[str, ...] = (
    "tests/**/*.py",
    "frontend/src/**/*.test.ts",
    "frontend/src/**/*.test.tsx",
    "frontend/src/**/*.test-support.ts",
    "frontend/src/**/*.test-support.tsx",
    "frontend/tests/**/*.ts",
)

# The documented security-contact pin: SECURITY.md names this real address.
EXEMPT: frozenset[tuple[str, str]] = frozenset(
    {("tests/unit/test_documentation_contract.py", "security@entrada.ai")}
)

# Local parts that change; any other local part is kept verbatim.
LOCAL_PART_ALIASES: dict[str, str] = {"skyler": "operator"}

SYNTHETIC_DOMAIN = "summit.example"

# A local part may end in an f-string / template substitution (``}``).
_ADDRESS_RE = re.compile(r"(?P<local>[A-Za-z0-9._%+{}-]*?)@(?P<domain>entrada\.ai)\b", re.IGNORECASE)


def iter_scanned_files(root: Path = ROOT) -> Iterator[Path]:
    seen: set[Path] = set()
    for pattern in SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            if "node_modules" in path.parts or path in seen or not path.is_file():
                continue
            seen.add(path)
            yield path


def _is_exempt(rel: str, address: str) -> bool:
    return (rel, address.lower()) in EXEMPT


def _alias(match: re.Match[str]) -> str:
    local = match.group("local")
    # Preserve case of the local part; the alias table is keyed lowercase.
    mapped = LOCAL_PART_ALIASES.get(local.lower())
    if mapped is not None:
        local = mapped.upper() if local.isupper() else mapped
    return f"{local}@{SYNTHETIC_DOMAIN}"


def rewrite_text(rel: str, text: str) -> tuple[str, int, int]:
    """Return (new_text, matches_found, replacements_made) for one file."""
    found = 0
    made = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal found, made
        if _is_exempt(rel, match.group(0)):
            return match.group(0)
        found += 1
        made += 1
        return _alias(match)

    new_text = _ADDRESS_RE.sub(repl, text)
    return new_text, found, made


def remaining_real_addresses(rel: str, text: str) -> list[str]:
    return [m.group(0) for m in _ADDRESS_RE.finditer(text) if not _is_exempt(rel, m.group(0))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report only; exit 1 if anything would change")
    args = parser.parse_args(argv)

    changed: dict[str, int] = {}
    for path in iter_scanned_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        new_text, found, made = rewrite_text(rel, text)
        if not found:
            continue
        # Per-file count assert: every match was rewritten, none left behind.
        assert made == found, f"{rel}: {made} replacements for {found} matches"
        left = remaining_real_addresses(rel, new_text)
        assert not left, f"{rel}: real-domain addresses survived the rewrite: {left}"
        changed[rel] = made
        if not args.check:
            path.write_text(new_text, encoding="utf-8")

    for rel, count in sorted(changed.items()):
        print(f"{count:4d}  {rel}")
    total = sum(changed.values())
    verb = "would rewrite" if args.check else "rewrote"
    print(f"{verb} {total} identities in {len(changed)} files")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
