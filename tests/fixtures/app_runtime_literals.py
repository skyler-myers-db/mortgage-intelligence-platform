"""The second net: string literals in the App's modules that name a repo path.

The flow scan (tests/fixtures/app_runtime_reads.py) follows path values and can
still miss a flow nobody taught it. This sweep follows nothing. In every module
the flow scan parsed (backend/ and the first-party modules it imports), it
flags each string constant, and the constant head of each f-string, whose
first segment is a tracked repo top-level name other than ``backend``
(``docs``, ``sql``, ``genie``, ``uv.lock``, ...) and that sits in a path
context:

* the text contains ``/`` (``"docs/a.md"``, ``f"docs/b_{n}.md"``), or
* it is an operand of ``/`` or ``/=``, an argument of ``Path(...)``, ``open(...)``,
  ``os.path.join(...)``, ``joinpath(...)``, ``glob``/``rglob``, or the default
  of ``os.environ.get``/``os.getenv``.

Docstrings and other bare string statements are skipped. A flagged literal is
extended through the constant segments joined after it, so ``ROOT / "frontend"
/ "dist"`` is ``frontend/dist``; a non-constant tail, an f-string or a glob
makes it a subtree (``docs/**``). tests/unit/test_app_runtime_closure_reads.py
requires each one to be covered by the closure or listed, with a reason, in
its reviewed allowlist.
"""

from __future__ import annotations

import ast
import posixpath
import subprocess
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

_PATH_CALLS = frozenset({"Path", "PurePath", "PosixPath", "PurePosixPath", "open", "joinpath",
                         "glob", "rglob"})
_ENV_CALLS = frozenset({"environ.get", "getenv", "environ.setdefault"})
_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class PathLiteral:
    origin: str  # file:line
    path: str  # repo-relative, normalised
    subtree: bool

    def key(self) -> str:
        return f"{self.path}/**" if self.subtree else self.path


def top_level_names(root: Path) -> frozenset[str]:
    """Tracked top-level names (git), else whatever ``root`` holds."""

    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        listing = ""
    names = {name.split("/")[0] for name in listing.split("\0") if name}
    return frozenset(names or (path.name for path in root.iterdir()))


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _callee(call: ast.Call) -> str:
    return _dotted(call.func)


def _is_path_call(call: ast.Call) -> bool:
    dotted = _callee(call)
    return dotted.rsplit(".", 1)[-1] in _PATH_CALLS or dotted.endswith("path.join")


def _env_default(call: ast.Call, node: ast.AST) -> bool:
    if not any(_callee(call).endswith(name) for name in _ENV_CALLS):
        return False
    return (len(call.args) >= 2 and call.args[1] is node) or any(
        kw.arg == "default" and kw.value is node for kw in call.keywords
    )


class _Sweep:
    def __init__(self, relfile: str, tree: ast.Module, top_levels: frozenset[str]) -> None:
        self.file, self.tree, self.top_levels = relfile, tree, top_levels
        self.parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node

    def _in_path_context(self, node: ast.AST, text: str) -> bool:
        if "/" in text:
            return True
        parent = self.parents.get(id(node))
        if isinstance(parent, ast.keyword):
            call = self.parents.get(id(parent))
            return isinstance(call, ast.Call) and _env_default(call, node)
        if isinstance(parent, ast.BinOp | ast.AugAssign):
            return isinstance(parent.op, ast.Div)
        if isinstance(parent, ast.Call) and node is not parent.func:
            return _is_path_call(parent) or _env_default(parent, node)
        return False

    def _extend(self, node: ast.AST, path: str) -> tuple[str, bool]:
        """Follow the constant segments joined after ``node``; True: a dynamic tail."""

        current = node
        while True:
            parent = self.parents.get(id(current))
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
                if parent.left is current:
                    right = parent.right
                    if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
                        return path, True
                    path = posixpath.join(path, right.value)
                current = parent  # the literal joined on the right: continue from the join
                continue
            if isinstance(parent, ast.Call) and _is_path_call(parent) and current in parent.args:
                name = _callee(parent).rsplit(".", 1)[-1]
                if name in ("glob", "rglob"):
                    return path, True
                tail = parent.args[parent.args.index(current) + 1 :]
                values = [a.value for a in tail
                          if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if len(values) != len(tail):
                    return path, True
                path = posixpath.join(path, *values)
                current = parent
                continue
            return path, False

    def literals(self) -> Iterator[PathLiteral]:
        for node in ast.walk(self.tree):
            text, dynamic = self._text(node)
            if text is None:
                continue
            text = text.removeprefix("./")
            if text.split("/")[0] not in self.top_levels or not self._in_path_context(node, text):
                continue
            prefix = text
            if dynamic or _GLOB_CHARS & set(text):
                prefix = posixpath.dirname(text.split("*")[0].split("?")[0].split("[")[0])
                dynamic = True
            path, tail = self._extend(node, posixpath.normpath(prefix or "."))
            yield PathLiteral(f"{self.file}:{getattr(node, 'lineno', 0)}", posixpath.normpath(path),
                              dynamic or tail)

    def _text(self, node: ast.AST) -> tuple[str | None, bool]:
        parent = self.parents.get(id(node))
        if isinstance(node, ast.JoinedStr):
            head = node.values[0] if node.values else None
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                return head.value, True
            return None, False
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            return None, False
        if isinstance(parent, ast.Expr | ast.JoinedStr | ast.FormattedValue):
            return None, False  # a docstring or bare string, or part of an f-string
        return node.value, False


def sweep(
    trees: Mapping[str, ast.Module], top_levels: Iterable[str], package: str = "backend"
) -> list[PathLiteral]:
    """Every path literal in ``trees`` under a top-level name other than ``package``."""

    names = frozenset(top_levels) - {package}
    found: list[PathLiteral] = []
    for relfile, tree in sorted(trees.items()):
        found.extend(_Sweep(relfile, tree, names).literals())
    return found
