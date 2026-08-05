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
