"""H4 transform: make every lib/api vi.mock factory spread the real module.

12.3 review leftover (W5e). A factory ``vi.mock('../lib/api', () => ({ api,
ApiError }))`` REPLACES the module: ``isAbortError``, ``isWarmingUpError``,
``dependencyLabel`` and every other export are undefined for the whole file,
which flakes as soon as some module the test loads imports one of them
(bde112b0). ``frontend/src/test/apiMockShape.test.ts`` now requires the
spread form; this script converts the factories that only defined
``ApiError``:

    vi.mock('../lib/api', async (importOriginal) => ({
      ...(await importOriginal<typeof import('../lib/api')>()),
      api: apiMocks,
    }));

Every override the factory returned is kept AFTER the spread, so a test
keeps its own ``api`` mocks and any ``isAbortError`` / ``dependencyLabel``
stub it relied on. One thing is dropped: a trivial fake
``ApiError: class extends Error {},`` line that nothing else in the file
names (it only shadowed the real class; the spread now supplies it). A fake
the test constructs or that carries fields is kept.

Handles the two shapes the repo uses: an expression body
(``() => ({ ... })``) and a block body whose last ``return { ... }`` is the
module. Per-file count assert: every sync factory found on lib/api is
converted, and the file afterwards has one ``importOriginal<typeof
import(...)>()`` spread per converted factory. Idempotent.

Usage (from the repository root)::

    python3 tools/test_harness/h4_api_mock_spread.py          # rewrite
    python3 tools/test_harness/h4_api_mock_spread.py --check  # report only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
TARGET = "src/lib/api"
GLOBS = ("src/**/*.test.ts", "src/**/*.test.tsx", "src/**/*.test-support.ts", "src/**/*.test-support.tsx")
# The gate's own inline cases are source strings, not mocks.
SKIP = {"src/test/apiMockShape.test.ts"}

_MOCK_RE = re.compile(r"vi\.(?:mock|doMock)\(\s*'([^']+)',\s*\(\)\s*=>\s*")
_TRIVIAL_FAKE_RE = re.compile(r"^[ \t]*ApiError: class(?: ApiError)? extends Error \{\},[ \t]*\n", re.MULTILINE)


def resolve(rel_file: str, spec: str) -> str:
    parts = rel_file.split("/")[:-1]
    for segment in spec.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            parts.pop()
        else:
            parts.append(segment)
    return re.sub(r"\.(tsx?|jsx?)$", "", "/".join(parts))


def closing(text: str, start: int) -> int:
    """Index of the bracket that closes the opening bracket at ``text[start]``."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack: list[str] = []
    quote: str | None = None
    i = start
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    raise ValueError("unbalanced brackets")


def spread_line(spec: str) -> str:
    return f"...(await importOriginal<typeof import('{spec}')>())"


def _insert_after_brace(text: str, brace: int, spec: str) -> str:
    """Insert the spread as the first member of the object literal opening at ``brace``."""
    if text[brace + 1] == "\n":
        indent = re.match(r"[ \t]*", text[brace + 2 :]).group(0)  # type: ignore[union-attr]
        return text[: brace + 1] + f"\n{indent}{spread_line(spec)}," + text[brace + 1 :]
    return text[: brace + 1] + f" {spread_line(spec)}," + text[brace + 1 :]


def convert(rel: str, text: str) -> tuple[str, int, int]:
    """Return (new_text, factories_found, factories_converted)."""
    found = converted = 0
    pos = 0
    while (match := _MOCK_RE.search(text, pos)) is not None:
        pos = match.end()
        spec = match.group(1)
        if resolve(rel, spec) != TARGET:
            continue
        found += 1
        head = text[match.start() : match.end()].replace("() =>", "async (importOriginal) =>", 1)
        body_start = match.end()
        if text.startswith("({", body_start):
            brace = body_start + 1
            new_body = _insert_after_brace(text, brace, spec)[body_start:]
        elif text.startswith("{", body_start):
            end = closing(text, body_start)
            block = text[body_start : end + 1]
            ret = block.rfind("return {")
            assert ret != -1, f"{rel}: block factory without a final `return {{`"
            brace = body_start + ret + len("return ")
            new_body = _insert_after_brace(text, brace, spec)[body_start:]
        else:
            raise AssertionError(f"{rel}: unrecognised factory shape at offset {body_start}")
        text = text[: match.start()] + head + new_body
        converted += 1
        pos = match.start() + len(head)
    return text, found, converted


def drop_trivial_fakes(text: str) -> str:
    for fake in list(_TRIVIAL_FAKE_RE.finditer(text)):
        line = fake.group(0)
        if text.count("ApiError") - line.count("ApiError") == 0:
            text = text.replace(line, "", 1)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    changed = 0
    for pattern in GLOBS:
        for path in sorted(FRONTEND.glob(pattern)):
            rel = path.relative_to(FRONTEND).as_posix()
            if "node_modules" in path.parts or rel in SKIP:
                continue
            text = path.read_text(encoding="utf-8")
            new_text, found, converted = convert(rel, text)
            if not found:
                continue
            assert converted == found, f"{rel}: converted {converted} of {found} sync lib/api factories"
            new_text = drop_trivial_fakes(new_text)
            assert new_text.count("...(await importOriginal<typeof import(") >= converted, rel
            changed += 1
            print(f"{converted:3d}  frontend/{rel}")
            if not args.check:
                path.write_text(new_text, encoding="utf-8")
    print(f"{'would rewrite' if args.check else 'rewrote'} {changed} file(s)")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
