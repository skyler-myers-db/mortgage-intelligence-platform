"""Tokenize one SQL statement and say what its outermost FROM binds.

Split out of ``genie_sql_predicates`` because the two questions are
independent and the combined module outgrew the file-size gate: this one is
purely structural (no cohort vocabulary, no floor semantics), and the reader
next door decides what a conjunct MEANS.

Two structural guarantees live here, and both exist because a wrong cohort
filter replays NARROWER in silence while refusing one replays BROADER and is
disclosed:

* A string literal becomes a contentless ``string`` token, so nothing spelled
  inside quotes can ever be read as SQL.
* :func:`rebound_columns` names the columns the outermost FROM does NOT bind
  to the base column of that name -- an aggregate, an expression or a rename
  in a CTE or derived table, a LATERAL VIEW's own alias columns, or every
  column at all when a source cannot be resolved. A conjunct that mentions one of those names is filtering
  something other than a borrower row (live 2026-08-11: a floor lifted from a
  state-average CTE dropped 26,308 of 70,576 eligible borrowers), so the
  reader must refuse and disclose it rather than replay it per borrower.

A bare pass-through survives even a GROUP BY: a projected column that is not
aggregated has to be a grouping key, so filtering the groups on it selects
exactly the base rows a replayed filter would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeVar

_TokensT = TypeVar("_TokensT", list["Token"], tuple["Token", ...])

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
WHITESPACE_RE = re.compile(r"\s+")
_MULTI_CHAR_PUNCT = ("<=>", "->>", "||", "->", "<=", ">=", "<>", "!=", "!<", "!>", "::", "=>")

# A depth-0 word that ends one clause and may begin another. A column or alias
# that happens to share one of these names closes a region early, which can
# only cost a floor -- never invent one.
CLAUSE_WORDS = frozenset(
    {
        "select",
        "from",
        "where",
        "group",
        "having",
        "qualify",
        "window",
        "order",
        "limit",
        "offset",
        "cluster",
        "distribute",
        "sort",
        "join",
        "on",
        "using",
        "lateral",
        "values",
        "into",
        "tablesample",
        "pivot",
        "unpivot",
        "fetch",
        "with",
        "inner",
        "cross",
        "outer",
        "left",
        "right",
        "full",
        "natural",
        "semi",
        "anti",
    }
)
SET_OPERATOR_WORDS = frozenset({"union", "intersect", "except", "minus"})
# Join flavours whose ON clause does NOT filter the preserved side.
NON_FILTERING_JOIN_WORDS = frozenset({"left", "right", "full", "natural", "semi", "anti"})

# A depth-0 word that ends a FROM source expression.
_SOURCE_END_WORDS = CLAUSE_WORDS | SET_OPERATOR_WORDS
# Depth-0 constructs that bind columns this reader cannot trace to a base
# table. Their presence makes every column rebound, which refuses -- and
# discloses -- every bound rather than replaying one at an unknown grain.
_UNRESOLVABLE_SOURCE_WORDS = frozenset({"values", "pivot", "unpivot", "unnest", "tablesample"})
# A trailing word that belongs to the expression, never an output alias.
# Anything else trailing a select item is READ as an alias: over-reading one
# only refuses more conjuncts, while missing one would replay a renamed
# column as if it were the base column of that name.
_NON_ALIAS_WORDS = frozenset(
    {
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
        "like",
        "ilike",
        "rlike",
        "between",
        "case",
        "when",
        "then",
        "else",
        "end",
        "distinct",
        "all",
        "interval",
        "as",
        "true",
        "false",
        "asc",
        "desc",
        "div",
        "exists",
        "over",
        "filter",
        "nulls",
    }
)

_MAX_TOKENS = 20_000
_MAX_SOURCE_DEPTH = 4


@dataclass(frozen=True)
class Token:
    kind: str  # word | number | string | param | punct
    text: str
    start: int
    end: int


def tokenize(sql: str) -> list[Token] | None:
    """Split SQL into tokens, dropping comments and opaquing string literals.

    Returns ``None`` for input this module refuses to reason about (an
    unterminated comment or quote), which the caller turns into "no floor".
    """

    tokens: list[Token] = []
    index = 0
    length = len(sql)
    while index < length:
        if len(tokens) > _MAX_TOKENS:
            return None
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            close = sql.find("*/", index + 2)
            if close < 0:
                return None
            index = close + 2
            continue
        if char in "'\"":
            end = _quoted_end(sql, index, char)
            if end < 0:
                return None
            tokens.append(Token("string", "", index, end))
            index = end
            continue
        if char == "`":
            close = sql.find("`", index + 1)
            if close < 0:
                return None
            tokens.append(Token("word", sql[index + 1 : close].lower(), index, close + 1))
            index = close + 1
            continue
        if char == ":" and not sql.startswith("::", index):
            named = _WORD_RE.match(sql, index + 1)
            if named:
                tokens.append(Token("param", named.group(0).lower(), index, named.end()))
                index = named.end()
                continue
        word = _WORD_RE.match(sql, index)
        if word:
            tokens.append(Token("word", word.group(0).lower(), index, word.end()))
            index = word.end()
            continue
        number = _NUMBER_RE.match(sql, index)
        if number:
            tokens.append(Token("number", number.group(0), index, number.end()))
            index = number.end()
            continue
        for operator in _MULTI_CHAR_PUNCT:
            if sql.startswith(operator, index):
                tokens.append(Token("punct", operator, index, index + len(operator)))
                index += len(operator)
                break
        else:
            tokens.append(Token("punct", char, index, index + 1))
            index += 1
    return tokens


def _quoted_end(sql: str, start: int, quote: str) -> int:
    index = start + 1
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            if index + 1 < length and sql[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return -1


def matching_paren(tokens: list[Token] | tuple[Token, ...], start: int) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.kind != "punct":
            continue
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def unwrap(tokens: _TokensT) -> _TokensT:
    while (
        len(tokens) > 1
        and tokens[0].kind == "punct"
        and tokens[0].text == "("
        and matching_paren(tokens, 0) == len(tokens) - 1
    ):
        tokens = tokens[1:-1]
    return tokens


def qualified_name(tokens: tuple[Token, ...], start: int) -> tuple[str, int] | None:
    """Read ``[catalog.][schema.][table.]name`` -> (name, next index)."""

    if start >= len(tokens) or tokens[start].kind != "word":
        return None
    name = tokens[start].text
    index = start + 1
    while (
        index + 1 < len(tokens)
        and tokens[index].kind == "punct"
        and tokens[index].text == "."
        and tokens[index + 1].kind == "word"
    ):
        name = tokens[index + 1].text
        index += 2
    return name, index


@dataclass(frozen=True)
class _QueryShape:
    """One query split into the parts that decide what its columns mean."""

    ctes: dict[str, tuple[Token, ...]]
    select_list: tuple[Token, ...]
    sources: tuple[tuple[Token, ...], ...]
    # Names bound by the query itself rather than by one of its sources --
    # today only a LATERAL VIEW's alias columns.
    generated: frozenset[str] = frozenset()


def _split_top_level(tokens: tuple[Token, ...], separator: str) -> list[tuple[Token, ...]]:
    parts: list[tuple[Token, ...]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if token.kind == "punct":
            if token.text == "(":
                depth += 1
            elif token.text == ")":
                depth -= 1
            elif token.text == separator and depth == 0:
                parts.append(tuple(current))
                current = []
                continue
        current.append(token)
    parts.append(tuple(current))
    return parts


def _query_shape(query: tuple[Token, ...]) -> _QueryShape | None:
    """Split a query into its CTE bodies, its select list and its FROM sources.

    ``None`` for anything whose columns this reader cannot trace: a set
    operation, a table-valued or reshaping source (VALUES, PIVOT, UNNEST,
    TABLESAMPLE), a statement with no FROM. A LATERAL VIEW is traceable --
    its alias columns come back as ``generated``.
    """

    tokens = unwrap(query)
    while tokens and tokens[-1].kind == "punct" and tokens[-1].text == ";":
        tokens = tokens[:-1]
    if not tokens or tokens[0].kind != "word" or tokens[0].text not in {"select", "with"}:
        return None

    ctes: dict[str, tuple[Token, ...]] = {}
    sources: list[tuple[Token, ...]] = []
    generated: set[str] = set()
    select_start: int | None = None
    select_end: int | None = None
    source_start: int | None = None
    in_lateral = False
    in_lateral_alias = False
    depth = 0
    for index, token in enumerate(tokens):
        if token.kind == "punct":
            if token.text == "(":
                depth += 1
            elif token.text == ")":
                depth -= 1
                if depth < 0:
                    return None
            elif token.text == "," and depth == 0 and source_start is not None:
                sources.append(tokens[source_start:index])
                source_start = index + 1
            continue
        if depth or token.kind != "word":
            continue
        word = token.text
        if word in SET_OPERATOR_WORDS or word in _UNRESOLVABLE_SOURCE_WORDS:
            return None
        if (
            word == "as"
            and index + 1 < len(tokens)
            and tokens[index + 1].kind == "punct"
            and tokens[index + 1].text == "("
        ):
            close = matching_paren(tokens, index + 1)
            if close < 0 or index == 0 or tokens[index - 1].kind != "word":
                return None
            ctes[tokens[index - 1].text] = tokens[index + 2 : close]
            continue
        if in_lateral and not (word in _SOURCE_END_WORDS and word != "outer"):
            # A LATERAL VIEW does not rebind the table's own columns -- it
            # adds one. Refusing the whole statement would drop the floor on
            # every "... by segment" answer the deployed space serves
            # (`FROM borrower_360 LATERAL VIEW explode(segment_codes) ...`),
            # so only the alias columns are treated as rebound.
            if word == "as":
                in_lateral_alias = True
            elif in_lateral_alias:
                generated.add(word)
            continue
        in_lateral = in_lateral_alias = False
        if source_start is not None and word in _SOURCE_END_WORDS:
            sources.append(tokens[source_start:index])
            source_start = None
        if word == "lateral":
            in_lateral = True
            continue
        if word == "select":
            if select_start is None:
                select_start = index + 1
                if is_word(tokens, select_start, "distinct") or is_word(
                    tokens, select_start, "all"
                ):
                    select_start += 1
        elif select_start is not None and select_end is None and word in CLAUSE_WORDS:
            select_end = index
        if word in {"from", "join"}:
            source_start = index + 1
    if source_start is not None:
        sources.append(tokens[source_start:])
    if select_start is None or select_end is None or not sources:
        return None
    return _QueryShape(
        ctes=ctes,
        select_list=tokens[select_start:select_end],
        sources=tuple(source for source in sources if source),
        generated=frozenset(generated),
    )


def _source_rebound(
    source: tuple[Token, ...],
    ctes: dict[str, tuple[Token, ...]],
    depth: int,
) -> frozenset[str] | None:
    """Names this FROM source binds to something other than its base column.

    ``None`` means the source could not be resolved, i.e. every column it
    offers must be treated as rebound.
    """

    if not source:
        return None
    if source[0].kind == "punct" and source[0].text == "(":
        close = matching_paren(source, 0)
        if close < 0:
            return None
        return _query_rebound(source[1:close], ctes, depth + 1)
    named = qualified_name(source, 0)
    if named is None:
        return None
    name, index = named
    if index < len(source) and source[index].kind == "punct" and source[index].text == "(":
        return None  # a table-valued function binds columns this reader cannot see
    if index > 1:
        return frozenset()  # catalog.schema.table -- a base table
    if not ctes:
        return frozenset()  # no CTE is in scope, so a bare name is a base table
    body = ctes.get(name)
    if body is None:
        return None
    return _query_rebound(body, ctes, depth + 1)


def _query_rebound(
    query: tuple[Token, ...],
    ctes: dict[str, tuple[Token, ...]],
    depth: int,
) -> frozenset[str] | None:
    """Output names this query binds to something other than the base column.

    A bare pass-through (``state``, ``b.state``, ``b.state AS state``) keeps
    its meaning even across a GROUP BY, because a projected column that is
    not aggregated has to be a grouping key -- filtering the groups on it
    selects exactly the base rows a replayed filter would. An aggregate, an
    expression or a rename does not.
    """

    if depth > _MAX_SOURCE_DEPTH:
        return None
    shape = _query_shape(query)
    if shape is None:
        return None
    scope = {**ctes, **shape.ctes}
    # A LATERAL VIEW's alias column is bound by this query, not by a base
    # table, so it is rebound wherever it surfaces.
    inner: set[str] = set(shape.generated)
    for source in shape.sources:
        source_rebound = _source_rebound(source, scope, depth)
        if source_rebound is None:
            return None
        inner |= source_rebound

    rebound: set[str] = set()
    for item in _split_top_level(shape.select_list, ","):
        if not item:
            return None
        if item[-1].kind == "punct" and item[-1].text == "*":
            rebound |= inner  # a star carries the inner names through unchanged
            continue
        named = qualified_name(item, 0)
        if named is not None and named[1] == len(item):
            if named[0] in inner:
                rebound.add(named[0])
            continue
        alias = _select_alias(item)
        if alias is None:
            continue  # an unnamed expression has no name an outer filter can use
        expression = item[:-1]
        if expression and expression[-1].kind == "word" and expression[-1].text == "as":
            expression = expression[:-1]
        projected = qualified_name(expression, 0) if expression else None
        if projected is not None and projected[1] == len(expression) and projected[0] == alias:
            if alias in inner:
                rebound.add(alias)
            continue
        rebound.add(alias)
    return frozenset(rebound)


def _select_alias(item: tuple[Token, ...]) -> str | None:
    if len(item) < 2:
        return None
    last = item[-1]
    if last.kind != "word" or last.text in _NON_ALIAS_WORDS:
        return None
    return last.text


def rebound_columns(tokens: list[Token]) -> frozenset[str] | None:
    """Names the OUTERMOST statement's FROM does not bind to a base column."""

    shape = _query_shape(tuple(tokens))
    if shape is None:
        return None
    rebound: set[str] = set(shape.generated)
    for source in shape.sources:
        source_rebound = _source_rebound(source, shape.ctes, 0)
        if source_rebound is None:
            return None
        rebound |= source_rebound
    return frozenset(rebound)


def is_base_grain(leaf: tuple[Token, ...], rebound: frozenset[str] | None) -> bool:
    """True when every name in ``leaf`` still means its base column."""

    if rebound is None:
        return False
    if not rebound:
        return True
    return not any(token.kind == "word" and token.text in rebound for token in leaf)


def is_word(tokens: tuple[Token, ...], index: int, word: str) -> bool:
    return index < len(tokens) and tokens[index].kind == "word" and tokens[index].text == word
