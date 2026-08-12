"""Safety-only normalization helpers for governed marketing text."""

import re

# Reviewed leetspeak substitutions. ``1`` is ambiguous, so both readings are
# scanned rather than picking one.
_LEET_DIGIT_TABLES = (
    str.maketrans("013457", "oleast"),
    str.maketrans("013457", "oieast"),
)
_DIGIT_RUN_RE = re.compile(r"[0-9]+")


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


def in_word_leet_folds(value: str) -> set[str]:
    """Return leetspeak de-obfuscations, applied only INSIDE a word.

    The fold exists to defeat in-word digit substitution -- ``w0men``,
    ``mus1im``, ``b1ack``, ``l4tino`` -- which by construction always leaves
    real letters around the digit. A digit run that touches no ASCII letter is
    a NUMBER, carries no evasion signal, and must not be able to mint a
    protected term out of nothing.

    It could, before this rule existed. ``4,140`` folds to ``a,lao``; the
    separator fold downstream turns that into the bare word ``lao``, which is
    in the governed national-origin bank, so a Genie answer reading
    "NEWCASTLE has 4,140 borrowers" was withheld as protected-class targeting
    (captured live on 2026-08-12). Any comma group of ``140`` did it, and
    ``1,405`` minted ``laos``.

    Digit runs adjacent to a letter on EITHER side still fold, so leading and
    trailing substitutions (``0men``, ``as14n``) stay covered. This mirrors the
    in-word requirement the reviewed symbol confusables already apply.
    """

    return {_fold_in_word_leet(value, table) for table in _LEET_DIGIT_TABLES}


def _fold_in_word_leet(value: str, table: dict[int, str]) -> str:
    def fold_run(match: re.Match[str]) -> str:
        start, end = match.span()
        before = value[start - 1] if start else ""
        after = value[end] if end < len(value) else ""
        if _is_ascii_letter(before) or _is_ascii_letter(after):
            return match.group().translate(table)
        return match.group()

    return _DIGIT_RUN_RE.sub(fold_run, value)
