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
arms, comments, CTE and subquery bodies -- is structurally out of reach
because the scanner never visits it.

String literals are opaqued, not skipped
----------------------------------------
The tokenizer emits a literal as a contentless ``string`` token, so a
threshold inside one can never become a floor. ``predicates`` (the accepted
conjuncts, which callers regex for reviewed criteria) is rebuilt from those
tokens rather than re-sliced from the raw statement, and a literal body is
reproduced only when it is a bare ``[A-Za-z0-9_]`` word -- enough for a real
``recommended_offer_code IN ('heloc','refi_plus_heloc')`` predicate, never
enough to spell one. Re-slicing the raw text handed the criteria readers the
literal's body, and DATA fabricated narrowing criteria: ``note =
'is_owner_occupied = true'`` yielded ``occupancy="Owner-occupied"``
(adversarial review 2026-08-11).

Position is not grain
---------------------
An outer WHERE stands in filter position even when the outermost FROM is not
a base table. If a CTE re-aggregates and REUSES the column name, the outer
bound filters GROUPS while the Lead Queue would replay it against individual
borrowers::

    WITH state_scores AS (
      SELECT state, CAST(ROUND(AVG(opportunity_score)) AS INT) AS opportunity_score
      FROM mip.gold.borrower_360 GROUP BY state)
    SELECT state, opportunity_score FROM state_scores WHERE opportunity_score >= 40

Live 2026-08-11: the bound picked 2 states whose AVERAGE clears 40 (70,576
eligible borrowers); replayed per-borrower it left 44,268 -- 26,308 dropped
with no disclosure at all. So a conjunct is admitted only when every name it
mentions is bound by the outermost FROM to the BASE column of that name: a
bare pass-through survives (including through a GROUP BY, where a projected
column must be a grouping key), an expression, an aggregate or a rename does
not, and a source this reader cannot resolve rebinds everything. A refused
bound is DISCLOSED, never dropped in silence.

What it deliberately refuses
----------------------------
* Set operations (UNION/INTERSECT/EXCEPT): the population is not one filter.
* Outer-join ON clauses: they do not filter the preserved side.
* CTE/subquery bodies: a real filter can live there, and refusing it replays
  broader, which is disclosed. Guessing replays narrower, which is not.
* Bounds on a column the outermost FROM rebound (see "Position is not
  grain"), and every bound at all when a source is a table function, a
  LATERAL VIEW, a set operation or an unresolvable name.
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

from backend.schemas.genie_numeric_filters import GENIE_NUMERIC_FILTER_BOUNDS
from backend.services.genie_sql_tokens import (
    CLAUSE_WORDS,
    COMMENT_RE,
    NON_FILTERING_JOIN_WORDS,
    SET_OPERATOR_WORDS,
    WHITESPACE_RE,
    Token,
    is_base_grain,
    is_word,
    matching_paren,
    qualified_name,
    rebound_columns,
    tokenize,
    unwrap,
)
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
# Column -> the cohort filter key it feeds. The RANGE is deliberately NOT
# repeated here: this module used to carry its own ceiling table, which made
# it a SIXTH per-vocabulary copy that the "one definition, five readers"
# collapse missed -- and it disagreed with the canonical minimum on the one
# signed column, so a negative rate-spread floor was parsed correctly and
# then rejected right here, before it could ever reach a cohort (found by
# adversarial review 2026-08-11; the workstream that "enabled" it was dead
# below this line). Bounds come from the shared table or not at all.
SQL_NUMERIC_FLOOR_COLUMNS: dict[str, str] = {
    "opportunity_score": "min_opportunity_score",
    "equity_pct": "min_equity_pct",
    "rate_spread_bps": "min_rate_spread_bps",
}
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

# A string literal's body is reproduced in ``predicates`` only when it is a
# bare word: enough for a real offer-code predicate, never enough to spell a
# predicate of its own.
_OPAQUE_LITERAL = "''"
_PLAIN_LITERAL_RE = re.compile(r"[A-Za-z0-9_]*")

_MAX_SQL_CHARS = 200_000

# Words that are BOTH a clause keyword and a scalar builtin. Only these get the
# "followed by `(` means function call" treatment -- `WHERE (`, `ON (` and
# `QUALIFY (` are all ordinary parenthesised predicates, so a blanket rule
# would stop opening filter regions at all.
_CLAUSE_WORDS_THAT_ARE_ALSO_FUNCTIONS = frozenset({"left", "right"})


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
    unread_predicates: tuple[str, ...] = ()
    """Normalized text of every filter-region conjunct this reader refused.

    READ THE WHOLE SPAN OR NOTHING. These are the shapes the floor reader will
    not reason about -- ORs, negations, bounds over a re-aggregating CTE -- so
    a consumer may only accept one by proving the ENTIRE span reduces to a
    predicate it can replay exactly, the way
    ``_segment_selection_from_sql`` proves a span is nothing but a disjunction
    of segment membership tests. Lifting a value out of a span whose remainder
    you have not accounted for is the truncation bug this module exists to
    prevent: in ``a OR b`` the ``a`` alone is narrower than the answer, and in
    ``NOT a`` it is the complement.
    """
    unread_filter_words: frozenset[str] = frozenset()
    """Word tokens of every filter-region conjunct this reader refused.

    A refused conjunct -- an OR, a negation, a bound over a re-aggregating CTE
    -- is simply not replayed, so the queue opens a cohort BROADER than the
    answer's. This field exists so a caller can name that on the way past
    instead of diverging in silence.

    It carries word tokens only. String literals and numbers tokenize as their
    own kinds and never appear here, so this can answer "was this column
    filtered in a shape you could not read?" and structurally cannot answer
    "what value did it filter on?" -- the narrowing question that every
    deleted text-matching reader got wrong.
    """


_EMPTY_READING = SqlFilterReading(floors={})


def _filter_regions(tokens: list[Token]) -> tuple[tuple[Token, ...], ...] | None:
    """Token spans of the outermost statement's own row filters.

    Only WHERE, QUALIFY, and the ON of a plain/INNER join qualify. Everything
    nested inside parentheses -- CTE bodies, subqueries, aggregate ``FILTER
    (WHERE ...)`` clauses, function arguments -- sits at depth > 0 and is
    never visited.
    """

    while tokens and tokens[-1].kind == "punct" and tokens[-1].text == ";":
        tokens = tokens[:-1]
    tokens = unwrap(tokens)
    if not tokens:
        return None
    if tokens[0].kind != "word" or tokens[0].text not in {"select", "with"}:
        return None

    regions: list[tuple[Token, ...]] = []
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
        if word in _CLAUSE_WORDS_THAT_ARE_ALSO_FUNCTIONS and (
            index + 1 < len(tokens)
            and tokens[index + 1].kind == "punct"
            and tokens[index + 1].text == "("
        ):
            # `LEFT(` / `RIGHT(` is a string builtin, not a join keyword. Both
            # words live in CLAUSE_WORDS for LEFT/RIGHT JOIN, so a call closed
            # the region mid-predicate: `WHERE array_contains(segment_codes,
            # 'itm') OR LEFT(zip,3)='606'` handed the caller the fragment
            # `array_contains(segment_codes,'itm') or`, which reads as a pure
            # membership test. The queue replayed `itm` alone against an answer
            # asking for `itm OR zip-prefix` -- a strict subset disclosed to
            # nobody (adversarial review 2026-08-11). A join keyword is never
            # followed by `(`, and this runs before the join bookkeeping so a
            # call cannot mark a later JOIN as non-filtering either.
            continue
        if word in SET_OPERATOR_WORDS:
            return None
        if word in NON_FILTERING_JOIN_WORDS:
            join_modifier = True
        elif word == "join":
            join_on_filters = not join_modifier
            join_modifier = False
        if word not in CLAUSE_WORDS:
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
    tokens: tuple[Token, ...],
) -> list[tuple[Token, ...]] | None:
    """Split on top-level AND. ``None`` means an OR makes nothing guaranteed.

    ``BETWEEN a AND b`` owns its AND, so it never splits a leaf.
    """

    parts: list[tuple[Token, ...]] = []
    current: list[Token] = []
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


def _conjuncts(
    tokens: tuple[Token, ...],
    dropped: list[tuple[Token, ...]] | None = None,
    disjunctions: list[tuple[Token, ...]] | None = None,
) -> list[tuple[Token, ...]]:
    """Leaf predicates that must ALL hold for a row to survive.

    Spans this refuses are appended to ``dropped`` when a caller passes one.
    Dropping is always the BROADENING direction — the queue replays fewer
    filters than the answer applied — but a caller that wants to say so out
    loud needs to know a filter was there at all.

    ``disjunctions`` collects the subset a caller may legitimately try to
    re-read: whole spans refused ONLY because they are a top-level OR. A
    NEGATED span is deliberately not among them -- its population is the
    complement, so any part of it read as membership is inverted.
    """

    parts = _split_conjunction(tokens)
    if parts is None:
        if dropped is not None:
            dropped.append(tokens)
        if disjunctions is not None:
            disjunctions.append(tokens)
        return []
    leaves: list[tuple[Token, ...]] = []
    for part in parts:
        if not part:
            continue
        if part[0].kind == "word" and part[0].text == "not":
            # Negated: the surviving population is the complement, which no
            # cohort floor can express. Drop it rather than invert it.
            if dropped is not None:
                dropped.append(part)
            continue
        if (
            part[0].kind == "punct"
            and part[0].text == "("
            and matching_paren(part, 0) == len(part) - 1
        ):
            leaves.extend(_conjuncts(part[1:-1], dropped, disjunctions))
            continue
        leaves.append(part)
    return leaves



def _number(tokens: tuple[Token, ...], start: int) -> tuple[float, int] | None:
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


def _is_parameter(tokens: tuple[Token, ...], start: int) -> bool:
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


def _high_opportunity_leaf(leaf: tuple[Token, ...]) -> bool:
    """True for a bare ``fn_high_opportunity(...)`` predicate (optionally = TRUE)."""

    named = qualified_name(leaf, 0)
    if named is None or named[0] != _HIGH_OPPORTUNITY_FN:
        return False
    index = named[1]
    if index >= len(leaf) or leaf[index].kind != "punct" or leaf[index].text != "(":
        return False
    close = matching_paren(leaf, index)
    if close < 0:
        return False
    rest = leaf[close + 1 :]
    if not rest:
        return True
    texts = [token.text for token in rest]
    return texts in (["=", "true"], ["is", "true"])


def _leaf_bound(leaf: tuple[Token, ...]) -> tuple[str, int | None] | None:
    """Read one conjunct as a cohort floor.

    Returns ``(filter key, floor)``; a ``None`` floor means the bound is real
    but unknowable here (a bound parameter). Anything that is not exactly a
    bare column against a literal is refused -- an arithmetic or wrapped
    column (``ABS(rate_spread_bps) >= 25``) is a different population.
    """

    if _high_opportunity_leaf(leaf):
        return _HIGH_OPPORTUNITY_KEY, HIGH_OPPORTUNITY_THRESHOLD

    named = qualified_name(leaf, 0)
    if named is not None:
        column, index = named
        entry = SQL_NUMERIC_FLOOR_COLUMNS.get(column)
        if entry is not None and index < len(leaf):
            operator = leaf[index]
            if operator.kind == "punct" and operator.text in {">=", ">"}:
                strict = operator.text == ">"
                number = _number(leaf, index + 1)
                if number is not None and number[1] == len(leaf):
                    return entry, _floor_value(number[0], strict=strict)
                if _is_parameter(leaf, index + 1) and index + 2 == len(leaf):
                    return entry, None
            if operator.kind == "word" and operator.text == "between":
                low = _number(leaf, index + 1)
                if low is not None and is_word(leaf, low[1], "and"):
                    high = _number(leaf, low[1] + 1)
                    if high is not None and high[1] == len(leaf):
                        return entry, _floor_value(low[0], strict=False)
                if _is_parameter(leaf, index + 1) and is_word(leaf, index + 2, "and"):
                    return entry, None

    # Mirrored form: ``80 <= opportunity_score``.
    reversed_bound = _number(leaf, 0)
    parameter_first = _is_parameter(leaf, 0)
    if reversed_bound is not None or parameter_first:
        index = reversed_bound[1] if reversed_bound is not None else 1
        if index < len(leaf) and leaf[index].kind == "punct" and leaf[index].text in {"<=", "<"}:
            mirrored = qualified_name(leaf, index + 1)
            if mirrored is not None and mirrored[1] == len(leaf):
                entry = SQL_NUMERIC_FLOOR_COLUMNS.get(mirrored[0])
                if entry is not None:
                    if reversed_bound is None:
                        return entry, None
                    return entry, _floor_value(
                        reversed_bound[0], strict=leaf[index].text == "<"
                    )
    return None


def _leaf_text(sql: str, leaf: tuple[Token, ...]) -> str:
    """Rebuild one conjunct from its TOKENS, with literal bodies opaqued.

    Re-slicing the raw statement handed the criteria readers text the
    tokenizer had deliberately made contentless, so a string literal's body
    was scanned as if it were SQL: ``note = 'is_owner_occupied = true'``
    fabricated ``occupancy="Owner-occupied"`` out of DATA (adversarial review
    2026-08-11). A body is reproduced only when it is a bare word, which
    keeps a real ``recommended_offer_code IN ('heloc','refi_plus_heloc')``
    readable without letting any literal spell a predicate.
    """

    pieces: list[str] = []
    cursor = leaf[0].start
    for token in leaf:
        if token.start > cursor:
            # Only whitespace and comments can sit between two tokens.
            pieces.append(COMMENT_RE.sub(" ", sql[cursor : token.start]))
        if token.kind == "string":
            body = sql[token.start + 1 : token.end - 1]
            plain = _PLAIN_LITERAL_RE.fullmatch(body)
            pieces.append(f"'{body}'" if plain else _OPAQUE_LITERAL)
        else:
            pieces.append(sql[token.start : token.end])
        cursor = token.end
    return WHITESPACE_RE.sub(" ", "".join(pieces)).strip().lower()


def _disclosure_name(key: str) -> str:
    for column, filter_key in SQL_NUMERIC_FLOOR_COLUMNS.items():
        if filter_key == key:
            return f"{column}{_DISCLOSURE_SUFFIX}"
    return f"{key}{_DISCLOSURE_SUFFIX}"


def read_sql_filters(sql_query: str | None) -> SqlFilterReading:
    """Read the outermost statement's own row filters. Never raises."""

    if not sql_query or not sql_query.strip() or len(sql_query) > _MAX_SQL_CHARS:
        return _EMPTY_READING
    tokens = tokenize(sql_query)
    if not tokens:
        return _EMPTY_READING
    regions = _filter_regions(tokens)
    if not regions:
        return _EMPTY_READING

    leaves: list[tuple[Token, ...]] = []
    dropped: list[tuple[Token, ...]] = []
    disjunctions: list[tuple[Token, ...]] = []
    for region in regions:
        leaves.extend(_conjuncts(region, dropped, disjunctions))

    # A conjunct only means what it says when the outermost FROM still binds
    # its names to the base columns: an outer bound over a re-aggregating CTE
    # filters GROUPS, and replaying it per borrower silently truncates.
    rebound = rebound_columns(tokens)
    # Only a whole span refused for SHAPE, at base grain, may be re-read.
    # Publishing every refused span let a caller re-admit exactly what the
    # grain gate had just thrown out: `WITH pool AS (SELECT (in_the_money OR
    # listed_for_sale) AS in_the_money ...) SELECT ... WHERE in_the_money`
    # was replayed as the `itm` segment alone (adversarial review
    # 2026-08-11). The floor reader discloses that refusal; re-reading it
    # silently is strictly worse than never publishing it.
    readable = [span for span in disjunctions if is_base_grain(span, rebound)]
    readable_texts = tuple(_leaf_text(sql_query, span) for span in readable)
    accounted = {id(span) for span in readable}

    def _unread_words() -> frozenset[str]:
        return frozenset(
            token.text
            for span in dropped
            if id(span) not in accounted
            for token in span
            if token.kind == "word"
        )

    if not leaves:
        return SqlFilterReading(
            floors={},
            unread_predicates=readable_texts,
            unread_filter_words=_unread_words(),
        )
    bounds: dict[str, set[int | None]] = {}
    unreplayable: list[str] = []
    accepted: list[tuple[Token, ...]] = []
    for leaf in leaves:
        bound = _leaf_bound(leaf)
        if not is_base_grain(leaf, rebound):
            if bound is not None:
                unreplayable.append(_disclosure_name(bound[0]))
            dropped.append(leaf)
            continue
        accepted.append(leaf)
        if bound is None:
            continue
        bounds.setdefault(bound[0], set()).add(bound[1])

    floors: dict[str, int] = {}
    for key, values in bounds.items():
        minimum, maximum = GENIE_NUMERIC_FILTER_BOUNDS.get(key, (0, 0))
        # Several disagreeing bounds on one column are not one cohort floor,
        # and a parameter's value is unknowable. Both are disclosed, never
        # guessed.
        if len(values) != 1 or None in values:
            unreplayable.append(_disclosure_name(key))
            continue
        floor = values.pop()
        if floor is None or not minimum <= floor <= maximum:
            unreplayable.append(_disclosure_name(key))
            continue
        floors[key] = floor
    return SqlFilterReading(
        floors=floors,
        unreplayable=tuple(sorted(set(unreplayable))),
        predicates=tuple(_leaf_text(sql_query, leaf) for leaf in accepted),
        unread_predicates=readable_texts,
        unread_filter_words=_unread_words(),
    )
