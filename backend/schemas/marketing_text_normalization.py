"""Safety-only normalization helpers for governed marketing text."""

import re
from collections.abc import Callable

# Reviewed leetspeak substitutions. ``1`` is ambiguous, so both readings are
# scanned rather than picking one.
_LEET_DIGIT_TABLES = (
    str.maketrans("013457", "oleast"),
    str.maketrans("013457", "oieast"),
)
_DIGIT_RUN_RE = re.compile(r"[0-9]+")
# A number, including its thousands groups: the unit a reserved-spelling
# check has to reason about, because the scanner sees both the whole
# literal and each group on its own.
_NUMERIC_LITERAL_RE = re.compile(r"[0-9]+(?:[,.\u00a0\u202f ][0-9]{3})*")


def ascii_confusable_folds(value: str) -> set[str]:
    """Return the closed ASCII lookalike variants reviewed by marketing policy."""

    capital_i_folded = value.replace("I", "l")
    double_v_folded = re.sub(r"vv", "w", value, flags=re.IGNORECASE)
    return {
        value,
        capital_i_folded,
        double_v_folded,
        re.sub(r"vv", "w", capital_i_folded, flags=re.IGNORECASE),
        double_v_folded.replace("I", "l"),
    }


def _is_ascii_letter(char: str) -> bool:
    return ("A" <= char <= "Z") or ("a" <= char <= "z")


def in_word_leet_folds(
    value: str,
    *,
    mints_governed_term: Callable[[str], bool],
) -> set[str]:
    """Return leetspeak de-obfuscations, withholding only the fold that lies.

    The fold defeats digit substitution -- ``w0men``, ``mus1im``, ``b1ack``,
    ``l4tino``, and the separated forms ``mus 1 im`` and ``c-h-r-1-s-t-i-a-n``
    that the scanner's rejoin step is there to catch. It has to keep doing all
    of that, so folding is the default and this withholds it in exactly one
    case.

    That case: a digit run touching NO ASCII letter -- a number -- whose folded
    spelling is itself a governed term. ``4,140`` folds to ``a,lao``, the comma
    bounds ``lao`` as a word on its own, and ``lao`` is in the national-origin
    bank, so "NEWCASTLE has 4,140 borrowers" was withheld as protected-class
    targeting (captured live on paychex, 2026-08-12). Every comma group of
    ``140`` did it and ``1,405`` minted ``laos``.

    Both halves of that test are load-bearing, and an earlier revision of this
    fix had only the first. Withholding the fold from every isolated digit run
    also silences ``1`` in ``mus 1 im`` -- which folds to a harmless ``l`` and
    exists only so the halves rejoin -- and adversarial review measured 123
    protected terms going fully open on the Ask Genie posture as a result.
    Position says those two are the same; only the folded SPELLING tells them
    apart.

    ``mints_governed_term`` decides that, and the caller owns it because the
    vocabulary lives with the scanner. It is required rather than defaulted:
    the default would have to be one of the two behaviors this function exists
    to distinguish, and either one is silently wrong at a call site that meant
    the other.

    Checked per table. ``1`` reads as both ``l`` and ``i``, so ``140`` reserves
    only the ``lao`` spelling and still folds to the harmless ``iao`` -- which
    by construction is not a governed term, since that is exactly what the
    predicate just asked.
    """

    return {
        _fold_in_word_leet(value, table, mints_governed_term) for table in _LEET_DIGIT_TABLES
    }


def _fold_in_word_leet(
    value: str,
    table: dict[int, int],
    mints_governed_term: Callable[[str], bool],
) -> str:
    def fold_literal(match: re.Match[str]) -> str:
        literal = match.group()
        folded = literal.translate(table)
        start, end = match.span()
        before = value[start - 1] if start else ""
        after = value[end] if end < len(value) else ""
        if _is_ascii_letter(before) or _is_ascii_letter(after):
            return folded
        # A grouped number is checked whole AND per group. Downstream the
        # scanner both folds separators to spaces (making each group its own
        # word) and rejoins adjacent runs (splicing them back together), so
        # either shape can be the one that lands on a governed term:
        # ``4,140`` mints ``lao`` from a single group, ``1,405`` mints
        # ``laos`` only once ``l`` and ``aos`` are rejoined.
        groups = [g.translate(table) for g in _DIGIT_RUN_RE.findall(literal)]
        candidates = [*groups, "".join(groups)] if len(groups) > 1 else groups
        return literal if any(mints_governed_term(c) for c in candidates) else folded

    return _NUMERIC_LITERAL_RE.sub(fold_literal, value)
