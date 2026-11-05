"""A small GitHub Actions expression parser, evaluator and reference walker for workflow tests.

It covers the expression grammar deploy-dev.yml uses: literals, context paths
(``a.b``, ``a['b']``, ``a.*``), function calls, ``!``, comparisons, ``&&`` and
``||`` with GitHub's value-returning semantics, and parentheses. Anything else
raises ``ExpressionError``, so a new form in the workflow fails a test until
this learns it, rather than slipping past one.

* ``references(expression)`` yields each use of a named context as
  ``(context, property)``: ``("secrets", "X")`` for ``secrets.X``, and
  ``("secrets", None)`` for every other use (the bare context, ``toJSON(secrets)``,
  ``secrets['X']`` or ``secrets[...]``).
* ``render(text, context)`` substitutes every ``${{ ... }}`` the way the
  runner does for a step's ``env:`` and ``with:``.
* ``scalars(document)`` walks a loaded workflow and yields each string with
  its path; an ``if:`` value is an expression even without ``${{ }}``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_TOKEN = re.compile(
    r"\s*(?:(?P<string>'(?:[^']|'')*')|(?P<number>-?\d+(?:\.\d+)?)|(?P<op>&&|\|\||==|!=|<=|>=|[!<>()\[\],.*])"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_-]*))"
)
_TEMPLATE = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_PRECEDENCE = {"||": 1, "&&": 2, "==": 3, "!=": 3, "<": 4, "<=": 4, ">": 4, ">=": 4}


class ExpressionError(ValueError):
    """An expression this parser does not understand."""


@dataclass(frozen=True)
class Node:
    kind: str  # literal | name | property | index | call | not | binary
    value: Any = None
    children: tuple[Node, ...] = ()


def _tokens(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            raise ExpressionError(f"cannot read {text[position:]!r}")
        kind = match.lastgroup
        assert kind is not None
        found.append((kind, match.group(kind)))
        position = match.end()
    return found


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.at = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None or (value is not None and token[1] != value):
            raise ExpressionError(f"expected {value or 'a token'}")
        self.at += 1
        return token

    def parse(self) -> Node:
        node = self.binary(0)
        if self.peek() is not None:
            raise ExpressionError(f"unexpected {self.peek()}")
        return node

    def binary(self, floor: int) -> Node:
        left = self.unary()
        while (token := self.peek()) and token[0] == "op" and _PRECEDENCE.get(token[1], 0) > floor:
            self.take()
            right = self.binary(_PRECEDENCE[token[1]])
            left = Node("binary", token[1], (left, right))
        return left

    def unary(self) -> Node:
        token = self.peek()
        if token == ("op", "!"):
            self.take()
            return Node("not", None, (self.unary(),))
        return self.postfix(self.primary())

    def primary(self) -> Node:
        kind, text = self.take()
        if kind == "string":
            return Node("literal", text[1:-1].replace("''", "'"))
        if kind == "number":
            return Node("literal", float(text) if "." in text else int(text))
        if (kind, text) == ("op", "("):
            node = self.binary(0)
            self.take(")")
            return node
        if kind != "name":
            raise ExpressionError(f"unexpected {text!r}")
        if text in ("true", "false", "null"):
            return Node("literal", {"true": True, "false": False, "null": None}[text])
        if self.peek() == ("op", "("):
            self.take("(")
            args: list[Node] = []
            while self.peek() != ("op", ")"):
                args.append(self.binary(0))
                if self.peek() == ("op", ","):
                    self.take(",")
            self.take(")")
            return Node("call", text, tuple(args))
        return Node("name", text)

    def postfix(self, node: Node) -> Node:
        while (token := self.peek()) in (("op", "."), ("op", "[")):
            self.take()
            if token == ("op", "."):
                kind, text = self.take()
                if kind not in ("name",) and text != "*":
                    raise ExpressionError(f"bad property {text!r}")
                node = Node("property", text, (node,))
            else:
                index = self.binary(0)
                self.take("]")
                node = Node("index", None, (node, index))
        return node


def parse(expression: str) -> Node:
    return _Parser(expression).parse()


def references(expression: str, contexts: tuple[str, ...] = ("secrets", "vars")) -> Iterator[tuple[str, str | None]]:
    """Each use of a named context (case-insensitive): (context, property) or (context, None)."""

    wanted = {name.casefold() for name in contexts}

    def walk(node: Node) -> Iterator[tuple[str, str | None]]:
        if node.kind == "property" and node.children[0].kind == "name" and node.children[0].value.casefold() in wanted:
            yield node.children[0].value.casefold(), (None if node.value == "*" else node.value)
            return
        if node.kind == "name" and node.value.casefold() in wanted:
            yield node.value.casefold(), None
            return
        for child in node.children:
            yield from walk(child)

    yield from walk(parse(expression))


def _truthy(value: Any) -> bool:
    return value not in (None, False, "", 0)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if value is True or value is False:
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, indent=2)
    return str(value)


def evaluate(node: Node, context: dict[str, Any]) -> Any:
    if node.kind == "literal":
        return node.value
    if node.kind == "name":
        return context.get(node.value.casefold())
    if node.kind == "property":
        owner = evaluate(node.children[0], context)
        return owner.get(node.value) if isinstance(owner, dict) else None
    if node.kind == "index":
        owner, key = evaluate(node.children[0], context), evaluate(node.children[1], context)
        return owner.get(_text(key)) if isinstance(owner, dict) else None
    if node.kind == "not":
        return not _truthy(evaluate(node.children[0], context))
    if node.kind == "binary":
        left = evaluate(node.children[0], context)
        if node.value == "&&":
            return evaluate(node.children[1], context) if _truthy(left) else left
        if node.value == "||":
            return left if _truthy(left) else evaluate(node.children[1], context)
        right = evaluate(node.children[1], context)
        if node.value in ("==", "!="):
            same = _text(left).casefold() == _text(right).casefold()
            return same if node.value == "==" else not same
        raise ExpressionError(f"operator {node.value} is not evaluated here")
    if node.kind == "call":
        args = [evaluate(child, context) for child in node.children]
        name = node.value.casefold()
        if name == "fromjson":
            return json.loads(args[0])
        if name == "tojson":
            return json.dumps(args[0], indent=2)
        if name in ("success", "always"):
            return True
        if name in ("failure", "cancelled"):
            return False
        raise ExpressionError(f"function {node.value} is not evaluated here")
    raise ExpressionError(f"node {node.kind}")


def render(text: str, context: dict[str, Any]) -> str:
    """``text`` with each ``${{ ... }}`` replaced by its value, as the runner renders env and with."""

    return _TEMPLATE.sub(lambda match: _text(evaluate(parse(match.group(1)), context)), text)


def expressions_in(value: str, *, condition: bool = False) -> list[str]:
    """Every expression in a workflow string; an ``if:`` value is one even without ``${{ }}``."""

    found = [match.group(1) for match in _TEMPLATE.finditer(value)]
    if condition and not found and value.strip():
        found = [value]
    return found


def scalars(document: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    """Each string in a loaded workflow, with its key path."""

    if isinstance(document, dict):
        for key, value in document.items():
            yield from scalars(value, (*path, str(key)))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from scalars(value, (*path, str(index)))
    elif isinstance(document, str):
        yield path, document
