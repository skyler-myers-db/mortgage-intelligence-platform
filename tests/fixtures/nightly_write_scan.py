"""Which tests nightly.yml runs that write a lead's sales state (M1-PR8, round-2 review).

A lead outcome, a call disposition, a snooze or a loan-officer assignment on a
real lead carries no run marker and no cleanup undoes it, so no test nightly
runs may write one. These scanners find every literal write path in what a
step runs and attribute it to the test that holds it. They fail closed: a
path anywhere no single test can be named (module level, a constant, a
fixture, a class, a hook, a decorated or indirectly referenced helper, an
imported module, a conftest) is a finding, whether or not a test reaches it.

* ``python_findings`` covers a pytest suite: a path may sit only in a
  deselected test, or in a top-level helper referenced (by name, anywhere)
  only from deselected tests and such helpers.
* ``spec_findings`` covers a Playwright spec: a path may sit only inside a
  ``test(...)`` call the step's ``--grep``/``--grep-invert`` leaves out,
  matched the way Playwright matches, against ``" <project> <file>
  <describe...> <title>"``. A relative import that holds one is a finding.

A UI control that posts one of these paths is not a literal in the spec;
nightly's specs drive none today, and the docs list what they write.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# POST /leads/{id}/outcome|disposition|assign|snooze, /leads/{id}/assignment/release,
# /sales/distribute(/release), /loan-officers/assignments(/{id}/status|/outcome).
# Reads stay out: /sales/outcomes/summary, /leads/{id}/assignment, /geo/assignment-overlay.
LEAD_STATE_WRITE = re.compile(
    r"/(?:outcome|disposition|assign|snooze)(?![\w-])|/assignment/release|/sales/distribute|/loan-officers/assignments"
)
# What Playwright's grep title starts with: the root suite's empty title, a project, the file.
GREP_TITLE_PREFIX = " project {file}"


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


# ---------------------------------------------------------------- pytest suites


def _module_path(root: Path, module: str) -> Path | None:
    base = root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imported(root: Path, path: Path, tree: ast.Module, prefixes: tuple[str, ...]) -> set[Path]:
    """The repo modules ``tree`` imports (any depth, relative imports included) under ``prefixes``."""

    package = ".".join(path.relative_to(root).with_suffix("").parts[:-1])
    found: set[Path] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*parts, *([base] if base else [])])
            names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        for name in names:
            if name.startswith(prefixes) and (module := _module_path(root, name)) is not None:
                found.add(module)
    return found


def whole_module_findings(root: Path, paths: Iterable[Path], prefixes: tuple[str, ...] = ("tests.", "tools.")) -> list[str]:
    """Every write path in these modules and, transitively, the repo modules they import."""

    findings: list[str] = []
    seen: set[Path] = set()
    todo = list(paths)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        source = path.read_text(encoding="utf-8")
        for match in LEAD_STATE_WRITE.finditer(source):
            findings.append(f"{path.relative_to(root)}:{_line_of(source, match.start())}: {match.group()} in a module nightly loads")
        todo.extend(_imported(root, path, ast.parse(source), prefixes))
    return sorted(findings)


def _conftests(root: Path, suite: Path) -> list[Path]:
    """The conftest.py files pytest loads for ``suite``: the root's and each directory's down to it."""

    directories = [root / parent for parent in reversed(suite.relative_to(root).parents)]
    return [directory / "conftest.py" for directory in directories if (directory / "conftest.py").is_file()]


def python_findings(root: Path, suite: str, deselected: set[str]) -> tuple[list[str], list[str]]:
    """(findings, the tests that write) for one suite a step runs, failing closed."""

    path = root / suite
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}

    def owner(line: int) -> str | None:
        return next((name for name, node in functions.items() if node.lineno <= line <= (node.end_lineno or node.lineno)), None)

    findings: list[str] = []
    writers: set[str] = set()
    for match in LEAD_STATE_WRITE.finditer(source):
        line = _line_of(source, match.start())
        name = owner(line)
        if name is None:
            findings.append(f"{suite}:{line}: {match.group()} outside a top-level function")
        else:
            writers.add(name)
    # Every function that names a writer (called, passed, aliased) writes too.
    references = [
        (node.id, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in functions
    ]
    changed = True
    while changed:
        changed = False
        for name, line in references:
            holder = owner(line)
            if name in writers and holder is not None and holder not in writers:
                writers.add(holder)
                changed = True
    for name, line in references:
        if name in writers and owner(line) is None:
            findings.append(f"{suite}:{line}: {name} writes and is referenced outside a top-level function")
    for name in sorted(writers):
        node = functions[name]
        if name.startswith("test"):
            if name not in deselected:
                findings.append(f"{suite}::{name} writes and is not deselected")
        elif node.decorator_list:
            findings.append(f"{suite}:{node.lineno}: {name} writes and is decorated (a fixture runs without a call)")
    imported = _imported(root, path, tree, ("tests.", "tools."))
    findings += whole_module_findings(root, [*imported, *_conftests(root, path)])
    return findings, sorted(name for name in writers if name.startswith("test"))


# ---------------------------------------------------------------- Playwright specs


@dataclass(frozen=True)
class _Token:
    kind: str  # ident | punct | string | template | regex | number
    value: str
    start: int
    end: int


class LexError(ValueError):
    """Source this lexer cannot read: the scan fails rather than guess."""


_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"\d[\w.]*")
_REGEX_AFTER_WORD = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "await", "yield"}


def _string_end(src: str, i: int) -> int:
    quote, j = src[i], i + 1
    while j < len(src):
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == quote:
            return j + 1
        if src[j] == "\n":
            break
        j += 1
    raise LexError(f"unterminated string at {i}")


def _regex_end(src: str, i: int) -> int:
    j, in_class = i + 1, False
    while j < len(src):
        char = src[j]
        if char == "\\":
            j += 2
            continue
        if char == "\n":
            raise LexError(f"unterminated regex at {i}")
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "/":
            j += 1
            while j < len(src) and src[j].isalpha():
                j += 1
            return j
        j += 1
    raise LexError(f"unterminated regex at {i}")


def _template_end(src: str, i: int) -> int:
    j = i + 1
    while j < len(src):
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == "`":
            return j + 1
        if src.startswith("${", j):
            _tokens, close = _lex(src, j + 2, nested=True)
            j = close + 1
            continue
        j += 1
    raise LexError(f"unterminated template at {i}")


def _regex_allowed(tokens: list[_Token]) -> bool:
    if not tokens:
        return True
    last = tokens[-1]
    if last.kind == "punct":
        return last.value not in (")", "]")
    return last.kind == "ident" and last.value in _REGEX_AFTER_WORD


def _lex(src: str, i: int = 0, *, nested: bool = False) -> tuple[list[_Token], int]:
    """Code tokens from ``i``; nested, up to the ``}`` that closes a ``${``, whose index is returned."""

    tokens: list[_Token] = []
    depth = 0
    while i < len(src):
        char = src[i]
        if char.isspace():
            i += 1
        elif src.startswith("//", i):
            end = src.find("\n", i)
            i = len(src) if end < 0 else end
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            if end < 0:
                raise LexError(f"unterminated comment at {i}")
            i = end + 2
        elif char in "'\"":
            end = _string_end(src, i)
            tokens.append(_Token("string", src[i + 1 : end - 1], i, end))
            i = end
        elif char == "`":
            end = _template_end(src, i)
            tokens.append(_Token("template", src[i + 1 : end - 1], i, end))
            i = end
        elif char == "/" and _regex_allowed(tokens):
            end = _regex_end(src, i)
            tokens.append(_Token("regex", src[i:end], i, end))
            i = end
        elif match := _IDENT.match(src, i):
            tokens.append(_Token("ident", match.group(), i, match.end()))
            i = match.end()
        elif match := _NUMBER.match(src, i):
            tokens.append(_Token("number", match.group(), i, match.end()))
            i = match.end()
        else:
            if nested and char == "{":
                depth += 1
            elif nested and char == "}":
                if depth == 0:
                    return tokens, i
                depth -= 1
            width = 2 if src.startswith("=>", i) else 1
            tokens.append(_Token("punct", src[i : i + width], i, i + width))
            i += width
    if nested:
        raise LexError("unterminated template expression")
    return tokens, i


@dataclass(frozen=True)
class SpecCall:
    kind: str  # test | describe
    title: str
    start: int
    end: int


_TEST_MODIFIERS = {"only", "skip", "fixme", "fail", "slow"}
_DESCRIBE_MODIFIERS = {"only", "skip", "fixme", "serial", "parallel"}


def spec_calls(src: str) -> list[SpecCall]:
    """Every ``test('title', ...)`` and ``test.describe('title', ...)`` call, with its span."""

    tokens, _end = _lex(src)
    calls: list[SpecCall] = []
    for k, token in enumerate(tokens):
        if token.kind != "ident" or token.value != "test" or (k and tokens[k - 1].value == "."):
            continue
        words: list[str] = []
        j = k + 1
        while j + 1 < len(tokens) and tokens[j].value == "." and tokens[j + 1].kind == "ident":
            words.append(tokens[j + 1].value)
            j += 2
        if j >= len(tokens) or tokens[j].value != "(":
            continue
        if not words or (len(words) == 1 and words[0] in _TEST_MODIFIERS):
            kind = "test"
        elif words[0] == "describe" and all(word in _DESCRIBE_MODIFIERS for word in words[1:]):
            kind = "describe"
        else:
            continue
        title = tokens[j + 1] if j + 1 < len(tokens) else None
        if title is None or title.kind not in ("string", "template") or "${" in title.value:
            continue
        depth = 0
        for close in tokens[j:]:
            depth += {"(": 1, ")": -1}.get(close.value, 0) if close.kind == "punct" else 0
            if depth == 0:
                calls.append(SpecCall(kind, title.value, tokens[j].start, close.end))
                break
        else:
            raise LexError(f"unclosed {kind} call at {token.start}")
    return calls


def _relative_imports(path: Path, src: str) -> list[Path]:
    found = []
    for module in re.findall(r"""(?:from|import)\s*\(?\s*['"](\.{1,2}/[^'"]+)['"]""", src):
        base = (path.parent / module).resolve()
        for candidate in (base, base.with_suffix(".ts"), base.with_suffix(".tsx"), base / "index.ts"):
            if candidate.is_file():
                found.append(candidate)
                break
    return found


def grep_matches(pattern: str, title: str) -> bool:
    """Playwright's forceRegExp: ``/source/flags`` keeps its flags, anything else is ``new RegExp(p, 'gi')``."""

    literal = re.fullmatch(r"/(.*)/([gi]*)", pattern)
    if literal:
        return re.search(literal[1], title, re.IGNORECASE if "i" in literal[2] else 0) is not None
    return re.search(pattern, title, re.IGNORECASE) is not None


def _grep_title(spec: str, calls: list[SpecCall], test: SpecCall) -> str:
    describes = sorted((c for c in calls if c.kind == "describe" and c.start < test.start and test.end <= c.end), key=lambda c: c.start)
    file = spec.removeprefix("tests/e2e/")
    return " ".join([GREP_TITLE_PREFIX.format(file=file), *(c.title for c in describes), test.title])


def spec_grep_titles(frontend: Path, spec: str) -> list[str]:
    """The grep title of every test in ``spec``."""

    calls = spec_calls((frontend / spec).read_text(encoding="utf-8"))
    return [_grep_title(spec, calls, call) for call in calls if call.kind == "test"]


def spec_findings(frontend: Path, spec: str, grep: str | None, grep_invert: str | None) -> tuple[list[str], list[str]]:
    """(findings, the grep titles of the tests that write) for one spec a step runs."""

    path = frontend / spec
    src = path.read_text(encoding="utf-8")
    calls = spec_calls(src)
    tests = [call for call in calls if call.kind == "test"]
    findings: list[str] = []
    writers: list[str] = []
    for match in LEAD_STATE_WRITE.finditer(src):
        line = _line_of(src, match.start())
        holders = [test for test in tests if test.start < match.start() < test.end]
        if not holders:
            findings.append(f"{spec}:{line}: {match.group()} outside any test (a helper, a hook or module level)")
            continue
        title = _grep_title(spec, calls, max(holders, key=lambda test: test.start))
        writers.append(title)
        runs = (grep is None or grep_matches(grep, title)) and not (grep_invert and grep_matches(grep_invert, title))
        if runs:
            findings.append(f"{spec}:{line}: {match.group()} in a test the step runs: {title.strip()}")
    seen: set[Path] = set()
    todo = _relative_imports(path, src)
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        text = module.read_text(encoding="utf-8")
        for match in LEAD_STATE_WRITE.finditer(text):
            findings.append(f"{module.relative_to(frontend)}:{_line_of(text, match.start())}: {match.group()} in a module {spec} imports")
        if module.is_relative_to(frontend / "tests"):
            todo.extend(_relative_imports(module, text))
    return findings, sorted(set(writers))

