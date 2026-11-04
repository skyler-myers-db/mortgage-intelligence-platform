"""Where an App can be stopped, started, deployed or updated, and who may ask (attack O16).

A shell stop outside the window is easy to spot; one inside a Python tool is
not. This module is the reviewed map of both, and the scanners that keep it
true (``tests/unit/test_stop_primitive_ast_contract.py`` and
``tests/unit/test_quiesce_window_lexical_contract.py``):

* ``python_primitive_sites`` walks every module under ``tools/`` with ``ast``
  and returns each call ``<expr>.apps.<method>(...)`` whose method is an SDK
  App stop, start, deploy or update (``SDK_METHODS``, their ``*_and_wait``
  forms included), also through a name bound from ``<expr>.apps`` or
  ``getattr(<expr>, "apps")``. ``PRIMITIVE_SITES`` must equal that set exactly.
* ``reaching_entry_points`` follows a coarse static call graph (calls and
  function references through imports, nested functions folded into their
  top-level function) from each site back to the CLI modules whose ``main``
  reaches it. ``ENTRY_POINTS`` must equal that set exactly, and names each
  entry's mutating modes and the shell files allowed to invoke them.
* ``shell_violations`` reads shell text with continuation lines joined and
  reports every invocation of a mutating mode (``-m <module>`` or the
  ``tools/databricks/<module>.py`` path; the mode is read from that one
  command's own tokens, up to ``;``, ``&&``, ``|`` or a comment), every
  import of a stop module (a heredoc or ``-c`` can call it directly), every
  ``databricks apps stop|start`` and every inline SDK primitive outside its
  allowlisted files.

The window library and the EXIT-trap library are the only shell callers of a
stop. Everything is matched lexically, so a comment line never counts.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SDK_METHODS = frozenset(
    {
        "stop",
        "stop_and_wait",
        "start",
        "start_and_wait",
        "deploy",
        "deploy_and_wait",
        "update",
        "create_update",
        "create_update_and_wait",
    }
)
WINDOW_LIBRARY = "scripts/lib/deploy_quiesce_window_lifecycle.sh"
EXIT_TRAP_LIBRARY = "scripts/lib/deploy_app_release_lifecycle.sh"
STOP_LIBRARIES = frozenset({WINDOW_LIBRARY, EXIT_TRAP_LIBRARY})

# (module path, top-level function, SDK method): every reviewed SDK call site.
PRIMITIVE_SITES = frozenset(
    {
        ("tools/databricks/stop_app_fail_closed.py", "stop_app_fail_closed", "stop"),
        ("tools/databricks/app_start_deployment.py", "start_app", "start"),
        ("tools/databricks/app_deployment_repin.py", "redeploy_signed_blue", "deploy_and_wait"),
        (
            "tools/databricks/app_rollback_resource_contract.py",
            "restore_signed_app_resource_contract",
            "update",
        ),
    }
)


@dataclass(frozen=True)
class EntryPoint:
    """A CLI module that reaches a primitive: its mutating modes and their shell callers.

    ``modes`` maps a mode to whether it mutates. A positional mode is the first
    argument after the module; ``--flag`` modes are flags; ``(default)`` is an
    invocation without any listed flag.
    """

    modes: Mapping[str, bool]
    callers: frozenset[str] = STOP_LIBRARIES
    positional: bool = True


ENTRY_POINTS: Mapping[str, EntryPoint] = {
    "tools.databricks.stop_app_fail_closed": EntryPoint(
        modes={"(default)": True, "--assert-identity-only": False}, positional=False
    ),
    "tools.databricks.converge_lakebase_oauth_role": EntryPoint(
        modes={"--stop-app-for-mutation": True, "(default)": False},
        callers=frozenset({WINDOW_LIBRARY}),
        positional=False,
    ),
    "tools.databricks.app_deployment_rollback": EntryPoint(
        modes={"ensure": True, "restore": True, "repin": True, "capture": False, "inspect": False}
    ),
    "tools.databricks.app_deployment_rollback_cli": EntryPoint(
        modes={"ensure": True, "restore": True, "repin": True, "capture": False, "inspect": False}
    ),
    "tools.databricks.quiesce_window": EntryPoint(
        modes={
            "open": True,
            "start": True,
            "await": True,
            "applicable": False,
            "plan": False,
            "close": False,
            "status": False,
            "assert-serving": False,
        },
        callers=frozenset({WINDOW_LIBRARY}),
    ),
}
# The modules that hold a primitive site: importing one is a stop path.
SITE_MODULES = frozenset(
    ".".join(Path(path).with_suffix("").parts) for path, _function, _method in PRIMITIVE_SITES
)
# Shell commands that stop or start an App directly, and inline SDK calls.
_CLI_PRIMITIVE = re.compile(r"\bdatabricks\s+apps\s+(stop|start)\b")
_INLINE_SDK = re.compile(
    r"\.apps\.(" + "|".join(sorted(SDK_METHODS)) + r")\s*\(|\b(deploy|start|stop)_and_wait\s*\("
)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _is_apps_client(node: ast.AST | None) -> bool:
    """``<expr>.apps`` or ``getattr(<expr>, "apps")``: the SDK Apps client."""

    if isinstance(node, ast.Attribute):
        return node.attr == "apps"
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "apps"
    )


def _apps_aliases(tree: ast.AST) -> set[str]:
    """Names bound from the Apps client anywhere in ``tree`` (``apps = workspace.apps``)."""

    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr) and _is_apps_client(
            node.value
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            aliases.update(target.id for target in targets if isinstance(target, ast.Name))
    return aliases


def python_primitive_sites(root: Path | None = None) -> set[tuple[str, str, str]]:
    """Every SDK primitive call on the Apps client below ``root``, aliases included."""

    sites: set[tuple[str, str, str]] = set()
    for path in _python_files(root or REPO / "tools"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _apps_aliases(tree)
        for top in tree.body:
            for node in ast.walk(top):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in SDK_METHODS
                    and (
                        _is_apps_client(node.func.value)
                        or (isinstance(node.func.value, ast.Name) and node.func.value.id in aliases)
                    )
                ):
                    owner = (
                        top.name if isinstance(top, ast.FunctionDef | ast.ClassDef) else "<module>"
                    )
                    sites.add((str(path.relative_to(REPO)), owner, node.func.attr))
    return sites


def _imports(tree: ast.Module) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Name aliases (``from m import f as g``) and module aliases, anywhere in the module."""

    names: dict[str, tuple[str, str]] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tools"):
            for alias in node.names:
                target = f"{node.module}.{alias.name}"
                names[alias.asname or alias.name] = (node.module, alias.name)
                modules[alias.asname or alias.name] = target
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tools"):
                    modules[alias.asname or alias.name] = alias.name
    return names, modules


def call_graph(root: Path | None = None) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """(module, function) -> the (module, function) pairs it calls or references."""

    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for path in _python_files(root or REPO / "tools"):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names, modules = _imports(tree)
        local = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        for top in tree.body:
            if not isinstance(top, ast.FunctionDef):
                continue
            edges: set[tuple[str, str]] = set()
            for node in ast.walk(top):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id in names:
                        edges.add(names[node.id])
                    elif node.id in local:
                        edges.add((module, node.id))
                elif (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in modules
                ):
                    edges.add((modules[node.value.id], node.attr))
            graph[(module, top.name)] = edges
    return graph


def reaching_entry_points(root: Path | None = None) -> dict[tuple[str, str, str], set[str]]:
    """For each primitive site, the CLI modules whose ``main`` reaches it."""

    graph = call_graph(root)
    reverse: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for caller, callees in graph.items():
        for callee in callees:
            reverse.setdefault(callee, set()).add(caller)
    reached: dict[tuple[str, str, str], set[str]] = {}
    for site in python_primitive_sites(root):
        start = (_module_name(REPO / site[0]), site[1])
        seen, frontier = {start}, [start]
        while frontier:
            for caller in reverse.get(frontier.pop(), ()):
                if caller not in seen:
                    seen.add(caller)
                    frontier.append(caller)
        reached[site] = {module for module, function in seen if function == "main"}
    return reached


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Shell lines with ``\\`` continuations joined; comment lines dropped."""

    out: list[tuple[int, str]] = []
    buffer, start = "", 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not buffer:
            start = number
            if line.lstrip().startswith("#"):
                continue
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        out.append((start, buffer + line))
        buffer = ""
    if buffer:
        out.append((start, buffer))
    return out


def _command_tail(line: str, start: int) -> str:
    """The rest of one command from ``start``: up to ``;``, ``&``, ``|`` or a comment.

    Quotes are respected, so a separator inside a quoted argument does not
    end the command. Only that command's own tokens can name its mode.
    """

    quote, previous = "", " "
    for index in range(start, len(line)):
        char = line[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in ";&|\n" or (char == "#" and previous.isspace()):
            return line[start:index]
        previous = char
    return line[start:]


def _stop_modules() -> list[tuple[str, str, str, frozenset[str]]]:
    """(module, package, name, allowed shell files) for each module whose import stops."""

    rules = []
    for module in sorted({*ENTRY_POINTS, *SITE_MODULES}):
        package, _, name = module.rpartition(".")
        callers = ENTRY_POINTS[module].callers if module in ENTRY_POINTS else STOP_LIBRARIES
        rules.append((module, package, name, callers))
    return rules


def _mode(entry: EntryPoint, tail: str) -> str:
    tokens = tail.split()
    if entry.positional:
        for token in tokens:
            if not token.startswith("-"):
                return token.strip("'\"")
        return "(default)"
    flags = [mode for mode in entry.modes if mode.startswith("--") and mode in tokens]
    return flags[0] if flags else "(default)"


def shell_violations(path: str, text: str) -> list[str]:
    """Every stop primitive in ``text`` that ``path`` is not allowed to hold."""

    found: list[str] = []
    for number, line in logical_lines(text):
        for module, entry in ENTRY_POINTS.items():
            invocation = rf"(?:-m\s+{re.escape(module)}|{re.escape(module.replace('.', '/'))}\.py)"
            for match in re.finditer(rf"{invocation}(?![\w.])", line):
                mode = _mode(entry, _command_tail(line, match.end()))
                if entry.modes.get(mode, True) and path not in entry.callers:
                    found.append(f"{path}:{number}: {module} {mode}")
        for module, package, name, callers in _stop_modules():
            imported = rf"\b(?:from|import)\s+{re.escape(module)}(?![\w.])"
            from_package = rf"\bfrom\s+{re.escape(package)}\s+import\b[^#]*?\b{name}\b"
            if path not in callers and (re.search(imported, line) or re.search(from_package, line)):
                found.append(f"{path}:{number}: import {module}")
        if path not in STOP_LIBRARIES:
            for pattern in (_CLI_PRIMITIVE, _INLINE_SDK):
                for match in pattern.finditer(line):
                    found.append(f"{path}:{number}: {match.group(0).strip()}")
    # A parenthesized ``from <package> import (...)`` spans lines in a heredoc.
    for match in re.finditer(r"\bfrom\s+(tools(?:\.\w+)*)\s+import\s*\(([^)]*)\)", text):
        names = set(re.findall(r"\w+", match.group(2)))
        for module, package, name, callers in _stop_modules():
            if package == match.group(1) and name in names and path not in callers:
                number = text.count("\n", 0, match.start()) + 1
                found.append(f"{path}:{number}: import {module}")
    return found


def shell_files(root: Path | None = None) -> list[Path]:
    base = root or REPO
    return sorted([base / "scripts" / "deploy.sh", *(base / "scripts").glob("lib/*.sh")])


def repo_shell_violations(
    files: Iterable[Path] | None = None, root: Path | None = None
) -> list[str]:
    base = root or REPO
    violations: list[str] = []
    for path in files or shell_files(base):
        violations.extend(
            shell_violations(str(path.relative_to(base)), path.read_text(encoding="utf-8"))
        )
    return violations
