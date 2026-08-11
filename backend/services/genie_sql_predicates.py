"""Read a Genie answer's own SQL for the predicates that really filter rows.

Why this exists
---------------
A Genie answer hands off to the Lead Queue. If the queue replays a threshold
the answer never applied, the user acts on a truncated list and the Lakebase
cohort row, the draft-campaign criteria and the VIEW_LEADS audit metadata all
assert a population the answer never described. Replaying BROADER is visible
(the ``X-Cohort-Count-Delta`` / ``X-Cohort-Unreplayable-Filters`` headers say
so); replaying NARROWER is silent. So every judgement call here resolves
toward "lift nothing".

Text shape is not evidence
--------------------------
The previous reader regexed ``column >= n`` over the whole statement, so it
lifted a floor from any text that merely LOOKED like a bound. Two independent
reviews found the same class of defect, and the two worst shapes are the ones
the deployed Genie space TEACHES:

    SELECT COUNT(*) AS borrowers
         , COUNT_IF(opportunity_score >= <top tier>) AS top_tier
    FROM mip.gold.borrower_360 WHERE state = 'IL'

    SELECT COUNT(*) FILTER (WHERE opportunity_score >= <top tier>) ...

Both report a breakdown OF the answer's population; neither narrows it. The
old reader lifted the breakdown's bound anyway (live 2026-08-11: IL eligible
76,711 -> 128 at the top-tier threshold, a 599x truncation).
The same class covers ``NOT (score >= 80)``, ``a OR score >= 80``,
``ORDER BY score > 80``, a threshold inside a string literal, a CASE ladder,
a comment, and a subquery/CTE body.

So this module gates on POSITION, not on shape: a bound counts only when it
stands as a top-level AND-conjunct of the outermost statement's own WHERE /
inner-join ON / QUALIFY, unnegated and not under an OR. Everything else --
the select list, HAVING, GROUP BY, ORDER BY, aggregate FILTER clauses, CASE
arms, string literals, comments, CTE and subquery bodies -- is structurally
out of reach because the scanner never visits it.

What it deliberately refuses
----------------------------
* Set operations (UNION/INTERSECT/EXCEPT): the population is not one filter.
* Outer-join ON clauses: they do not filter the preserved side.
* CTE/subquery bodies: a real filter can live there, and refusing it replays
  broader, which is disclosed. Guessing replays narrower, which is not.
* Bound parameters (``>= :min_score``): the value is unknowable here, so the
  threshold is DISCLOSED as unreplayable instead of dropped in silence.
* Bounds outside the reviewed cohort domain (a negative ``rate_spread_bps``
  floor, ``score >= 900``): parsed, then disclosed rather than replayed --
  the closed cohort vocabulary rejects them, and a rejected floor 400s the
  whole action.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD

# Numeric floors the Lead Queue replays verbatim, keyed by the gold column and
# mapped to (reviewed result_filters key, inclusive ceiling). Lifting the
# answer's OWN threshold is what makes the handoff reproduce the answer's
# population: measured live 2026-08-11, "in-the-money borrowers in IL" scored
# at or above a floor answered 32 while the cohort replayed 1,766 (55x),
# because the threshold never left the SQL. Every one of these columns is a
# whole number in gold (verified live 2026-08-11 on paychex: typeof = int,
# zero fractional rows across 5,156,184 rows), so ``> n`` is exactly
# ``>= n + 1``.
SQL_NUMERIC_FLOOR_COLUMNS: dict[str, tuple[str, int]] = {
    "opportunity_score": ("min_opportunity_score", 100),
    "equity_pct": ("min_equity_pct", 100),
    "rate_spread_bps": ("min_rate_spread_bps", 5000),
}
_FLOOR_CEILINGS: dict[str, int] = {key: ceiling for key, ceiling in SQL_NUMERIC_FLOOR_COLUMNS.values()}
# Canonical MIP SQL expresses the top tier through a UC function rather than a
# literal, so the threshold is read from the constant the function is
# generated from -- but only in the same conjunctive, unnegated position a
# literal bound would have to hold.
_HIGH_OPPORTUNITY_FN = "fn_high_opportunity"
_HIGH_OPPORTUNITY_KEY = "min_opportunity_score"

# Disclosure names for a threshold that was found in filter position but
# cannot be replayed. They ride the existing ``unreplayable_filters``
# disclosure, so the divergence is visible instead of silent.
_DISCLOSURE_SUFFIX = "_threshold"

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")
_MULTI_CHAR_PUNCT = ("<=>", "->>", "||", "->", "<=", ">=", "<>", "!=", "!<", "!>", "::", "=>")

# A depth-0 word that ends one clause and may begin another. A column or alias
# that happens to share one of these names closes a region early, which can
# only cost a floor -- never invent one.
_CLAUSE_WORDS = frozenset(
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
_SET_OPERATOR_WORDS = frozenset({"union", "intersect", "except", "minus"})
# Join flavours whose ON clause does NOT filter the preserved side.
_NON_FILTERING_JOIN_WORDS = frozenset({"left", "right", "full", "natural", "semi", "anti"})

_MAX_SQL_CHARS = 200_000
_MAX_TOKENS = 20_000


@dataclass(frozen=True)
class _Token:
    kind: str  # word | number | string | param | punct
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class SqlFilterReading:
    """What the outermost statement provably filters on.

    ``floors`` are replayable cohort floors. ``unreplayable`` names thresholds
    found in filter position whose value the queue cannot apply.
    ``predicates`` is the normalized text of every accepted conjunct, for
    callers that still pattern-match reviewed criteria -- matching over these
    keeps them position-gated too.
    """

    floors: dict[str, int]
    unreplayable: tuple[str, ...] = ()
    predicates: tuple[str, ...] = ()


_EMPTY_READING = SqlFilterReading(floors={})


def _tokenize(sql: str) -> list[_Token] | None:
    """Split SQL into tokens, dropping comments and opaquing string literals.

    Returns ``None`` for input this module refuses to reason about (an
    unterminated comment or quote), which the caller turns into "no floor".
    """

    tokens: list[_Token] = []
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
            tokens.append(_Token("string", "", index, end))
            index = end
            continue
        if char == "`":
            close = sql.find("`", index + 1)
            if close < 0:
                return None
            tokens.append(_Token("word", sql[index + 1 : close].lower(), index, close + 1))
            index = close + 1
            continue
        if char == ":" and not sql.startswith("::", index):
            named = _WORD_RE.match(sql, index + 1)
            if named:
                tokens.append(_Token("param", named.group(0).lower(), index, named.end()))
                index = named.end()
                continue
        word = _WORD_RE.match(sql, index)
        if word:
            tokens.append(_Token("word", word.group(0).lower(), index, word.end()))
            index = word.end()
            continue
        number = _NUMBER_RE.match(sql, index)
        if number:
            tokens.append(_Token("number", number.group(0), index, number.end()))
            index = number.end()
            continue
        for operator in _MULTI_CHAR_PUNCT:
            if sql.startswith(operator, index):
                tokens.append(_Token("punct", operator, index, index + len(operator)))
                index += len(operator)
                break
        else:
            tokens.append(_Token("punct", char, index, index + 1))
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


def _matching_paren(tokens: list[_Token] | tuple[_Token, ...], start: int) -> int:
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


def _unwrap(tokens: list[_Token]) -> list[_Token]:
    while (
        len(tokens) > 1
        and tokens[0].kind == "punct"
        and tokens[0].text == "("
        and _matching_paren(tokens, 0) == len(tokens) - 1
    ):
        tokens = tokens[1:-1]
    return tokens


def _filter_regions(tokens: list[_Token]) -> tuple[tuple[_Token, ...], ...] | None:
    """Token spans of the outermost statement's own row filters.

    Only WHERE, QUALIFY, and the ON of a plain/INNER join qualify. Everything
    nested inside parentheses -- CTE bodies, subqueries, aggregate ``FILTER
    (WHERE ...)`` clauses, function arguments -- sits at depth > 0 and is
    never visited.
    """

    while tokens and tokens[-1].kind == "punct" and tokens[-1].text == ";":
        tokens = tokens[:-1]
    tokens = _unwrap(tokens)
    if not tokens:
        return None
    if tokens[0].kind != "word" or tokens[0].text not in {"select", "with"}:
        return None

    regions: list[tuple[_Token, ...]] = []
    depth = 0
    open_start: int | None = None
    join_modifier = False
    join_on_filters = False
    for index, token in enumerate(tokens):
        if token.kind == "punct":
            if token.text == "(":
                depth += 1
            elif token.text == ")":
                depth -= 1
                if depth < 0:
                    return None
            elif token.text == ";" and depth == 0:
                # A second statement is out of scope: refuse the whole read.
                return None
            continue
        if depth != 0 or token.kind != "word":
            continue
        word = token.text
        if word in _SET_OPERATOR_WORDS:
            return None
        if word in _NON_FILTERING_JOIN_WORDS:
            join_modifier = True
        elif word == "join":
            join_on_filters = not join_modifier
            join_modifier = False
        if word not in _CLAUSE_WORDS:
            continue
        if open_start is not None:
            regions.append(tuple(tokens[open_start:index]))
            open_start = None
        if word in {"where", "qualify"} or (word == "on" and join_on_filters):
            open_start = index + 1
    if open_start is not None:
        regions.append(tuple(tokens[open_start:]))
    return tuple(region for region in regions if region)


def _split_conjunction(
    tokens: tuple[_Token, ...],
) -> list[tuple[_Token, ...]] | None:
    """Split on top-level AND. ``None`` means an OR makes nothing guaranteed.

    ``BETWEEN a AND b`` owns its AND, so it never splits a leaf.
    """

    parts: list[tuple[_Token, ...]] = []
    current: list[_Token] = []
    depth = 0
    pending_between = 0
    for token in tokens:
        if token.kind == "punct":
            if token.text == "(":
                depth += 1
            elif token.text == ")":
                depth -= 1
        elif depth == 0 and token.kind == "word":
            if token.text == "or":
                return None
            if token.text == "between":
                pending_between += 1
            elif token.text == "and":
                if pending_between:
                    pending_between -= 1
                else:
                    parts.append(tuple(current))
                    current = []
                    continue
        current.append(token)
    parts.append(tuple(current))
    return parts


def _conjuncts(tokens: tuple[_Token, ...]) -> list[tuple[_Token, ...]]:
    """Leaf predicates that must ALL hold for a row to survive."""

    parts = _split_conjunction(tokens)
    if parts is None:
        return []
    leaves: list[tuple[_Token, ...]] = []
    for part in parts:
        if not part:
            continue
        if part[0].kind == "word" and part[0].text == "not":
            # Negated: the surviving population is the complement, which no
            # cohort floor can express. Drop it rather than invert it.
            continue
        if (
            part[0].kind == "punct"
            and part[0].text == "("
            and _matching_paren(part, 0) == len(part) - 1
        ):
            leaves.extend(_conjuncts(part[1:-1]))
            continue
        leaves.append(part)
    return leaves


def _qualified_name(tokens: tuple[_Token, ...], start: int) -> tuple[str, int] | None:
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


def _number(tokens: tuple[_Token, ...], start: int) -> tuple[float, int] | None:
    index = start
    sign = 1.0
    if index < len(tokens) and tokens[index].kind == "punct" and tokens[index].text in {"+", "-"}:
        sign = -1.0 if tokens[index].text == "-" else 1.0
        index += 1
    if index >= len(tokens) or tokens[index].kind != "number":
        return None
    try:
        value = float(tokens[index].text)
    except ValueError:
        return None
    return sign * value, index + 1


def _is_parameter(tokens: tuple[_Token, ...], start: int) -> bool:
    if start >= len(tokens):
        return False
    token = tokens[start]
    return token.kind == "param" or (token.kind == "punct" and token.text == "?")


def _floor_value(value: float, *, strict: bool) -> int:
    """Turn a bound into the inclusive integer floor of the same population.

    The three floor columns are whole numbers in gold, so ``> n`` is exactly
    ``>= n + 1``. A fractional literal rounds DOWN so the replay can only be
    broader than the answer, never narrower.
    """

    if float(value).is_integer():
        return int(value) + (1 if strict else 0)
    return math.floor(value)


def _high_opportunity_leaf(leaf: tuple[_Token, ...]) -> bool:
    """True for a bare ``fn_high_opportunity(...)`` predicate (optionally = TRUE)."""

    named = _qualified_name(leaf, 0)
    if named is None or named[0] != _HIGH_OPPORTUNITY_FN:
        return False
    index = named[1]
    if index >= len(leaf) or leaf[index].kind != "punct" or leaf[index].text != "(":
        return False
    close = _matching_paren(leaf, index)
    if close < 0:
        return False
    rest = leaf[close + 1 :]
    if not rest:
        return True
    texts = [token.text for token in rest]
    return texts in (["=", "true"], ["is", "true"])


def _leaf_bound(leaf: tuple[_Token, ...]) -> tuple[str, int | None] | None:
    """Read one conjunct as a cohort floor.

    Returns ``(filter key, floor)``; a ``None`` floor means the bound is real
    but unknowable here (a bound parameter). Anything that is not exactly a
    bare column against a literal is refused -- an arithmetic or wrapped
    column (``ABS(rate_spread_bps) >= 25``) is a different population.
    """

    if _high_opportunity_leaf(leaf):
        return _HIGH_OPPORTUNITY_KEY, HIGH_OPPORTUNITY_THRESHOLD

    named = _qualified_name(leaf, 0)
    if named is not None:
        column, index = named
        entry = SQL_NUMERIC_FLOOR_COLUMNS.get(column)
        if entry is not None and index < len(leaf):
            operator = leaf[index]
            if operator.kind == "punct" and operator.text in {">=", ">"}:
                strict = operator.text == ">"
                number = _number(leaf, index + 1)
                if number is not None and number[1] == len(leaf):
                    return entry[0], _floor_value(number[0], strict=strict)
                if _is_parameter(leaf, index + 1) and index + 2 == len(leaf):
                    return entry[0], None
            if operator.kind == "word" and operator.text == "between":
                low = _number(leaf, index + 1)
                if low is not None and _is_word(leaf, low[1], "and"):
                    high = _number(leaf, low[1] + 1)
                    if high is not None and high[1] == len(leaf):
                        return entry[0], _floor_value(low[0], strict=False)
                if _is_parameter(leaf, index + 1) and _is_word(leaf, index + 2, "and"):
                    return entry[0], None

    # Mirrored form: ``80 <= opportunity_score``.
    reversed_bound = _number(leaf, 0)
    parameter_first = _is_parameter(leaf, 0)
    if reversed_bound is not None or parameter_first:
        index = reversed_bound[1] if reversed_bound is not None else 1
        if index < len(leaf) and leaf[index].kind == "punct" and leaf[index].text in {"<=", "<"}:
            mirrored = _qualified_name(leaf, index + 1)
            if mirrored is not None and mirrored[1] == len(leaf):
                entry = SQL_NUMERIC_FLOOR_COLUMNS.get(mirrored[0])
                if entry is not None:
                    if reversed_bound is None:
                        return entry[0], None
                    return entry[0], _floor_value(
                        reversed_bound[0], strict=leaf[index].text == "<"
                    )
    return None


def _is_word(leaf: tuple[_Token, ...], index: int, word: str) -> bool:
    return index < len(leaf) and leaf[index].kind == "word" and leaf[index].text == word


def _leaf_text(sql: str, leaf: tuple[_Token, ...]) -> str:
    raw = sql[leaf[0].start : leaf[-1].end]
    return _WHITESPACE_RE.sub(" ", _COMMENT_RE.sub(" ", raw)).strip().lower()


def _disclosure_name(key: str) -> str:
    for column, (filter_key, _) in SQL_NUMERIC_FLOOR_COLUMNS.items():
        if filter_key == key:
            return f"{column}{_DISCLOSURE_SUFFIX}"
    return f"{key}{_DISCLOSURE_SUFFIX}"


def read_sql_filters(sql_query: str | None) -> SqlFilterReading:
    """Read the outermost statement's own row filters. Never raises."""

    if not sql_query or not sql_query.strip() or len(sql_query) > _MAX_SQL_CHARS:
        return _EMPTY_READING
    tokens = _tokenize(sql_query)
    if not tokens:
        return _EMPTY_READING
    regions = _filter_regions(tokens)
    if not regions:
        return _EMPTY_READING

    leaves: list[tuple[_Token, ...]] = []
    for region in regions:
        leaves.extend(_conjuncts(region))
    if not leaves:
        return _EMPTY_READING

    bounds: dict[str, set[int | None]] = {}
    for leaf in leaves:
        bound = _leaf_bound(leaf)
        if bound is None:
            continue
        bounds.setdefault(bound[0], set()).add(bound[1])

    floors: dict[str, int] = {}
    unreplayable: list[str] = []
    for key, values in bounds.items():
        maximum = _FLOOR_CEILINGS.get(key, 0)
        # Several disagreeing bounds on one column are not one cohort floor,
        # and a parameter's value is unknowable. Both are disclosed, never
        # guessed.
        if len(values) != 1 or None in values:
            unreplayable.append(_disclosure_name(key))
            continue
        floor = values.pop()
        if floor is None or not 0 <= floor <= maximum:
            unreplayable.append(_disclosure_name(key))
            continue
        floors[key] = floor
    return SqlFilterReading(
        floors=floors,
        unreplayable=tuple(sorted(unreplayable)),
        predicates=tuple(_leaf_text(sql_query, leaf) for leaf in leaves),
    )
