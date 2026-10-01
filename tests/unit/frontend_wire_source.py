"""TypeScript source scanning for the frontend wire-contract parser.

The lexical half of tests/unit/frontend_wire_contract.py: per-character
classification (code, comment, string, template, regex), bracket matching,
generic and call argument parsing, and the module resolver that follows
imports and ``export ... from`` re-export chains to a type's declaring
module. Pure Python; no Node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
EXTENSIONS = (".ts", ".tsx")
REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%<>~^")
REGEX_KEYWORDS = frozenset({"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "await", "yield"})


class Unresolvable(ValueError):
    """A path or type argument the static rules cannot resolve."""


def rel_path(path: Path) -> str:
    """Repo-relative POSIX path; a planted module outside the repo keeps its absolute path."""

    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# --------------------------------------------------------------------------
# Source scanning: comments, strings, templates and regex literals.
# --------------------------------------------------------------------------


def classify(text: str) -> list[str]:
    """Per-character kind: c code, m comment, s string, t template text, r regex.

    The ``${`` / ``}`` delimiters of a template are template text; the code
    inside them is code, so nested templates and braces balance correctly.
    """

    kinds = ["c"] * len(text)
    stack: list[int] = []
    mode = "code"
    last = ""
    i, n = 0, len(text)

    def mark(start: int, end: int, kind: str) -> None:
        for k in range(start, min(end, n)):
            kinds[k] = kind

    while i < n:
        ch = text[i]
        if mode == "template":
            if ch == "\\":
                mark(i, i + 2, "t")
                i += 2
            elif ch == "`":
                kinds[i] = "t"
                mode = "code"
                last = "x"
                i += 1
            elif text.startswith("${", i):
                mark(i, i + 2, "t")
                stack.append(0)
                mode = "code"
                last = "("
                i += 2
            else:
                kinds[i] = "t"
                i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            mark(i, end, "m")
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            mark(i, end, "m")
            i = end
            continue
        if ch in "'\"":
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            mark(i, j + 1, "s")
            i = j + 1
            last = "x"
            continue
        if ch == "`":
            kinds[i] = "t"
            mode = "template"
            i += 1
            continue
        if ch == "/" and (last == "" or last in REGEX_PRECEDERS or last in REGEX_KEYWORDS):
            j = i + 1
            in_class = False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and (text[j].isalnum()):
                j += 1
            mark(i, j, "r")
            i = j
            last = "x"
            continue
        if ch == "{" and stack:
            stack[-1] += 1
        elif ch == "}" and stack:
            if stack[-1] == 0:
                stack.pop()
                kinds[i] = "t"
                mode = "template"
                i += 1
                continue
            stack[-1] -= 1
        if ch.isalnum() or ch in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last = text[i:j] if text[i:j] in REGEX_KEYWORDS else "x"
            i = j
            continue
        if not ch.isspace():
            last = ch if ch not in ")]" else "x"
        i += 1
    return kinds


def code_only(text: str, kinds: list[str]) -> str:
    """The text with comments blanked (newlines kept), so offsets still match."""

    return "".join(" " if kind == "m" and ch != "\n" else ch for ch, kind in zip(text, kinds, strict=True))


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def match_bracket(text: str, kinds: list[str], start: int, pairs: str = "(){}[]") -> int:
    """Index of the bracket closing the one at ``start`` (code characters only)."""

    opens = pairs[0::2]
    closes = pairs[1::2]
    depth = 0
    for k in range(start, len(text)):
        if kinds[k] != "c":
            continue
        if text[k] in opens:
            depth += 1
        elif text[k] in closes:
            depth -= 1
            if depth == 0:
                return k
    raise Unresolvable(f"unbalanced bracket at offset {start}")


def parse_type_args(text: str, kinds: list[str], lt: int) -> tuple[list[str], int]:
    """The comma-separated type arguments of the ``<`` at ``lt`` and the index after ``>``."""

    depth = 0
    begin = lt + 1
    args: list[str] = []
    k = lt
    while k < len(text):
        if kinds[k] == "c":
            ch = text[k]
            if ch in "<([{":
                depth += 1
            elif ch == ">" and not (k > 0 and text[k - 1] == "="):
                depth -= 1
                if depth == 0:
                    args.append(text[begin:k].strip())
                    return args, k + 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 1:
                args.append(text[begin:k].strip())
                begin = k + 1
        k += 1
    raise Unresolvable("unterminated type argument list")


def parse_call_args(text: str, kinds: list[str], lparen: int) -> tuple[list[str], int]:
    """The top-level arguments of the call whose ``(`` is at ``lparen``."""

    rparen = match_bracket(text, kinds, lparen)
    depth = 0
    begin = lparen + 1
    args: list[str] = []
    for k in range(lparen + 1, rparen):
        if kinds[k] != "c":
            continue
        ch = text[k]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(text[begin:k].strip())
            begin = k + 1
    tail = text[begin:rparen].strip()
    if tail:
        args.append(tail)
    return args, rparen + 1


# --------------------------------------------------------------------------
# Modules: declarations, imports, re-export chains.
# --------------------------------------------------------------------------

_IMPORT = re.compile(r"\bimport\s+(type\s+)?\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
_REEXPORT_NAMED = re.compile(r"\bexport\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
_REEXPORT_STAR = re.compile(r"\bexport\s+(?:type\s+)?\*\s+from\s*['\"]([^'\"]+)['\"]")
_DECL = re.compile(r"(?m)^\s*(export\s+)?(?:declare\s+)?(?:interface|type)\s+([A-Za-z_$][\w$]*)\b")
_CONST_PATH = re.compile(r"(?m)^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*'(/[^']*)'\s*;")


@dataclass
class Module:
    path: Path
    text: str
    kinds: list[str]
    code: str

    @classmethod
    def load(cls, path: Path) -> Module:
        text = path.read_text(encoding="utf-8")
        kinds = classify(text)
        return cls(path, text, kinds, code_only(text, kinds))

    @property
    def rel(self) -> str:
        return rel_path(self.path)

    def declarations(self) -> dict[str, bool]:
        """Every interface/type declared at the start of a line: name -> exported."""

        return {m.group(2): bool(m.group(1)) for m in _DECL.finditer(self.code)}

    def imports(self) -> dict[str, tuple[str, str]]:
        """Local name -> (specifier, imported name), for named imports."""

        found: dict[str, tuple[str, str]] = {}
        for m in _IMPORT.finditer(self.code):
            for item in m.group(2).split(","):
                item = item.strip()
                if not item:
                    continue
                item = re.sub(r"^type\s+", "", item)
                imported, _, local = item.partition(" as ")
                found[(local or imported).strip()] = (m.group(3), imported.strip())
        return found

    def path_consts(self) -> dict[str, str]:
        return {m.group(1): m.group(2) for m in _CONST_PATH.finditer(self.code)}


class Project:
    """Module cache plus the import / re-export resolver."""

    def __init__(self, src: Path = SRC) -> None:
        self.src = src
        self._modules: dict[Path, Module] = {}

    def module(self, path: Path) -> Module:
        if path not in self._modules:
            self._modules[path] = Module.load(path)
        return self._modules[path]

    def resolve_specifier(self, origin: Path, specifier: str) -> Path | None:
        if not specifier.startswith("."):
            return None
        base = (origin.parent / specifier).resolve()
        candidates = [base] if base.suffix in EXTENSIONS else []
        candidates += [base.with_name(base.name + ext) for ext in EXTENSIONS]
        candidates += [base / f"index{ext}" for ext in EXTENSIONS]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def find_declaration(self, path: Path, name: str, seen: frozenset[Path] = frozenset()) -> tuple[Path, str] | None:
        """Follow ``export {X} from``, ``export type * from`` and ``export * from`` to X's declaring module."""

        if path in seen:
            return None
        module = self.module(path)
        if name in module.declarations():
            return path, name
        seen = seen | {path}
        for m in _REEXPORT_NAMED.finditer(module.code):
            for item in m.group(1).split(","):
                item = re.sub(r"^type\s+", "", item.strip())
                if not item:
                    continue
                imported, _, exported = item.partition(" as ")
                if (exported or imported).strip() == name:
                    target = self.resolve_specifier(path, m.group(2))
                    if target is not None:
                        return self.find_declaration(target, imported.strip(), seen)
        for m in _REEXPORT_STAR.finditer(module.code):
            target = self.resolve_specifier(path, m.group(1))
            if target is not None:
                found = self.find_declaration(target, name, seen)
                if found is not None:
                    return found
        via_import = module.imports().get(name)
        if via_import is not None and re.search(rf"\bexport\s+(?:type\s+)?\{{[^}}]*\b{re.escape(name)}\b", module.code):
            target = self.resolve_specifier(path, via_import[0])
            if target is not None:
                return self.find_declaration(target, via_import[1], seen)
        return None

    def resolve_type(self, path: Path, name: str) -> tuple[Path, str] | None:
        """The declaring (module, name) of ``name`` as used inside ``path``."""

        module = self.module(path)
        if name in module.declarations():
            return path, name
        imported = module.imports().get(name)
        if imported is None:
            return None
        target = self.resolve_specifier(path, imported[0])
        if target is None:
            return None
        return self.find_declaration(target, imported[1])

    def resolve_const(self, path: Path, name: str) -> str | None:
        """A module-level ``const NAME = '/path'`` in ``path`` or imported by name."""

        module = self.module(path)
        local = module.path_consts().get(name)
        if local is not None:
            return local
        imported = module.imports().get(name)
        if imported is None:
            return None
        target = self.resolve_specifier(path, imported[0])
        return None if target is None else self.module(target).path_consts().get(imported[1])
