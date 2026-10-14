"""Generated-type aliases in the wire-contract coverage files (D-api-types-a3 P3).

A P3 alias replaces a hand type by its generated view, keeping the exported
name: ``export type X = ApiResponse<'X'>;`` (or ``ApiRequest<'X'>``). That
form is its own proof, so it counts as coverage for that side, and only when
the schema key equals the exported name; any other alias form keeps its
pairs. An exported indexed-alias vocabulary (``export type Code =
ApiResponse<'S'>['code']`` and the like) must carry a matching
``Expect<string extends Code ? false : true>`` guard in a check file, so a
backend field widened to ``str`` fails tsc instead of silently turning the
vocabulary into ``string``.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.unit.frontend_wire_contract import coverage_files
from tests.unit.frontend_wire_source import Project, rel_path

_ALIAS = re.compile(r"(?m)^export\s+type\s+([A-Za-z_$][\w$]*)\s*=\s*(ApiResponse|ApiRequest)<'([^']+)'>\s*;")
_INDEXED = re.compile(
    r"(?m)^export\s+type\s+([A-Za-z_$][\w$]*)\s*=\s*[^;]*?\bApi(?:Response|Request|Ok|Body)<'[^']+'>\s*\[\s*'[^']+'\s*\]"
)
_SIDE = {"ApiResponse": "response", "ApiRequest": "request"}


def alias_coverage(project: Project) -> set[tuple[str, str, str]]:
    """(side, module rel, name) for every same-named generated alias in a coverage file."""

    found: set[tuple[str, str, str]] = set()
    for path in coverage_files(project.src):
        if not path.is_file():
            continue
        module = project.module(path)
        for m in _ALIAS.finditer(module.code):
            if m.group(1) == m.group(3):
                found.add((_SIDE[m.group(2)], module.rel, m.group(1)))
    return found


def indexed_vocabularies(project: Project) -> list[tuple[str, str]]:
    """(module rel, name) of every exported indexed-alias vocabulary in a coverage file."""

    return [
        (rel_path(path), m.group(1))
        for path in coverage_files(project.src)
        if path.is_file()
        for m in _INDEXED.finditer(project.module(path).code)
    ]


def unguarded_vocabularies(project: Project, check_files: list[Path]) -> list[str]:
    """Indexed vocabularies with no ``Expect<string extends X ? false : true>`` line in any check file."""

    text = "\n".join(project.module(path).code for path in check_files)
    return [
        f"{rel}:{name}"
        for rel, name in indexed_vocabularies(project)
        if not re.search(rf"Expect<\s*string\s+extends\s+{re.escape(name)}\s*\?\s*false\s*:\s*true\s*>", text)
    ]
