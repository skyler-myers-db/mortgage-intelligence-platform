"""A value-free skeleton of a ``bundle plan -o json`` document, for a private report (wave 7f).

When ``bundle_plan_shape.parse_plan`` refuses a document, the operator needs
to see its shape to extend the parser from it, and the raw plan must never
leave the runner: it embeds the deployer's root path, resolved names, remote
state and ids. ``skeleton`` keeps the structure and drops every value:

* an object keeps its keys, each mapped to the skeleton of its value; a key
  that occurs twice is listed under ``__duplicate_keys__``;
* a list becomes ``{"__list__": <length>, "items": <skeleton>}``, the items'
  distinct skeletons folded into ``{"__union__": [...]}`` when they differ;
* every scalar becomes its JSON type name: ``str``, ``int``, ``float``,
  ``bool``, ``null``, or ``number_constant`` for ``NaN`` and ``Infinity``;
* a document that is not JSON at all keeps only its byte and line counts.

Keys are kept because they are what a parser follows (field names, the
``resources.<kind>.<key>`` keys, ``vars`` and ``changes`` paths). A remote
object's map keys (a tag key, a task key) are keys too, so a skeleton is as
private as the report it goes to, never a public line.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA = "mip.bundle-plan-skeleton.v1"
# Deeper than any plan the CLI writes; past it a value is only "nested".
MAX_DEPTH = 64


class _Pairs(list[tuple[str, Any]]):
    """An object's key-value pairs in order, duplicates kept."""


class _Constant:
    """A ``NaN`` or ``Infinity`` literal, which strict JSON does not allow."""


def _shape(value: Any, depth: int) -> Any:
    if depth > MAX_DEPTH:
        return "nested"
    if isinstance(value, _Pairs):
        shape: dict[str, Any] = {}
        duplicates: set[str] = set()
        for key, item in value:
            if key in shape:
                duplicates.add(key)
            shape[key] = _shape(item, depth + 1)
        if duplicates:
            shape["__duplicate_keys__"] = sorted(duplicates)
        return shape
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            item_shape = _shape(item, depth + 1)
            if item_shape not in items:
                items.append(item_shape)
        folded: Any = items[0] if len(items) == 1 else {"__union__": items} if items else None
        return {"__list__": len(value), "items": folded}
    if isinstance(value, _Constant):
        return "number_constant"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return type(value).__name__


def skeleton(text: str) -> dict[str, Any]:
    """The value-free skeleton of ``text``; never raises."""

    size = {"bytes": len(text.encode("utf-8", "replace")), "lines": text.count("\n")}
    try:
        document = json.loads(text, object_pairs_hook=_Pairs, parse_constant=lambda _name: _Constant())
    except (ValueError, RecursionError):
        return {"schema": SCHEMA, "json": False, **size}
    return {"schema": SCHEMA, "json": True, **size, "shape": _shape(document, 0)}
