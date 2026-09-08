"""Safety-only normalization helpers for governed marketing text."""

import re


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


def _unfold_capital_i_word(word: str) -> str:
    other_letters = [char for char in word if char.isalpha() and char != "l"]
    if other_letters and all(char.isupper() for char in other_letters):
        return word.replace("l", "I")
    if word.startswith("l"):
        return "I" + word[1:]
    return word


def unfold_capital_i(value: str) -> str:
    """Invert the capital-I fold on a captured span, for a membership screen.

    :func:`ascii_confusable_folds` rewrites every ``I`` as ``l``, and the
    criterion state machine reads that variant alongside the text as written,
    so a governed place captured from it arrives as ``lllinois`` (title case)
    or ``lLLlNOlS`` (upper case) and a screen that lowercases the span cannot
    tell which ``l`` was an ``I``. One tripping variant refuses the whole
    prompt, so "Rank borrowers with a rate spread in Illinois." refused while
    the Texas twin answered (measured 2026-09-08, pre-existing).

    The fold is closed and case-directed, so its inverse is too. In a word
    whose other letters are all upper case every ``l`` was an ``I``; otherwise
    only a word-initial ``l`` can be one, because a title-case proper noun
    capitalizes its first letter alone. A screen tries the span as written
    FIRST, so a place that really starts with ``l`` ("Los Angeles") is never
    stopped by its own unfolded twin. This is a membership convenience on the
    allow side of a governed vocabulary, never a term detector: the protected
    term banks keep reading every variant unchanged.
    """

    return re.sub(r"[A-Za-z]+", lambda match: _unfold_capital_i_word(match.group(0)), value)
