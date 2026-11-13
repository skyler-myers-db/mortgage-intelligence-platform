"""Where the deploy shell can change a named variable, read lexically (B3's digest-parity pin).

The pre-lease activation prediction prints a digest that equals the late
gate's only while nothing between the two changes a payload-builder input
(tests/unit/test_activation_prediction_deploy_contract.py). These helpers find
every place scripts/ could do that, for a given set of names, and say which
shell function each place is in, so the tests pin sites and not only names.

``assignments`` over-approximates on purpose: a false site is reviewed once in
the pinned map, a missed one moves a builder input with every test green. It
counts, on a logical line (backslash continuations joined):

* ``NAME=`` and ``NAME+=`` (a plain assignment, a one-command prefix, a
  ``local``/``declare``/``export`` with a value, ``${NAME=default}``);
* ``${NAME:=default}``;
* ``for NAME in`` and ``select NAME in``;
* a bare ``NAME`` (not ``$NAME``, not ``${NAME...}``) on a line that holds a
  builtin which writes the variables it is handed by name (``read``,
  ``mapfile``, ``readarray``, ``getopts``, ``printf -v``, ``declare``,
  ``typeset``, ``local``, ``readonly``, ``export``, ``unset``, ``let``, an
  arithmetic ``((``), wherever on the line it stands: ``IFS=$'\\t' read -r
  NAME``, ``while IFS= read -r NAME``, ``foo && export NAME``;
* a bare ``NAME`` in the list of a ``for``/``select`` loop, whose body may
  assign each member by name (``for v in NAME; do export -n "$v"``);
* a bare ``NAME`` on a line that names a shell function which itself writes
  through a name it is handed (``by_name``: ``mint_m2m_token NAME ...``);
* any ``NAME`` at all on a line that starts with ``export`` or ``read``.

Not seen, by construction: a name built at run time (``printf -v "$prefix"_ID``),
and a file sourced under allexport, which the env-file test covers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping

TOP = "<top>"
_FUNCTION = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*(?:[{(].*)?$")
_WORD = r"(?![A-Za-z0-9_-])"
_NOT_WORD = r"(?<![A-Za-z0-9_-])"
_WRITERS = re.compile(
    rf"{_NOT_WORD}(?:read|mapfile|readarray|getopts|declare|typeset|local|readonly|export|unset|let){_WORD}"
    r"|printf\s+-v\b|\(\(|\b(?:for|select)\s+[A-Za-z_][A-Za-z0-9_]*\s+in\s"
)
# A write through a name held in another variable: printf -v "$name", export "${name?}",
# unset "$name", read -r "$name" (never its prompt, here-string or redirect), eval.
BY_NAME = re.compile(
    r"printf\s+-v\s+\"?\$"
    rf"|{_NOT_WORD}(?:export|declare|typeset|local|readonly|unset)(?:\s+-\w+)*\s+\"?\$"
    rf"|{_NOT_WORD}(?:read|mapfile|readarray){_WORD}[^<|;&\n]*\s\"?\$[{{A-Za-z_]"
    rf"|{_NOT_WORD}eval{_WORD}"
)


def code_lines(text: str) -> str:
    """``text`` without its comment lines."""

    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def logical_lines(text: str) -> list[str]:
    """The lines of ``text`` with backslash continuations joined."""

    return re.sub(r"\\\n", " ", text).splitlines()


def assignments(text: str, names: Iterable[str], by_name: Iterable[str] = ()) -> set[str]:
    """Every name of ``names`` that ``text`` could assign, unset or re-export (see the module text)."""

    wanted = set(names)
    alternation = "|".join(sorted(wanted, key=len, reverse=True))
    found = set(re.findall(rf"(?<![A-Za-z0-9_$])({alternation})(?:\+)?=", text))
    found |= set(re.findall(rf"\$\{{({alternation}):=", text))
    found |= set(re.findall(rf"\b(?:for|select)\s+({alternation})\s+in\b", text))
    bare = re.compile(rf"(?<![A-Za-z0-9_$])(?<!\$\{{)({alternation})(?![A-Za-z0-9_])")
    helpers = sorted(set(by_name), key=len, reverse=True)
    handed = re.compile(rf"{_NOT_WORD}(?:{'|'.join(helpers)}){_WORD}") if helpers else None
    for line in logical_lines(text):
        code = line.split(" #", 1)[0]
        if re.match(r"\s*(export|read)\b", code):
            found |= {word for word in re.findall(r"[A-Z][A-Z0-9_]+", code) if word in wanted}
        if _WRITERS.search(code) or (handed is not None and handed.search(code)):
            found |= set(bare.findall(code))
    return found


def chunks(texts: Mapping[str, str]) -> Iterator[tuple[str, str, str]]:
    """(file, function or ``<top>``, text) for every top-level function and each file's remainder."""

    for name, text in texts.items():
        top: list[str] = []
        current: str | None = None
        body: list[str] = []
        for line in text.splitlines():
            match = _FUNCTION.match(line) if current is None else None
            if match is not None:
                current, body = match.group(1), [line]
                if "{" in line and line.rstrip().endswith("}"):  # a one-line function
                    yield name, current, line
                    current = None
            elif current is not None:
                body.append(line)
                if line in ("}", ")"):
                    yield name, current, "\n".join(body)
                    current = None
            else:
                top.append(line)
        if current is not None:  # an unclosed function reads as one, never as nothing
            yield name, current, "\n".join(body)
        yield name, TOP, "\n".join(top)


def functions(texts: Mapping[str, str]) -> dict[str, str]:
    """Each top-level function's text; two definitions of one name are joined."""

    found: dict[str, str] = {}
    for _file, function, text in chunks(texts):
        if function != TOP:
            found[function] = f"{found[function]}\n{text}" if function in found else text
    return found


def sites(texts: Mapping[str, str], names: Iterable[str], by_name: Iterable[str] = ()) -> dict[str, set[str]]:
    """``"<file>:<function>"`` to the names assigned there; sites that assign none are left out."""

    wanted = set(names)
    found: dict[str, set[str]] = {}
    for file, function, text in chunks(texts):
        assigned = assignments(text, wanted, by_name)
        if assigned:
            found.setdefault(f"{file}:{function}", set()).update(assigned)
    return found


def by_name_sites(texts: Mapping[str, str]) -> set[str]:
    """Every ``"<file>:<function>"`` whose text writes a variable through a name it holds in another."""

    def writes(line: str) -> bool:
        return BY_NAME.search(re.sub(r"\s-p\s+(?:\"[^\"]*\"|'[^']*')", " ", line)) is not None

    return {
        f"{file}:{function}"
        for file, function, text in chunks(texts)
        if any(writes(line) for line in logical_lines(text))
    }


def reachable(roots: Iterable[str], bodies: Mapping[str, str]) -> set[str]:
    """The functions ``roots`` name, and the ones those name, to a fixed point (a mention counts as a call)."""

    def named(text: str) -> set[str]:
        return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)) & set(bodies)

    reached: set[str] = set()
    frontier = set().union(*(named(text) for text in roots))
    while frontier:
        reached |= frontier
        frontier = set().union(*(named(bodies[name]) for name in frontier)) - reached
    return reached
