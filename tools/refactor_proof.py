#!/usr/bin/env python3
"""Pure-move proofs for file splits.

The file-size gate (tools/check_file_sizes.py) forces oversized files to be
split. A split is only reviewable when the mover can prove nothing changed
except location. This tool records that proof three ways; each subcommand
exits 0 only when the move is verbatim.

  defset PRE POST [POST ...]
      Compare every top-level definition of PRE (functions, classes,
      assignments) against the union of the POST files. A pure move reports
      no removed and no text-changed definitions. ``--flatten-classes``
      compares class members by bare name so methods moved into mixins still
      match. ``--allow-changed NAME`` documents an intentional edit.

  branches PRE_FILE:FUNC POST_FILE:FUNC --modules FILE [FILE ...]
      Prove a branch extraction: FUNC in POST_FILE must keep PRE's statement
      skeleton verbatim, and every top-level ``if`` whose body became
      ``return _helper(...)`` must have a helper whose body (after the
      docstring and ``name = ctx.name`` prologue) is the original branch body
      verbatim. Also checks each helper body for names it can no longer see.

  css ORIGINAL ENTRY
      Prove a CSS slicing: expanding ENTRY's ``@import`` lines in place must
      reproduce ORIGINAL byte for byte, and the class-selector inventory of
      the slices must equal ORIGINAL's.

Usage from a repo root, comparing against the pre-split blob::

    git show HEAD:backend/services/foo.py > /tmp/foo_pre.py
    python tools/refactor_proof.py defset /tmp/foo_pre.py \
        backend/services/foo.py backend/services/foo_bar.py
"""

from __future__ import annotations

import argparse
import ast
import builtins
import difflib
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

Definition = tuple[str, str]  # (name, normalized source text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in textwrap.dedent(text).strip("\n").splitlines())


def _node_source(lines: list[str], node: ast.AST) -> str:
    start = node.lineno
    decorators = getattr(node, "decorator_list", None)
    if decorators:
        start = min(start, *(d.lineno for d in decorators))
    return "\n".join(lines[start - 1 : node.end_lineno])


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        names: list[str] = []
        for target in node.targets:
            names.extend(_target_names_from(target))
        return names
    if isinstance(node, ast.AnnAssign):
        return _target_names_from(node.target)
    return []


def _target_names_from(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple | ast.List):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_names_from(elt))
        return names
    if isinstance(target, ast.Attribute):
        return [ast.unparse(target)]
    return []


def _definitions(path: Path, *, flatten_classes: bool) -> tuple[list[Definition], int]:
    """Return (named definitions in file order, count of unnamed statements)."""
    source = _read(path)
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    defs: list[Definition] = []
    unnamed = 0
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # module docstring / bare string
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            defs.append((node.name, _normalize(_node_source(lines, node))))
        elif isinstance(node, ast.ClassDef):
            if flatten_classes:
                defs.extend(_class_members(lines, node))
            else:
                defs.append((node.name, _normalize(_node_source(lines, node))))
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            text = _normalize(_node_source(lines, node))
            for name in _target_names(node):
                defs.append((name, text))
        else:
            unnamed += 1
    return defs, unnamed


def _class_members(lines: list[str], node: ast.ClassDef) -> list[Definition]:
    members: list[Definition] = []
    for child in node.body:
        if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
            continue
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            members.append((f"{child.name}", _normalize(_node_source(lines, child))))
        elif isinstance(child, ast.Assign | ast.AnnAssign):
            text = _normalize(_node_source(lines, child))
            for name in _target_names(child):
                members.append((name, text))
        elif isinstance(child, ast.ClassDef):
            members.append((child.name, _normalize(_node_source(lines, child))))
    return members


def _group(defs: list[Definition]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for name, text in defs:
        grouped.setdefault(name, []).append(text)
    return grouped


def cmd_defset(args: argparse.Namespace) -> int:
    pre_defs, pre_unnamed = _definitions(args.pre, flatten_classes=args.flatten_classes)
    post_defs: list[Definition] = []
    post_unnamed = 0
    for path in args.post:
        defs, unnamed = _definitions(path, flatten_classes=args.flatten_classes)
        post_defs.extend(defs)
        post_unnamed += unnamed
    pre, post = _group(pre_defs), _group(post_defs)
    allow_changed = set(args.allow_changed or [])

    removed = sorted(name for name in pre if name not in post)
    added = sorted(name for name in post if name not in pre)
    changed: list[str] = []
    for name in sorted(set(pre) & set(post)):
        if pre[name] != post[name]:
            changed.append(name)

    print(f"pre: {len(pre_defs)} definitions ({len(pre)} names, {pre_unnamed} unnamed statements)")
    print(
        f"post: {len(post_defs)} definitions ({len(post)} names, {post_unnamed} unnamed statements)"
        f" across {len(args.post)} file(s)"
    )
    print(f"removed: {', '.join(removed) or 'none'}")
    print(f"added: {', '.join(added) or 'none'}")
    print(f"text-changed: {', '.join(changed) or 'none'}")
    for name in changed:
        marker = " (allowed)" if name in allow_changed else ""
        print(f"--- {name}{marker}")
        before = "\n".join(pre[name]).splitlines()
        after = "\n".join(post[name]).splitlines()
        for line in difflib.unified_diff(before, after, "pre", "post", lineterm="", n=1):
            print(line)
    failures = removed + [name for name in changed if name not in allow_changed]
    if args.strict and added:
        failures.extend(added)
    if failures:
        print(f"defset proof FAILED: {len(failures)} definition(s) differ", file=sys.stderr)
        return 1
    print("defset proof OK")
    return 0


# --------------------------------------------------------------------------
# branch extraction


@dataclass
class _Function:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    lines: list[str]
    module_names: set[str]
    path: Path


def _load_function(spec: str) -> _Function:
    file_part, _, func_name = spec.rpartition(":")
    path = Path(file_part)
    source = _read(path)
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == func_name:
            return _Function(node, source.splitlines(), _module_names(tree), path)
    raise SystemExit(f"{spec}: function not found")


def _module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            names.update(_target_names(node))
    return names


def _module_functions(paths: list[Path]) -> dict[str, _Function]:
    found: dict[str, _Function] = {}
    for path in paths:
        source = _read(path)
        tree = ast.parse(source, filename=str(path))
        module_names = _module_names(tree)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[node.name] = _Function(node, source.splitlines(), module_names, path)
    return found


def _extracted_call(stmt: ast.stmt) -> str | None:
    """Return the helper name when ``stmt`` is ``if ...: return _helper(...)``."""
    if not isinstance(stmt, ast.If) or stmt.orelse or len(stmt.body) != 1:
        return None
    ret = stmt.body[0]
    if not isinstance(ret, ast.Return) or not isinstance(ret.value, ast.Call):
        return None
    if isinstance(ret.value.func, ast.Name):
        return ret.value.func.id
    return None


def _strip_prologue(func: _Function) -> tuple[list[ast.stmt], set[str]]:
    body = list(func.node.body)
    provided: set[str] = {a.arg for a in func.node.args.args}
    provided.update(a.arg for a in func.node.args.kwonlyargs)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    while body:
        stmt = body[0]
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Attribute)
            and isinstance(stmt.value.value, ast.Name)
            and stmt.value.value.id == "ctx"
            and stmt.value.attr == stmt.targets[0].id
        ):
            provided.add(stmt.targets[0].id)
            body = body[1:]
            continue
        break
    return body, provided


def _body_text(lines: list[str], stmts: list[ast.stmt]) -> str:
    if not stmts:
        return ""
    start = stmts[0].lineno
    decorators = getattr(stmts[0], "decorator_list", None)
    if decorators:
        start = min(start, *(d.lineno for d in decorators))
    return _normalize("\n".join(lines[start - 1 : stmts[-1].end_lineno]))


class _NameUse(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.stored: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.loaded.add(node.id)
        else:
            self.stored.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stored.add(node.name)
        for arg in node.args.args + node.args.kwonlyargs:
            self.stored.add(arg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.stored.add(node.name)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.stored.update(_target_names_from(node.target))
        self.generic_visit(node)


def _unbound_names(stmts: list[ast.stmt], provided: set[str], module_names: set[str]) -> set[str]:
    usage = _NameUse()
    for stmt in stmts:
        usage.visit(stmt)
    known = provided | module_names | set(dir(builtins)) | usage.stored
    return {name for name in usage.loaded if name not in known}


def cmd_branches(args: argparse.Namespace) -> int:
    pre = _load_function(args.pre)
    post = _load_function(args.post)
    helpers = _module_functions([Path(p) for p in args.modules])
    pre_names = _module_names(ast.parse(_read(pre.path)))
    failures: list[str] = []
    extracted = 0

    pre_body = pre.node.body
    # The dispatcher may add ``ctx = ...`` construction statements; every
    # other statement must pair with a pre statement in order.
    ctx_statements = 0
    post_body: list[ast.stmt] = []
    for stmt in post.node.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "ctx"
        ):
            ctx_statements += 1
            continue
        post_body.append(stmt)
    if len(pre_body) != len(post_body):
        failures.append(
            f"statement count differs: pre {len(pre_body)} vs post {len(post_body)}"
            f" (after ignoring {ctx_statements} ctx construction statement(s))"
        )
    for index, (before, after) in enumerate(zip(pre_body, post_body, strict=False)):
        helper_name = _extracted_call(after)
        if helper_name is not None and helper_name not in pre_names:
            helper = helpers.get(helper_name)
            if helper is None:
                failures.append(f"stmt {index}: helper {helper_name} not found in --modules")
                continue
            if not isinstance(before, ast.If):
                failures.append(f"stmt {index}: post extracts {helper_name} but pre is not an if")
                continue
            assert isinstance(after, ast.If)
            pre_test = _normalize(ast.get_source_segment("\n".join(pre.lines), before.test) or "")
            post_test = _normalize(ast.get_source_segment("\n".join(post.lines), after.test) or "")
            if pre_test != post_test:
                failures.append(f"stmt {index}: test changed for {helper_name}: {pre_test!r} -> {post_test!r}")
            if before.orelse:
                failures.append(f"stmt {index}: pre branch for {helper_name} has an else clause")
            if not isinstance(before.body[-1], ast.Return):
                failures.append(f"stmt {index}: pre branch for {helper_name} does not end in return")
            helper_body, provided = _strip_prologue(helper)
            expected = _body_text(pre.lines, before.body)
            actual = _body_text(helper.lines, helper_body)
            if expected != actual:
                failures.append(f"stmt {index}: body of {helper_name} is not verbatim")
                for line in difflib.unified_diff(
                    expected.splitlines(), actual.splitlines(), "pre", helper_name, lineterm="", n=1
                ):
                    print(line)
            unbound = _unbound_names(helper_body, provided, helper.module_names)
            if unbound:
                failures.append(f"{helper_name}: names no longer in scope: {sorted(unbound)}")
            extracted += 1
            continue
        before_text = _normalize(_body_text(pre.lines, [before]))
        after_text = _normalize(_body_text(post.lines, [after]))
        if before_text != after_text:
            failures.append(f"stmt {index}: skeleton statement changed")
            for line in difflib.unified_diff(
                before_text.splitlines(), after_text.splitlines(), "pre", "post", lineterm="", n=1
            ):
                print(line)

    print(f"skeleton statements: {len(pre_body)}; extracted branches: {extracted}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        print(f"branches proof FAILED: {len(failures)} finding(s)", file=sys.stderr)
        return 1
    print("branches proof OK")
    return 0


# --------------------------------------------------------------------------
# css slicing

_IMPORT_RE = re.compile(r"""^@import\s+(?:url\()?["']([^"']+)["']\)?\s*;\s*$""")
_CLASS_RE = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")


def _expand_css(entry: Path) -> tuple[bytes, list[Path]]:
    out = bytearray()
    slices: list[Path] = []
    for raw in entry.read_bytes().split(b"\n"):
        line = raw.decode("utf-8")
        match = _IMPORT_RE.match(line)
        if match:
            target = (entry.parent / match.group(1)).resolve()
            slices.append(target)
            out += target.read_bytes()
        else:
            out += raw + b"\n"
    # Every chunk was re-terminated with "\n", including the chunk after the
    # entry's final newline, so drop exactly one trailing newline.
    return bytes(out[:-1]), slices


def cmd_css(args: argparse.Namespace) -> int:
    original = args.original.read_bytes()
    expanded, slices = _expand_css(args.entry)
    failures: list[str] = []
    print(f"slices: {len(slices)}")
    for path in slices:
        line_count = path.read_bytes().count(b"\n")
        print(f"  {path.name}: {line_count} lines")
    if expanded != original:
        failures.append(
            f"expansion differs from original ({len(expanded)} vs {len(original)} bytes)"
        )
        before = original.decode("utf-8").splitlines()
        after = expanded.decode("utf-8").splitlines()
        for line in list(difflib.unified_diff(before, after, "original", "expanded", lineterm="", n=1))[:80]:
            print(line)
    original_classes = set(_CLASS_RE.findall(original.decode("utf-8")))
    slice_classes: set[str] = set()
    for path in slices:
        slice_classes.update(_CLASS_RE.findall(path.read_text(encoding="utf-8")))
    print(f"class inventory: original {len(original_classes)}, slices {len(slice_classes)}")
    missing = sorted(original_classes - slice_classes)
    extra = sorted(slice_classes - original_classes)
    if missing:
        failures.append(f"classes missing from slices: {missing[:20]}")
    if extra:
        failures.append(f"classes only in slices: {extra[:20]}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        print("css proof FAILED", file=sys.stderr)
        return 1
    print("css proof OK: byte-identical expansion, identical class inventory")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    defset = sub.add_parser("defset", help="top-level definition diff")
    defset.add_argument("pre", type=Path)
    defset.add_argument("post", type=Path, nargs="+")
    defset.add_argument("--flatten-classes", action="store_true")
    defset.add_argument("--allow-changed", nargs="*", default=[])
    defset.add_argument("--strict", action="store_true", help="also fail on added names")
    defset.set_defaults(func=cmd_defset)

    branches = sub.add_parser("branches", help="branch extraction proof")
    branches.add_argument("pre", help="PRE_FILE:FUNC")
    branches.add_argument("post", help="POST_FILE:FUNC")
    branches.add_argument("--modules", nargs="+", required=True, help="files holding the helpers")
    branches.set_defaults(func=cmd_branches)

    css = sub.add_parser("css", help="css slice proof")
    css.add_argument("original", type=Path)
    css.add_argument("entry", type=Path)
    css.set_defaults(func=cmd_css)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
