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
  only from deselected tests and such helpers. A helper that writes and no
  test reaches is a finding (pytest or a string may call it), and so is one
  pytest runs by name (xunit setup/teardown, a module hook, a dunder). A
  ``pytest_plugins`` entry is an import; one this scan cannot read is a
  finding.
* ``spec_findings`` covers a Playwright spec: a path may sit only inside a
  ``test(...)`` call the step's ``--grep``/``--grep-invert`` leaves out,
  matched the way Playwright matches, against ``" <project> <file>
  <describe...> <title>"``. A relative import that holds one is a finding,
  and so is one that resolves to no file. Imports are read from the code's
  tokens, so a comment or a string that names a module is no import.

A UI control that posts one of these paths is not a literal in the spec;
nightly's specs drive none today, and the docs list what they write.
"""

from __future__ import annotations

import ast
import functools
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
# The top-level functions pytest runs by name in a test module (_pytest/python.py: the xunit
# setup/teardown, the module's own hooks), and a dunder such as a module __getattr__ that
# collection's attribute lookups call.
_RUN_BY_NAME = re.compile(r"setUpModule|setup_module|tearDownModule|teardown_module|setup_function|teardown_function|pytest_\w+|__\w+__")


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


# ---------------------------------------------------------------- pytest suites


def _module_path(root: Path, module: str) -> Path | None:
    base = root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _plugin_specs(tree: ast.Module) -> tuple[list[str], list[int]]:
    """(the modules a ``pytest_plugins`` assignment names, the lines of one this scan cannot read).

    pytest imports each name in a list, a tuple or a comma-separated string.
    """

    names: list[str] = []
    unreadable: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign | ast.AugAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "pytest_plugins" for target in targets):
            continue
        items = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
        strings = [item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        if len(strings) != len(items):
            unreadable.append(node.lineno)
        names += [name.strip() for text in strings for name in text.split(",") if name.strip()]
    return names, unreadable


@functools.cache
def _python_names(package: str, source: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]:
    """(the modules ``source`` imports or lists in ``pytest_plugins``, the tools scripts it names, the
    lines of a ``pytest_plugins`` it cannot read): from the text alone, so a cached answer is never stale."""

    tree = ast.parse(source)
    names, unreadable = _plugin_specs(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*parts, *([base] if base else [])])
            names += [base, *(f"{base}.{alias.name}" for alias in node.names)]
    return tuple(names), tuple(sorted(_tool_path_literals(tree))), tuple(unreadable)


def _names(root: Path, path: Path, source: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]:
    return _python_names(".".join(path.relative_to(root).with_suffix("").parts[:-1]), source)


def _imported(root: Path, path: Path, source: str, prefixes: tuple[str, ...] = ("tests.", "tools.")) -> set[Path]:
    """The repo modules ``source`` imports (any depth, relative imports and ``pytest_plugins`` included) under ``prefixes``."""

    return {module for name in _names(root, path, source)[0] if name.startswith(prefixes) and (module := _module_path(root, name)) is not None}


def _scripts(root: Path, path: Path, source: str) -> list[Path]:
    """The tools scripts a Python module names by path that are files (a child process runs one)."""

    return [root / name for name in _names(root, path, source)[1] if (root / name).is_file()]


# ---------------------------------------------------------------- what a file runs or loads


# A tools script named by path, and a tools module run with ``-m``.
TOOL_PATH = re.compile(r"(?<![\w.-])tools/[\w./-]*\.(?:py|sh)(?![\w.-])")
TOOL_MODULE = re.compile(r"(?<![\w-])-m\s+(tools(?:\.\w+)+)")
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def split_heredocs(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(the shell lines outside here-document bodies, (opening line, body) of each ``<<WORD`` one)."""

    lines = text.splitlines()
    outside: list[str] = []
    documents: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        outside.append(lines[i])
        match = _HEREDOC.search(lines[i])
        if match is None:
            i += 1
            continue
        end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == match[2]), len(lines))
        documents.append((lines[i], "\n".join(lines[i + 1 : end])))
        i = end + 1
    return outside, documents


def heredoc_modules(root: Path, body: str) -> set[Path] | None:
    """The tests/tools modules a here-document imports, or None when it does not parse as Python."""

    try:
        return _imported(root, root / "heredoc.py", body)
    except SyntaxError:
        return None


def _tool_path_literals(tree: ast.Module) -> set[str]:
    """The tools scripts a module names: a string, or strings joined by ``/`` (``REPO / "tools" / "x.py"``)."""

    found: set[str] = set()
    for node in ast.walk(tree):
        parts: list[str] = []
        each = node
        while isinstance(each, ast.BinOp) and isinstance(each.op, ast.Div) and isinstance(each.right, ast.Constant) and isinstance(each.right.value, str):
            parts.insert(0, each.right.value)
            each = each.left
        if isinstance(each, ast.Constant) and isinstance(each.value, str):
            parts.insert(0, each.value)
        if TOOL_PATH.fullmatch("/".join(parts)):
            found.add("/".join(parts))
    return found


# A script node runs loads its relative imports. An app module it reaches is read, not followed, as for a spec:
# the app's own API client names every write path.
_JS = frozenset({".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx", ".jsx"})
_JS_UNFOLLOWED = ("frontend/src/",)


def _js_imports(root: Path, path: Path, text: str) -> tuple[list[Path], list[str]]:
    """(the repo files a node script's relative imports load, findings: one that resolves nowhere, or source it cannot lex)."""

    if path.relative_to(root).as_posix().startswith(_JS_UNFOLLOWED):
        return [], []
    where = path.relative_to(root)
    try:
        found, unresolved = _relative_imports(path, text)
    except LexError as error:
        return [], [f"{where}: source this scan cannot lex ({error})"]
    inside = [each for each in found if each.is_relative_to(root)]
    problems = [f"{where}: imports {name}, which resolves to no file this scan can read" for name in unresolved]
    problems += [f"{where}: imports {each}, outside the repo" for each in found if not each.is_relative_to(root)]
    return inside, problems


def loaded_files(root: Path, paths: Iterable[Path]) -> set[Path]:
    """These files and, transitively, every repo file they run or load.

    A Python module loads what it imports (``pytest_plugins`` included) and the
    tools scripts it names by path (a child process runs one); a node script
    loads its relative imports and the tools scripts it names; a shell script
    loads the tools scripts it names (run or sourced), the tools modules it
    runs with ``-m`` and what a here-document that parses as Python imports.
    """

    seen: set[Path] = set()
    todo = list(paths)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            todo.extend(_imported(root, path, text))
            todo.extend(_scripts(root, path, text))
        elif path.suffix in _JS:
            todo.extend(root / name for name in sorted(set(TOOL_PATH.findall(text))) if (root / name).is_file())
            todo.extend(_js_imports(root, path, text)[0])
        else:
            todo.extend(root / name for name in sorted(set(TOOL_PATH.findall(text))) if (root / name).is_file())
            todo.extend(module for name in TOOL_MODULE.findall(text) if (module := _module_path(root, name)) is not None)
            for _opening, body in split_heredocs(text)[1]:
                todo.extend(heredoc_modules(root, body) or ())
    return seen


def whole_module_findings(root: Path, paths: Iterable[Path]) -> list[str]:
    """Every write path in these files and, transitively, the repo files they run or load."""

    findings: list[str] = []
    for path in loaded_files(root, paths):
        source = path.read_text(encoding="utf-8")
        for match in LEAD_STATE_WRITE.finditer(source):
            findings.append(f"{path.relative_to(root)}:{_line_of(source, match.start())}: {match.group()} in a file nightly loads")
        if path.suffix == ".py":
            findings += _unreadable_plugin_findings(root, path, source)
        elif path.suffix in _JS:
            findings += _js_imports(root, path, source)[1]
    return sorted(findings)


def _unreadable_plugin_findings(root: Path, path: Path, source: str) -> list[str]:
    return [f"{path.relative_to(root)}:{line}: a pytest_plugins this scan cannot read" for line in _names(root, path, source)[2]]


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
    # What the tests reach by reference; anything else that writes runs only if pytest (or a string) names it.
    edges: dict[str, set[str]] = {}
    for name, line in references:
        if (holder := owner(line)) is not None:
            edges.setdefault(holder, set()).add(name)
    reached = {name for name in functions if name.startswith("test")}
    todo = sorted(reached)
    while todo:
        for name in edges.get(todo.pop(), set()) - reached:
            reached.add(name)
            todo.append(name)
    for name in sorted(writers):
        node = functions[name]
        if name.startswith("test"):
            if name not in deselected:
                findings.append(f"{suite}::{name} writes and is not deselected")
        elif node.decorator_list:
            findings.append(f"{suite}:{node.lineno}: {name} writes and is decorated (a fixture runs without a call)")
        elif _RUN_BY_NAME.fullmatch(name):
            findings.append(f"{suite}:{node.lineno}: {name} writes and pytest runs it by name")
        elif name not in reached:
            findings.append(f"{suite}:{node.lineno}: {name} writes and no test reaches it")
    findings += _unreadable_plugin_findings(root, path, source)
    findings += whole_module_findings(root, [*_imported(root, path, source), *_scripts(root, path, source), *_conftests(root, path)])
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


# A specifier names the file itself, the file with an extension appended (``analytics.lib`` is
# analytics.lib.ts, never analytics.tsx), or a directory's index; a ``.js`` one its TypeScript twin.
_APPENDED = ("", ".ts", ".tsx", "/index.ts", "/index.tsx")
_TS_TWINS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


def _specifiers(src: str) -> list[str]:
    """The relative specifiers ``src`` imports, from its code tokens: a comment or a string's text is no import.

    ``import 'x'``, ``import(...)``, ``from 'x'`` (``import ... from``, ``export ... from``) and
    ``require('x')``, not a member such as ``Array.from('x')``. A template specifier is kept as
    written, so one that builds its path at run time (``${...}``) resolves to no file.
    """

    tokens, _end = _lex(src)
    found: list[str] = []
    for k, token in enumerate(tokens):
        if token.kind != "ident" or token.value not in ("import", "from", "require") or (k and tokens[k - 1].value == "."):
            continue
        j = k + 1
        if token.value != "from" and j < len(tokens) and tokens[j].value == "(":
            j += 1
        elif token.value == "require":
            continue
        if j < len(tokens) and tokens[j].kind in ("string", "template") and tokens[j].value.startswith(("./", "../")):
            found.append(tokens[j].value)
    return found


def _relative_imports(path: Path, src: str) -> tuple[list[Path], list[str]]:
    """(the files ``src``'s relative imports resolve to, the specifiers that resolve to no file)."""

    found: list[Path] = []
    unresolved: list[str] = []
    for module in _specifiers(src):
        base = (path.parent / module).resolve()
        candidates = [Path(f"{base}{suffix}") for suffix in _APPENDED] + [base.with_suffix(twin) for twin in _TS_TWINS.get(base.suffix, ())]
        resolved = next((candidate for candidate in candidates if candidate.is_file()), None)
        if resolved is None:
            unresolved.append(module)
        else:
            found.append(resolved)
    return found, unresolved


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
    todo, unresolved = _relative_imports(path, src)
    findings += [f"{spec}: imports {name}, which resolves to no file this scan can read" for name in unresolved]
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        text = module.read_text(encoding="utf-8")
        for match in LEAD_STATE_WRITE.finditer(text):
            findings.append(f"{module.relative_to(frontend)}:{_line_of(text, match.start())}: {match.group()} in a module {spec} imports")
        if module.is_relative_to(frontend / "tests"):
            try:
                more, unresolved = _relative_imports(module, text)
            except LexError as error:
                findings.append(f"{module.relative_to(frontend)}: source this scan cannot lex ({error})")
                continue
            todo.extend(more)
            findings += [f"{module.relative_to(frontend)}: imports {name}, which resolves to no file this scan can read" for name in unresolved]
    return findings, sorted(set(writers))

